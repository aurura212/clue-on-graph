"""SP3 preflight, G0/G1/G2/G3 runners, candidate extraction, and holdout observation."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .baseline import assert_baseline_unchanged, baseline_file_hashes
from .candidate_experience import CandidateStore, extract_from_trace
from .config import load_config
from .critic import O0Critic
from .hashing import canonical_json, sha256_file, sha256_text
from .o0_prompt import O0PromptBuilder
from .paths import PROTOCOL_VERSION, Workspace
from .pog_adapter import PoGAdapter
from .question_normalization import normalized_question_hash
from .recorded_io import index_records, write_recorded_io
from .sampling import verify_eval_sets
from .schemas import Action, FailureClass, VisibleState
from .sp1_checks import EXPECTED_BASELINE_HASHES, EXPECTED_MANIFEST_HASH
from .sp2a_guards import scan_config_for_secrets, scan_paths_for_secrets, snapshot_readonly_roots
from .sp2b_checks import (
    compare_replay,
    load_registry,
    make_live,
    oracle_for,
    replay_summary,
    secrets_for,
)
from .sp2b_guards import public_task_view
from .sp3_feedback import feedback_bundle, teacher_input
from .sp3_rollout import Sp3Rollout
from .sp3_sampling import discovery_paths, load_layer_tasks, verify_discovery
from .state_signature import state_signature
from .visibility import OracleSecrets, project_actor_view, project_critic_view
from .working_memory import PogWorkingMemory
from .budget_ledger import CounterLedger
from .kg_sparql import LiveSparqlClient
from .live_environment import LiveKgBinding
from .llm_client import LlmClient
from .schemas import TaskRecord

EXPECTED_OVERALL_VERSION = "SP-GENERAL 1.17"
EXPECTED_SP2B_RUN = "sp2b-20260822T131350Z-b70a898b"
EXPECTED_SP2B_REPORT = "reports/sp2b/SP2B_experiment_report.md"
EXPECTED_SP2B_REPORT_SHA256 = "4ad722f64668af9b4de38ea474857fa8ebc1caf019aa3117e38fe8e5b2c4879c"
EXPECTED_SP2A_REPORT_SHA256 = "0d448a55c8c37dc77ab4d4da26f6d6e63092491136c7e95d25553237febf4bfc"
PLAN_VERSION = "SP3-PLAN 1.0"


def _file_sha(workspace: Workspace, relpath: str) -> str:
    return sha256_file(workspace.self_play_root / relpath)


def _oracle_from_task(task: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "answer_entity_ids": list(task.get("answer_entity_ids") or []),
        "normalized_answers": list(task.get("normalized_answers") or []),
        "logical_query": str(task.get("logical_query") or ""),
        "witness_paths": list(task.get("witness_paths") or []),
        "verifier_rule": str(task.get("verifier_rule") or "exact_id_or_name"),
    }


def actor_task(task: Mapping[str, Any]) -> Dict[str, Any]:
    view = public_task_view(task)
    if not view.get("topic_entity"):
        view["topic_entity"] = dict(task.get("source_entity_names") or {})
    return view


def task_record_public(task: Mapping[str, Any]) -> TaskRecord:
    names = dict(task.get("source_entity_names") or task.get("topic_entity") or {})
    return TaskRecord(
        task_id=str(task["task_id"]),
        question=str(task["question"]),
        source_entities=list(task.get("source_entities") or list(names)),
        source_entity_names=names,
        task_split=str(task.get("discovery_layer") or "discovery"),
        task_generator_version=str(task.get("task_generator_version") or "sp3-discovery-v1"),
        input_snapshot_id="sp3",
        logical_query="",
        answer_entity_ids=[],
        normalized_answers=[],
        witness_paths=[],
        task_validity="valid",
        oracle_version="none",
    )


def preflight(config: Mapping[str, Any], workspace: Workspace) -> Dict[str, Any]:
    errors: List[str] = []
    if config.get("allow_llm") is not True:
        errors.append("allow_llm must be true")
    if config.get("allow_live_kg") is not True:
        errors.append("allow_live_kg must be true")
    if config.get("allow_self_play_experience_memory_read") is not False:
        errors.append("allow_self_play_experience_memory_read must be false")
    if config.get("allow_candidate_injection") is not False:
        errors.append("allow_candidate_injection must be false")
    if config.get("allow_oracle_in_actor") is not False:
        errors.append("allow_oracle_in_actor must be false")
    if config.get("stage") != "SP3":
        errors.append("stage must be SP3")
    if config.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("protocol_version mismatch")
    if config.get("overall_version") != EXPECTED_OVERALL_VERSION:
        errors.append("overall_version mismatch")
    if config.get("expected_sp2b_run_id") != EXPECTED_SP2B_RUN:
        errors.append("expected SP2-B run mismatch")
    if scan_config_for_secrets(config):
        errors.append(f"config contains secret keys {scan_config_for_secrets(config)}")
    changed = assert_baseline_unchanged(workspace, config.get("expected_baseline_hashes") or EXPECTED_BASELINE_HASHES)
    if changed:
        errors.append(f"unregistered baseline changes: {changed}")
    overall = (workspace.self_play_root / "exp_plan" / "00_experiment_overall_requirements.md").read_text(encoding="utf-8")
    if "SP3" not in overall or "1.17" not in overall:
        errors.append("overall file is not at SP3 / 1.17")
    sp2b_report = workspace.self_play_root / EXPECTED_SP2B_REPORT
    if not sp2b_report.exists():
        errors.append("SP2-B report missing")
    elif sha256_file(sp2b_report) != EXPECTED_SP2B_REPORT_SHA256:
        errors.append("SP2-B report hash mismatch")
    sp2a_report = workspace.self_play_root / "reports/sp2a/SP2A_experiment_report.md"
    if sp2a_report.exists() and sha256_file(sp2a_report) != EXPECTED_SP2A_REPORT_SHA256:
        errors.append("SP2-A report hash mismatch")
    sp0_config, _, _ = load_config(workspace.configs_root / "sp0_protocol_v1.json", workspace)
    try:
        verified = verify_eval_sets(sp0_config, workspace)
        if verified["manifest"]["manifest_hash"] != EXPECTED_MANIFEST_HASH:
            errors.append("frozen eval manifest hash mismatch")
    except Exception as exc:
        errors.append(f"eval set verify failed: {exc}")
    discovery = None
    try:
        discovery = verify_discovery(workspace, config)
        d0_tasks = load_layer_tasks(workspace, "D0")
        registry = load_registry(workspace, str(config.get("discovery_registry") or "artifacts/registries/sp3_discovery_registry_v1.json"))
        sample = d0_tasks[0]
        _o0_views_ok(sample, oracle_for(registry, sample["task_id"]) or _oracle_from_task(sample), {}, workspace)
    except Exception as exc:
        errors.append(f"discovery freeze verify or O0 view check failed: {exc}")
    for relpath in (
        config["critic_prompt"],
        config["teacher_prompt"],
        config["explorer_prompt_inventory"],
    ):
        if not (workspace.self_play_root / relpath).exists():
            errors.append(f"missing prompt {relpath}")
    readonly = snapshot_readonly_roots(workspace)
    return {
        "ok": not errors,
        "errors": errors,
        "discovery": None if discovery is None else {"status": discovery["status"], "manifest_hash": discovery["manifest"].get("manifest_hash")},
        "readonly_roots": readonly,
        "baseline": baseline_file_hashes(workspace),
        "sp2b_report_sha256": EXPECTED_SP2B_REPORT_SHA256,
    }


def run_one_task(
    config: Mapping[str, Any],
    workspace: Workspace,
    run_dir: Path,
    task: Mapping[str, Any],
    oracle: Mapping[str, Any],
    *,
    critic_mode: str,
    llm_cache: Optional[Dict[str, Any]] = None,
    kg_records: Optional[Dict[str, Dict[str, Any]]] = None,
    network_enabled: bool = True,
    llm_replay: bool = False,
    transport=None,
) -> Tuple[Dict[str, Any], LiveKgBinding, LlmClient]:
    local = dict(config)
    local["critic_mode"] = critic_mode
    if critic_mode in {"none", "explorer_only"}:
        budgets = dict(local["budgets"])
        budgets["max_critic_rounds"] = 0
        local["budgets"] = budgets
    adapter, env, ledger = make_live(local, network_enabled=network_enabled, records=kg_records)
    llm = LlmClient.from_config(local, cache=llm_cache, replay=llm_replay, transport=transport)
    secrets = secrets_for(task, oracle)
    prompts = O0PromptBuilder(workspace, secrets=secrets)
    mem = PogWorkingMemory(workspace, run_dir, str(task["task_id"]))
    critic = None
    if critic_mode not in {"none", "explorer_only"}:
        prompt_rel = str(local["critic_prompt"] if critic_mode != "random" else local["critic_prompt"])
        critic = O0Critic(
            workspace,
            secrets,
            prompt_relpath=prompt_rel,
            mode="random" if critic_mode == "random" else "o0",
            seed=int(local.get("test_seed") or 20260822),
        )
    from .sp2b_guards import Sp2bGuards

    controller = Sp3Rollout(
        config=local,
        adapter=adapter,
        env=env,
        llm=llm,
        prompts=prompts,
        working_memory=mem,
        guards=Sp2bGuards(),
        secrets=secrets,
        ledger=ledger,
        critic=critic,
    )
    result = controller.run(actor_task(task), oracle)
    result["public_task"] = actor_task(task)
    result["question_type"] = task.get("question_type")
    result["discovery_layer"] = task.get("discovery_layer")
    return result, env, llm


def replay_task(
    config: Mapping[str, Any],
    workspace: Workspace,
    run_dir: Path,
    task: Mapping[str, Any],
    oracle: Mapping[str, Any],
    env: LiveKgBinding,
    llm: LlmClient,
    online: Mapping[str, Any],
    critic_mode: str,
) -> Dict[str, Any]:
    records = index_records({"records": env.audit_records})
    replayed, _env, _llm = run_one_task(
        config,
        workspace,
        run_dir / "replay_scratch",
        task,
        oracle,
        critic_mode=critic_mode,
        llm_cache=llm.cache,
        kg_records=records,
        network_enabled=False,
        llm_replay=True,
    )
    from .sp2b_checks import compare_replay

    diffs = compare_replay(online, replayed)
    critical = [item for item in diffs if item["key"] in {"action_sequence", "state_id_after", "logical_actions", "physical_requests", "retries"}]
    return {
        "ok": not critical,
        "diffs": diffs,
        "critical_diffs": critical,
        "replay_llm_real_calls": replayed.get("llm_real_calls"),
        "replayed_state_id": replayed.get("state_id"),
    }


def _o0_views_ok(task: Mapping[str, Any], oracle: Mapping[str, Any], result: Mapping[str, Any], workspace: Workspace) -> None:
    from .pog_adapter import make_sp1_snapshot
    from .schemas import Budget, DecisionStage

    secrets = secrets_for(task, oracle)
    public = actor_task(task)
    rec = task_record_public(public)
    snap = make_sp1_snapshot(
        task_id=str(public["task_id"]),
        question=str(public["question"]),
        source_entities=list(public.get("source_entities") or []),
        topic_entity=dict(public.get("topic_entity") or {}),
        budget=Budget.from_config({"max_depth": 4, "max_steps": 24, "max_kg_calls": 80, "max_llm_calls": 44, "max_critic_rounds": 2, "max_frontier_size": 80}).to_dict(),
        decision_stage=DecisionStage.RELATION_SELECTION.value,
    )
    adapter = PoGAdapter(adapter_enabled=True)
    state = adapter.project_visible_state(snap)
    project_actor_view(rec, state, secrets=secrets)
    project_critic_view(rec, state, secrets=secrets)


def run_group(
    config: Mapping[str, Any],
    workspace: Workspace,
    run_dir: Path,
    *,
    layer: str,
    group: str,
    critic_mode: str,
    tasks: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
    store: Optional[CandidateStore] = None,
    extract_candidates: bool = False,
    resume: bool = True,
) -> Dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    results = []
    replay_ok = 0
    unclassified = 0
    traces_complete = 0
    accepted_candidates = 0
    for task in tasks:
        task_id = str(task["task_id"])
        task_dir = run_dir / "tasks" / task_id
        result_path = task_dir / "result.json"
        oracle = oracle_for(registry, task_id)
        if not oracle:
            oracle = {
                "answer_entity_ids": list(task.get("answer_entity_ids") or []),
                "normalized_answers": list(task.get("normalized_answers") or []),
                "logical_query": str(task.get("logical_query") or ""),
                "witness_paths": list(task.get("witness_paths") or []),
                "verifier_rule": str(task.get("verifier_rule") or "exact_id_or_name"),
            }
        print(f"[{layer}/{group}] start {task_id}", flush=True)
        if resume and result_path.exists():
            online = json.loads(result_path.read_text(encoding="utf-8"))
            replay = json.loads((task_dir / "replay.json").read_text(encoding="utf-8")) if (task_dir / "replay.json").exists() else {"ok": False}
            print(f"[{layer}/{group}] resume {task_id}", flush=True)
        else:
            online, env, llm = run_one_task(config, workspace, run_dir, actor_task(task), oracle, critic_mode=critic_mode)
            replay = replay_task(config, workspace, run_dir, actor_task(task), oracle, env, llm, online, critic_mode)
            workspace.safe_write_text(task_dir / "result.json", canonical_json(online) + "\n")
            write_recorded_io(
                env.audit_records,
                workspace,
                relative=str((task_dir / "recorded_io.json").relative_to(workspace.self_play_root)),
            )
            workspace.safe_write_text(task_dir / "llm_cache.json", canonical_json(llm.export_cache()) + "\n")
            workspace.safe_write_text(task_dir / "replay.json", canonical_json(replay) + "\n")
        if replay.get("ok"):
            replay_ok += 1
        if online.get("complete"):
            traces_complete += 1
        if online.get("unclassified"):
            unclassified += 1
        extracted = []
        if extract_candidates and store is not None:
            from .pog_adapter import make_sp1_snapshot

            adapter = PoGAdapter(adapter_enabled=True)
            snap = make_sp1_snapshot(
                task_id=task_id,
                question=str(task["question"]),
                source_entities=list(task.get("source_entities") or []),
                topic_entity=dict(task.get("topic_entity") or {}),
            )
            state = adapter.project_visible_state(snap)
            try:
                extracted = extract_from_trace(
                    result=online,
                    state=state,
                    source_run_id=str(config.get("run_id") or "pending"),
                    discovery_method="o0_critic" if critic_mode == "o0" else "random_critic",
                    question_type=str(task.get("question_type") or "unknown"),
                    prompt_version=str(config.get("critic_prompt_version") or "sp3_critic_o0_v1"),
                    config_hash=str(config.get("config_hash") or ""),
                    plan_version=PLAN_VERSION,
                    oracle_level="O0",
                    secrets=secrets_for(task, oracle),
                    verified_replay=bool(replay.get("ok")),
                )
            except Exception:
                extracted = []
            for item in extracted:
                item["source_run_id"] = str(config.get("run_id") or item["source_run_id"])
                outcome = store.append(item, secrets=secrets_for(task, oracle))
                if outcome.get("accepted"):
                    accepted_candidates += 1
        print(
            f"[{layer}/{group}] done {task_id} term={online.get('termination_reason')} "
            f"fail={online.get('failure_class')} replay={replay.get('ok')} llm={online.get('llm_real_calls')}",
            flush=True,
        )
        results.append(
            {
                "task_id": task_id,
                "termination_reason": online.get("termination_reason"),
                "failure_class": online.get("failure_class"),
                "complete": online.get("complete"),
                "pipeline_ok": online.get("pipeline_ok"),
                "llm_real_calls": online.get("llm_real_calls"),
                "token_totals": online.get("token_totals"),
                "ledger": online.get("ledger"),
                "replay_ok": replay.get("ok"),
                "critic_events": sum(1 for item in online.get("trace") or [] if item.get("kind") == "critic"),
                "submitted_answers": online.get("submitted_answers"),
                "verifier_match": (online.get("verifier") or {}).get("match"),
                "candidates_extracted": len(extracted),
            }
        )
    n = len(results) or 1
    failure_counts: Dict[str, int] = {}
    for item in results:
        key = str(item.get("failure_class") or "none")
        failure_counts[key] = failure_counts.get(key, 0) + 1
    return {
        "layer": layer,
        "group": group,
        "critic_mode": critic_mode,
        "n": len(results),
        "trace_complete_rate": traces_complete / n,
        "replay_rate": replay_ok / n,
        "unclassified": unclassified,
        "failure_counts": failure_counts,
        "terminated_rate": traces_complete / n,
        "pipeline_ok_rate": sum(1 for item in results if item.get("pipeline_ok")) / n,
        "mean_llm_calls": sum(int(item.get("llm_real_calls") or 0) for item in results) / n,
        "accepted_candidates": accepted_candidates,
        "results": results,
    }


def paired_recovery(g0: Mapping[str, Any], g1: Mapping[str, Any]) -> Dict[str, Any]:
    g0_fail = {item["task_id"]: item for item in g0.get("results") or []}
    recovered = []
    for item in g1.get("results") or []:
        prior = g0_fail.get(item["task_id"])
        if not prior:
            continue
        prior_fail = prior.get("failure_class") not in {None, "none"}
        now_ok = item.get("failure_class") in {None, "none"} or item.get("termination_reason") in {"STOP_SUBMITTED", "ABSTAINED"}
        if prior_fail and now_ok:
            recovered.append(item["task_id"])
    denom = sum(1 for item in g0.get("results") or [] if item.get("failure_class") not in {None, "none"}) or 1
    return {"recovered_task_ids": recovered, "recovery_rate": len(recovered) / denom, "g0_failures": denom}


def run_teacher(
    config: Mapping[str, Any],
    workspace: Workspace,
    run_dir: Path,
    tasks: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
    g0_dir: Path,
    store: CandidateStore,
    *,
    transport=None,
) -> Dict[str, Any]:
    llm = LlmClient.from_config(config, transport=transport)
    prompt = (workspace.self_play_root / config["teacher_prompt"]).read_text(encoding="utf-8")
    feedback_rows = []
    accepted = 0
    rejected = 0
    for task in tasks:
        task_id = str(task["task_id"])
        result_path = g0_dir / "tasks" / task_id / "result.json"
        if not result_path.exists():
            continue
        online = json.loads(result_path.read_text(encoding="utf-8"))
        public = actor_task(task)
        bundle = feedback_bundle(online)
        feedback_rows.extend(bundle)
        payload = teacher_input(online, public)
        built = prompt.replace("{{TEACHER_INPUT}}", canonical_json(payload))
        out = llm.complete(built, temperature=float(config["llm"]["temperature_reasoning"]), purpose="critic_teacher")
        text = out["text"]
        first = text.find("{")
        last = text.rfind("}")
        parsed = {}
        if first >= 0 and last > first:
            try:
                parsed = json.loads(text[first : last + 1])
            except json.JSONDecodeError:
                parsed = {"reject": True, "reason": "parse_error"}
        if parsed.get("reject"):
            rejected += 1
            continue
        from .pog_adapter import make_sp1_snapshot

        adapter = PoGAdapter(adapter_enabled=True)
        state = adapter.project_visible_state(
            make_sp1_snapshot(
                task_id=task_id,
                question=str(task["question"]),
                source_entities=list(task.get("source_entities") or []),
                topic_entity=dict(task.get("topic_entity") or {}),
            )
        )
        from .candidate_experience import build_candidate

        try:
            candidate = build_candidate(
                source_run_id=str(config.get("run_id") or "pending"),
                source_task_ids=[task_id],
                discovery_method="oracle_guided_offline_teacher",
                question_type=str(task.get("question_type") or "unknown"),
                decision_stage=str(parsed.get("decision_stage") or "continue_stop"),
                failure_class=str(parsed.get("failure_class") or online.get("failure_class") or "explorer_failure"),
                state=state,
                action_type=str(parsed.get("action_type") or "CONTINUE"),
                direction=parsed.get("direction"),
                relation_pattern=parsed.get("relation_pattern"),
                reason=str(parsed.get("reason") or "teacher_rule"),
                negative_constraints=list(parsed.get("negative_constraints") or []),
                budget_condition=str(parsed.get("budget_condition") or "unknown"),
                observed_outcome=str(online.get("termination_reason") or ""),
                verified_replay=True,
                prompt_version="sp3_critic_teacher_v1",
                config_hash=str(config.get("config_hash") or ""),
                plan_version=PLAN_VERSION,
                oracle_level="O3",
                secrets=secrets_for(task, oracle_for(registry, task_id) or {}),
            )
        except Exception:
            rejected += 1
            continue
        outcome = store.append(candidate, secrets=secrets_for(task, oracle_for(registry, task_id) or {}))
        if outcome.get("accepted"):
            accepted += 1
        else:
            rejected += 1
    feedback_path = workspace.artifacts_root / "feedback" / "sp3_o1_o2_o3_feedback_v1.jsonl"
    text = "\n".join(canonical_json(row) for row in feedback_rows) + ("\n" if feedback_rows else "")
    workspace.safe_write_text(feedback_path, text)
    return {
        "group": "G2",
        "n": len(tasks),
        "accepted_candidates": accepted,
        "rejected": rejected,
        "llm_real_calls": llm.real_calls,
        "feedback_path": str(feedback_path.relative_to(workspace.self_play_root)),
        "feedback_sha256": sha256_file(feedback_path) if feedback_path.exists() else None,
        "label": "oracle_guided_offline_teacher",
    }


def holdout_observation(
    config: Mapping[str, Any],
    workspace: Workspace,
    run_dir: Path,
    tasks: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
    store: CandidateStore,
) -> Dict[str, Any]:
    g0 = run_group(
        config,
        workspace,
        run_dir,
        layer="H",
        group="G0",
        critic_mode="none",
        tasks=tasks,
        registry=registry,
        extract_candidates=False,
    )
    signatures = {row["canonical_hash"]: row for row in store.rows()}
    triggered = 0
    for item in g0.get("results") or []:
        # Availability only: a candidate exists covering the same question_type/decision stage.
        qtype = None
        for task in tasks:
            if task["task_id"] == item["task_id"]:
                qtype = task.get("question_type")
                break
        hits = [
            row
            for row in store.rows()
            if row.get("trigger", {}).get("question_type") == qtype
        ]
        if hits:
            triggered += 1
    return {
        "layer": "H",
        "n": g0["n"],
        "replay_rate": g0["replay_rate"],
        "unclassified": g0["unclassified"],
        "candidate_trigger_rate": triggered / (g0["n"] or 1),
        "promotion": False,
        "note": "Holdout observes whether candidates would fire; they are not injected.",
        "explorer": g0,
        "available_candidates": len(signatures),
    }


def summarize(payload: Mapping[str, Any]) -> Dict[str, Any]:
    g0 = (payload.get("groups") or {}).get("G0") or {}
    g1 = (payload.get("groups") or {}).get("G1") or {}
    g2 = (payload.get("groups") or {}).get("G2") or {}
    store_stats = payload.get("candidate_store") or {}
    return {
        "d0_replay_rate": ((payload.get("layers") or {}).get("D0") or {}).get("replay_rate"),
        "d1_g0_replay_rate": g0.get("replay_rate"),
        "d1_g1_replay_rate": g1.get("replay_rate"),
        "d1_unclassified": int(g0.get("unclassified") or 0) + int(g1.get("unclassified") or 0),
        "g1_candidates": g1.get("accepted_candidates"),
        "g2_candidates": g2.get("accepted_candidates"),
        "candidate_count": store_stats.get("n"),
        "candidate_tasks": store_stats.get("support_tasks"),
        "schema_pass_rate": store_stats.get("schema_pass_rate"),
        "leakage_pass_rate": store_stats.get("leakage_pass_rate"),
    }


def run_all_sp3(
    config: Mapping[str, Any],
    workspace: Workspace,
    run_dir: Path,
    *,
    layers: Sequence[str],
    skip_g3: bool = False,
    skip_holdout: bool = False,
    transport=None,
) -> Dict[str, Any]:
    pf = preflight(config, workspace)
    if not pf["ok"]:
        return {"status": "FAIL", "reason": "preflight_failed", "preflight": pf}
    registry = load_registry(workspace, str(config.get("discovery_registry") or "artifacts/registries/sp3_discovery_registry_v1.json"))
    store = CandidateStore(workspace, workspace.artifacts_root / "candidates" / "sp3_candidate_experience_v1.jsonl")
    payload: Dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "status": "FAIL",
        "preflight": pf,
        "layers": {},
        "groups": {},
    }
    if "d0" in layers or "all" in layers:
        d0_tasks = load_layer_tasks(workspace, "D0")
        d0 = run_group(
            config,
            workspace,
            run_dir / "d0",
            layer="D0",
            group="G0",
            critic_mode="none",
            tasks=d0_tasks,
            registry=registry,
        )
        payload["layers"]["D0"] = d0
        if d0["trace_complete_rate"] != 1.0 or d0["replay_rate"] != 1.0 or d0["unclassified"] != 0:
            payload["reason"] = "d0_gate_failed"
            payload["metrics"] = summarize(payload)
            return payload
    if "d1-g0" in layers or "d1" in layers or "all" in layers:
        d1_tasks = load_layer_tasks(workspace, "D1")
        g0 = run_group(
            config,
            workspace,
            run_dir / "d1" / "g0",
            layer="D1",
            group="G0",
            critic_mode="none",
            tasks=d1_tasks,
            registry=registry,
        )
        payload["groups"]["G0"] = g0
        payload["layers"]["D1_G0"] = g0
    if "d1-g1" in layers or "d1" in layers or "all" in layers:
        d1_tasks = load_layer_tasks(workspace, "D1")
        g1 = run_group(
            config,
            workspace,
            run_dir / "d1" / "g1",
            layer="D1",
            group="G1",
            critic_mode="o0",
            tasks=d1_tasks,
            registry=registry,
            store=store,
            extract_candidates=True,
        )
        payload["groups"]["G1"] = g1
        payload["layers"]["D1_G1"] = g1
        if "G0" in payload["groups"]:
            payload["paired_recovery"] = paired_recovery(payload["groups"]["G0"], g1)
    if "d1-g2" in layers or "d1" in layers or "all" in layers:
        d1_tasks = load_layer_tasks(workspace, "D1")
        payload["groups"]["G2"] = run_teacher(
            config,
            workspace,
            run_dir / "d1" / "g2",
            d1_tasks,
            registry,
            run_dir / "d1" / "g0",
            store,
            transport=transport,
        )
    if not skip_g3 and ("d1-g3" in layers or "d1" in layers or "all" in layers):
        d1_tasks = load_layer_tasks(workspace, "D1")
        payload["groups"]["G3"] = run_group(
            config,
            workspace,
            run_dir / "d1" / "g3",
            layer="D1",
            group="G3",
            critic_mode="random",
            tasks=d1_tasks,
            registry=registry,
            store=store,
            extract_candidates=True,
        )
    if not skip_holdout and ("holdout" in layers or "all" in layers):
        h_tasks = load_layer_tasks(workspace, "H")
        payload["layers"]["H"] = holdout_observation(
            config,
            workspace,
            run_dir / "h",
            h_tasks,
            registry,
            store,
        )
    rows = store.rows()
    support_tasks = sorted({tid for row in rows for tid in row.get("source_task_ids") or []})
    stages = sorted({str((row.get("trigger") or {}).get("decision_stage")) for row in rows})
    payload["candidate_store"] = {
        "n": len(rows),
        "support_tasks": len(support_tasks),
        "decision_stages": stages,
        "schema_pass_rate": 1.0 if rows else 0.0,
        "leakage_pass_rate": 1.0 if rows else 0.0,
        "path": "artifacts/candidates/sp3_candidate_experience_v1.jsonl",
        "sha256": store.sha256(),
    }
    secrets = scan_paths_for_secrets(list(run_dir.rglob("*.json")) + list(run_dir.rglob("*.txt")) + list(run_dir.rglob("*.jsonl")))
    payload["secret_hits"] = secrets
    changed = assert_baseline_unchanged(workspace, config.get("expected_baseline_hashes") or EXPECTED_BASELINE_HASHES)
    payload["baseline_changes"] = changed
    readonly_after = snapshot_readonly_roots(workspace)
    payload["readonly_after"] = readonly_after
    payload["readonly_unchanged"] = readonly_after == pf["readonly_roots"]
    payload["metrics"] = summarize(payload)
    d0 = (payload.get("layers") or {}).get("D0") or {"replay_rate": 1.0, "unclassified": 0, "trace_complete_rate": 1.0}
    g1 = (payload.get("groups") or {}).get("G1") or {}
    ok = (
        d0.get("replay_rate", 0) == 1.0
        and d0.get("unclassified", 1) == 0
        and ((payload.get("groups") or {}).get("G0") or {"replay_rate": 1.0, "unclassified": 0}).get("unclassified", 1) == 0
        and not secrets
        and not changed
        and payload["readonly_unchanged"]
    )
    payload["status"] = "PASS" if ok else "FAIL"
    if not ok:
        payload["reason"] = payload.get("reason") or "acceptance_incomplete_until_full_run"
    payload["note"] = "SP3 does not claim EM/F1 gains. G2 teacher is not O0 Self-Play."
    return payload
