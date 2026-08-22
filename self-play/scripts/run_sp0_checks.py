#!/usr/bin/env python3
"""Single entry for SP0 protocol checks. No LLM. No KGQA scoring."""

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

from sp_memory.checks import run_all_experiments
from sp_memory.config import load_config
from sp_memory.hashing import canonical_json, sha256_file
from sp_memory.manifest import RunSession
from sp_memory.paths import PROTOCOL_VERSION, Workspace
from sp_memory.schemas import RunStatus


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
    parser.add_argument("--fail-fixture", action="store_true", help="unused; E0.7 runs an isolated missing-set fixture")
    args = parser.parse_args()

    workspace = Workspace.from_this_package()
    workspace.ensure_output_dirs()
    config, config_hash, config_path = load_config(Path(args.config) if args.config else None, workspace)
    session = RunSession(
        workspace,
        plan_version=str(config.get("plan_version", "SP0-PLAN 1.4")),
        config_hash=config_hash,
        command=sys.argv,
        seed=(config.get("eval_sampling") or {}).get("seed"),
        model_metadata={"llm_called": False, "kg_called": False},
        input_files=[{"path": str(config_path), "sha256": config_hash, "role": "config"}],
    )
    session.write_text("config_snapshot.json", canonical_json(config) + "\n", "frozen_config")

    exit_code = 1
    try:
        test_result = {"skipped": True}
        if not args.skip_tests:
            test_result = run_unittests()
            if test_result["skipped"]:
                raise RuntimeError("critical unit tests were skipped")
            if not test_result["was_successful"]:
                payload = {
                    "protocol_version": PROTOCOL_VERSION,
                    "status": "FAIL",
                    "reason": "unit_tests_failed",
                    "unit_tests": test_result,
                }
                session.write_text("sp0_check_result.json", canonical_json(payload) + "\n", "result")
                session.finish(RunStatus.FAILED, {"type": "unit_tests_failed", "unit_tests": test_result})
                print(json.dumps({"run_id": session.run_id, "status": "FAIL", "reason": "unit_tests"}, indent=2))
                return 1

        result = run_all_experiments(config, workspace)
        result["run_id"] = session.run_id
        result["config_path"] = str(config_path)
        result["config_sha256"] = config_hash
        result["unit_tests"] = test_result
        result_path = session.write_text("sp0_check_result.json", canonical_json(result) + "\n", "result")
        summary_lines = [
            f"SP0 checks status={result['status']}",
            f"run_id={session.run_id}",
            f"protocol={PROTOCOL_VERSION}",
            f"config_sha256={config_hash}",
        ]
        for name, item in result["experiments"].items():
            summary_lines.append(f"{name}: {item['status']}")
        for key, value in result["metrics"].items():
            summary_lines.append(f"{key}={value}")
        session.write_text("summary.txt", "\n".join(summary_lines) + "\n", "summary")
        artifacts_result = workspace.artifacts_root / "protocol" / "sp0_check_result.json"
        workspace.safe_write_text(artifacts_result, canonical_json(result) + "\n")
        session.add_output(artifacts_result, "shared_result")
        status = RunStatus.SUCCESS if result["status"] == "PASS" else RunStatus.FAILED
        session.finish(status, None if status is RunStatus.SUCCESS else {"type": "check_failed"})
        print("\n".join(summary_lines))
        print(f"result={result_path}")
        exit_code = 0 if result["status"] == "PASS" else 1
        return exit_code
    except Exception as exc:
        err = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        try:
            session.write_text("sp0_check_result.json", canonical_json({"status": "FAIL", "error": err}) + "\n", "result")
            session.write_text("stderr_summary.txt", err["traceback"], "stderr")
            session.finish(RunStatus.FAILED, err)
        except Exception:
            traceback.print_exc()
        print(json.dumps({"run_id": session.run_id, "status": "FAIL", "error": err["type"]}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
