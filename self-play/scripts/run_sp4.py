#!/usr/bin/env python3
"""SP4 precondition, counterfactual, distillation, and promotion. Does not run SP5 or WebQSP/CWQ EM/F1."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

from sp_memory.config import load_config
from sp_memory.hashing import canonical_json, sha256_file
from sp_memory.manifest import RunSession, redact_env
from sp_memory.paths import PROTOCOL_VERSION, Workspace
from sp_memory.schemas import RunStatus
from sp_memory.sp4_checks import (
    PLAN_VERSION,
    artifact_paths,
    preflight,
    stage_audit,
    stage_counterfactual,
    stage_critic,
    stage_generate,
    stage_validate_and_promote,
    write_report,
)
from sp_memory.sp4_io import write_json
from sp_memory.synthetic_tasks import snapshot_paths, verify_synthetic


def run_unittests() -> dict:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    skipped = len(result.skipped) if hasattr(result, "skipped") else 0
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": skipped,
        "was_successful": result.wasSuccessful(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "command",
        nargs="?",
        default="preflight",
        choices=[
            "preflight",
            "generate-synthetic",
            "run-critic",
            "audit-candidates",
            "run-counterfactual",
            "distill",
            "validate",
            "promote",
            "report",
            "all",
        ],
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--allow-live-kg", action="store_true")
    args = parser.parse_args()

    workspace = Workspace.from_this_package()
    workspace.ensure_output_dirs()
    config_path = Path(args.config) if args.config else workspace.configs_root / "sp4_precondition_and_promotion_v2.json"
    config, config_hash, config_path = load_config(config_path, workspace)
    config = dict(config)
    config["config_hash"] = config_hash
    config["allow_llm"] = bool(args.allow_llm)
    config["allow_live_kg"] = bool(args.allow_live_kg)
    config["allow_self_play_experience_memory_read"] = False
    config["allow_candidate_injection"] = False
    config["allow_oracle_in_actor"] = False
    if args.seed is not None:
        config["synthetic_seed"] = args.seed

    session = RunSession(
        workspace,
        run_id=args.run_id,
        plan_version=str(config.get("plan_version") or PLAN_VERSION),
        config_hash=config_hash,
        command=sys.argv,
        seed=config.get("synthetic_seed"),
        model_metadata={
            "llm_called": bool(args.allow_llm),
            "kg_called": bool(args.allow_live_kg),
            "allow_candidate_injection": False,
            "allow_self_play_experience_memory_read": False,
            "model": (config.get("llm") or {}).get("model"),
            "dry_run": bool(args.dry_run),
            "command": args.command,
        },
        input_files=[{"path": str(config_path), "sha256": config_hash, "role": "config"}],
        prefix="sp4",
    )
    config["run_id"] = session.run_id
    session.write_text("config_snapshot.json", canonical_json(config) + "\n", "frozen_config")
    session.write_text("env_names.json", canonical_json(redact_env()) + "\n", "env")

    try:
        pf = preflight(config, workspace)
        session.write_text("preflight.json", canonical_json(pf) + "\n", "preflight")
        if not pf["ok"]:
            payload = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "reason": "preflight_failed", "preflight": pf}
            session.write_text("sp4_check_result.json", canonical_json(payload) + "\n", "result")
            session.finish(RunStatus.FAILED, {"type": "preflight_failed", "errors": pf["errors"]})
            print(json.dumps({"run_id": session.run_id, "status": "FAIL", "reason": "preflight", "errors": pf["errors"]}, indent=2))
            return 1

        test_result = {"skipped": True}
        if not args.skip_tests and args.command in {"preflight", "all"}:
            test_result = run_unittests()
            if test_result["skipped"]:
                raise RuntimeError("critical unit tests were skipped")
            if not test_result["was_successful"]:
                payload = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "reason": "unit_tests_failed", "unit_tests": test_result}
                session.write_text("sp4_check_result.json", canonical_json(payload) + "\n", "result")
                session.finish(RunStatus.FAILED, {"type": "unit_tests_failed", "unit_tests": test_result})
                print(json.dumps({"run_id": session.run_id, "status": "FAIL", "reason": "unit_tests"}, indent=2))
                return 1

        if args.dry_run:
            payload = {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "reason": "dry_run", "preflight": pf, "run_id": session.run_id}
            session.write_text("sp4_check_result.json", canonical_json(payload) + "\n", "result")
            session.finish(RunStatus.SUCCESS, {"type": "dry_run"})
            print(json.dumps({"run_id": session.run_id, "status": "PASS", "reason": "dry_run"}, indent=2))
            return 0

        generated = None
        critic = None
        audit = None
        cf = None
        promo = None
        command = args.command
        need_generate = command in {"generate-synthetic", "all"}
        need_critic = command in {"run-critic", "all", "run-counterfactual", "distill", "validate", "promote", "report"}
        if command == "preflight":
            payload = {
                "protocol_version": PROTOCOL_VERSION,
                "status": "PASS",
                "reason": "preflight_only",
                "preflight": pf,
                "unit_tests": test_result,
                "run_id": session.run_id,
            }
        else:
            if need_generate or command != "audit-candidates":
                if command in {"generate-synthetic", "all"} or not (workspace.artifacts_root / "datasets" / "sp4_synthetic_manifest_v1.json").exists():
                    generated = stage_generate(config, workspace)
                    session.write_text("synthetic_manifest.json", canonical_json(generated["manifest"]) + "\n", "synthetic")
            if command in {"run-critic", "all", "run-counterfactual", "distill", "validate", "promote", "report"}:
                critic = stage_critic(config, workspace, session.run_id)
                session.write_text("critic_summary.json", canonical_json(critic["summary"]) + "\n", "critic")
            if command in {"audit-candidates", "all", "report"}:
                audit = stage_audit(workspace)
                session.write_text("audit_summary.json", canonical_json(audit["summary"]) + "\n", "audit")
            local = list((critic or {}).get("local_candidates") or [])
            if command in {"run-counterfactual", "all", "distill", "validate", "promote", "report"}:
                if critic is None:
                    critic = stage_critic(config, workspace, session.run_id)
                    local = critic["local_candidates"]
                cf = stage_counterfactual(config, workspace, local)
                session.write_text("cf_summary.json", canonical_json(cf["summary"]) + "\n", "cf")
            if command in {"distill", "validate", "promote", "all", "report"}:
                if audit is None:
                    audit = stage_audit(workspace)
                if cf is None:
                    if critic is None:
                        critic = stage_critic(config, workspace, session.run_id)
                    cf = stage_counterfactual(config, workspace, critic["local_candidates"])
                promo = stage_validate_and_promote(
                    config,
                    workspace,
                    local_candidates=critic["local_candidates"],
                    cf_rows=cf["rows"],
                    audit_summary=audit["summary"],
                    sham_better_ids=cf["sham_better_ids"],
                )
                session.write_text("promotion_manifest.json", canonical_json(promo["promotion"]) + "\n", "promotion")
            if command in {"report", "all"}:
                syn_paths = snapshot_paths(workspace)
                snapshot = json.loads(syn_paths["snapshot"].read_text(encoding="utf-8"))
                conclusion = "CONDITIONAL PASS"
                note = "前置能力、合成 split、反事实和 promotion 判定已落地；无规则达到 promotion 门槛时不得进入 SP5。模板 verbalizer 与启发式 Critic 是登记的降级。"
                if promo and promo["promotion"]["n_promoted"] > 0:
                    note = "至少一条规则进入 promoted_memory，但仍不得把结果写成 WebQSP/CWQ EM/F1 提升。"
                n_promoted = (promo or {}).get("promotion", {}).get("n_promoted", 0)
                isolation_ok = pf["ok"] and not pf.get("secret_hits")
                report_payload = {
                    "run_id": session.run_id,
                    "git_commit": session.manifest.git_commit,
                    "git_dirty": session.manifest.git_dirty,
                    "unit_tests": test_result,
                    "llm_called": False,
                    "kg_called": False,
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "snapshot_hash": snapshot.get("snapshot_hash"),
                    "synthetic_md": canonical_json((generated or {}).get("manifest") or verify_synthetic(workspace, {**config, "expected_synthetic_manifest_hash": None})["manifest"]),
                    "critic_md": canonical_json((critic or {}).get("summary")),
                    "audit_md": canonical_json((audit or {}).get("summary")),
                    "cf_md": canonical_json((cf or {}).get("summary")),
                    "promo_md": canonical_json((promo or {}).get("promotion")),
                    "preflight_ok": pf["ok"],
                    "isolation_ok": isolation_ok,
                    "cf_ok": bool(cf and cf["ok"]),
                    "v1_ok": bool(promo and promo["ok"]),
                    "conclusion": conclusion,
                    "conclusion_note": note,
                }
                report_path = write_report(workspace, report_payload)
                metrics = {
                    "protocol_version": PROTOCOL_VERSION,
                    "plan_version": PLAN_VERSION,
                    "run_id": session.run_id,
                    "conclusion": conclusion,
                    "n_promoted": n_promoted,
                    "preflight_ok": pf["ok"],
                    "critic": (critic or {}).get("summary"),
                    "audit": (audit or {}).get("summary"),
                    "counterfactual": (cf or {}).get("summary"),
                    "promotion": (promo or {}).get("promotion"),
                    "report_path": str(report_path.relative_to(workspace.self_play_root)),
                    "report_sha256": sha256_file(report_path),
                    "config_sha256": config_hash,
                }
                write_json(workspace, artifact_paths(workspace)["metrics"], metrics)
                write_json(workspace, artifact_paths(workspace)["check"], {"protocol_version": PROTOCOL_VERSION, "status": conclusion, "run_id": session.run_id, "metrics": metrics})
            payload = {
                "protocol_version": PROTOCOL_VERSION,
                "status": "PASS" if command != "all" else "CONDITIONAL PASS",
                "command": command,
                "run_id": session.run_id,
                "preflight": pf,
                "unit_tests": test_result,
                "generate": None if generated is None else {"manifest_hash": generated["manifest"].get("manifest_hash")},
                "critic": None if critic is None else critic["summary"],
                "audit": None if audit is None else {k: v for k, v in audit["summary"].items() if k != "audits"},
                "counterfactual": None if cf is None else cf["summary"],
                "promotion": None if promo is None else promo["promotion"],
            }
        session.write_text("sp4_check_result.json", canonical_json(payload) + "\n", "result")
        artifacts_result = workspace.artifacts_root / "protocol" / "sp4_check_result.json"
        workspace.safe_write_text(artifacts_result, canonical_json(payload) + "\n")
        session.finish(RunStatus.SUCCESS, {"type": command})
        print(json.dumps({"run_id": session.run_id, "status": payload.get("status"), "command": command}, indent=2))
        return 0
    except Exception as exc:
        tb = traceback.format_exc()
        session.write_text("error.txt", tb, "error")
        payload = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "reason": str(exc), "run_id": session.run_id}
        try:
            session.write_text("sp4_check_result.json", canonical_json(payload) + "\n", "result")
        except Exception:
            pass
        session.finish(RunStatus.FAILED, {"type": "exception", "message": str(exc)})
        print(json.dumps(payload, indent=2))
        print(tb, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
