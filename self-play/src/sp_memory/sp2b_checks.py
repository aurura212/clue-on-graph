"""SP2-B B0/B1/B2 runners: LLM+live KG, no Self-Play Experience Memory."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .baseline import assert_baseline_unchanged, baseline_file_hashes
from .config import load_config
from .budget_ledger import CounterLedger
from .hashing import canonical_hash, canonical_json, sha256_file, sha256_text
from .kg_sparql import LiveSparqlClient
from .live_environment import LiveKgBinding
from .llm_client import LlmClient, PROMPT_VERSION
from .o0_prompt import O0PromptBuilder, prompt_inventory
from .paths import PROTOCOL_VERSION, Workspace
from .pog_adapter import PoGAdapter
from .question_normalization import normalized_question_hash
from .recorded_io import diff_replay, index_records, write_recorded_io
from .rollout import Sp2bRollout
from .sampling import eval_set_paths, verify_eval_sets
from .schemas import FailureClass
from .sp1_checks import EXPECTED_BASELINE_HASHES, EXPECTED_EVAL_HASHES, EXPECTED_MANIFEST_HASH
from .sp2a_guards import (
    load_exclusion_task_ids,
    scan_config_for_secrets,
    scan_paths_for_secrets,
    snapshot_readonly_roots,
)
from .sp2b_guards import Sp2bGuards, is_eval_task_id, public_task_view
from .visibility import OracleSecrets
from .working_memory import PogWorkingMemory

EXPECTED_OVERALL_VERSION = "SP-GENERAL 1.15"
EXPECTED_SP2A_RUN = "sp2a-20260822T082704Z-28a5bc97"
EXPECTED_SP2A_SUPP_RUN = "sp2a-supp-20260822T111116Z-79aa8ea8"
B1_REPLAY_THRESHOLD = 0.95


def _ok(name: str, metrics: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"name": name, "status": "PASS", "metrics": metrics}
    if extra:
        payload.update(extra)
    return payload


def _fail(name: str, metrics: Dict[str, Any], error: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"name": name, "status": "FAIL", "metrics": metrics, "error": error}
    if extra:
        payload.update(extra)
    return payload


def load_registry(workspace: Workspace, relpath: str) -> Dict[str, Any]:
    path = workspace.self_play_root / relpath
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_path"] = str(path)
    payload["_sha256"] = sha256_file(path)
    return payload


def oracle_for(registry: Mapping[str, Any], task_id: str) -> Dict[str, Any]:
    return dict((registry.get("oracle") or {}).get(task_id) or {})


def secrets_for(task: Mapping[str, Any], oracle: Mapping[str, Any]) -> OracleSecrets:
    return OracleSecrets(
        answer_entity_ids=list(oracle.get("answer_entity_ids") or []),
        normalized_answers=list(oracle.get("normalized_answers") or []),
        witness_tokens=[],
        logical_query=str(oracle.get("logical_query") or ""),
        future_neighbors=[],
    )


def make_live(config: Mapping[str, Any], *, network_enabled: bool, records=None) -> Tuple[PoGAdapter, LiveKgBinding, CounterLedger]:
    ledger = CounterLedger()
    client = LiveSparqlClient(
        endpoint=config["endpoint"],
        allowed_endpoints=config["allowed_endpoints"],
        timeout_sec=config["timeout_sec"],
        max_retries=config["max_retries"],
        retry_backoff_sec=config["retry_backoff_sec"],
        network_enabled=network_enabled,
        max_recorded_bindings=int(config.get("max_recorded_bindings") or 200),
    )
    env = LiveKgBinding(client, ledger, records=records or {}, network_enabled=network_enabled)
    adapter = PoGAdapter(
        adapter_enabled=True,
        allow_llm=True,
        allow_live_kg=True,
        stage="sp2b",
        environment=env,
        backtrack_state_policy=str(config.get("backtrack_state_policy") or "unsupported"),
    )
    return adapter, env, ledger


def replay_summary(result: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "status": result.get("termination_reason"),
        "failure_class": result.get("failure_class"),
        "submitted_answers": result.get("submitted_answers"),
        "state_id_after": result.get("state_id"),
        "logical_actions": (result.get("ledger") or {}).get("logical_actions"),
        "physical_requests": (result.get("ledger") or {}).get("physical_requests"),
        "retries": (result.get("ledger") or {}).get("retries"),
        "llm_real_calls": result.get("llm_real_calls"),
        "action_sequence": [
            item.get("action", {}).get("action_type")
            for item in result.get("trace") or []
            if item.get("kind") == "action"
        ],
        "canonical_targets": [
            item.get("environment_status")
            for item in result.get("trace") or []
            if item.get("kind") == "action"
        ],
        "budget": result.get("budget"),
    }


def compare_replay(online: Mapping[str, Any], replayed: Mapping[str, Any]) -> List[Dict[str, Any]]:
    left = replay_summary(online)
    right = replay_summary(replayed)
    keys = (
        "status",
        "failure_class",
        "submitted_answers",
        "state_id_after",
        "logical_actions",
        "physical_requests",
        "retries",
        "action_sequence",
        "canonical_targets",
        "budget",
    )
    diffs = []
    for key in keys:
        if left.get(key) != right.get(key):
            diffs.append({"key": key, "online": left.get(key), "replay": right.get(key)})
    return diffs


def exclusion_overlap(workspace: Workspace, tasks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    path = workspace.artifacts_root / "registries" / "benchmark_exclusion_registry_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    banned_ids = {str(item.get("task_id")) for item in payload.get("records") or []}
    banned_q = {str(item.get("normalized_question_hash")) for item in payload.get("records") or []}
    topic_mids: Set[str] = set()
    answer_mids: Set[str] = set()
    for item in payload.get("records") or []:
        topic_mids.update(item.get("topic_entities") or [])
        answer_mids.update(item.get("answer_entities") or [])
    overlaps = []
    question_hits = []
    id_hits = []
    for task in tasks:
        task_id = str(task["task_id"])
        if is_eval_task_id(task_id, banned_ids):
            id_hits.append(task_id)
        qh = normalized_question_hash(str(task["question"]))
        if qh in banned_q:
            question_hits.append({"task_id": task_id, "normalized_question_hash": qh})
        mids = set(task.get("source_entities") or []) | set((task.get("topic_entity") or {}).keys())
        hit = sorted((mids & topic_mids) | (mids & answer_mids))
        if hit:
            overlaps.append({"task_id": task_id, "mids": hit})
    return {
        "eval_task_id_hits": id_hits,
        "eval_question_hits": question_hits,
        "mid_overlaps": overlaps,
        "ok": not id_hits and not question_hits,
    }


def preflight(config: Mapping[str, Any], workspace: Workspace) -> Dict[str, Any]:
    errors: List[str] = []
    if config.get("allow_llm") is not True:
        errors.append("allow_llm must be true")
    if config.get("allow_live_kg") is not True:
        errors.append("allow_live_kg must be true")
    if config.get("allow_self_play_experience_memory") is not False:
        errors.append("allow_self_play_experience_memory must be false")
    if config.get("allow_oracle_in_actor") is not False:
        errors.append("allow_oracle_in_actor must be false")
    if config.get("allow_memory") is not False:
        errors.append("allow_memory must be false")
    if config.get("readonly_kg") is not True:
        errors.append("readonly_kg must be true")
    if config.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("protocol_version mismatch")
    if config.get("overall_version") != EXPECTED_OVERALL_VERSION:
        errors.append("overall_version mismatch")
    if config.get("stage") != "SP2-B":
        errors.append("stage must be SP2-B")
    if config.get("backtrack_state_policy") != "unsupported":
        errors.append("backtrack_state_policy must be unsupported")
    if config.get("expected_sp2a_run_id") != EXPECTED_SP2A_RUN:
        errors.append("expected SP2-A run mismatch")
    if config.get("expected_sp2a_supplement_run_id") != EXPECTED_SP2A_SUPP_RUN:
        errors.append("expected SP2-A supplement run mismatch")
    if int(config["budgets"]["max_critic_rounds"]) != 0:
        errors.append("max_critic_rounds must be 0")
    secrets = scan_config_for_secrets(config)
    if secrets:
        errors.append(f"config contains secret keys {secrets}")
    changed = assert_baseline_unchanged(workspace, config.get("expected_baseline_hashes") or EXPECTED_BASELINE_HASHES)
    if changed:
        errors.append(f"unregistered baseline changes: {changed}")
    overall = (workspace.self_play_root / "exp_plan" / "00_experiment_overall_requirements.md").read_text(encoding="utf-8")
    if "SP2-B" not in overall:
        errors.append("overall file is not at SP2-B")
    if "1.15" not in overall:
        errors.append("overall version is not 1.15")
    sp0_path = workspace.configs_root / "sp0_protocol_v1.json"
    sp0_config, _, _ = load_config(sp0_path, workspace)
    try:
        verified = verify_eval_sets(sp0_config, workspace)
        if verified["manifest"]["manifest_hash"] != EXPECTED_MANIFEST_HASH:
            errors.append("frozen manifest hash mismatch")
    except Exception as exc:
        errors.append(f"eval set verify failed: {exc}")
    b0 = load_registry(workspace, config["b0_task_registry"])
    b1 = load_registry(workspace, config["b1_task_registry"])
    if any("oracle" in task for task in b0["tasks"] + b1["tasks"]):
        errors.append("public tasks embed oracle fields")
    cover = {task.get("coverage") for task in b0["tasks"]}
    needed = {"one_hop_entity_relation", "two_hop_or_consecutive_state_update", "literal_or_answer_submission", "empty_result_or_early_stop"}
    if not needed <= cover:
        errors.append(f"B0 coverage missing {sorted(needed - cover)}")
    if len(b1["tasks"]) < 15:
        errors.append("B1 registry too small")
    overlap0 = exclusion_overlap(workspace, b0["tasks"])
    overlap1 = exclusion_overlap(workspace, b1["tasks"])
    if not overlap0["ok"] or not overlap1["ok"]:
        errors.append("B0/B1 overlap frozen eval questions or task ids")
    readonly = snapshot_readonly_roots(workspace)
    return {
        "ok": not errors,
        "errors": errors,
        "b0_sha256": b0["_sha256"],
        "b1_sha256": b1["_sha256"],
        "b0_exclusion": overlap0,
        "b1_exclusion": overlap1,
        "readonly_roots": readonly,
        "baseline": baseline_file_hashes(workspace),
    }


def run_one_task(
    config: Mapping[str, Any],
    workspace: Workspace,
    run_dir: Path,
    task: Mapping[str, Any],
    oracle: Mapping[str, Any],
    *,
    llm_cache: Optional[Dict[str, Any]] = None,
    kg_records: Optional[Dict[str, Dict[str, Any]]] = None,
    network_enabled: bool = True,
    llm_replay: bool = False,
    transport=None,
) -> Tuple[Dict[str, Any], LiveKgBinding, LlmClient]:
    adapter, env, ledger = make_live(config, network_enabled=network_enabled, records=kg_records)
    llm = LlmClient.from_config(config, cache=llm_cache, replay=llm_replay, transport=transport)
    secrets = secrets_for(task, oracle)
    prompts = O0PromptBuilder(workspace, secrets=secrets)
    mem = PogWorkingMemory(workspace, run_dir, str(task["task_id"]))
    guards = Sp2bGuards()
    controller = Sp2bRollout(
        config=config,
        adapter=adapter,
        env=env,
        llm=llm,
        prompts=prompts,
        working_memory=mem,
        guards=guards,
        secrets=secrets,
        ledger=ledger,
    )
    result = controller.run(dict(task), oracle)
    result["public_task"] = public_task_view(task)
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
) -> Dict[str, Any]:
    records = index_records({"records": env.audit_records})
    replayed, _env, _llm = run_one_task(
        config,
        workspace,
        run_dir / "replay_scratch",
        task,
        oracle,
        llm_cache=llm.cache,
        kg_records=records,
        network_enabled=False,
        llm_replay=True,
    )
    diffs = compare_replay(online, replayed)
    critical = [item for item in diffs if item["key"] in {"action_sequence", "state_id_after", "logical_actions", "physical_requests", "retries"}]
    return {
        "ok": not critical,
        "diffs": diffs,
        "critical_diffs": critical,
        "replay_llm_real_calls": replayed.get("llm_real_calls"),
        "replayed_state_id": replayed.get("state_id"),
    }


def run_layer(
    config: Mapping[str, Any],
    workspace: Workspace,
    run_dir: Path,
    layer: str,
    tasks: List[Mapping[str, Any]],
    registry: Mapping[str, Any],
    *,
    allow_eval_ids: bool = False,
) -> Dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    banned = load_exclusion_task_ids(workspace)
    results = []
    replay_ok = 0
    traces_complete = 0
    unclassified = 0
    leaks = 0
    for task in tasks:
        task_id = str(task["task_id"])
        if not allow_eval_ids and is_eval_task_id(task_id, banned):
            results.append({"task_id": task_id, "failure_class": FailureClass.INVALID_TASK.value, "complete": False})
            continue
        oracle = oracle_for(registry, task_id) if not allow_eval_ids else {
            "answer_entity_ids": list(task.get("answer_entity_ids") or []),
            "normalized_answers": list(task.get("normalized_answers") or []),
            "verifier_rule": "observed_optional",
        }
        actor_task = public_task_view(task) if allow_eval_ids else dict(task)
        if allow_eval_ids:
            actor_task = {
                "task_id": task["task_id"],
                "question": task["question"],
                "source_entities": list(task.get("source_entities") or []),
                "source_entity_names": dict(task.get("source_entity_names") or {}),
                "topic_entity": dict(task.get("source_entity_names") or {}),
                "layer": "B2",
                "max_depth": config["budgets"]["max_depth"],
                "allow_multihop": True,
                "answer_type": "unknown",
            }
        print(f"[{layer}] start {task_id}", flush=True)
        online, env, llm = run_one_task(config, workspace, run_dir, actor_task, oracle)
        replay = replay_task(config, workspace, run_dir, actor_task, oracle, env, llm, online)
        print(
            f"[{layer}] done {task_id} term={online.get('termination_reason')} "
            f"fail={online.get('failure_class')} pipeline={online.get('pipeline_ok')} "
            f"replay={replay['ok']} llm={online.get('llm_real_calls')}",
            flush=True,
        )
        if replay["ok"]:
            replay_ok += 1
        if online.get("complete"):
            traces_complete += 1
        if online.get("unclassified"):
            unclassified += 1
        task_dir = run_dir / "tasks" / task_id
        workspace.safe_write_text(task_dir / "result.json", canonical_json(online) + "\n")
        write_recorded_io(env.audit_records, workspace, relative=str((task_dir / "recorded_io.json").relative_to(workspace.self_play_root)))
        workspace.safe_write_text(task_dir / "llm_cache.json", canonical_json(llm.export_cache()) + "\n")
        workspace.safe_write_text(task_dir / "replay.json", canonical_json(replay) + "\n")
        results.append(
            {
                "task_id": task_id,
                "termination_reason": online.get("termination_reason"),
                "failure_class": online.get("failure_class"),
                "submitted_answers": online.get("submitted_answers"),
                "complete": online.get("complete"),
                "pipeline_ok": online.get("pipeline_ok"),
                "llm_real_calls": online.get("llm_real_calls"),
                "ledger": online.get("ledger"),
                "replay_ok": replay["ok"],
                "replay_diffs": replay["diffs"],
                "verifier_match": (online.get("verifier") or {}).get("match"),
                "working_memory_path": (online.get("working_memory") or {}).get("path"),
            }
        )
    n = len(results) or 1
    failure_counts: Dict[str, int] = {}
    for item in results:
        key = str(item.get("failure_class") or "none")
        failure_counts[key] = failure_counts.get(key, 0) + 1
    return {
        "layer": layer,
        "n": len(results),
        "trace_complete_rate": traces_complete / n,
        "replay_rate": replay_ok / n,
        "unclassified": unclassified,
        "oracle_leaks": leaks,
        "failure_counts": failure_counts,
        "results": results,
        "pipeline_ok_rate": sum(1 for item in results if item.get("pipeline_ok")) / n,
        "terminated_rate": traces_complete / n,
    }


def load_b2_tasks(workspace: Workspace, config: Mapping[str, Any]) -> List[Dict[str, Any]]:
    path = workspace.self_play_root / config["b2_dataset"]
    expected = (config.get("expected_eval_file_hashes") or EXPECTED_EVAL_HASHES)["webqsp_smoke_20"]
    actual = sha256_file(path)
    if actual != expected:
        raise RuntimeError(f"webqsp_smoke_20 hash {actual} != {expected}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    if len(rows) != 20:
        raise RuntimeError(f"webqsp_smoke_20 has {len(rows)} rows")
    return rows


def summarize_metrics(b0: Mapping[str, Any], b1: Mapping[str, Any], b2: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "self_play_experience_memory_rw": 0,
        "b0_terminated_rate": b0.get("terminated_rate"),
        "b1_trace_complete_rate": b1.get("trace_complete_rate"),
        "b1_replay_rate": b1.get("replay_rate"),
        "b1_unclassified": b1.get("unclassified"),
        "b2_ran": b2 is not None,
        "b2_n": None if b2 is None else b2.get("n"),
    }


def run_all_sp2b(config: Mapping[str, Any], workspace: Workspace, run_dir: Path) -> Dict[str, Any]:
    pf = preflight(config, workspace)
    if not pf["ok"]:
        return {"status": "FAIL", "reason": "preflight_failed", "preflight": pf}
    module, prompt_hash = (O0PromptBuilder(workspace).module, O0PromptBuilder(workspace).source_sha256)
    inventory = prompt_inventory(module, prompt_hash)
    prompt_path = workspace.safe_write_text(
        workspace.self_play_root / config["prompt_inventory"],
        canonical_json(inventory) + "\n",
    )
    b0_reg = load_registry(workspace, config["b0_task_registry"])
    b1_reg = load_registry(workspace, config["b1_task_registry"])
    b0 = run_layer(config, workspace, run_dir / "b0", "B0", b0_reg["tasks"], b0_reg)
    b0_gate = (
        b0["terminated_rate"] == 1.0
        and b0["unclassified"] == 0
        and b0["oracle_leaks"] == 0
        and all(item.get("pipeline_ok") for item in b0["results"])
    )
    payload: Dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "status": "FAIL",
        "preflight": pf,
        "prompt_inventory_sha256": sha256_file(prompt_path),
        "layers": {"B0": b0},
    }
    if not b0_gate:
        payload["reason"] = "b0_gate_failed"
        payload["metrics"] = summarize_metrics(b0, {"trace_complete_rate": 0, "replay_rate": 0, "unclassified": 0}, None)
        return payload
    b1 = run_layer(config, workspace, run_dir / "b1", "B1", b1_reg["tasks"], b1_reg)
    payload["layers"]["B1"] = b1
    b1_gate = (
        b1["trace_complete_rate"] == 1.0
        and b1["replay_rate"] >= B1_REPLAY_THRESHOLD
        and b1["unclassified"] == 0
        and b1["oracle_leaks"] == 0
        and all(item.get("complete") for item in b1["results"])
    )
    if not b1_gate:
        payload["reason"] = "b1_gate_failed"
        payload["metrics"] = summarize_metrics(b0, b1, None)
        return payload
    b2_tasks = load_b2_tasks(workspace, config)
    b2 = run_layer(
        config,
        workspace,
        run_dir / "b2",
        "B2",
        b2_tasks,
        {"oracle": {}},
        allow_eval_ids=True,
    )
    payload["layers"]["B2"] = b2
    payload["metrics"] = summarize_metrics(b0, b1, b2)
    secrets = scan_paths_for_secrets(list(run_dir.rglob("*.json")) + list(run_dir.rglob("*.txt")))
    payload["secret_hits"] = secrets
    changed = assert_baseline_unchanged(workspace, config.get("expected_baseline_hashes") or EXPECTED_BASELINE_HASHES)
    payload["baseline_changes"] = changed
    readonly_after = snapshot_readonly_roots(workspace)
    payload["readonly_after"] = readonly_after
    payload["readonly_unchanged"] = readonly_after == pf["readonly_roots"]
    ok = (
        b0_gate
        and b1_gate
        and b2["unclassified"] == 0
        and not secrets
        and not changed
        and payload["readonly_unchanged"]
        and payload["metrics"]["self_play_experience_memory_rw"] == 0
    )
    payload["status"] = "PASS" if ok else "FAIL"
    if not ok:
        payload["reason"] = "acceptance_failed"
    return payload
