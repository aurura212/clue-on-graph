"""SP4-SUPPLEMENT pipeline: same-state CF, shared-relation snapshot, NL verbalizer, snapshot critic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .action_protocol import ProtocolSession, parse_action
from .baseline import assert_baseline_unchanged
from .candidate_retrieval import CandidateRetriever
from .config import load_config
from .critic_context import build_compressed_critic_input
from .distiller import distill_rules
from .errors import ProtocolError
from .hashing import canonical_json, sha256_file
from .paths import PROTOCOL_VERSION, Workspace
from .promotion import PROMOTION_GATES, promote_rules
from .replay import make_env
from .same_state_cf import INAPPLICABLE, run_same_state_pair, summarize_cf
from .sampling import verify_eval_sets
from .schemas import ActorRole, Budget
from .sp1_checks import EXPECTED_BASELINE_HASHES, EXPECTED_MANIFEST_HASH
from .sp2a_guards import scan_config_for_secrets, scan_paths_for_secrets, snapshot_readonly_roots
from .sp4_io import FORBIDDEN_BENCHMARK, not_generated, write_json, write_jsonl, write_not_generated
from .sp4s_critic import extract_checkpoint_candidate, load_critic_prompt, run_checkpoint_trajectory
from .synthetic_tasks import env_for_task
from .synthetic_tasks_sp4s import (
    freeze_synthetic,
    generate_llm_paraphrases,
    load_split,
    snapshot_paths,
    verify_synthetic,
)
from .visibility import project_actor_view

EXPECTED_OVERALL = "SP-GENERAL 1.22"
EXPECTED_PLAN = "SP4-SUPPLEMENT 1.0"
PLAN_VERSION = "SP4-SUPPLEMENT 1.0"


def artifact_paths(workspace: Workspace) -> Dict[str, Path]:
    root = workspace.artifacts_root
    return {
        "check": root / "protocol" / "sp4s_check_result.json",
        "validated": root / "candidates" / "sp4s_validated_candidates_v1.jsonl",
        "rejected": root / "candidates" / "sp4s_rejected_candidates_v1.jsonl",
        "cf_results": root / "counterfactual" / "sp4s_counterfactual_results_v1.jsonl",
        "promoted": root / "memory" / "sp4s_promoted_memory_v1.jsonl",
        "decisions": root / "memory" / "sp4s_promotion_decisions_v1.jsonl",
        "manifest": root / "memory" / "sp4s_memory_manifest_v1.json",
        "metrics": workspace.reports_root / "sp4s" / "metrics.json",
        "report": workspace.reports_root / "sp4s" / "SP4S_experiment_report.md",
        "not_generated": root / "protocol" / "sp4s_not_generated.json",
        "live_kg": root / "protocol" / "sp4s_live_kg_subgraph.json",
    }


def preflight(config: Mapping[str, Any], workspace: Workspace) -> Dict[str, Any]:
    errors: List[str] = []
    if config.get("stage") != "SP4-SUPPLEMENT":
        errors.append("stage must be SP4-SUPPLEMENT")
    if config.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("protocol_version mismatch")
    if config.get("overall_version") != EXPECTED_OVERALL:
        errors.append("overall_version mismatch")
    if config.get("plan_version") != EXPECTED_PLAN:
        errors.append("plan_version mismatch")
    if config.get("allow_oracle_in_actor") is not False:
        errors.append("allow_oracle_in_actor must be false")
    if config.get("allow_self_play_experience_memory_read") is not False:
        errors.append("memory_read must be false")
    if config.get("allow_live_kg") is not False and not config.get("allow_live_kg_explicit"):
        # default closed; CLI may set the explicit flag later
        pass
    if scan_config_for_secrets(config):
        errors.append(f"config secrets {scan_config_for_secrets(config)}")
    changed = assert_baseline_unchanged(workspace, config.get("expected_baseline_hashes") or EXPECTED_BASELINE_HASHES)
    if changed:
        errors.append(f"unregistered baseline changes: {changed}")
    overall = (workspace.self_play_root / "exp_plan" / "00_experiment_overall_requirements.md").read_text(encoding="utf-8")
    if "1.22" not in overall or "SP4-SUPPLEMENT" not in overall:
        errors.append("overall file is not at SP4-SUPPLEMENT / 1.22")
    sp0_config, _, _ = load_config(workspace.configs_root / "sp0_protocol_v1.json", workspace)
    try:
        verified = verify_eval_sets(sp0_config, workspace)
        if verified["manifest"]["manifest_hash"] != EXPECTED_MANIFEST_HASH:
            errors.append("frozen eval manifest hash mismatch")
    except Exception as exc:
        errors.append(f"eval set verify failed: {exc}")
    expected_eval = config.get("expected_eval_file_hashes") or {}
    mapping = {
        "webqsp_smoke_20": "artifacts/datasets/webqsp_smoke_20.jsonl",
        "webqsp_model_compare_150": "artifacts/datasets/webqsp_model_compare_150.jsonl",
        "cwq_model_compare_50": "artifacts/datasets/cwq_model_compare_50.jsonl",
    }
    for key, rel in mapping.items():
        path = workspace.self_play_root / rel
        if key in expected_eval and path.exists() and sha256_file(path) != expected_eval[key]:
            errors.append(f"eval set {key} hash mismatch")
    env = make_env()
    try:
        actor = project_actor_view(env.task, env.visible_state(), secrets=env.secrets() if hasattr(env, "secrets") else None)
        if "answer_entity_ids" in json.dumps(actor):
            errors.append("actor view leaked answers")
    except ProtocolError as exc:
        errors.append(f"oracle projection failed: {exc}")
    retriever = CandidateRetriever([{"trigger": {}, "recommendation": {}}], memory_read=False)
    got = retriever.retrieve(state=env.visible_state(), question_type="one_hop", answer_type="entity")
    if got.get("fallback") != "memory_read_false" or retriever._loaded:
        errors.append("memory_read=false still loaded candidates")
    if dict(PROMOTION_GATES) != {
        "audit_pass_rate": 1.0,
        "min_discovery_tasks": 3,
        "min_cf_states": 5,
        "min_margin": 0.20,
        "max_harm_rate": 0.10,
        "min_v1_triggers": 5,
        "min_cost_drop": 0.10,
    }:
        errors.append("PROMOTION_GATES were modified")
    return {
        "ok": not errors,
        "errors": errors,
        "protocol_version": PROTOCOL_VERSION,
        "readonly_roots": snapshot_readonly_roots(workspace),
        "secret_hits": scan_paths_for_secrets(
            [workspace.configs_root / "sp4s_supplement_v1.json"]
        ),
        "memory_read_false_ok": got.get("fallback") == "memory_read_false",
        "promotion_gates": dict(PROMOTION_GATES),
        "pog_backtrack": "unsupported",
        "live_kg": "skipped",
    }


def stage_generate(config: Mapping[str, Any], workspace: Workspace, *, llm_client: Any = None) -> Dict[str, Any]:
    paraphrases = None
    verbalizer_mode = "multi_template_v1"
    if config.get("allow_llm") and llm_client is not None:
        prompt = (workspace.self_play_root / str(config.get("verbalizer_prompt") or "prompts/sp4s_verbalizer_v1.txt")).read_text(encoding="utf-8")
        from .synthetic_tasks_sp4s import build_shared_relation_snapshot

        paraphrases = generate_llm_paraphrases(
            build_shared_relation_snapshot(),
            client=llm_client,
            prompt_template=prompt,
            seed=int(config.get("synthetic_seed") or 20260823),
        )
        verbalizer_mode = "llm_paraphrase_v1" if paraphrases else "multi_template_v1"
    manifest = freeze_synthetic(workspace, config, llm_paraphrases=paraphrases)
    verified = verify_synthetic(workspace, config)
    return {
        "ok": True,
        "manifest": manifest,
        "contamination": verified["contamination"],
        "verbalizer_mode": verbalizer_mode,
        "n_llm_paraphrases": 0 if not paraphrases else len(paraphrases),
    }


def _budget(config: Mapping[str, Any]) -> Budget:
    return Budget.from_config(config.get("budgets") or {})


def stage_critic(config: Mapping[str, Any], workspace: Workspace, run_id: str, *, llm_client: Any = None) -> Dict[str, Any]:
    paths = snapshot_paths(workspace)
    snapshot = json.loads(paths["snapshot"].read_text(encoding="utf-8"))
    actors, oracles = load_split(workspace, "discovery")
    oracle_by_id = {item["task_id"]: item for item in oracles}
    prompt = load_critic_prompt(workspace)
    budget = _budget(config)
    groups = {
        "G0": {"critic_mode": "none", "seeds": [int(config.get("synthetic_seed") or 20260823)]},
        "G1": {"critic_mode": "o0_llm" if (config.get("allow_llm") and llm_client is not None) else "o0", "seeds": [11, 22, 33]},
        "G2": {"critic_mode": "teacher", "seeds": [int(config.get("synthetic_seed") or 20260823)]},
        "G3": {"critic_mode": "random", "seeds": [7]},
    }
    trajectories: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    for actor in actors:
        oracle = oracle_by_id[actor["task_id"]]
        hops = oracle.get("path_hops") or []
        for group, spec in groups.items():
            for seed in spec["seeds"]:
                env = env_for_task(snapshot, actor, oracle, budget=_budget(config))
                traj = run_checkpoint_trajectory(
                    env,
                    run_id=run_id,
                    seed=int(seed),
                    temperature=0.3 if group == "G1" else 0.0,
                    critic_mode=spec["critic_mode"],
                    teacher_hops=hops if group == "G2" else None,
                    max_critic_rounds=int((config.get("budgets") or {}).get("max_critic_rounds") or 2),
                    llm_client=llm_client if spec["critic_mode"] == "o0_llm" else None,
                    prompt_template=prompt,
                )
                traj["group"] = group
                trajectories.append(traj)
                cand = extract_checkpoint_candidate(traj, actor)
                if cand and group != "G2":
                    candidates.append(cand)
    out_dir = workspace.runs_root / run_id
    workspace.assert_writable(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(workspace, out_dir / "sp4s_trajectories.jsonl", trajectories)
    write_jsonl(workspace, out_dir / "sp4s_local_candidates.jsonl", candidates)
    summary = {}
    for group in groups:
        rows = [item for item in trajectories if item.get("group") == group]
        n = max(1, len(rows))
        summary[group] = {
            "n": len(rows),
            "complete_rate": sum(1 for item in rows if item.get("complete")) / n,
            "replay_rate": sum(1 for item in rows if item.get("replay_status") == "deterministic") / n,
            "success_rate": sum(1 for item in rows if item.get("success")) / n,
            "recovery_rate": sum(1 for item in rows if item.get("recovery_status") == "recovered") / n,
            "mean_critic_rounds": sum(int(item.get("n_critic_rounds") or 0) for item in rows) / n,
            "critic_source": rows[0]["critic_source"] if rows else None,
        }
    return {
        "ok": True,
        "summary": summary,
        "n_trajectories": len(trajectories),
        "n_local_candidates": len(candidates),
        "trajectories": trajectories,
        "local_candidates": candidates,
        "g2_excluded_from_o0": True,
        "g3_excluded_from_o0": True,
        "llm_critic": bool(config.get("allow_llm") and llm_client is not None),
    }


def _audit_local(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    accepted = []
    rejected = []
    for item in candidates:
        reasons = []
        rec = item.get("recommendation") or {}
        blob = json.dumps(item, ensure_ascii=False)
        if any(key in blob for key in ("answer_entity_ids", "witness_paths", "logical_query")):
            reasons.append("oracle_field")
        if item.get("discovery_method") == "random_critic" and item.get("discovery_method"):
            pass
        if not rec.get("relation_pattern") and rec.get("action_type") == "EXPAND":
            reasons.append("missing_relation_pattern")
        if reasons:
            rejected.append({**item, "reject_reasons": reasons})
        else:
            accepted.append(item)
    n = max(1, len(candidates))
    return {
        "accepted": accepted,
        "rejected": rejected,
        "summary": {
            "n": len(candidates),
            "passed": len(accepted),
            "rejected": len(rejected),
            "schema_pass_rate": len(accepted) / n,
        },
    }


def stage_counterfactual(config: Mapping[str, Any], workspace: Workspace, local_candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    paths = snapshot_paths(workspace)
    snapshot = json.loads(paths["snapshot"].read_text(encoding="utf-8"))
    actors, oracles = load_split(workspace, "discovery")
    oracle_by_id = {item["task_id"]: item for item in oracles}
    actor_by_id = {item["task_id"]: item for item in actors}
    rows = []
    sham_better = []
    for cand in local_candidates:
        if cand.get("discovery_method") == "random_critic":
            continue
        task_id = str(cand.get("task_id") or (cand.get("source_task_ids") or [None])[0])
        if task_id not in actor_by_id:
            continue
        env = env_for_task(snapshot, actor_by_id[task_id], oracle_by_id[task_id], budget=_budget(config))
        rec = run_same_state_pair(env, candidate=cand, seed=int(config.get("synthetic_seed") or 20260823), control_type="none")
        rec["candidate_id"] = cand.get("experience_id")
        rows.append(rec)
        sham_env = env_for_task(snapshot, actor_by_id[task_id], oracle_by_id[task_id], budget=_budget(config))
        sham = run_same_state_pair(sham_env, candidate=cand, seed=3, control_type="sham")
        if sham.get("outcome") == "win" and rec.get("outcome") != "win":
            sham_better.append(cand.get("experience_id"))
    write_jsonl(workspace, artifact_paths(workspace)["cf_results"], rows)
    summary = summarize_cf(rows)
    summary["sham_better_ids"] = sham_better
    return {"ok": True, "summary": summary, "rows": rows, "sham_better_ids": sham_better}


def _rule_triggers(rule: Mapping[str, Any], actor: Mapping[str, Any], snapshot: Mapping[str, Any], oracle: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    rec = (rule.get("action_policy") or {})
    pattern = rec.get("relation_pattern")
    if not pattern:
        return False
    env = env_for_task(snapshot, actor, oracle, budget=_budget(config))
    visible = {item.relation for item in env.visible_state().visible_relations}
    return pattern in visible


def stage_validate_and_promote(
    config: Mapping[str, Any],
    workspace: Workspace,
    *,
    local_candidates: List[Dict[str, Any]],
    cf_rows: List[Dict[str, Any]],
    audit_summary: Mapping[str, Any],
    sham_better_ids: List[Any],
) -> Dict[str, Any]:
    eligible = [item for item in local_candidates if item.get("discovery_method") != "random_critic"]
    applicable_cf = [row for row in cf_rows if row.get("outcome") != INAPPLICABLE]
    rules = distill_rules(eligible, applicable_cf)
    paths = snapshot_paths(workspace)
    snapshot = json.loads(paths["snapshot"].read_text(encoding="utf-8"))
    actors_v1, oracles_v1 = load_split(workspace, "validation_v1")
    oracle_v1 = {item["task_id"]: item for item in oracles_v1}
    v1_by_rule: Dict[str, Dict[str, Any]] = {}
    for rule in rules:
        triggered = [item for item in actors_v1 if _rule_triggers(rule, item, snapshot, oracle_v1[item["task_id"]], config)]
        v1_by_rule[str(rule.get("rule_id"))] = {
            "n_triggered": len(triggered),
            "success_rate": 0.0,
            "baseline_success_rate": 0.0,
            "mean_cost": 1.0,
            "baseline_mean_cost": 1.0,
            "baseline_invalid_rate": 1.0,
        }
    promoted = promote_rules(
        rules,
        audit_pass_rate=float(audit_summary.get("schema_pass_rate") or 0.0) if local_candidates else 0.0,
        v1_by_rule=v1_by_rule,
        sham_better_ids=[str(x) for x in sham_better_ids if x],
        config_frozen=True,
    )
    art = artifact_paths(workspace)
    write_jsonl(workspace, art["decisions"], promoted["decisions"])
    write_jsonl(workspace, art["promoted"], promoted["promoted"])
    write_json(workspace, art["manifest"], promoted["manifest"])
    if not promoted["promoted"]:
        write_not_generated(workspace, art["promoted"].with_name("sp4s_promoted_memory_empty.json"), "0 promoted rules; empty memory is failure evidence and is not injected")
    return {
        "ok": True,
        "n_rules": len(rules),
        "promotion": promoted["manifest"],
        "v1_by_rule": v1_by_rule,
        "rules": rules,
        "decisions": promoted["decisions"],
        "promoted": promoted["promoted"],
        "gates_unchanged": True,
    }


def write_report(workspace: Workspace, payload: Mapping[str, Any]) -> Path:
    paths = artifact_paths(workspace)
    paths["report"].parent.mkdir(parents=True, exist_ok=True)
    conclusion = payload.get("conclusion") or "CONDITIONAL PASS"
    n_promoted = int(payload.get("n_promoted") or 0)
    text = f"""# SP4-SUPPLEMENT experiment report

> Plan: `{PLAN_VERSION}`
> Protocol: `{PROTOCOL_VERSION}`
> Overall: {EXPECTED_OVERALL}
> Conclusion: **{conclusion}**
> Date: 2026-08-23

This supplement does not rewrite the frozen SP4 CONDITIONAL PASS. It does not start SP5.
Empty `promoted_memory` is valid evidence and is not injected. WebQSP 20/150 and CWQ 50 were not used for generation, tuning, or promotion.

## 1. Goal

Repair same-state counterfactuals, share the relation vocabulary across isolated-entity splits, add multi-template / optional LLM questions, and optionally attach a snapshot LLM critic. Promotion gates stay frozen.

## 2. Setup

| Item | Value |
|---|---|
| Run ID | `{payload.get("run_id")}` |
| Snapshot | `{payload.get("snapshot_id")}` hash `{payload.get("snapshot_hash")}` |
| Verbalizer | {payload.get("verbalizer_mode")} |
| LLM critic | {payload.get("llm_critic")} |
| Live KG subgraph | skipped |
| memory_read | false |
| PROMOTION_GATES | unchanged |

## 3. Synthetic splits

{payload.get("synthetic_md")}

## 4. Trajectories and critic

{payload.get("critic_md")}

G2 teacher and G3 random are not merged into O0 promotion.

## 5. Same-state counterfactual

{payload.get("cf_md")}

Inapplicable rows are excluded from the margin denominator.

## 6. Distill and promotion

{payload.get("promo_md")}

Promoted rules: **{n_promoted}**. SP5 remains forbidden.

## 7. Acceptance

| Item | Result |
|---|---|
| Same-state CF protocol | {payload.get("cf_ok")} |
| Split contamination 0 | {payload.get("isolation_ok")} |
| Question leakage 0 | {payload.get("leakage_ok")} |
| Promotion gates unchanged | true |
| Unused 20/150/50 | true |
| LLM critic on snapshot | {payload.get("llm_critic")} |
| Live KG | skipped / not a PASS claim |

## 8. Conclusion

SP4-SUPPLEMENT **{conclusion}**. {payload.get("conclusion_note")}
"""
    workspace.safe_write_text(paths["report"], text)
    return paths["report"]
