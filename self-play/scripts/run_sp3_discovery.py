#!/usr/bin/env python3
"""SP3 candidate experience discovery. Does not inject memory. Does not use WebQSP 20/150 or CWQ 50."""

from __future__ import annotations

import argparse
import json
import os
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
from sp_memory.sp3_checks import preflight, run_all_sp3
from sp_memory.sp3_sampling import freeze_discovery, verify_discovery


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
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument(
        "--layer",
        choices=["preflight", "d0", "d1-g0", "d1-g1", "d1-g2", "d1-g3", "d1", "holdout", "all"],
        default="all",
    )
    parser.add_argument("--skip-g3", action="store_true")
    parser.add_argument("--skip-holdout", action="store_true")
    parser.add_argument("--freeze-if-missing", action="store_true", help="freeze D0/D1/H if the manifest is absent")
    parser.add_argument(
        "--continue-run",
        default=None,
        help="reuse an existing sp3-<timestamp>-<suffix> run directory so later layers can resume tasks and G2 can read G0 traces",
    )
    args = parser.parse_args()

    workspace = Workspace.from_this_package()
    workspace.ensure_output_dirs()
    config_path = Path(args.config) if args.config else workspace.configs_root / "sp3_candidate_discovery_v1.json"
    config, config_hash, config_path = load_config(config_path, workspace)
    config = dict(config)
    config["config_hash"] = config_hash
    config["allow_llm"] = True
    config["allow_live_kg"] = True
    config["allow_self_play_experience_memory_read"] = False
    config["allow_candidate_injection"] = False
    config["allow_oracle_in_actor"] = False

    if args.freeze_if_missing:
        freeze_discovery(workspace, config)
    try:
        verify_discovery(workspace, config)
    except Exception as exc:
        print(f"Discovery data is not frozen or failed verification: {exc}", file=sys.stderr)
        print("Run: python3 scripts/freeze_sp3_discovery.py", file=sys.stderr)
        return 2

    if args.layer != "preflight":
        if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("OPEANI_API_KEYS"):
            print("OPENAI_API_KEY is required for SP3 live discovery", file=sys.stderr)
            return 2
        os.environ.setdefault("OPENAI_API_BASE", str(config["llm"].get("default_api_base") or ""))

    continue_run = args.continue_run
    if continue_run:
        existing = workspace.runs_root / continue_run
        if not existing.is_dir():
            print(f"continue-run directory not found: {existing}", file=sys.stderr)
            return 2

    session = RunSession(
        workspace,
        run_id=continue_run,
        plan_version=str(config.get("plan_version", "SP3-PLAN 1.0")),
        config_hash=config_hash,
        command=sys.argv,
        seed=config.get("test_seed"),
        model_metadata={
            "llm_called": args.layer != "preflight",
            "kg_called": args.layer != "preflight",
            "allow_candidate_injection": False,
            "allow_self_play_experience_memory_read": False,
            "model": config["llm"]["model"],
            "prompt_version": config.get("prompt_version"),
            "critic_prompt_version": config.get("critic_prompt_version"),
            "continue_run": bool(continue_run),
        },
        input_files=[{"path": str(config_path), "sha256": config_hash, "role": "config"}],
        prefix="sp3",
    )
    config["run_id"] = session.run_id
    session.write_text("config_snapshot.json", canonical_json(config) + "\n", "frozen_config")
    session.write_text("env_names.json", canonical_json(redact_env()) + "\n", "env")

    try:
        pf = preflight(config, workspace)
        session.write_text("preflight.json", canonical_json(pf) + "\n", "preflight")
        if not pf["ok"]:
            payload = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "reason": "preflight_failed", "preflight": pf}
            session.write_text("sp3_check_result.json", canonical_json(payload) + "\n", "result")
            session.finish(RunStatus.FAILED, {"type": "preflight_failed", "errors": pf["errors"]})
            print(json.dumps({"run_id": session.run_id, "status": "FAIL", "reason": "preflight", "errors": pf["errors"]}, indent=2))
            return 1

        test_result = {"skipped": True}
        if not args.skip_tests:
            test_result = run_unittests()
            if test_result["skipped"]:
                raise RuntimeError("critical unit tests were skipped")
            if not test_result["was_successful"]:
                payload = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "reason": "unit_tests_failed", "unit_tests": test_result}
                session.write_text("sp3_check_result.json", canonical_json(payload) + "\n", "result")
                session.finish(RunStatus.FAILED, {"type": "unit_tests_failed", "unit_tests": test_result})
                print(json.dumps({"run_id": session.run_id, "status": "FAIL", "reason": "unit_tests"}, indent=2))
                return 1

        if args.layer == "preflight":
            result = {
                "protocol_version": PROTOCOL_VERSION,
                "status": "PASS",
                "reason": "preflight_only",
                "preflight": pf,
                "unit_tests": test_result,
                "run_id": session.run_id,
            }
        else:
            layers = [args.layer]
            result = run_all_sp3(
                config,
                workspace,
                session.run_dir,
                layers=layers,
                skip_g3=args.skip_g3 or args.layer in {"d0", "d1-g0", "d1-g1", "d1-g2"},
                skip_holdout=args.skip_holdout or args.layer != "all" and args.layer != "holdout",
            )
            result["run_id"] = session.run_id
            result["config_path"] = str(config_path)
            result["config_sha256"] = config_hash
            result["unit_tests"] = test_result

        session.write_text("sp3_check_result.json", canonical_json(result) + "\n", "result")
        summary_lines = [
            f"SP3 checks status={result['status']}",
            f"run_id={session.run_id}",
            f"protocol={PROTOCOL_VERSION}",
            f"config_sha256={config_hash}",
            f"model={config['llm']['model']}",
            f"endpoint={config.get('endpoint')}",
            "candidate_injection=false",
            "oracle_level_actor=O0",
        ]
        session.write_text("summary.txt", "\n".join(summary_lines) + "\n", "summary")
        artifacts_result = workspace.artifacts_root / "protocol" / "sp3_check_result.json"
        workspace.safe_write_text(artifacts_result, canonical_json(result) + "\n")
        session.add_output(artifacts_result, "shared_result")
        status = RunStatus.SUCCESS if result["status"] == "PASS" else RunStatus.FAILED
        session.finish(status, None if status is RunStatus.SUCCESS else {"type": result.get("reason") or result["status"]})
        print("\n".join(summary_lines))
        return 0 if status is RunStatus.SUCCESS else 1
    except Exception:
        tb = traceback.format_exc()
        session.write_text("stderr_summary.txt", tb, "stderr")
        session.finish(RunStatus.FAILED, {"type": "unhandled_exception"})
        print(tb, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
