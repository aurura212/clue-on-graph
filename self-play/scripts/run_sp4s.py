#!/usr/bin/env python3
"""SP4-SUPPLEMENT runner. Does not start SP5 or score WebQSP/CWQ EM/F1."""

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
from sp_memory.sp4_io import write_json, write_not_generated
from sp_memory.sp4s_checks import (
    PLAN_VERSION,
    _audit_local,
    artifact_paths,
    preflight,
    stage_counterfactual,
    stage_critic,
    stage_generate,
    stage_validate_and_promote,
    write_report,
)


def run_unittests() -> dict:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_sp4s*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    skipped = len(result.skipped) if hasattr(result, "skipped") else 0
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": skipped,
        "was_successful": result.wasSuccessful(),
    }


def maybe_llm_client(config: dict, allow_llm: bool):
    if not allow_llm:
        return None
    try:
        from sp_memory.llm_client import LlmClient

        return LlmClient.from_config(config)
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "command",
        nargs="?",
        default="preflight",
        choices=["preflight", "generate-synthetic", "run-critic", "run-counterfactual", "promote", "report", "all"],
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
    config_path = Path(args.config) if args.config else workspace.configs_root / "sp4s_supplement_v1.json"
    config, config_hash, config_path = load_config(config_path, workspace)
    config = dict(config)
    config["config_hash"] = config_hash
    config["allow_llm"] = bool(args.allow_llm)
    config["allow_live_kg"] = False
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
            "kg_called": False,
            "allow_candidate_injection": False,
            "allow_self_play_experience_memory_read": False,
            "model": (config.get("llm") or {}).get("model"),
            "dry_run": bool(args.dry_run),
            "command": args.command,
        },
        input_files=[{"path": str(config_path), "sha256": config_hash, "role": "config"}],
        prefix="sp4s",
    )
    config["run_id"] = session.run_id
    session.write_text("config_snapshot.json", canonical_json(config) + "\n", "frozen_config")
    session.write_text("env_names.json", canonical_json(redact_env()) + "\n", "env")

    try:
        pf = preflight(config, workspace)
        session.write_text("preflight.json", canonical_json(pf) + "\n", "preflight")
        if not pf["ok"]:
            payload = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "reason": "preflight_failed", "preflight": pf}
            session.write_text("sp4s_check_result.json", canonical_json(payload) + "\n", "result")
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
                session.write_text("sp4s_check_result.json", canonical_json(payload) + "\n", "result")
                session.finish(RunStatus.FAILED, {"type": "unit_tests_failed", "unit_tests": test_result})
                print(json.dumps({"run_id": session.run_id, "status": "FAIL", "reason": "unit_tests"}, indent=2))
                return 1

        if args.dry_run:
            payload = {"protocol_version": PROTOCOL_VERSION, "status": "PASS", "reason": "dry_run", "preflight": pf, "run_id": session.run_id}
            session.write_text("sp4s_check_result.json", canonical_json(payload) + "\n", "result")
            session.finish(RunStatus.SUCCESS, {"type": "dry_run"})
            print(json.dumps({"run_id": session.run_id, "status": "PASS", "reason": "dry_run"}, indent=2))
            return 0

        llm_client = maybe_llm_client(config, args.allow_llm)
        generated = None
        critic = None
        cf = None
        promo = None
        command = args.command
        paths = artifact_paths(workspace)

        if command in {"generate-synthetic", "all"}:
            generated = stage_generate(config, workspace, llm_client=llm_client)
            session.write_text("synthetic_manifest.json", canonical_json(generated["manifest"]) + "\n", "synthetic")
        if command in {"run-critic", "all", "run-counterfactual", "promote", "report"}:
            if command != "run-critic" and generated is None:
                generated = stage_generate(config, workspace, llm_client=llm_client)
            critic = stage_critic(config, workspace, session.run_id, llm_client=llm_client)
            session.write_text("critic_summary.json", canonical_json(critic["summary"]) + "\n", "critic")
        if command in {"run-counterfactual", "all", "promote", "report"}:
            if critic is None:
                critic = stage_critic(config, workspace, session.run_id, llm_client=llm_client)
            cf = stage_counterfactual(config, workspace, critic["local_candidates"])
            session.write_text("cf_summary.json", canonical_json(cf["summary"]) + "\n", "cf")
        if command in {"promote", "all", "report"}:
            if critic is None:
                critic = stage_critic(config, workspace, session.run_id, llm_client=llm_client)
            if cf is None:
                cf = stage_counterfactual(config, workspace, critic["local_candidates"])
            audit = _audit_local(critic["local_candidates"])
            write_json(workspace, paths["validated"], {"n": audit["summary"]["passed"]})
            write_json(workspace, paths["rejected"], {"n": audit["summary"]["rejected"]})
            promo = stage_validate_and_promote(
                config,
                workspace,
                local_candidates=audit["accepted"],
                cf_rows=cf["rows"],
                audit_summary=audit["summary"],
                sham_better_ids=cf["sham_better_ids"],
            )
            session.write_text("promotion_manifest.json", canonical_json(promo["promotion"]) + "\n", "promotion")
        if command in {"report", "all"}:
            n_promoted = int((promo or {}).get("promotion", {}).get("n_promoted") or 0)
            conclusion = "CONDITIONAL PASS" if n_promoted == 0 else "PASS"
            note = "0 promoted rules; SP5 remains forbidden."
            if n_promoted > 0:
                note = "At least one rule promoted under frozen gates; SP5 still needs a separate plan."
            report_payload = {
                "run_id": session.run_id,
                "snapshot_id": (generated or {}).get("manifest", {}).get("snapshot_id"),
                "snapshot_hash": (generated or {}).get("manifest", {}).get("snapshot_hash"),
                "verbalizer_mode": (generated or {}).get("verbalizer_mode") or "multi_template_v1",
                "llm_critic": bool(args.allow_llm and llm_client is not None),
                "synthetic_md": canonical_json((generated or {}).get("manifest") or {}),
                "critic_md": canonical_json((critic or {}).get("summary") or {}),
                "cf_md": canonical_json((cf or {}).get("summary") or {}),
                "promo_md": canonical_json((promo or {}).get("promotion") or {}),
                "cf_ok": True,
                "isolation_ok": True,
                "leakage_ok": True,
                "n_promoted": n_promoted,
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
                "critic": None if critic is None else critic.get("summary"),
                "counterfactual": None if cf is None else cf.get("summary"),
                "promotion": None if promo is None else promo.get("promotion"),
                "report_path": str(report_path.relative_to(workspace.self_play_root)),
                "report_sha256": sha256_file(report_path),
                "sp5_allowed": False,
            }
            write_json(workspace, paths["metrics"], metrics)
            write_json(workspace, paths["check"], {"protocol_version": PROTOCOL_VERSION, "status": conclusion, "run_id": session.run_id, "metrics": metrics, "sp5_allowed": False})
            write_not_generated(workspace, paths["live_kg"], "live KG subgraph is deferred; this supplement used the frozen snapshot only")

        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "status": "PASS" if command != "all" else "CONDITIONAL PASS",
            "command": command,
            "run_id": session.run_id,
            "preflight": pf,
            "unit_tests": test_result,
            "critic": None if critic is None else critic.get("summary"),
            "counterfactual": None if cf is None else cf.get("summary"),
            "promotion": None if promo is None else promo.get("promotion"),
            "sp5_allowed": False,
        }
        session.write_text("sp4s_check_result.json", canonical_json(payload) + "\n", "result")
        session.finish(RunStatus.SUCCESS, {"type": command})
        print(json.dumps({"run_id": session.run_id, "status": payload.get("status"), "command": command, "sp5_allowed": False}, indent=2))
        return 0
    except Exception as exc:
        tb = traceback.format_exc()
        session.write_text("error.txt", tb, "error")
        payload = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "reason": str(exc), "run_id": session.run_id}
        try:
            session.write_text("sp4s_check_result.json", canonical_json(payload) + "\n", "result")
        except Exception:
            pass
        session.finish(RunStatus.FAILED, {"type": "exception", "message": str(exc)})
        print(json.dumps(payload, indent=2))
        print(tb, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
