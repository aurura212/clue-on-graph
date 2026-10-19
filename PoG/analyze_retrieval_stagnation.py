#!/usr/bin/env python3
"""Detect trajectories that keep exploring but do not move.

An *exploration* is one (entity, relation) expansion inside a PoG depth,
or a failed relation selection, or a triggered reverse-retrieval. A single
PoG depth often contains several explorations: the model selects multiple
relations and expands them one after another in the same hop. Gold 1-hop
questions can also run several PoG depths if the first round is not enough.

A question is flagged when it has at least two explorations and the last
one adds no new KG neighbors, no new kept entities, and no new triples
versus the union of earlier explorations.

Two report columns:

* ``require_same_subgoal``: last exploration must share the previous
  exploration's reconstructed sub-goal.
* ``ignore_subgoal_state``: only the no-new-evidence check.

Example::

    python analyze_retrieval_stagnation.py \\
        --run-dir result/webqsp_gpt-3.5-turbo-0125_mem-prompt_top2_hybrid_stages-relation_decompmem-prompt_top2_n1639_20260814_032036
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from jsonl_io import iter_jsonl_records


SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_RUN_DIR = os.path.join(
    SCRIPT_DIR,
    "result",
    "webqsp_gpt-3.5-turbo-0125_mem-prompt_top2_hybrid_stages-relation_decompmem-prompt_top2_n1639_20260814_032036",
)
DEFAULT_GOLD_PATH = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "data", "WebQSP.json"))


def parse_subobjectives(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(text)
        except (TypeError, ValueError, json.JSONDecodeError, SyntaxError):
            continue
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [text]


def reconstructed_subgoal(depth: int, subobjectives: list[str]) -> tuple[int, str]:
    n_steps = max(1, len(subobjectives) or 1)
    idx = min(max(int(depth) - 1, 0), n_steps - 1)
    text = subobjectives[idx] if subobjectives else ""
    return idx, text


def logged_subgoal(depth_record: dict[str, Any], subobjectives: list[str]) -> tuple[int, str]:
    evaluation = depth_record.get("evaluation") if isinstance(depth_record.get("evaluation"), dict) else {}
    raw_idx = depth_record.get("subobjective_idx")
    if raw_idx is None:
        raw_idx = evaluation.get("subobjective_idx")
    if raw_idx is not None:
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            idx = reconstructed_subgoal(depth_record.get("depth") or 1, subobjectives)[0]
        n_steps = max(1, len(subobjectives) or 1)
        idx = min(max(idx, 0), n_steps - 1)
        text = subobjectives[idx] if subobjectives else ""
        return idx, text
    return reconstructed_subgoal(depth_record.get("depth") or 1, subobjectives)


def clean_entity_names(values: Iterable[Any] | None) -> tuple[set[str], bool]:
    names: set[str] = set()
    truncated = False
    for item in values or []:
        text = str(item)
        if text.startswith("..."):
            truncated = True
            continue
        if text:
            names.add(text)
    return names, truncated


def load_gold_hop_counts(gold_path: str) -> dict[str, int | None]:
    if not os.path.exists(gold_path):
        return {}
    with open(gold_path, encoding="utf-8") as handle:
        dataset = json.load(handle)
    out: dict[str, int | None] = {}
    for item in dataset:
        question = str(item.get("RawQuestion") or item.get("question") or "")
        lengths = [
            len(parse.get("InferentialChain") or [])
            for parse in (item.get("Parses") or [])
            if parse.get("InferentialChain")
        ]
        out[question] = min(lengths) if lengths else None
    return out


def collect_explorations(depth_record: dict[str, Any], subobjectives: list[str]) -> list[dict[str, Any]]:
    depth = int(depth_record.get("depth") or 1)
    sub_idx, sub_text = logged_subgoal(depth_record, subobjectives)
    stop_reason = depth_record.get("stop_reason")
    explorations: list[dict[str, Any]] = []

    for prune in depth_record.get("relation_prune") or []:
        selected = prune.get("selected_relations") or []
        if selected:
            continue
        explorations.append(
            {
                "kind": "empty_select",
                "depth": depth,
                "subgoal_idx": sub_idx,
                "subgoal_text": sub_text,
                "entity_id": str(prune.get("entity_id") or ""),
                "entity_name": str(prune.get("entity_name") or prune.get("entity_id") or ""),
                "relation": None,
                "before": set(),
                "after": set(),
                "triples": set(),
                "truncated": False,
                "stop_reason": stop_reason,
            }
        )

    for detail in depth_record.get("entity_prune_details") or []:
        before, trunc_before = clean_entity_names(detail.get("candidates_before_prune"))
        after, trunc_after = clean_entity_names(detail.get("candidates_after_prune"))
        relation = str(detail.get("relation") or "")
        entity_name = str(detail.get("topic_entity") or "")
        triples = {(entity_name, relation, neighbor) for neighbor in after}
        explorations.append(
            {
                "kind": "expand",
                "depth": depth,
                "subgoal_idx": sub_idx,
                "subgoal_text": sub_text,
                "entity_id": str(detail.get("topic_entity_id") or ""),
                "entity_name": entity_name,
                "relation": relation or None,
                "before": before,
                "after": after,
                "triples": triples,
                "truncated": trunc_before or trunc_after,
                "stop_reason": stop_reason,
            }
        )

    reverse = depth_record.get("reverse_retrieval") or {}
    if isinstance(reverse, dict) and reverse.get("triggered"):
        added = {str(name) for name in (reverse.get("add_entities") or []) if str(name)}
        explorations.append(
            {
                "kind": "reverse",
                "depth": depth,
                "subgoal_idx": sub_idx,
                "subgoal_text": sub_text,
                "entity_id": "",
                "entity_name": "(reverse)",
                "relation": None,
                "before": added,
                "after": added,
                "triples": set(),
                "truncated": False,
                "stop_reason": stop_reason,
            }
        )
    return explorations


def classify_stuck(explorations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(explorations) < 2:
        return None
    known_before: set[str] = set()
    known_after: set[str] = set()
    known_triples: set[tuple[str, str, str]] = set()
    known_starts: set[str] = set()
    known_relations: set[str] = set()
    for item in explorations[:-1]:
        known_before |= item["before"]
        known_after |= item["after"]
        known_triples |= item["triples"]
        if item["entity_id"]:
            known_starts.add(item["entity_id"])
        if item["relation"]:
            known_relations.add(item["relation"])

    last = explorations[-1]
    prev = explorations[-2]
    if last["truncated"]:
        return None
    if (last["before"] - known_before) or (last["after"] - known_after) or (last["triples"] - known_triples):
        return None

    last_start = {last["entity_id"]} if last["entity_id"] else set()
    last_rels = {last["relation"]} if last["relation"] else set()
    replay = last["kind"] == "expand" and last_start <= known_starts and last_rels <= known_relations
    dead_end = last["kind"] in {"empty_select", "reverse"} or (
        not last["relation"] and not last["before"] and not last["after"]
    )
    if replay:
        kind = "replay"
    elif dead_end:
        kind = "dead_end"
    else:
        kind = "other"

    same_subgoal = last["subgoal_idx"] == prev["subgoal_idx"] and last["subgoal_text"] == prev["subgoal_text"]
    return {
        "kind": kind,
        "same_subgoal": same_subgoal,
        "n_explorations": len(explorations),
        "last_depth": last["depth"],
        "prev_depth": prev["depth"],
        "last_entity": last["entity_name"],
        "last_relation": last["relation"],
        "last_kind": last["kind"],
        "last_stop_reason": last["stop_reason"],
        "prev_subgoal_idx": prev["subgoal_idx"],
        "last_subgoal_idx": last["subgoal_idx"],
        "prev_subgoal": prev["subgoal_text"],
        "last_subgoal": last["subgoal_text"],
    }


@dataclass
class StuckQuestion:
    question: str
    correct: bool
    n_depths: int
    n_explorations: int
    gold_hops: int | None
    n_subobjectives: int
    final_stop_reason: str | None
    kind: str
    same_subgoal: bool
    last_entity: str
    last_relation: str | None
    last_kind: str
    last_stop_reason: str | None
    prev_subgoal_idx: int
    last_subgoal_idx: int
    prev_subgoal: str
    last_subgoal: str


def load_error_questions(run_dir: str) -> set[str]:
    meta_path = os.path.join(run_dir, "run_meta.json")
    if not os.path.exists(meta_path):
        return set()
    with open(meta_path, encoding="utf-8") as handle:
        meta = json.load(handle)
    return set((meta.get("evaluation") or {}).get("error_questions") or [])


def question_key(record: dict[str, Any]) -> str:
    for key in ("RawQuestion", "question"):
        if record.get(key):
            return str(record[key])
    return ""


def _count_breakdown(questions: list[StuckQuestion], field: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for item in questions:
        key = str(getattr(item, field))
        bucket = out.setdefault(key, {"total": 0, "correct": 0, "wrong": 0})
        bucket["total"] += 1
        if item.correct:
            bucket["correct"] += 1
        else:
            bucket["wrong"] += 1
    return dict(sorted(out.items(), key=lambda kv: kv[0]))


def summarize(questions: list[StuckQuestion], total: int, n_wrong: int) -> dict[str, Any]:
    correct = sum(1 for item in questions if item.correct)
    wrong = sum(1 for item in questions if not item.correct)
    by_kind: dict[str, dict[str, int]] = {}
    for item in questions:
        bucket = by_kind.setdefault(item.kind, {"total": 0, "correct": 0, "wrong": 0})
        bucket["total"] += 1
        if item.correct:
            bucket["correct"] += 1
        else:
            bucket["wrong"] += 1
    return {
        "questions_stuck": len(questions),
        "stuck_correct": correct,
        "stuck_wrong": wrong,
        "stuck_accuracy": round(correct / len(questions), 4) if questions else None,
        "share_of_all_questions": round(len(questions) / total, 4) if total else None,
        "share_of_wrong_questions": round(wrong / n_wrong, 4) if n_wrong else None,
        "by_kind": by_kind,
        "by_n_depths": _count_breakdown(questions, "n_depths"),
        "by_gold_hops": _count_breakdown(questions, "gold_hops"),
        "questions": [asdict(item) for item in questions],
    }


def analyze_run(run_dir: str, gold_path: str) -> dict[str, Any]:
    trace_path = os.path.join(run_dir, "pog_trace.jsonl")
    if not os.path.exists(trace_path):
        raise FileNotFoundError(f"missing trace file: {trace_path}")

    error_questions = load_error_questions(run_dir)
    gold_hops = load_gold_hop_counts(gold_path)
    buckets: dict[str, list[StuckQuestion]] = {
        "require_same_subgoal": [],
        "ignore_subgoal_state": [],
    }
    total = 0
    n_wrong = 0
    n_multi_depth = 0
    n_multi_explore = 0
    depth_hist: dict[int, int] = defaultdict(int)
    explore_hist: dict[int, int] = defaultdict(int)
    gold_depth_hist: dict[str, int] = defaultdict(int)
    logged_subgoal_hops = 0
    total_hops = 0

    for record in iter_jsonl_records(trace_path):
        total += 1
        question = question_key(record)
        correct = question not in error_questions
        if not correct:
            n_wrong += 1
        trace = record.get("pog_trace") or {}
        depths = list(trace.get("depths") or [])
        depth_hist[len(depths)] += 1
        if len(depths) >= 2:
            n_multi_depth += 1
        subobjectives = parse_subobjectives(trace.get("subquestions"))
        explorations: list[dict[str, Any]] = []
        for depth_record in depths:
            total_hops += 1
            explorations.extend(collect_explorations(depth_record, subobjectives))
            evaluation = depth_record.get("evaluation") if isinstance(depth_record.get("evaluation"), dict) else {}
            if depth_record.get("subobjective_idx") is not None or evaluation.get("subobjective_idx") is not None:
                logged_subgoal_hops += 1
        explore_hist[len(explorations)] += 1
        if len(explorations) >= 2:
            n_multi_explore += 1
        gold = gold_hops.get(question)
        gold_depth_hist[f"gold={gold}|depths={len(depths)}"] += 1

        info = classify_stuck(explorations)
        if not info:
            continue
        item = StuckQuestion(
            question=question,
            correct=correct,
            n_depths=len(depths),
            n_explorations=info["n_explorations"],
            gold_hops=gold,
            n_subobjectives=len(subobjectives),
            final_stop_reason=trace.get("final_stop_reason"),
            kind=info["kind"],
            same_subgoal=info["same_subgoal"],
            last_entity=info["last_entity"],
            last_relation=info["last_relation"],
            last_kind=info["last_kind"],
            last_stop_reason=info["last_stop_reason"],
            prev_subgoal_idx=info["prev_subgoal_idx"],
            last_subgoal_idx=info["last_subgoal_idx"],
            prev_subgoal=info["prev_subgoal"],
            last_subgoal=info["last_subgoal"],
        )
        buckets["ignore_subgoal_state"].append(item)
        if info["same_subgoal"]:
            buckets["require_same_subgoal"].append(item)

    n_correct = total - n_wrong
    return {
        "run_dir": os.path.abspath(run_dir),
        "total_questions": total,
        "correct": n_correct,
        "wrong": n_wrong,
        "multi_depth_questions": n_multi_depth,
        "multi_exploration_questions": n_multi_explore,
        "depth_histogram": dict(sorted(depth_hist.items())),
        "exploration_histogram": dict(sorted(explore_hist.items())),
        "gold_vs_depth": dict(sorted(gold_depth_hist.items())),
        "logged_subgoal_hops": logged_subgoal_hops,
        "total_hops": total_hops,
        "subgoal_source": (
            "logged subobjective_idx"
            if logged_subgoal_hops
            else "reconstructed from depth and subquestions (idx = min(depth-1, n_subobjectives-1))"
        ),
        "definition": {
            "unit": "one (entity, relation) expansion, empty relation selection, or reverse retrieval",
            "stuck": (
                "n_explorations >= 2 and the last exploration adds no new neighbors "
                "before prune, no new kept entities, and no new triples versus earlier explorations"
            ),
            "require_same_subgoal": "last exploration reconstructed subgoal equals previous exploration",
            "kinds": {
                "replay": "last expansion reuses an already-used start entity and relation",
                "dead_end": "last exploration is empty selection, reverse, or yields nothing",
                "other": "no new evidence, but last expansion used a new start or relation",
            },
        },
        "require_same_subgoal": summarize(buckets["require_same_subgoal"], total, n_wrong),
        "ignore_subgoal_state": summarize(buckets["ignore_subgoal_state"], total, n_wrong),
    }


def _fmt_breakdown(block: dict[str, Any], field: str) -> str:
    parts = []
    for key, stats in (block.get(field) or {}).items():
        parts.append(f"{key}:{stats['total']}(对{stats['correct']}/错{stats['wrong']})")
    return ", ".join(parts) if parts else "-"


def print_report(report: dict[str, Any]) -> None:
    print(f"run_dir: {report['run_dir']}")
    print(
        f"questions: {report['total_questions']}  "
        f"correct={report['correct']}  wrong={report['wrong']}  "
        f"PoG_depth>=2={report['multi_depth_questions']}  "
        f"explorations>=2={report['multi_exploration_questions']}"
    )
    print(f"subgoal_source: {report['subgoal_source']}")
    print(f"gold vs PoG depths: {report['gold_vs_depth']}")
    print()
    for key, title in (
        ("require_same_subgoal", "考虑子目标状态是否相同（最后一次探索与上一次同一子目标）"),
        ("ignore_subgoal_state", "不考虑子目标状态是否相同"),
    ):
        block = report[key]
        print(title)
        print(f"  多次探索后未移动的题数: {block['questions_stuck']}")
        print(f"  其中答对: {block['stuck_correct']}")
        print(f"  其中答错: {block['stuck_wrong']}")
        acc = block["stuck_accuracy"]
        if acc is not None:
            print(f"  这些题 EM: {acc:.4f}")
        by_kind = block.get("by_kind") or {}
        if by_kind:
            parts = [
                f"{kind}={stats['total']}(对{stats['correct']}/错{stats['wrong']})"
                for kind, stats in by_kind.items()
            ]
            print("  分型: " + ", ".join(parts))
        print(f"  按 PoG depth 数: {_fmt_breakdown(block, 'by_n_depths')}")
        print(f"  按 gold hop 数: {_fmt_breakdown(block, 'by_gold_hops')}")
        print()

    ignore = report["ignore_subgoal_state"]
    print("题目明细（不考虑子目标口径）:")
    for item in ignore["questions"]:
        mark = "对" if item["correct"] else "错"
        rel = item["last_relation"] or "(no relation)"
        print(
            f"  [{mark}] [{item['kind']}] gold={item['gold_hops']} "
            f"depths={item['n_depths']} exps={item['n_explorations']} "
            f"{item['question']} | last={item['last_entity']} --{rel} | stop={item['final_stop_reason']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", default=DEFAULT_RUN_DIR, help="PoG result directory with pog_trace.jsonl")
    parser.add_argument("--gold-path", default=DEFAULT_GOLD_PATH, help="WebQSP json for InferentialChain length")
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON output path. Default: <run-dir>/stagnation_analysis.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = analyze_run(args.run_dir, args.gold_path)
    print_report(report)
    out_path = args.out or os.path.join(args.run_dir, "stagnation_analysis.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
