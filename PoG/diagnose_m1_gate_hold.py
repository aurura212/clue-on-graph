#!/usr/bin/env python3
"""GATE_HOLD diagnosis: why M1 gold-next-relation selected recall lags C1/C2/B0.

Offline only. Writes kg_memory_gate_hold_diagnosis.json next to this script's --out.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from typing import Any

from jsonl_io import iter_jsonl_records


def qtext(record: dict[str, Any]) -> str:
    return str(record.get("RawQuestion") or record.get("question") or "")


def load_gold(path: str) -> dict[str, set[str]]:
    with open(path, encoding="utf-8") as handle:
        datas = json.load(handle)
    gold: dict[str, set[str]] = {}
    for item in datas:
        question = str(item.get("RawQuestion") or "")
        hops: set[str] = set()
        for parse in item.get("Parses") or []:
            chain = [str(rel) for rel in (parse.get("InferentialChain") or []) if str(rel).strip()]
            if chain:
                hops.add(chain[0])
        if question and hops:
            gold[question] = hops
    return gold


def load_run(run_dir: str) -> dict[str, dict[str, Any]]:
    by_q: dict[str, dict[str, Any]] = {}
    results_path = os.path.join(run_dir, "results.jsonl")
    trace_path = os.path.join(run_dir, "pog_trace.jsonl")
    meta_path = os.path.join(run_dir, "run_meta.json")
    meta = {}
    if os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)
    errors = set((meta.get("evaluation") or {}).get("error_questions") or [])
    if os.path.isfile(results_path):
        for rec in iter_jsonl_records(results_path):
            q = qtext(rec)
            by_q[q] = {"question": q, "wrong": q in errors, "depth1": []}
    if os.path.isfile(trace_path):
        for rec in iter_jsonl_records(trace_path):
            q = qtext(rec)
            row = by_q.setdefault(q, {"question": q, "wrong": q in errors, "depth1": []})
            pog = rec.get("pog_trace") or {}
            depth1 = next((d for d in (pog.get("depths") or []) if d.get("depth") == 1), None)
            if not depth1:
                continue
            events = []
            selected: set[str] = set()
            for rel_trace in depth1.get("relation_prune") or []:
                if not isinstance(rel_trace, dict):
                    continue
                sel = []
                for item in rel_trace.get("selected_relations") or []:
                    if isinstance(item, dict) and item.get("relation"):
                        sel.append(str(item["relation"]))
                        selected.add(str(item["relation"]))
                kgm = rel_trace.get("kg_memory") or {}
                scores = {str(s.get("relation")): s for s in (kgm.get("scores") or []) if isinstance(s, dict)}
                before = [str(x) for x in (kgm.get("order_before") or rel_trace.get("retrieved_relations") or [])]
                after = [str(x) for x in (kgm.get("order_after") or before)]
                events.append(
                    {
                        "entity_id": rel_trace.get("entity_id"),
                        "entity_name": rel_trace.get("entity_name"),
                        "selected": sel,
                        "before": before,
                        "after": after,
                        "scores": scores,
                        "n_hits": int(kgm.get("n_memory_hits") or 0),
                        "reordered": before != after,
                    }
                )
            row["depth1"] = events
            row["selected"] = sorted(selected)
    return by_q


def rank_of(seq: list[str], gold: set[str]) -> int | None:
    for i, rel in enumerate(seq):
        if rel in gold:
            return i
    return None


def analyze(runs: dict[str, dict[str, dict[str, Any]]], gold: dict[str, set[str]]) -> dict[str, Any]:
    questions = sorted(set(runs["B0"]) & set(runs["M1"]) & set(runs["C1"]) & set(runs["C2"]))
    em = {}
    for name, data in runs.items():
        correct = sum(1 for q in questions if not data[q]["wrong"])
        em[name] = {"n": len(questions), "correct": correct, "em": round(correct / len(questions), 4)}

    paired = {
        "b0_ok_m1_bad": [q for q in questions if not runs["B0"][q]["wrong"] and runs["M1"][q]["wrong"]],
        "b0_bad_m1_ok": [q for q in questions if runs["B0"][q]["wrong"] and not runs["M1"][q]["wrong"]],
        "m1_ok_c1_bad": [q for q in questions if not runs["M1"][q]["wrong"] and runs["C1"][q]["wrong"]],
        "m1_bad_c1_ok": [q for q in questions if runs["M1"][q]["wrong"] and not runs["C1"][q]["wrong"]],
        "m1_ok_c2_bad": [q for q in questions if not runs["M1"][q]["wrong"] and runs["C2"][q]["wrong"]],
        "m1_bad_c2_ok": [q for q in questions if runs["M1"][q]["wrong"] and not runs["C2"][q]["wrong"]],
    }

    gold_events = []
    demote_cases = []
    promote_cases = []
    sel_by = defaultdict(lambda: {"gold_in_retrieved": 0, "gold_selected": 0, "gold_demoted": 0, "gold_promoted": 0})
    struct_when_miss = []
    struct_when_hit = []

    for q in questions:
        hops = gold.get(q) or set()
        if not hops:
            continue
        for name in ("B0", "M1", "C1", "C2"):
            selected = set(runs[name][q].get("selected") or [])
            events = runs[name][q].get("depth1") or []
            retrieved: list[str] = []
            for ev in events:
                seq = ev.get("after") or ev.get("before") or []
                retrieved.extend(seq)
            in_ret = bool(hops & set(retrieved))
            in_sel = bool(hops & selected)
            if in_ret:
                sel_by[name]["gold_in_retrieved"] += 1
            if in_sel:
                sel_by[name]["gold_selected"] += 1

        for ev in runs["M1"][q].get("depth1") or []:
            before = ev.get("before") or []
            after = ev.get("after") or []
            if not (hops & set(before)):
                continue
            rb = rank_of(before, hops)
            ra = rank_of(after, hops)
            gold_rel = next(rel for rel in (after or before) if rel in hops)
            score = (ev.get("scores") or {}).get(gold_rel) or {}
            top_after = after[0] if after else ""
            top_score = (ev.get("scores") or {}).get(top_after) or {}
            rec = {
                "question": q,
                "gold": gold_rel,
                "rank_before": rb,
                "rank_after": ra,
                "reordered": ev.get("reordered"),
                "gold_selected": gold_rel in (ev.get("selected") or []),
                "gold_semantic": score.get("semantic"),
                "gold_structural": score.get("structural"),
                "gold_fused": score.get("fused"),
                "gold_coverage": score.get("coverage"),
                "top_after": top_after,
                "top_structural": top_score.get("structural"),
                "top_semantic": top_score.get("semantic"),
                "top_coverage": top_score.get("coverage"),
            }
            gold_events.append(rec)
            if rb is not None and ra is not None and ra > rb:
                sel_by["M1"]["gold_demoted"] += 1
                demote_cases.append(rec)
            if rb is not None and ra is not None and ra < rb:
                sel_by["M1"]["gold_promoted"] += 1
                promote_cases.append(rec)
            if rec["gold_structural"] is not None:
                if rec["gold_selected"]:
                    struct_when_hit.append(float(rec["gold_structural"]))
                else:
                    struct_when_miss.append(float(rec["gold_structural"]))

    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None

    # M1 miss / C1 hit on gold selection at depth1
    m1_miss_c1_hit = []
    m1_hit_c1_miss = []
    for q in questions:
        hops = gold.get(q) or set()
        if not hops:
            continue
        m1_sel = bool(hops & set(runs["M1"][q].get("selected") or []))
        c1_sel = bool(hops & set(runs["C1"][q].get("selected") or []))
        if (not m1_sel) and c1_sel:
            m1_miss_c1_hit.append(q)
        if m1_sel and (not c1_sel):
            m1_hit_c1_miss.append(q)

    # Among demotions, how often was gold low-coverage vs top high-coverage?
    coverage_override = 0
    semantic_override = 0
    for rec in demote_cases:
        gs, ts = rec.get("gold_structural"), rec.get("top_structural")
        gsem, tsem = rec.get("gold_semantic"), rec.get("top_semantic")
        if gs is not None and ts is not None and ts > gs + 0.05:
            coverage_override += 1
        if gsem is not None and tsem is not None and gsem > tsem + 0.05:
            semantic_override += 1

    top_demote = sorted(
        demote_cases,
        key=lambda r: (r.get("rank_after") or 0) - (r.get("rank_before") or 0),
        reverse=True,
    )[:12]

    def slim(rec: dict[str, Any]) -> dict[str, Any]:
        keep = (
            "question",
            "gold",
            "rank_before",
            "rank_after",
            "gold_semantic",
            "gold_structural",
            "gold_fused",
            "gold_coverage",
            "top_after",
            "top_semantic",
            "top_structural",
            "top_coverage",
            "gold_selected",
        )
        return {k: rec.get(k) for k in keep}

    return {
        "n_questions": len(questions),
        "em": em,
        "paired_em": {k: {"n": len(v), "questions": v[:20]} for k, v in paired.items()},
        "gold_counts": dict(sel_by),
        "m1_gold_events": len(gold_events),
        "m1_demotions": len(demote_cases),
        "m1_promotions": len(promote_cases),
        "m1_demote_then_unselected": sum(1 for r in demote_cases if not r["gold_selected"]),
        "m1_promote_then_selected": sum(1 for r in promote_cases if r["gold_selected"]),
        "gold_struct_zero": sum(1 for r in gold_events if float(r.get("gold_structural") or 0) == 0),
        "gold_struct_zero_among_demotions": sum(
            1 for r in demote_cases if float(r.get("gold_structural") or 0) == 0
        ),
        "mean_gold_struct_when_selected": mean(struct_when_hit),
        "mean_gold_struct_when_unselected": mean(struct_when_miss),
        "demotions_top_has_higher_struct": coverage_override,
        "demotions_gold_has_higher_semantic": semantic_override,
        "m1_miss_c1_hit_gold_sel": {"n": len(m1_miss_c1_hit), "questions": m1_miss_c1_hit[:20]},
        "m1_hit_c1_miss_gold_sel": {"n": len(m1_hit_c1_miss), "questions": m1_hit_c1_miss[:20]},
        "example_demotions": [slim(r) for r in top_demote],
        "b0_ok_m1_bad_examples": paired["b0_ok_m1_bad"][:12],
        "b0_bad_m1_ok_examples": paired["b0_bad_m1_ok"][:12],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default=os.path.join(os.path.dirname(__file__), "..", "data", "WebQSP.json"))
    parser.add_argument("--out", default="")
    parser.add_argument("--b0", required=True)
    parser.add_argument("--m1", required=True)
    parser.add_argument("--c1", required=True)
    parser.add_argument("--c2", required=True)
    args = parser.parse_args()
    gold = load_gold(os.path.abspath(args.gold))
    runs = {
        "B0": load_run(args.b0),
        "M1": load_run(args.m1),
        "C1": load_run(args.c1),
        "C2": load_run(args.c2),
    }
    report = analyze(runs, gold)
    out = args.out or os.path.join(args.m1, "kg_memory_gate_hold_diagnosis.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k not in {"example_demotions"}}, ensure_ascii=False, indent=2)[:4000])
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
