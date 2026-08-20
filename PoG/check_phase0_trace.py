#!/usr/bin/env python3
"""Offline Phase 0 checker for pog_trace.jsonl completeness.

Does not call LLMs. Exit 0 only when every question has the required stage fields.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from jsonl_io import iter_jsonl_records


NO_DEPTH_STOP_REASONS = {"no_topic_entity_cot"}


def resolve_trace_path(path: str) -> str:
    if os.path.isdir(path):
        candidate = os.path.join(path, "pog_trace.jsonl")
        if os.path.isfile(candidate):
            return candidate
        raise FileNotFoundError(f"No pog_trace.jsonl in directory: {path}")
    if os.path.isfile(path):
        return path
    raise FileNotFoundError(path)


def question_text(record: dict[str, Any]) -> str:
    if "RawQuestion" in record:
        return str(record["RawQuestion"])
    if "question" in record:
        return str(record["question"])
    for key, value in record.items():
        if key != "pog_trace" and isinstance(value, str):
            return value
    return "<unknown>"


def check_relation_trace(rel_trace: Any, qid: str, depth: int, idx: int) -> list[str]:
    errors = []
    prefix = f"{qid} depth={depth} relation_prune[{idx}]"
    if not isinstance(rel_trace, dict):
        return [f"{prefix}: not an object"]
    for field in (
        "candidate_relations",
        "retrieved_relations",
        "selected_relations",
        "head_relations_before_filter",
        "tail_relations_before_filter",
    ):
        if field not in rel_trace:
            errors.append(f"{prefix}: missing {field}")
    return errors


def check_depth(depth_record: Any, qid: str) -> list[str]:
    errors = []
    if not isinstance(depth_record, dict):
        return [f"{qid}: depth record is not an object"]
    depth = depth_record.get("depth")
    prefix = f"{qid} depth={depth}"

    if "relation_prune" not in depth_record or not isinstance(depth_record["relation_prune"], list):
        errors.append(f"{prefix}: missing relation_prune list")
    else:
        for idx, rel_trace in enumerate(depth_record["relation_prune"]):
            errors.extend(check_relation_trace(rel_trace, qid, depth, idx))

    if "entity_search" not in depth_record or not isinstance(depth_record.get("entity_search"), list):
        errors.append(f"{prefix}: missing entity_search list")

    stats = depth_record.get("exploration_stats")
    if not isinstance(stats, dict):
        errors.append(f"{prefix}: missing exploration_stats")
    else:
        for field in (
            "n_frontier_entities",
            "n_relations_selected",
            "n_entity_search_attempts",
            "n_relations_dead_end",
            "n_entities_before_prune",
            "n_entities_after_prune",
        ):
            if field not in stats:
                errors.append(f"{prefix}: exploration_stats missing {field}")

    reverse = depth_record.get("reverse_retrieval")
    if reverse is None:
        return errors
    if not isinstance(reverse, dict):
        errors.append(f"{prefix}: reverse_retrieval is not an object")
        return errors
    if reverse.get("skipped"):
        return errors
    decision_a = reverse.get("decision_a")
    if not isinstance(decision_a, dict) or "add" not in decision_a:
        errors.append(f"{prefix}: reflection Decision A missing (need decision_a.add)")
        return errors
    if decision_a.get("add"):
        decision_b = reverse.get("decision_b")
        if not isinstance(decision_b, dict) or not decision_b.get("invoked"):
            errors.append(f"{prefix}: Decision A add=true but Decision B was not invoked")
    return errors


def check_record(record: dict[str, Any], require_decomp_memory: bool) -> list[str]:
    qid = question_text(record)
    errors = []
    pog_trace = record.get("pog_trace")
    if not isinstance(pog_trace, dict):
        return [f"{qid}: missing pog_trace object"]

    depths = pog_trace.get("depths")
    if not isinstance(depths, list):
        errors.append(f"{qid}: missing depths list")
        return errors

    stop_reason = pog_trace.get("final_stop_reason")
    if not depths and stop_reason not in NO_DEPTH_STOP_REASONS:
        errors.append(f"{qid}: empty depths with stop_reason={stop_reason!r}")

    for depth_record in depths:
        errors.extend(check_depth(depth_record, qid))

    if require_decomp_memory:
        decomp = pog_trace.get("decomposition") or {}
        context = decomp.get("memory_context") if isinstance(decomp, dict) else ""
        if not str(context or "").strip():
            errors.append(f"{qid}: expected non-empty decomposition.memory_context for B2")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Phase 0 pog_trace completeness.")
    parser.add_argument("path", help="Run directory or pog_trace.jsonl path")
    parser.add_argument(
        "--require-decomp-memory",
        action="store_true",
        help="Fail if any question has empty decomposition.memory_context (use for B2).",
    )
    parser.add_argument(
        "--min-questions",
        type=int,
        default=1,
        help="Minimum number of trace records expected.",
    )
    args = parser.parse_args()

    trace_path = resolve_trace_path(args.path)
    records = list(iter_jsonl_records(trace_path))
    errors: list[str] = []
    if len(records) < args.min_questions:
        errors.append(f"expected at least {args.min_questions} questions, found {len(records)}")

    decomp_nonempty = 0
    for record in records:
        rec_errors = check_record(record, require_decomp_memory=False)
        errors.extend(rec_errors)
        pog_trace = record.get("pog_trace") or {}
        decomp = pog_trace.get("decomposition") or {}
        if str((decomp.get("memory_context") if isinstance(decomp, dict) else "") or "").strip():
            decomp_nonempty += 1

    if args.require_decomp_memory:
        if decomp_nonempty == 0:
            errors.append("B2 check: no question has non-empty decomposition.memory_context")
        # Require majority, not every question: retrieval can miss on a few items.
        if records and decomp_nonempty < max(1, int(0.5 * len(records))):
            errors.append(
                f"B2 check: only {decomp_nonempty}/{len(records)} questions have "
                "decomposition memory context (need >= 50%)"
            )

    print(f"trace_file={trace_path}")
    print(f"questions={len(records)}")
    print(f"decomp_memory_nonempty={decomp_nonempty}")
    if errors:
        print(f"FAIL ({len(errors)} issues)")
        for err in errors[:80]:
            print(f"  - {err}")
        if len(errors) > 80:
            print(f"  ... {len(errors) - 80} more")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
