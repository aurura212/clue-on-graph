"""V2 reflection-only structural evidence for Decision A/B.

Does not rerank first-hop relations. Does not emit continue/stop/backtrack conclusions.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from kg_memory_retrieval import should_use_kg_memory_at_stage
from v2_protocol import FORBIDDEN_REFLECTION_CONCLUSION_PHRASES, REFLECTION_EVIDENCE_AFTER_SEMANTIC_FILTER

DEFAULT_APPLICABILITY = 1.0
DEFAULT_EVIDENCE_PRODUCT_GATE = 0.36
HIGH_BRANCHING = 8.0
FORBIDDEN_STAGES_FOR_RELATION_RERANK = ("reflection_judge", "reflection_select", "reflection_a", "reflection_b")


def evidence_score(coverage: float, confidence: float, applicability: float) -> float:
    return max(0.0, float(coverage)) * max(0.0, float(confidence)) * max(0.0, float(applicability))


def utility_score(score: float, branching: float) -> float:
    return float(score) / (1.0 + max(0.0, float(branching)))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _has_witness(record: dict[str, Any]) -> bool:
    evidence = record.get("evidence") or {}
    paths = evidence.get("witness_paths") or []
    query_hash = str(evidence.get("query_hash") or "").strip()
    template = str(evidence.get("query_template_id") or "").strip()
    if paths:
        return True
    return bool(query_hash or template)


def empty_reflection_event(
    *,
    stage: str,
    candidate_frontier: list[str] | None = None,
    memory_mode: str = "none",
) -> dict[str, Any]:
    return {
        "stage": stage,
        "memory_mode": memory_mode,
        "candidate_frontier": list(candidate_frontier or []),
        "evidence_items": [],
        "grouped": {
            "validated_unexplored": [],
            "unknown": [],
            "already_explored": [],
            "high_cost": [],
        },
        "prompt_visible_evidence": False,
        "prompt_text": "",
        "prompt_changed": False,
        "timeout": False,
        "retry_count": 0,
        "semantic_filter_already_applied": REFLECTION_EVIDENCE_AFTER_SEMANTIC_FILTER,
        "n_candidates": len(candidate_frontier or []),
        "n_evidence_items": 0,
        "n_positive_interventions": 0,
        "llm_decision": None,
        "selected_entity": None,
        "post_decision_new_triples": None,
        "post_decision_found_answer": None,
    }


def item_from_record(
    record: dict[str, Any],
    *,
    candidate_entity: str = "",
    applicability: float = DEFAULT_APPLICABILITY,
    already_explored: bool = False,
    present_on_frontier: bool = True,
) -> dict[str, Any]:
    stats = record.get("statistics") or {}
    key = record.get("key") or {}
    coverage = _as_float(stats.get("validation_coverage"))
    confidence = _as_float(stats.get("confidence"))
    branching = _as_float(stats.get("median_branching"))
    support = int(stats.get("validation_entity_support") or 0)
    score = evidence_score(coverage, confidence, applicability)
    util = utility_score(score, branching)
    witness_ok = _has_witness(record)
    status = str(record.get("status") or "")
    return {
        "memory_id": str(record.get("memory_id") or ""),
        "source_type": str(key.get("source_type") or ""),
        "candidate_entity": candidate_entity,
        "relation_path": list(key.get("relation_path") or []),
        "target_type": str(key.get("target_type") or ""),
        "validation_coverage": coverage,
        "coverage": coverage,
        "entity_support": support,
        "median_branching": branching,
        "branching": branching,
        "confidence": confidence,
        "applicability": float(applicability),
        "evidence_score": round(score, 6),
        "utility_score": round(util, 6),
        "witness_replayable": witness_ok,
        "status": status,
        "already_explored": bool(already_explored),
        "present_on_frontier": bool(present_on_frontier),
        "product": round(confidence * float(applicability), 6),
    }


def passes_evidence_gate(
    item: dict[str, Any],
    *,
    product_gate: float = DEFAULT_EVIDENCE_PRODUCT_GATE,
    require_validated: bool = True,
) -> bool:
    if require_validated and item.get("status") != "validated":
        return False
    if not item.get("witness_replayable"):
        return False
    if not item.get("present_on_frontier"):
        return False
    if item.get("already_explored"):
        return False
    if float(item.get("entity_support") or 0) <= 0:
        return False
    if float(item.get("product") or 0) < float(product_gate):
        return False
    return True


def group_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {
        "validated_unexplored": [],
        "unknown": [],
        "already_explored": [],
        "high_cost": [],
    }
    for item in items:
        if item.get("already_explored"):
            grouped["already_explored"].append(item)
        elif float(item.get("median_branching") or item.get("branching") or 0) >= HIGH_BRANCHING:
            grouped["high_cost"].append(item)
        elif item.get("status") == "validated" and item.get("witness_replayable"):
            grouped["validated_unexplored"].append(item)
        else:
            grouped["unknown"].append(item)
    return grouped


def _item_line(item: dict[str, Any]) -> str:
    path = " -> ".join(str(x) for x in (item.get("relation_path") or []) if str(x))
    entity = item.get("candidate_entity") or "frontier"
    return (
        f"- entity={entity}; path={path or '(none)'}; "
        f"coverage={item.get('validation_coverage')}; confidence={item.get('confidence')}; "
        f"branching={item.get('median_branching')}; evidence_score={item.get('evidence_score')}; "
        f"utility={item.get('utility_score')}; witness={item.get('witness_replayable')}"
    )


def format_evidence_summary(grouped: dict[str, list[dict[str, Any]]]) -> str:
    sections = [
        ("validated unexplored routes", grouped.get("validated_unexplored") or []),
        ("unknown routes", grouped.get("unknown") or []),
        ("already explored routes", grouped.get("already_explored") or []),
        ("high-cost / high-branching routes", grouped.get("high_cost") or []),
    ]
    lines = ["Structural evidence (not a continue/stop/backtrack instruction):"]
    for title, items in sections:
        lines.append(title + ":")
        if not items:
            lines.append("- (none)")
            continue
        ranked = sorted(items, key=lambda x: -float(x.get("utility_score") or 0))
        lines.extend(_item_line(item) for item in ranked[:8])
    text = "\n".join(lines)
    lowered = text.lower()
    for phrase in FORBIDDEN_REFLECTION_CONCLUSION_PHRASES:
        if phrase in lowered:
            raise ValueError(f"evidence summary leaked a decision conclusion: {phrase}")
    return text


def apply_ablation(items: list[dict[str, Any]], ablation: str, seed: int) -> list[dict[str, Any]]:
    ablation = str(ablation or "none").strip().lower()
    if ablation in {"", "none"} or not items:
        return [dict(item) for item in items]
    rng = random.Random(int(seed))
    out = [dict(item) for item in items]
    numeric_keys = [
        "validation_coverage",
        "coverage",
        "confidence",
        "applicability",
        "median_branching",
        "branching",
        "entity_support",
        "evidence_score",
        "utility_score",
        "product",
    ]
    if ablation == "shuffle":
        bags = {key: [item.get(key) for item in out] for key in numeric_keys}
        for key in numeric_keys:
            rng.shuffle(bags[key])
        for i, item in enumerate(out):
            for key in numeric_keys:
                item[key] = bags[key][i]
        return out
    if ablation == "irrelevant":
        for item in out:
            item["source_type"] = "music.release_track"
            item["target_type"] = "music.recording"
            item["relation_path"] = ["music.recording.tracks"]
            item["memory_id"] = "irrelevant_" + hashlib.sha256(str(item.get("memory_id") or "").encode()).hexdigest()[:8]
        return out
    return out


def select_positive_interventions(
    items: list[dict[str, Any]],
    *,
    product_gate: float = DEFAULT_EVIDENCE_PRODUCT_GATE,
) -> list[dict[str, Any]]:
    return [item for item in items if passes_evidence_gate(item, product_gate=product_gate)]


def explored_relation_paths(depth_ent_rel_ent_dict: dict | None) -> set[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = set()
    for _dep, ent_rel_ent_dict in (depth_ent_rel_ent_dict or {}).items():
        if not isinstance(ent_rel_ent_dict, dict):
            continue
        for _topic, h_t_dict in ent_rel_ent_dict.items():
            if not isinstance(h_t_dict, dict):
                continue
            for _h_t, r_e_dict in h_t_dict.items():
                if not isinstance(r_e_dict, dict):
                    continue
                for rela in r_e_dict:
                    rel = str(rela)
                    if rel:
                        paths.add((rel,))
    return paths


def _path_from_record(record: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(x) for x in ((record.get("key") or {}).get("relation_path") or []) if str(x))


def build_reflection_event(
    *,
    stage: str,
    args: Any,
    candidate_frontier: list[str],
    records: list[dict[str, Any]] | None = None,
    entity_records: list[tuple[str, dict[str, Any]]] | None = None,
    already_explored_paths: set[tuple[str, ...]] | None = None,
    present_paths: set[tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    mode = str(getattr(args, "kg_memory_mode", "none") or "none").strip().lower()
    reflection_stage = "reflection_judge" if stage in {"reflection_a", "reflection_judge"} else "reflection_select"
    event = empty_reflection_event(
        stage=stage,
        candidate_frontier=candidate_frontier,
        memory_mode=mode,
    )
    if not should_use_kg_memory_at_stage(args, reflection_stage) and not should_use_kg_memory_at_stage(
        args, "reflection"
    ):
        # mode=none / relation-only: identical to B0 prompts.
        return event
    explored = already_explored_paths or set()
    present = present_paths
    pairs: list[tuple[str, dict[str, Any]]] = list(entity_records or [])
    if not pairs:
        default_entity = candidate_frontier[0] if candidate_frontier else ""
        pairs = [(default_entity, record) for record in (records or [])]
    items: list[dict[str, Any]] = []
    for entity, record in pairs:
        path = _path_from_record(record)
        already = path in explored
        on_frontier = True if present is None else (path in present)
        items.append(
            item_from_record(
                record,
                candidate_entity=entity,
                already_explored=already,
                present_on_frontier=on_frontier,
            )
        )
    ablation = str(getattr(args, "kg_memory_ablation", "none") or "none")
    seed = int(getattr(args, "kg_memory_seed", 42))
    items = apply_ablation(items, ablation, seed)
    grouped = group_items(items)
    positive = select_positive_interventions(items)
    event["evidence_items"] = items
    event["grouped"] = grouped
    event["n_evidence_items"] = len(items)
    event["n_positive_interventions"] = len(positive)
    if positive:
        event["prompt_text"] = format_evidence_summary(grouped)
        event["prompt_visible_evidence"] = True
        event["prompt_changed"] = True
    else:
        event["prompt_text"] = ""
        event["prompt_visible_evidence"] = False
        event["prompt_changed"] = False
    return event


def maybe_prepend_reflection_evidence(prefix: str, event: dict[str, Any]) -> str:
    text = str(event.get("prompt_text") or "")
    if not text or not event.get("prompt_visible_evidence"):
        return prefix
    return text + "\n" + prefix


def compact_event_for_trace(event: dict[str, Any]) -> dict[str, Any]:
    grouped = event.get("grouped") or {}
    return {
        "stage": event.get("stage"),
        "memory_mode": event.get("memory_mode"),
        "candidate_frontier": list(event.get("candidate_frontier") or []),
        "evidence_items": list(event.get("evidence_items") or []),
        "grouped_counts": {key: len(value or []) for key, value in grouped.items()},
        "prompt_visible_evidence": bool(event.get("prompt_visible_evidence")),
        "prompt_text": event.get("prompt_text") or "",
        "prompt_changed": bool(event.get("prompt_changed")),
        "timeout": bool(event.get("timeout")),
        "retry_count": int(event.get("retry_count") or 0),
        "semantic_filter_already_applied": bool(event.get("semantic_filter_already_applied")),
        "n_candidates": int(event.get("n_candidates") or 0),
        "n_evidence_items": int(event.get("n_evidence_items") or 0),
        "n_positive_interventions": int(event.get("n_positive_interventions") or 0),
        "llm_decision": event.get("llm_decision"),
        "selected_entity": event.get("selected_entity"),
        "post_decision_new_triples": event.get("post_decision_new_triples"),
        "post_decision_found_answer": event.get("post_decision_found_answer"),
    }
