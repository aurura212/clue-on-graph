#!/usr/bin/env python3
"""SP2-B LLM+live KG baseline rollout. No Self-Play Experience Memory. No WebQSP 150 / CWQ 50."""

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
from sp_memory.sp2b_checks import preflight, run_all_sp2b, run_layer, load_registry, load_b2_tasks


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
    parser.add_argument("--layer", choices=["all", "b0", "b1", "b2"], default="all")
    args = parser.parse_args()

    workspace = Workspace.from_this_package()
    workspace.ensure_output_dirs()
    config_path = Path(args.config) if args.config else workspace.configs_root / "sp2b_llm_kg_baseline_v1.json"
    config, config_hash, config_path = load_config(config_path, workspace)
    config = dict(config)
    config["allow_llm"] = True
    config["allow_live_kg"] = True
    config["allow_self_play_experience_memory"] = False
    config["allow_oracle_in_actor"] = False
    config["allow_memory"] = False

    if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("OPEANI_API_KEYS"):
        print("OPENAI_API_KEY is required for SP2-B live rollout", file=sys.stderr)
        return 2
    os.environ.setdefault("OPENAI_API_BASE", str(config["llm"].get("default_api_base") or ""))

    session = RunSession(
        workspace,
        plan_version=str(config.get("plan_version", "SP2B-PLAN 1.1")),
        config_hash=config_hash,
        command=sys.argv,
        seed=config.get("test_seed"),
        model_metadata={
            "llm_called": True,
            "kg_called": True,
            "allow_llm": True,
            "allow_live_kg": True,
            "allow_self_play_experience_memory": False,
            "model": config["llm"]["model"],
            "prompt_version": config.get("prompt_version"),
        },
        input_files=[{"path": str(config_path), "sha256": config_hash, "role": "config"}],
        prefix="sp2b",
    )
    session.write_text("config_snapshot.json", canonical_json(config) + "\n", "frozen_config")
    session.write_text("env_names.json", canonical_json(redact_env()) + "\n", "env")

    try:
        pf = preflight(config, workspace)
        session.write_text("preflight.json", canonical_json(pf) + "\n", "preflight")
        if not pf["ok"]:
            payload = {
                "protocol_version": PROTOCOL_VERSION,
                "status": "FAIL",
                "reason": "preflight_failed",
                "preflight": pf,
            }
            session.write_text("sp2b_check_result.json", canonical_json(payload) + "\n", "result")
            session.finish(RunStatus.FAILED, {"type": "preflight_failed", "errors": pf["errors"]})
            print(json.dumps({"run_id": session.run_id, "status": "FAIL", "reason": "preflight", "errors": pf["errors"]}, indent=2))
            return 1

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
                session.write_text("sp2b_check_result.json", canonical_json(payload) + "\n", "result")
                session.finish(RunStatus.FAILED, {"type": "unit_tests_failed", "unit_tests": test_result})
                print(json.dumps({"run_id": session.run_id, "status": "FAIL", "reason": "unit_tests"}, indent=2))
                return 1

        if args.layer == "all":
            result = run_all_sp2b(config, workspace, session.run_dir)
        else:
            result = {"protocol_version": PROTOCOL_VERSION, "status": "FAIL", "preflight": pf}
            if args.layer == "b0":
                reg = load_registry(workspace, config["b0_task_registry"])
                result["layers"] = {"B0": run_layer(config, workspace, session.run_dir / "b0", "B0", reg["tasks"], reg)}
                result["status"] = "PASS" if result["layers"]["B0"]["unclassified"] == 0 else "FAIL"
            elif args.layer == "b1":
                reg = load_registry(workspace, config["b1_task_registry"])
                result["layers"] = {"B1": run_layer(config, workspace, session.run_dir / "b1", "B1", reg["tasks"], reg)}
                result["status"] = "PASS" if result["layers"]["B1"]["unclassified"] == 0 else "FAIL"
            else:
                tasks = load_b2_tasks(workspace, config)
                result["layers"] = {
                    "B2": run_layer(config, workspace, session.run_dir / "b2", "B2", tasks, {"oracle": {}}, allow_eval_ids=True)
                }
                result["status"] = "PASS" if result["layers"]["B2"]["unclassified"] == 0 else "FAIL"
        result["run_id"] = session.run_id
        result["config_path"] = str(config_path)
        result["config_sha256"] = config_hash
        result["unit_tests"] = test_result
        session.write_text("sp2b_check_result.json", canonical_json(result) + "\n", "result")
        summary_lines = [
            f"SP2-B checks status={result['status']}",
            f"run_id={session.run_id}",
            f"protocol={PROTOCOL_VERSION}",
            f"config_sha256={config_hash}",
            f"model={config['llm']['model']}",
            f"endpoint={config.get('endpoint')}",
        ]
        for name, item in (result.get("layers") or {}).items():
            summary_lines.append(
                f"{name}: n={item.get('n')} terminated={item.get('terminated_rate')} replay={item.get('replay_rate')} unclassified={item.get('unclassified')}"
            )
        session.write_text("summary.txt", "\n".join(summary_lines) + "\n", "summary")
        artifacts_result = workspace.artifacts_root / "protocol" / "sp2b_check_result.json"
        workspace.safe_write_text(artifacts_result, canonical_json(result) + "\n")
        session.add_output(artifacts_result, "shared_result")
        overlap = {
            "protocol_version": PROTOCOL_VERSION,
            "stage": "SP2-B",
            "purpose": "development_entity_exposure",
            "b0": (result.get("preflight") or {}).get("b0_exclusion"),
            "b1": (result.get("preflight") or {}).get("b1_exclusion"),
            "note": "Public MIDs used in SP2-B development tasks. Exclude from later memory discovery. B2 uses frozen smoke 20 only after B0/B1 gates.",
        }
        exposure_path = workspace.artifacts_root / "registries" / "sp2b_exposure_registry_v1.json"
        workspace.safe_write_text(exposure_path, canonical_json(overlap) + "\n")
        session.add_output(exposure_path, "exposure")
        if result["status"] == "PASS":
            status = RunStatus.SUCCESS
        elif result["status"] == "INVALID":
            status = RunStatus.INVALID
        else:
            status = RunStatus.FAILED
        session.finish(
            status,
            None if status is RunStatus.SUCCESS else {"type": result.get("reason") or result["status"]},
        )
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
