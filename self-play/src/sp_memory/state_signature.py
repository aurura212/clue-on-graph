"""Entity-agnostic state signatures for SP3 candidate experiences."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from .hashing import canonical_hash
from .schemas import DecisionStage, VisibleState

MID_RE = re.compile(r"\b[mg]\.[0-9a-zA-Z_]+\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def budget_bucket(remaining: Mapping[str, int]) -> Dict[str, str]:
    def _bucket(value: int, high: int) -> str:
        if value <= 0:
            return "exhausted"
        if value <= max(1, high // 4):
            return "low"
        if value <= max(2, high // 2):
            return "mid"
        return "high"

    return {
        "steps": _bucket(int(remaining.get("steps") or 0), 24),
        "kg_calls": _bucket(int(remaining.get("kg_calls") or 0), 80),
        "llm_calls": _bucket(int(remaining.get("llm_calls") or 0), 40),
        "critic_rounds": _bucket(int(remaining.get("critic_rounds") or 0), 2),
        "depth": _bucket(int(remaining.get("depth") or 0), 4),
    }


def abstract_relation(relation: str) -> str:
    text = str(relation or "")
    return MID_RE.sub("<ENT>", text)


def replace_secrets(text: str, secrets: Optional[Iterable[str]] = None) -> str:
    out = MID_RE.sub("<ENT>", text or "")
    out = DATE_RE.sub("<DATE>", out)
    for secret in sorted({item for item in (secrets or []) if item}, key=len, reverse=True):
        if len(secret) < 3:
            continue
        out = out.replace(secret, "<SECRET>")
    return out


def collect_visible_ids(state: VisibleState) -> Set[str]:
    found = set(state.visible_entities) | set(state.frontier)
    for item in state.visible_relations:
        found.add(item.entity)
    for triple in state.observed_triples_or_summaries:
        found.update(str(value) for value in triple.values())
    return {item for item in found if item}


def state_signature(
    state: VisibleState,
    *,
    failure_class: Optional[str] = None,
    question_type: Optional[str] = None,
) -> Dict[str, Any]:
    relations = sorted(
        {
            (abstract_relation(item.relation), item.direction.value)
            for item in state.visible_relations
        }
    )
    payload = {
        "decision_stage": state.decision_stage.value
        if isinstance(state.decision_stage, DecisionStage)
        else str(state.decision_stage),
        "question_type": question_type or "unknown",
        "failure_class": failure_class or "none",
        "frontier_size_bucket": (
            "empty"
            if not state.frontier
            else "small"
            if len(state.frontier) <= 3
            else "medium"
            if len(state.frontier) <= 10
            else "large"
        ),
        "failed_branch_count_bucket": (
            "none"
            if not state.failed_or_exhausted_branches
            else "few"
            if len(state.failed_or_exhausted_branches) <= 2
            else "many"
        ),
        "relation_patterns": [{"relation": rel, "direction": direction} for rel, direction in relations],
        "budget_bucket": budget_bucket(state.remaining_budget),
        "history_kinds": sorted(
            {MID_RE.sub("<ENT>", item).split(":")[0] for item in state.action_history_summary if item}
        ),
    }
    digest = canonical_hash(payload)
    payload["state_signature"] = "sig-" + digest[:24]
    return payload


def experience_text_is_abstract(text: str, extra_secrets: Optional[Sequence[str]] = None) -> List[str]:
    hits = []
    blob = text or ""
    if MID_RE.search(blob):
        hits.append("entity_id")
    if DATE_RE.search(blob):
        hits.append("date_literal")
    for secret in extra_secrets or []:
        if secret and len(secret) >= 3 and secret in blob:
            hits.append("oracle_value")
            break
    lowered = blob.lower()
    for marker in ("gold_path", "witness", "logical_query", "sparql", "future_neighbors"):
        if marker in lowered:
            hits.append(marker)
    return sorted(set(hits))
