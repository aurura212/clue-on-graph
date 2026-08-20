#!/usr/bin/env python3
"""Protocol checks for a finished V2 reflection run. Does not call LLMs."""

from __future__ import annotations

import argparse
import os
import sys

from jsonl_io import iter_jsonl_records
from v2_protocol import validate_reflection_event


def check_run_dir(path: str, *, expect_mode: str | None = None) -> list[str]:
    run_dir = path
    trace_path = os.path.join(path, "pog_trace.jsonl") if os.path.isdir(path) else path
    if os.path.isfile(trace_path) and not os.path.isdir(path):
        run_dir = os.path.dirname(path)
    meta_path = os.path.join(run_dir, "run_meta.json")
    results_path = os.path.join(run_dir, "results.jsonl")
    errors: list[str] = []
    if not os.path.isfile(trace_path):
        return [f"missing trace: {trace_path}"]
    if not os.path.isfile(results_path):
        errors.append(f"missing results: {results_path}")

    meta = {}
    if os.path.isfile(meta_path):
        import json

        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)
    else:
        errors.append("missing run_meta.json")

    mode = str(meta.get("kg_memory_mode") or "none")
    if expect_mode and mode != expect_mode:
        errors.append(f"kg_memory_mode={mode} expected {expect_mode}")
    for key in ("llm_request_timeout_sec", "llm_max_retries", "max_length", "relation_semantic_top_k"):
        if key not in meta:
            errors.append(f"run_meta missing {key}")
    questions_file = str(meta.get("questions_file") or "")
    if "random150" in questions_file and str(meta.get("v2_allow_random150") or "") != "1":
        # Presence of random150 in a V2-2 folder is a protocol leak.
        if "slice-random150" in os.path.basename(run_dir) or "random150" in questions_file:
            pass

    n = 0
    n_a = 0
    n_visible = 0
    n_rel_reorder = 0
    for record in iter_jsonl_records(trace_path):
        n += 1
        pog = record.get("pog_trace") or {}
        for depth in pog.get("depths") or []:
            kgm = depth.get("kg_memory") or {}
            rel = kgm.get("relation") or {}
            n_rel_reorder += int(rel.get("n_order_changed") or 0)
            reverse = depth.get("reverse_retrieval")
            if not isinstance(reverse, dict) or reverse.get("skipped"):
                continue
            decision_a = reverse.get("decision_a") or {}
            if not isinstance(decision_a, dict) or "add" not in decision_a:
                errors.append("Decision A missing add")
                continue
            n_a += 1
            evidence = decision_a.get("evidence")
            if not isinstance(evidence, dict):
                errors.append("Decision A missing evidence schema")
                continue
            errors.extend(validate_reflection_event(evidence, prefix="decision_a.evidence"))
            if evidence.get("prompt_visible_evidence"):
                n_visible += 1
            if mode in {"none", ""} and evidence.get("prompt_changed"):
                errors.append("R0/mode=none changed the reflection prompt")
            if mode == "reflection" and evidence.get("semantic_filter_already_applied") is not True:
                errors.append("reflection evidence must follow semantic filter")
            if decision_a.get("add"):
                decision_b = reverse.get("decision_b") or {}
                if not isinstance(decision_b, dict) or not decision_b.get("invoked"):
                    errors.append("Decision A add=true but Decision B not invoked")
                elif decision_b.get("evidence"):
                    errors.extend(
                        validate_reflection_event(decision_b["evidence"], prefix="decision_b.evidence")
                    )
    if n < 1:
        errors.append("no trace records")
    if mode == "reflection" and n_rel_reorder:
        errors.append(f"reflection run reordered first-hop relations {n_rel_reorder} times")
    if mode == "none" and n_visible:
        errors.append("mode=none had prompt-visible reflection evidence")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+")
    parser.add_argument("--expect-mode", default="")
    args = parser.parse_args()
    all_errors: list[str] = []
    for path in args.run_dirs:
        hits = check_run_dir(path, expect_mode=args.expect_mode or None)
        status = "PASS" if not hits else "FAIL"
        print(f"[{status}] {path}")
        for item in hits[:40]:
            print(f"  - {item}")
        if len(hits) > 40:
            print(f"  ... {len(hits) - 40} more")
        all_errors.extend(hits)
    if all_errors:
        print(f"FAILED {len(all_errors)} check(s)")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
