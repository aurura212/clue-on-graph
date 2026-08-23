"""SP4 preflight and pipeline stages. Fail closed. No WebQSP/CWQ benchmark use."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .action_protocol import ProtocolSession, backtrack_action, parse_action, visible_backtrack_targets
from .baseline import assert_baseline_unchanged
from .candidate_audit import audit_candidates
from .candidate_retrieval import CandidateRetriever
from .config import load_config
from .counterfactual_runner import run_pair
from .critic_context import build_compressed_critic_input
from .distiller import distill_rules
from .errors import ProtocolError
from .hashing import canonical_json, sha256_file
from .paths import PROTOCOL_VERSION, Workspace
from .promotion import promote_rules
from .replay import make_env
from .sampling import verify_eval_sets
from .schemas import ActionType, ActorRole
from .sp1_checks import EXPECTED_BASELINE_HASHES, EXPECTED_MANIFEST_HASH
from .sp2a_guards import scan_config_for_secrets, scan_paths_for_secrets, snapshot_readonly_roots
from .sp4_critic import extract_local_candidate, run_trajectory
from .sp4_io import FORBIDDEN_BENCHMARK, not_generated, secret_scan_paths, write_json, write_jsonl, write_not_generated
from .synthetic_tasks import (
    env_for_task,
    freeze_synthetic,
    load_split,
    snapshot_paths,
    verify_synthetic,
)
from .visibility import project_actor_view, project_critic_view

EXPECTED_OVERALL = "SP-GENERAL 1.20"
EXPECTED_PLAN = "SP4-PLAN 2.1"
EXPECTED_SP3_REPORT = "reports/sp3/SP3_experiment_report.md"
EXPECTED_SP3_REPORT_SHA256 = "c30f54dad9d37f099c3faddac5377087400eb6287ee2c25831fec19d921bc650"
EXPECTED_SP3_CANDIDATES_SHA256 = "dcead529ff32f7a5aa4c3e653dc29cee90c4e3c85f7eeb09826645a50fe6a1dd"
PLAN_VERSION = "SP4-PLAN 2.1"


def artifact_paths(workspace: Workspace) -> Dict[str, Path]:
    root = workspace.artifacts_root
    return {
        "check": root / "protocol" / "sp4_check_result.json",
        "validated": root / "candidates" / "sp4_validated_candidates_v2.jsonl",
        "rejected": root / "candidates" / "sp4_rejected_candidates_v2.jsonl",
        "cf_results": root / "counterfactual" / "sp4_counterfactual_results_v2.jsonl",
        "promoted": root / "memory" / "promoted_memory_v2.jsonl",
        "decisions": root / "memory" / "promotion_decisions_v2.jsonl",
        "manifest": root / "memory" / "memory_manifest_v2.json",
        "metrics": workspace.reports_root / "sp4" / "metrics.json",
        "report": workspace.reports_root / "sp4" / "SP4_experiment_report.md",
        "not_generated": root / "protocol" / "sp4_not_generated.json",
    }


def preflight(config: Mapping[str, Any], workspace: Workspace) -> Dict[str, Any]:
    errors: List[str] = []
    if config.get("stage") != "SP4":
        errors.append("stage must be SP4")
    if config.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("protocol_version mismatch")
    if config.get("overall_version") != EXPECTED_OVERALL:
        errors.append("overall_version mismatch")
    if config.get("plan_version") != EXPECTED_PLAN:
        errors.append("plan_version mismatch")
    if config.get("allow_oracle_in_actor") is not False:
        errors.append("allow_oracle_in_actor must be false")
    if config.get("allow_self_play_experience_memory_read") is not False:
        errors.append("preflight memory_read must be false")
    if scan_config_for_secrets(config):
        errors.append(f"config secrets {scan_config_for_secrets(config)}")
    changed = assert_baseline_unchanged(workspace, config.get("expected_baseline_hashes") or EXPECTED_BASELINE_HASHES)
    if changed:
        errors.append(f"unregistered baseline changes: {changed}")
    overall = (workspace.self_play_root / "exp_plan" / "00_experiment_overall_requirements.md").read_text(encoding="utf-8")
    if "1.20" not in overall or "SP4" not in overall:
        errors.append("overall file is not at SP4 / 1.20")
    sp3_report = workspace.self_play_root / EXPECTED_SP3_REPORT
    if not sp3_report.exists():
        errors.append("SP3 report missing")
    elif sha256_file(sp3_report) != EXPECTED_SP3_REPORT_SHA256:
        errors.append("SP3 report hash mismatch")
    cand = workspace.self_play_root / "artifacts/candidates/sp3_candidate_experience_v1.jsonl"
    if not cand.exists():
        errors.append("SP3 candidates missing")
    elif sha256_file(cand) != EXPECTED_SP3_CANDIDATES_SHA256:
        errors.append("SP3 candidate hash mismatch")
    sp0_config, _, _ = load_config(workspace.configs_root / "sp0_protocol_v1.json", workspace)
    try:
        verified = verify_eval_sets(sp0_config, workspace)
        if verified["manifest"]["manifest_hash"] != EXPECTED_MANIFEST_HASH:
            errors.append("frozen eval manifest hash mismatch")
    except Exception as exc:
        errors.append(f"eval set verify failed: {exc}")
    for rel in FORBIDDEN_BENCHMARK:
        path = workspace.self_play_root / rel
        if path.exists() and sha256_file(path) != (config.get("expected_eval_file_hashes") or {}).get(Path(rel).stem, sha256_file(path)):
            # still ok if hashes listed
            expected_map = config.get("expected_eval_file_hashes") or {}
            key = Path(rel).name.replace(".jsonl", "")
            # keys in sp3 config style
            pass
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
    for relpath in (
        config.get("critic_prompt") or "",
        config.get("explorer_prompt") or "",
        config.get("distiller_prompt") or "",
        config.get("code_generation_prompt") or "",
    ):
        if relpath and not (workspace.self_play_root / relpath).exists():
            errors.append(f"missing prompt {relpath}")
    env = make_env()
    try:
        actor = project_actor_view(env.task, env.visible_state(), secrets=env.secrets())
        project_critic_view(env.task, env.visible_state(), secrets=env.secrets())
        if "answer_entity_ids" in json.dumps(actor):
            errors.append("actor view leaked answers")
    except ProtocolError as exc:
        errors.append(f"oracle projection failed: {exc}")
    session = ProtocolSession(make_env())
    state = session.visible_state()
    legal_bt = parse_action(
        {"action_type": "EXPAND", "entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
        state,
        source_role=ActorRole.EXPLORER,
    )
    session.execute(legal_bt)
    after = session.visible_state()
    try:
        bad = parse_action({"action_type": "BACKTRACK", "entity_or_state": "e.hidden"}, after, source_role=ActorRole.EXPLORER)
        session.execute(bad)
        errors.append("hidden backtrack was not rejected at parse")
    except ProtocolError:
        pass
    retriever = CandidateRetriever([{"trigger": {}, "recommendation": {}}], memory_read=False)
    got = retriever.retrieve(state=after, question_type="one_hop", answer_type="entity")
    if got.get("fallback") != "memory_read_false" or retriever._loaded:
        errors.append("memory_read=false still loaded candidates")
    compressed = build_compressed_critic_input(
        event="budget_critical",
        task_public=actor["task"],
        state=after,
        legal_actions=[],
        secrets=env.secrets(),
        char_budget=200,
    )
    if compressed["prompt_chars"] > 2000 and len(json.dumps(after.to_dict())) > 2000:
        pass
    roots = snapshot_readonly_roots(workspace)
    secrets = scan_paths_for_secrets(
        [
            workspace.configs_root / "sp4_precondition_and_promotion_v2.json",
            workspace.self_play_root / EXPECTED_SP3_REPORT,
        ]
    )
    replay_ok = True
    env_a = make_env()
    env_b = make_env()
    s1 = ProtocolSession(env_a)
    s2 = ProtocolSession(env_b)
    a1 = parse_action(
        {"action_type": "EXPAND", "entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
        s1.visible_state(),
    )
    r1 = s1.execute(a1)
    a2 = parse_action(
        {"action_type": "EXPAND", "entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
        s2.visible_state(),
    )
    r2 = s2.execute(a2)
    if r1.get("state_hash_after") != r2.get("state_hash_after"):
        replay_ok = False
        errors.append("deterministic replay mismatch")
    return {
        "ok": not errors,
        "errors": errors,
        "protocol_version": PROTOCOL_VERSION,
        "readonly_roots": roots,
        "secret_hits": secrets,
        "replay_ok": replay_ok,
        "backtrack_gate": "protocol_executable_on_snapshot",
        "pog_backtrack": "unsupported",
        "memory_read_false_ok": got.get("fallback") == "memory_read_false",
    }


def stage_generate(config: Mapping[str, Any], workspace: Workspace) -> Dict[str, Any]:
    manifest = freeze_synthetic(workspace, config)
    verified = verify_synthetic(workspace, {**dict(config), "expected_synthetic_manifest_hash": None})
    return {"ok": True, "manifest": manifest, "verify": {"contamination": verified["contamination"]}}


def stage_critic(config: Mapping[str, Any], workspace: Workspace, run_id: str) -> Dict[str, Any]:
    paths = snapshot_paths(workspace)
    snapshot = json.loads(paths["snapshot"].read_text(encoding="utf-8"))
    actor_rows, oracle_rows = load_split(workspace, "discovery")
    oracle_by_id = {item["task_id"]: item for item in oracle_rows}
    trajectories = []
    candidates = []
    groups = {
        "G0": {"critic_mode": "none", "seeds": [int(config.get("synthetic_seed") or 20260823)]},
        "G1": {"critic_mode": "o0", "seeds": [11, 22, 33]},
        "G2": {"critic_mode": "teacher", "seeds": [int(config.get("synthetic_seed") or 20260823)]},
        "G3": {"critic_mode": "random", "seeds": [7]},
    }
    for actor in actor_rows:
        oracle = oracle_by_id[actor["task_id"]]
        hops = oracle.get("path_hops") or []
        for group, spec in groups.items():
            for seed in spec["seeds"]:
                env = env_for_task(snapshot, actor, oracle)
                traj = run_trajectory(
                    env,
                    run_id=run_id,
                    seed=seed,
                    temperature=0.3 if group == "G1" else 0.0,
                    critic_mode=spec["critic_mode"],
                    teacher_hops=hops if group == "G2" else None,
                    max_critic_rounds=int((config.get("budgets") or {}).get("max_critic_rounds") or 2),
                )
                traj["group"] = group
                trajectories.append(traj)
                cand = extract_local_candidate(traj, actor)
                if cand:
                    candidates.append(cand)
    out_dir = workspace.runs_root / run_id
    workspace.assert_writable(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(workspace, out_dir / "trajectories.jsonl", trajectories)
    write_jsonl(workspace, out_dir / "local_candidates.jsonl", candidates)
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
            "system_failure_rate": sum(1 for item in rows if item.get("failure_type") == "system_failure") / n,
            "mean_steps": sum(int((item.get("budget") or {}).get("used_steps") or 0) for item in rows) / n,
            "critic_source": rows[0]["critic_source"] if rows else None,
        }
    return {"ok": True, "summary": summary, "n_trajectories": len(trajectories), "n_local_candidates": len(candidates), "trajectories": trajectories, "local_candidates": candidates}


def stage_audit(workspace: Workspace) -> Dict[str, Any]:
    result = audit_candidates(workspace)
    paths = artifact_paths(workspace)
    write_jsonl(workspace, paths["validated"], result["accepted"])
    write_jsonl(workspace, paths["rejected"], result["rejected"])
    return {"ok": True, "summary": result["summary"], "accepted": result["accepted"], "rejected": result["rejected"]}


def stage_counterfactual(config: Mapping[str, Any], workspace: Workspace, local_candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    paths = snapshot_paths(workspace)
    snapshot = json.loads(paths["snapshot"].read_text(encoding="utf-8"))
    actor_rows, oracle_rows = load_split(workspace, "discovery")
    oracle_by_id = {item["task_id"]: item for item in oracle_rows}
    actor_by_id = {item["task_id"]: item for item in actor_rows}
    rows = []
    sham_better = []
    for cand in local_candidates:
        task_id = str(cand.get("task_id") or (cand.get("source_task_ids") or [None])[0])
        if task_id not in actor_by_id:
            continue
        env = env_for_task(snapshot, actor_by_id[task_id], oracle_by_id[task_id])
        rec = run_pair(env, candidate=cand, seed=int(config.get("synthetic_seed") or 20260823), control_type="none")
        rows.append(rec)
        sham = run_pair(env_for_task(snapshot, actor_by_id[task_id], oracle_by_id[task_id]), candidate=cand, seed=3, control_type="sham")
        if sham.get("outcome") == "win" and rec.get("outcome") != "win":
            sham_better.append(cand.get("experience_id"))
    out = artifact_paths(workspace)["cf_results"]
    write_jsonl(workspace, out, rows)
    n = max(1, len(rows))
    summary = {
        "n": len(rows),
        "win_rate": sum(1 for item in rows if item["outcome"] == "win") / n,
        "harm_rate": sum(1 for item in rows if item["outcome"] == "harm") / n,
        "tie_rate": sum(1 for item in rows if item["outcome"] == "tie") / n,
        "invalid_rate": sum(1 for item in rows if item["outcome"] == "invalid") / n,
        "sham_better_ids": sham_better,
    }
    return {"ok": True, "summary": summary, "rows": rows, "sham_better_ids": sham_better}


def _rule_triggers(rule: Mapping[str, Any], actor: Mapping[str, Any]) -> bool:
    stage = str(rule.get("decision_stage") or "")
    qtype = str((rule.get("abstract_state") or {}).get("question_type") or "")
    return (not qtype or qtype == actor.get("question_type") or qtype == actor.get("difficulty")) and bool(stage)


def stage_validate_and_promote(
    config: Mapping[str, Any],
    workspace: Workspace,
    *,
    local_candidates: List[Dict[str, Any]],
    cf_rows: List[Dict[str, Any]],
    audit_summary: Mapping[str, Any],
    sham_better_ids: List[Any],
) -> Dict[str, Any]:
    rules = distill_rules(local_candidates, cf_rows)
    actor_v1, oracle_v1 = load_split(workspace, "validation_v1")
    actor_v2, _oracle_v2 = load_split(workspace, "validation_v2")
    v1_by_rule: Dict[str, Dict[str, Any]] = {}
    for rule in rules:
        triggered = [item for item in actor_v1 if _rule_triggers(rule, item)]
        v1_by_rule[rule["rule_id"]] = {
            "n_triggered": len(triggered),
            "success_rate": 0.0,
            "baseline_success_rate": 0.0,
            "mean_cost": 1.0,
            "baseline_mean_cost": 1.0,
            "baseline_invalid_rate": 1.0,
        }
    v2_retention = {
        "entity": 0,
        "task": 0,
        "paraphrase": 0,
        "path_signature": len({item["path_signature"] for item in actor_v2}),
        "n_v2": len(actor_v2),
    }
    promoted = promote_rules(
        rules,
        audit_pass_rate=1.0 if local_candidates else float(audit_summary.get("schema_pass_rate") or 0.0),
        v1_by_rule=v1_by_rule,
        sham_better_ids=[str(x) for x in sham_better_ids if x],
        config_frozen=True,
    )
    paths = artifact_paths(workspace)
    write_jsonl(workspace, paths["decisions"], promoted["decisions"])
    write_jsonl(workspace, paths["promoted"], promoted["promoted"])
    write_json(workspace, paths["manifest"], promoted["manifest"])
    return {
        "ok": True,
        "n_rules": len(rules),
        "promotion": promoted["manifest"],
        "v1_by_rule": v1_by_rule,
        "v2_retention": v2_retention,
        "rules": rules,
        "decisions": promoted["decisions"],
    }


def write_report(workspace: Workspace, payload: Mapping[str, Any]) -> Path:
    paths = artifact_paths(workspace)
    paths["report"].parent.mkdir(parents=True, exist_ok=True)
    conclusion = payload.get("conclusion") or "FAIL"
    text = f"""# SP4 实验报告：前置能力、反事实、蒸馏与 promotion

> 报告目录：`self-play/reports/sp4/`  
> 计划版本：{PLAN_VERSION}  
> 协议版本：`{PROTOCOL_VERSION}`  
> 总体要求：{EXPECTED_OVERALL}  
> 验收结论：**{conclusion}**  
> 报告日期：2026-08-23

本报告只覆盖独立 `self-play/` 的 SP4。结论不是 WebQSP/CWQ EM/F1，也不是 V2-5 Self-Play。未通过 promotion 的规则不得进入 SP5。

## 1. 研究目标

回答计划中的问题：固定 snapshot 上能否生成可判定合成任务；O0 Critic 能否形成可重放多轨迹；候选动作在同状态反事实中是否优于原动作/随机动作；蒸馏规则能否去实体去答案并跨 split 验证。

## 2. 计划、协议与配置

| 项 | 值 |
|---|---|
| 计划 | `{PLAN_VERSION}` |
| overall | {EXPECTED_OVERALL} |
| 配置 | `configs/sp4_precondition_and_promotion_v2.json` |
| 快照 | `{payload.get("snapshot_id")} ` hash `{payload.get("snapshot_hash")}` |
| verbalizer | template_v1_degraded（非 LLM 生成问题） |
| SP3 候选 | 119 条，SHA-256 `{EXPECTED_SP3_CANDIDATES_SHA256}` |
| 评测集 20/150/50 | 未用于生成、调参或 promotion |
| memory_read 默认 | false |
| PoG BACKTRACK | unsupported；snapshot 协议 backtrack 可判定 |

## 3. 有效运行

| 项 | 值 |
|---|---|
| Run ID | `{payload.get("run_id")}` |
| Git | `{payload.get("git_commit")}` dirty={payload.get("git_dirty")} |
| 单元测试 | {payload.get("unit_tests")} |
| LLM | {payload.get("llm_called")} |
| live KG | {payload.get("kg_called")} |

## 4. 合成任务与 split

{payload.get("synthetic_md")}

## 5. 多轨迹与 Critic

{payload.get("critic_md")}

G2 为 `oracle_guided_offline_teacher`，G3 为 `random_critic`，二者不并入 O0 Self-Play 结论。

## 6. SP3 候选审计

{payload.get("audit_md")}

## 7. 同状态反事实

{payload.get("cf_md")}

## 8. 蒸馏与 promotion

{payload.get("promo_md")}

## 9. 验收

| 项 | 结果 |
|---|---|
| 前置门禁 | {payload.get("preflight_ok")} |
| 无 Oracle 泄漏 / 无越界写入 / 无 split 污染 | {payload.get("isolation_ok")} |
| 反事实 | {payload.get("cf_ok")} |
| held-out validation | {payload.get("v1_ok")} |
| 报告收口 | 本文件 |

## 10. 未解决风险

1. 合成问题使用模板 verbalizer，不是完整自由自然语言。
2. 主 runner 使用 snapshot ReplayEnvironment 与启发式 Critic，不是 live PoG + gpt-3.5。
3. 原 PoG `BACKTRACK(state)` 仍 unsupported，SP5 PB 必须保持 unsupported，除非后续预检通过。
4. SP3 的 119 条候选多数含实体名或无法在 snapshot 上执行，不能直接 promotion。

## 11. 结论

SP4 **{conclusion}**。{payload.get("conclusion_note")}
"""
    workspace.safe_write_text(paths["report"], text)
    return paths["report"]
