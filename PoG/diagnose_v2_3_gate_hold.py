#!/usr/bin/env python3
"""Offline GATE_HOLD diagnosis for V2-3 random150 traces. No LLM calls."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from typing import Any

from jsonl_io import iter_jsonl_records

RUNS_RANDOM = {
    "R0": "result/webqsp_gpt-3.5-turbo-0125_slice-random150-v1_n150_20260820_174057",
    "R2": "result/webqsp_gpt-3.5-turbo-0125_kgmem-reflection_b_top6_slice-random150-v1_n150_20260820_174108",
    "RC1": "result/webqsp_gpt-3.5-turbo-0125_kgmem-reflection_a-b_top6_shuffle_slice-random150-v1_n150_20260820_174123",
    "RC2": "result/webqsp_gpt-3.5-turbo-0125_kgmem-reflection_a-b_top6_irrelevant_slice-random150-v1_n150_20260820_174138",
}
RUNS_HARD = {
    "R0": "result/webqsp_gpt-3.5-turbo-0125_slice-hard150-v1_n150_20260820_142449",
    "R2": "result/webqsp_gpt-3.5-turbo-0125_kgmem-reflection_b_top6_slice-hard150-v1_n150_20260820_142519",
}

STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "to", "for", "and", "or", "is", "was",
    "did", "do", "does", "what", "who", "where", "when", "which", "how", "with",
    "from", "by", "at", "as", "it", "be", "are", "were", "that", "this",
}


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", str(text).lower()) if t not in STOPWORDS and len(t) > 1}


def path_tokens(path: list[str] | None) -> set[str]:
    out: set[str] = set()
    for rel in path or []:
        out.update(tokens(rel.replace(".", " ").replace("_", " ")))
    return out


def load_errors(run_dir: str) -> set[str]:
    meta = json.load(open(os.path.join(run_dir, "run_meta.json"), encoding="utf-8"))
    return set((meta.get("evaluation") or {}).get("error_questions") or [])


def iter_reverse_depths(pog: dict[str, Any]):
    for depth in pog.get("depths") or []:
        reverse = depth.get("reverse_retrieval")
        if not isinstance(reverse, dict) or reverse.get("skipped"):
            continue
        yield depth, reverse


def summarize_question(rec: dict[str, Any], errors: set[str]) -> dict[str, Any]:
    q = rec.get("RawQuestion") or rec.get("question") or ""
    pog = rec.get("pog_trace") or {}
    depths = pog.get("depths") or []
    a_seq: list[bool | None] = []
    b_selected: list[list[str]] = []
    b_n_pos: list[int] = []
    b_vis = 0
    b_invoked = 0
    a_continue = 0
    a_n = 0
    first_a = None
    first_b_sel: list[str] = []
    first_b_paths: list[str] = []
    first_b_entities: list[str] = []
    n_entities_added = 0
    top_path_overlap = []
    prompt_chars = 0
    grouped_pos = []

    topic = pog.get("topic_entity") or {}
    topic_names = []
    if isinstance(topic, dict):
        topic_names = [str(v) for v in topic.values()]
    elif isinstance(topic, list):
        topic_names = [str(x) for x in topic]

    qtok = tokens(q)
    for depth, reverse in iter_reverse_depths(pog):
        da = reverse.get("decision_a") or {}
        db = reverse.get("decision_b") or {}
        ea = da.get("evidence") if isinstance(da, dict) else None
        eb = db.get("evidence") if isinstance(db, dict) else None
        if isinstance(da, dict) and "add" in da:
            a_n += 1
            add = bool(da.get("add"))
            a_seq.append(add)
            if add:
                a_continue += 1
            if first_a is None:
                first_a = add
        if isinstance(db, dict) and db.get("invoked"):
            b_invoked += 1
            sel = list(db.get("selected_entities") or [])
            b_selected.append(sel)
            n_entities_added += len(sel)
            if isinstance(eb, dict):
                if eb.get("prompt_visible_evidence"):
                    b_vis += 1
                n_pos = int(eb.get("n_positive_interventions") or 0)
                b_n_pos.append(n_pos)
                prompt_chars += len(str(eb.get("prompt_text") or ""))
                grouped_pos.append(eb.get("grouped_counts") or {})
                items = list(eb.get("evidence_items") or [])
                ranked = sorted(items, key=lambda x: -float(x.get("utility_score") or 0))[:8]
                overlap = 0
                for item in ranked:
                    pt = path_tokens(item.get("relation_path"))
                    if pt & qtok:
                        overlap += 1
                top_path_overlap.append(overlap / len(ranked) if ranked else None)
                if not first_b_sel:
                    first_b_sel = sel
                    first_b_entities = [str(item.get("candidate_entity") or "") for item in ranked]
                    first_b_paths = [".".join(item.get("relation_path") or []) for item in ranked]

    n_after = []
    for depth in depths:
        stats = depth.get("exploration_stats") or {}
        n_after.append(int(stats.get("n_entities_after_prune") or 0))

    return {
        "q": q,
        "em": q not in errors,
        "n_depths": len(depths),
        "final_stop_reason": pog.get("final_stop_reason"),
        "topic_names": topic_names,
        "a_n": a_n,
        "a_continue": a_continue,
        "first_a_continue": first_a,
        "a_seq": a_seq,
        "b_invoked": b_invoked,
        "b_vis": b_vis,
        "n_entities_added": n_entities_added,
        "first_b_selected": first_b_sel,
        "first_b_top_entities": first_b_entities[:8],
        "first_b_top_paths": first_b_paths[:8],
        "mean_top8_path_q_overlap": (
            round(sum(x for x in top_path_overlap if x is not None) / max(1, sum(x is not None for x in top_path_overlap)), 4)
            if any(x is not None for x in top_path_overlap)
            else None
        ),
        "mean_b_n_pos": round(sum(b_n_pos) / len(b_n_pos), 2) if b_n_pos else 0.0,
        "prompt_chars": prompt_chars,
        "n_entities_after_prune": n_after,
        "b_selected_all": b_selected,
    }


def load_run(run_dir: str) -> dict[str, dict[str, Any]]:
    errors = load_errors(run_dir)
    out = {}
    for rec in iter_jsonl_records(os.path.join(run_dir, "pog_trace.jsonl")):
        row = summarize_question(rec, errors)
        out[row["q"]] = row
    return out


def mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def pairwise(a: dict, b: dict) -> tuple[list[str], list[str]]:
    fix, brk = [], []
    for q in a:
        if b[q]["em"] and not a[q]["em"]:
            fix.append(q)
        elif a[q]["em"] and not b[q]["em"]:
            brk.append(q)
    return fix, brk


def selected_is_topic(row: dict[str, Any]) -> bool:
    topics = {t.lower() for t in row.get("topic_names") or []}
    sels = {s.lower() for s in row.get("first_b_selected") or []}
    if not topics or not sels:
        return False
    return bool(topics & sels)


def main() -> int:
    random_runs = {k: load_run(p) for k, p in RUNS_RANDOM.items()}
    hard_runs = {k: load_run(p) for k, p in RUNS_HARD.items()}

    def agg(run: dict[str, dict[str, Any]], label: str) -> dict[str, Any]:
        rows = list(run.values())
        return {
            "label": label,
            "n": len(rows),
            "em": sum(r["em"] for r in rows),
            "mean_depths": mean([r["n_depths"] for r in rows]),
            "mean_a_n": mean([r["a_n"] for r in rows]),
            "mean_a_continue": mean([r["a_continue"] for r in rows]),
            "first_a_continue_rate": mean([1.0 if r["first_a_continue"] else 0.0 for r in rows if r["first_a_continue"] is not None]),
            "n_with_first_a": sum(r["first_a_continue"] is not None for r in rows),
            "mean_b_invoked": mean([r["b_invoked"] for r in rows]),
            "mean_b_vis": mean([r["b_vis"] for r in rows]),
            "mean_entities_added": mean([r["n_entities_added"] for r in rows]),
            "mean_path_q_overlap": mean([r["mean_top8_path_q_overlap"] for r in rows if r["mean_top8_path_q_overlap"] is not None]),
            "n_with_overlap": sum(r["mean_top8_path_q_overlap"] is not None for r in rows),
            "mean_prompt_chars": mean([r["prompt_chars"] for r in rows]),
            "mean_b_n_pos": mean([r["mean_b_n_pos"] for r in rows if r["b_vis"] > 0]),
            "n_first_b_selects_topic": sum(selected_is_topic(r) for r in rows if r["first_b_selected"]),
            "n_first_b_selected": sum(bool(r["first_b_selected"]) for r in rows),
        }

    print("=== random150 aggregates ===")
    random_agg = {k: agg(v, k) for k, v in random_runs.items()}
    for k, v in random_agg.items():
        print(k, v)

    print("\n=== hard150 aggregates (R0/R2) ===")
    hard_agg = {k: agg(v, k) for k, v in hard_runs.items()}
    for k, v in hard_agg.items():
        print(k, v)

    r0, r2, rc1, rc2 = random_runs["R0"], random_runs["R2"], random_runs["RC1"], random_runs["RC2"]
    qs = sorted(r0)

    # first Decision A agreement
    both_a = [q for q in qs if r0[q]["first_a_continue"] is not None and r2[q]["first_a_continue"] is not None]
    agree_a = sum(r0[q]["first_a_continue"] == r2[q]["first_a_continue"] for q in both_a)
    r0_stop_r2_cont = [
        q for q in both_a if (not r0[q]["first_a_continue"]) and r2[q]["first_a_continue"]
    ]
    r0_cont_r2_stop = [
        q for q in both_a if r0[q]["first_a_continue"] and (not r2[q]["first_a_continue"])
    ]
    print("\n=== first Decision A R0 vs R2 ===")
    print("both", len(both_a), "agree", agree_a, "R0stop→R2cont", len(r0_stop_r2_cont), "R0cont→R2stop", len(r0_cont_r2_stop))

    # depth among R0-correct
    r0_ok = [q for q in qs if r0[q]["em"]]
    print("\n=== among R0-correct n=", len(r0_ok), "===")
    print("mean depths R0", mean([r0[q]["n_depths"] for q in r0_ok]), "R2", mean([r2[q]["n_depths"] for q in r0_ok]))
    print("R2 deeper", sum(r2[q]["n_depths"] > r0[q]["n_depths"] for q in r0_ok),
          "R2 shallower", sum(r2[q]["n_depths"] < r0[q]["n_depths"] for q in r0_ok),
          "same", sum(r2[q]["n_depths"] == r0[q]["n_depths"] for q in r0_ok))
    r0_ok_r2_bad = [q for q in r0_ok if not r2[q]["em"]]
    print("R0-correct R2-wrong", len(r0_ok_r2_bad),
          "deeper", sum(r2[q]["n_depths"] > r0[q]["n_depths"] for q in r0_ok_r2_bad),
          "Bvis", sum(r2[q]["b_vis"] > 0 for q in r0_ok_r2_bad))

    fix, brk = pairwise(r0, r2)
    print("\n=== flips R2 vs R0 ===")
    print("fix", len(fix), "break", len(brk))

    def dump_flip(label: str, q: str) -> dict[str, Any]:
        a, b = r0[q], r2[q]
        return {
            "q": q,
            "label": label,
            "R0_depths": a["n_depths"],
            "R2_depths": b["n_depths"],
            "R0_first_a": a["first_a_continue"],
            "R2_first_a": b["first_a_continue"],
            "R0_a_seq": a["a_seq"],
            "R2_a_seq": b["a_seq"],
            "R2_b_vis": b["b_vis"],
            "R2_first_b_selected": b["first_b_selected"],
            "R2_selects_topic": selected_is_topic(b),
            "R2_top_paths": b["first_b_top_paths"],
            "R2_path_q_overlap": b["mean_top8_path_q_overlap"],
            "R2_mean_b_n_pos": b["mean_b_n_pos"],
            "R0_stop": a["final_stop_reason"],
            "R2_stop": b["final_stop_reason"],
            "RC1_em": rc1[q]["em"],
            "RC2_em": rc2[q]["em"],
            "RC1_first_b_selected": rc1[q]["first_b_selected"],
            "RC1_path_q_overlap": rc1[q]["mean_top8_path_q_overlap"],
        }

    flips = [dump_flip("FIXED", q) for q in fix] + [dump_flip("BROKEN", q) for q in brk]
    for row in flips:
        print(
            f"[{row['label']}] d {row['R0_depths']}→{row['R2_depths']} "
            f"A0={row['R0_first_a']} A2={row['R2_first_a']} Bsel={row['R2_first_b_selected']} "
            f"topic={row['R2_selects_topic']} ov={row['R2_path_q_overlap']} "
            f"n_pos={row['R2_mean_b_n_pos']} RC1={row['RC1_em']} | {row['q']}"
        )
        if row["R2_top_paths"]:
            print("   paths:", row["R2_top_paths"][:5])

    # R2 vs RC1 selected overlap on questions where both invoked B at first reverse
    both_b = [q for q in qs if r2[q]["first_b_selected"] is not None and rc1[q]["first_b_selected"] is not None]
    sel_equal = sum(set(r2[q]["first_b_selected"]) == set(rc1[q]["first_b_selected"]) for q in both_b if r2[q]["first_b_selected"] or rc1[q]["first_b_selected"])
    both_nonempty = [q for q in qs if r2[q]["first_b_selected"] and rc1[q]["first_b_selected"]]
    jacc = []
    for q in both_nonempty:
        s1, s2 = set(r2[q]["first_b_selected"]), set(rc1[q]["first_b_selected"])
        jacc.append(len(s1 & s2) / len(s1 | s2))
    print("\n=== R2 vs RC1 first B selection ===")
    print("both nonempty", len(both_nonempty), "mean jaccard", mean(jacc), "exact equal", sum(set(r2[q]["first_b_selected"]) == set(rc1[q]["first_b_selected"]) for q in both_nonempty))

    # overlap distribution for R2 B-visible vs RC2 (irrelevant paths should have near-zero overlap)
    print("\n=== path-question overlap (B-visible questions) ===")
    for name, run in random_runs.items():
        ovs = [r["mean_top8_path_q_overlap"] for r in run.values() if r["mean_top8_path_q_overlap"] is not None]
        print(name, "n", len(ovs), "mean", mean(ovs), "zero_frac", round(sum(x == 0 for x in ovs) / len(ovs), 3) if ovs else None)

    # among questions with B vis: does selecting topic entity correlate with break?
    bvis_qs = [q for q in qs if r2[q]["b_vis"] > 0]
    topic_sel = [q for q in bvis_qs if selected_is_topic(r2[q])]
    print("\n=== R2 Bvis selects topic entity ===")
    print("bvis", len(bvis_qs), "selects_topic", len(topic_sel),
          "EM topic", sum(r2[q]["em"] for q in topic_sel), "/", len(topic_sel),
          "EM not-topic", sum(r2[q]["em"] for q in bvis_qs if q not in topic_sel), "/", len(bvis_qs) - len(topic_sel))
    print("R0 EM on topic-sel", sum(r0[q]["em"] for q in topic_sel), "R0 EM on other bvis", sum(r0[q]["em"] for q in bvis_qs if q not in topic_sel))

    # R0-correct, first A stop, R2 first A continue → extra search that can break
    extra_search = [q for q in r0_ok if (r0[q]["first_a_continue"] is False) and (r2[q]["first_a_continue"] is True)]
    print("\n=== R0-correct and R0 first-A stop but R2 first-A continue ===", len(extra_search),
          "R2 still EM", sum(r2[q]["em"] for q in extra_search))

    # continue rate by whether B evidence was shown at previous depth cannot be causal for FIRST A
    # first A has no B yet. So first-A disagreement is temperature or upstream path.
    # Later A can be affected by B-added entities.
    later_only = []
    r0_later_c = r2_later_c = r0_later_n = r2_later_n = 0
    for q in qs:
        if len(r0[q]["a_seq"]) > 1:
            r0_later_n += len(r0[q]["a_seq"]) - 1
            r0_later_c += sum(1 for x in r0[q]["a_seq"][1:] if x)
        if len(r2[q]["a_seq"]) > 1:
            r2_later_n += len(r2[q]["a_seq"]) - 1
            r2_later_c += sum(1 for x in r2[q]["a_seq"][1:] if x)
    print("\n=== later-than-first Decision A continue ===")
    print("R0", r0_later_c, "/", r0_later_n, round(r0_later_c / r0_later_n, 4) if r0_later_n else None)
    print("R2", r2_later_c, "/", r2_later_n, round(r2_later_c / r2_later_n, 4) if r2_later_n else None)

    # path prefix counters on broken Bvis
    path_counter = Counter()
    for q in brk:
        if r2[q]["b_vis"] <= 0:
            continue
        for p in r2[q]["first_b_top_paths"][:8]:
            path_counter[p] += 1
    print("\n=== top evidence paths on BROKEN+Bvis ===")
    for p, c in path_counter.most_common(12):
        print(c, p)

    report = {
        "slice": "random150_v1",
        "random_aggregates": random_agg,
        "hard150_aggregates": hard_agg,
        "first_decision_a": {
            "n_both": len(both_a),
            "agree": agree_a,
            "r0_stop_r2_continue": r0_stop_r2_cont,
            "r0_continue_r2_stop": r0_cont_r2_stop,
        },
        "r0_correct_depth": {
            "n": len(r0_ok),
            "r2_deeper": sum(r2[q]["n_depths"] > r0[q]["n_depths"] for q in r0_ok),
            "r2_shallower": sum(r2[q]["n_depths"] < r0[q]["n_depths"] for q in r0_ok),
            "r2_wrong": len(r0_ok_r2_bad),
        },
        "flips": flips,
        "r2_vs_rc1_first_b": {
            "n_both_nonempty": len(both_nonempty),
            "mean_jaccard": mean(jacc),
            "n_exact_equal": sum(set(r2[q]["first_b_selected"]) == set(rc1[q]["first_b_selected"]) for q in both_nonempty),
        },
        "path_question_overlap_mean": {k: random_agg[k]["mean_path_q_overlap"] for k in random_agg},
        "topic_selection": {
            "n_bvis": len(bvis_qs),
            "n_selects_topic": len(topic_sel),
            "r2_em_topic": sum(r2[q]["em"] for q in topic_sel),
            "r2_em_other": sum(r2[q]["em"] for q in bvis_qs if q not in topic_sel),
            "r0_em_topic": sum(r0[q]["em"] for q in topic_sel),
        },
        "later_decision_a_continue": {
            "R0": [r0_later_c, r0_later_n],
            "R2": [r2_later_c, r2_later_n],
        },
        "broken_bvis_top_paths": path_counter.most_common(15),
    }
    out_path = "result/v2_3_gate_hold_diagnosis_20260821.json"
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("\nwrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
