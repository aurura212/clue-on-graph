#!/usr/bin/env python3
"""Offline M1/pilot analyzer: gold-next-relation recall and exploration stats.

Does not call LLMs. Writes kg_memory_analysis.json next to pog_trace.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any

from jsonl_io import iter_jsonl_records


def load_webqsp_gold(path: str) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        datas = json.load(handle)
    gold: dict[str, dict[str, Any]] = {}
    for item in datas:
        question = str(item.get("RawQuestion") or item.get("question") or "")
        chains: list[list[str]] = []
        for parse in item.get("Parses") or []:
            chain = [str(rel) for rel in (parse.get("InferentialChain") or []) if str(rel).strip()]
            if chain:
                chains.append(chain)
        if question:
            gold[question] = {
                "first_hops": sorted({chain[0] for chain in chains if chain}),
                "chains": chains,
            }
    return gold


def question_text(record: dict[str, Any]) -> str:
    return str(record.get("RawQuestion") or record.get("question") or "")


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def analyze_run(run_dir: str, gold_path: str) -> dict[str, Any]:
    trace_path = os.path.join(run_dir, "pog_trace.jsonl")
    meta_path = os.path.join(run_dir, "run_meta.json")
    gold = load_webqsp_gold(gold_path)
    meta = {}
    if os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)

    n = 0
    n_with_gold = 0
    n_gold_in_candidates = 0
    n_gold_in_retrieved = 0
    n_gold_selected = 0
    n_depth1 = 0
    hits = 0
    order_changed = 0
    events = 0
    stats_acc: dict[str, list[float]] = defaultdict(list)

    for record in iter_jsonl_records(trace_path):
        n += 1
        question = question_text(record)
        gold_first = set((gold.get(question) or {}).get("first_hops") or [])
        pog = record.get("pog_trace") or {}
        depths = pog.get("depths") or []
        depth1 = next((d for d in depths if d.get("depth") == 1), None)
        if depth1 is None:
            continue
        n_depth1 += 1
        selected: set[str] = set()
        candidates: set[str] = set()
        retrieved: set[str] = set()
        for rel_trace in depth1.get("relation_prune") or []:
            if not isinstance(rel_trace, dict):
                continue
            candidates.update(str(x) for x in (rel_trace.get("candidate_relations") or []))
            retrieved.update(str(x) for x in (rel_trace.get("retrieved_relations") or []))
            for item in rel_trace.get("selected_relations") or []:
                if isinstance(item, dict) and item.get("relation"):
                    selected.add(str(item["relation"]))
            kgm = rel_trace.get("kg_memory") or {}
            if kgm:
                events += 1
                hits += int(kgm.get("n_memory_hits") or 0)
                if kgm.get("order_before") != kgm.get("order_after"):
                    order_changed += 1
        if gold_first:
            n_with_gold += 1
            if gold_first & candidates:
                n_gold_in_candidates += 1
            if gold_first & retrieved:
                n_gold_in_retrieved += 1
            if gold_first & selected:
                n_gold_selected += 1
        stats = depth1.get("exploration_stats") or {}
        for key in (
            "n_frontier_entities",
            "n_relations_selected",
            "n_entities_before_prune",
            "n_entities_after_prune",
            "n_triples_kept",
        ):
            if key in stats:
                stats_acc[key].append(float(stats[key] or 0))

    analysis = {
        "run_dir": os.path.abspath(run_dir),
        "kg_memory_mode": meta.get("kg_memory_mode"),
        "kg_memory_ablation": meta.get("kg_memory_ablation"),
        "kg_memory_fusion": meta.get("kg_memory_fusion"),
        "kg_memory_hash": meta.get("kg_memory_hash"),
        "n_questions": n,
        "n_depth1": n_depth1,
        "n_with_gold_first_hop": n_with_gold,
        "gold_next_relation": {
            "candidate_recall": round(n_gold_in_candidates / n_with_gold, 4) if n_with_gold else None,
            "retrieved_recall": round(n_gold_in_retrieved / n_with_gold, 4) if n_with_gold else None,
            "selected_recall": round(n_gold_selected / n_with_gold, 4) if n_with_gold else None,
            "n_gold_in_candidates": n_gold_in_candidates,
            "n_gold_in_retrieved": n_gold_in_retrieved,
            "n_gold_selected": n_gold_selected,
        },
        "kg_memory_relation": {
            "n_events": events,
            "n_memory_hits": hits,
            "n_order_changed": order_changed,
        },
        "exploration_depth1_mean": {key: mean(vals) for key, vals in stats_acc.items()},
        "evaluation": meta.get("evaluation") or {},
    }
    out_path = os.path.join(run_dir, "kg_memory_analysis.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(analysis, handle, ensure_ascii=False, indent=2)
    return analysis


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+", help="PoG result directories")
    parser.add_argument(
        "--gold",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "WebQSP.json"),
    )
    args = parser.parse_args()
    gold_path = os.path.abspath(args.gold)
    for run_dir in args.run_dirs:
        analysis = analyze_run(run_dir, gold_path)
        gold = analysis["gold_next_relation"]
        ev = analysis.get("evaluation") or {}
        print(
            f"{os.path.basename(run_dir)} n={analysis['n_questions']} "
            f"EM={ev.get('exact_match')} F1={ev.get('f1')} "
            f"gold_sel={gold['selected_recall']} gold_cand={gold['candidate_recall']} "
            f"hits={analysis['kg_memory_relation']['n_memory_hits']} "
            f"reorder={analysis['kg_memory_relation']['n_order_changed']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
