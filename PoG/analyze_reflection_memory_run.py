#!/usr/bin/env python3
"""Offline V2 reflection metrics from pog_trace.jsonl + run_meta.json.

Does not call LLMs. Writes reflection_decision_metrics.json next to the trace.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from jsonl_io import iter_jsonl_records


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def question_text(record: dict[str, Any]) -> str:
    return str(record.get("RawQuestion") or record.get("question") or "")


def analyze_run(run_dir: str) -> dict[str, Any]:
    trace_path = os.path.join(run_dir, "pog_trace.jsonl")
    meta_path = os.path.join(run_dir, "run_meta.json")
    results_path = os.path.join(run_dir, "results.jsonl")
    meta: dict[str, Any] = {}
    if os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)

    n = 0
    n_depth_with_reverse = 0
    n_decision_a = 0
    n_decision_a_add = 0
    n_decision_b = 0
    n_prompt_a = 0
    n_prompt_b = 0
    n_positive_a = 0
    n_positive_b = 0
    n_relation_order_changed = 0
    n_relation_events = 0
    n_timeout = 0
    n_missing_evidence_a = 0
    calls: list[float] = []
    tokens: list[float] = []
    seconds: list[float] = []

    if os.path.isfile(results_path):
        for row in iter_jsonl_records(results_path):
            if "call_num" in row or "LLM_call_num" in row:
                calls.append(float(row.get("call_num") or row.get("LLM_call_num") or 0))
            token = row.get("token") or row.get("tokens") or {}
            if isinstance(token, dict) and token.get("total") is not None:
                tokens.append(float(token.get("total") or 0))
            elif row.get("total_token") is not None:
                tokens.append(float(row["total_token"]))
            if row.get("time") is not None:
                seconds.append(float(row.get("time") or 0))

    for record in iter_jsonl_records(trace_path) if os.path.isfile(trace_path) else []:
        n += 1
        pog = record.get("pog_trace") or {}
        for depth in pog.get("depths") or []:
            kgm_rel = ((depth.get("kg_memory") or {}).get("relation") or {})
            n_relation_events += int(kgm_rel.get("n_events") or 0)
            n_relation_order_changed += int(kgm_rel.get("n_order_changed") or 0)
            reverse = depth.get("reverse_retrieval")
            if not isinstance(reverse, dict) or reverse.get("skipped"):
                continue
            n_depth_with_reverse += 1
            decision_a = reverse.get("decision_a") or {}
            decision_b = reverse.get("decision_b") or {}
            evidence_a = decision_a.get("evidence") if isinstance(decision_a, dict) else None
            evidence_b = decision_b.get("evidence") if isinstance(decision_b, dict) else None
            if isinstance(decision_a, dict) and "add" in decision_a:
                n_decision_a += 1
                if decision_a.get("add"):
                    n_decision_a_add += 1
                if not isinstance(evidence_a, dict):
                    n_missing_evidence_a += 1
                else:
                    if evidence_a.get("prompt_visible_evidence"):
                        n_prompt_a += 1
                    n_positive_a += int(evidence_a.get("n_positive_interventions") or 0)
                    if evidence_a.get("timeout"):
                        n_timeout += 1
            if isinstance(decision_b, dict) and decision_b.get("invoked"):
                n_decision_b += 1
                if isinstance(evidence_b, dict) and evidence_b.get("prompt_visible_evidence"):
                    n_prompt_b += 1
                if isinstance(evidence_b, dict):
                    n_positive_b += int(evidence_b.get("n_positive_interventions") or 0)

    ev = meta.get("evaluation") or {}
    analysis = {
        "run_dir": os.path.abspath(run_dir),
        "kg_memory_mode": meta.get("kg_memory_mode"),
        "kg_memory_stages": meta.get("kg_memory_stages"),
        "kg_memory_ablation": meta.get("kg_memory_ablation"),
        "kg_memory_hash": meta.get("kg_memory_hash"),
        "questions_file": meta.get("questions_file"),
        "n_questions": n,
        "evaluation": {
            "exact_match": ev.get("exact_match"),
            "f1": ev.get("f1"),
            "total": ev.get("total"),
        },
        "efficiency": {
            "mean_calls": _mean(calls),
            "mean_tokens": _mean(tokens),
            "mean_seconds": _mean(seconds),
        },
        "reflection": {
            "n_depth_with_reverse": n_depth_with_reverse,
            "n_decision_a": n_decision_a,
            "n_decision_a_add": n_decision_a_add,
            "decision_a_continue_rate": round(n_decision_a_add / n_decision_a, 4) if n_decision_a else None,
            "n_decision_b_invoked": n_decision_b,
            "n_prompt_visible_a": n_prompt_a,
            "n_prompt_visible_b": n_prompt_b,
            "n_positive_interventions_a": n_positive_a,
            "n_positive_interventions_b": n_positive_b,
            "prompt_visible_a_rate": round(n_prompt_a / n_decision_a, 4) if n_decision_a else None,
            "n_missing_evidence_a": n_missing_evidence_a,
            "n_timeout_flags": n_timeout,
        },
        "first_hop": {
            "n_relation_memory_events": n_relation_events,
            "n_relation_order_changed": n_relation_order_changed,
        },
        "llm_request_timeout_sec": meta.get("llm_request_timeout_sec"),
        "llm_max_retries": meta.get("llm_max_retries"),
        "max_length": meta.get("max_length"),
        "relation_semantic_top_k": meta.get("relation_semantic_top_k"),
    }
    out_path = os.path.join(run_dir, "reflection_decision_metrics.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(analysis, handle, ensure_ascii=False, indent=2)
    return analysis


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+")
    args = parser.parse_args()
    for run_dir in args.run_dirs:
        analysis = analyze_run(run_dir)
        ev = analysis["evaluation"]
        ref = analysis["reflection"]
        print(
            f"{os.path.basename(run_dir)} n={analysis['n_questions']} "
            f"mode={analysis['kg_memory_mode']} stages={analysis['kg_memory_stages']} "
            f"ablation={analysis['kg_memory_ablation']} "
            f"EM={ev.get('exact_match')} F1={ev.get('f1')} "
            f"A_vis={ref.get('n_prompt_visible_a')}/{ref.get('n_decision_a')} "
            f"B_vis={ref.get('n_prompt_visible_b')}/{ref.get('n_decision_b_invoked')} "
            f"rel_reorder={analysis['first_hop']['n_relation_order_changed']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
