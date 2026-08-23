"""Distill entity/answer/path-free rules from audited candidates with CF evidence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence

from .hashing import canonical_hash
from .paths import PROTOCOL_VERSION
from .sp4_schemas import SP4_RULE_VERSION, make_rule_id, validate_memory_rule
from .state_signature import MID_RE, experience_text_is_abstract, replace_secrets
from .visibility import find_sensitive_keys

FORBIDDEN_SNIPPETS = (
    "gold_path",
    "witness",
    "future_state",
    "answer_entity",
    "logical_query",
    "O4",
)


def _abstract_reason(text: str) -> str:
    clean = replace_secrets(text or "", [])
    clean = MID_RE.sub("<ENT>", clean)
    return clean.strip()


def distill_rules(
    candidates: Sequence[Mapping[str, Any]],
    counterfactuals: Sequence[Mapping[str, Any]],
    *,
    rule_version: str = "v2",
) -> List[Dict[str, Any]]:
    cf_by_cand: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in counterfactuals:
        cid = str(row.get("candidate_id") or "")
        if cid:
            cf_by_cand[cid].append(dict(row))
    buckets: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        rec = item.get("recommendation") or {}
        trigger = item.get("trigger") or {}
        reason = _abstract_reason(str(rec.get("reason") or ""))
        if experience_text_is_abstract(reason):
            continue
        if any(snippet.lower() in reason.lower() for snippet in FORBIDDEN_SNIPPETS):
            continue
        if find_sensitive_keys({"reason": reason}):
            continue
        key = canonical_hash(
            {
                "stage": trigger.get("decision_stage"),
                "action": rec.get("action_type"),
                "relation": rec.get("relation_pattern"),
                "question_type": trigger.get("question_type"),
            }
        )
        bucket = buckets.setdefault(
            key,
            {
                "decision_stage": trigger.get("decision_stage"),
                "abstract_state": {
                    "question_type": trigger.get("question_type"),
                    "failure_class": trigger.get("failure_class"),
                    "state_signature_family": str(trigger.get("state_signature") or "")[:12],
                },
                "action_policy": {
                    "recommended_action": rec.get("action_type"),
                    "relation_pattern": rec.get("relation_pattern"),
                    "direction": rec.get("direction"),
                    "forbidden_action": None,
                    "reason": reason,
                },
                "applicability": {
                    "preconditions": [str(x) for x in (rec.get("negative_constraints") and ["visible_relation_match"] or ["visible_relation_match"])],
                    "negative_constraints": list(rec.get("negative_constraints") or []),
                    "budget_condition": rec.get("budget_condition"),
                },
                "candidates": [],
                "cf": [],
                "tasks": set(),
                "methods": set(),
            },
        )
        bucket["candidates"].append(item.get("experience_id"))
        bucket["tasks"].update(item.get("source_task_ids") or [])
        bucket["methods"].add(item.get("discovery_method"))
        bucket["cf"].extend(cf_by_cand.get(str(item.get("experience_id") or ""), []))
        if item.get("task_id"):
            bucket["tasks"].add(item["task_id"])
    rules = []
    for bucket in buckets.values():
        cf_rows = bucket["cf"]
        wins = sum(1 for row in cf_rows if row.get("outcome") == "win")
        harms = sum(1 for row in cf_rows if row.get("outcome") == "harm")
        invalids = sum(1 for row in cf_rows if row.get("outcome") == "invalid")
        ties = sum(1 for row in cf_rows if row.get("outcome") == "tie")
        n = max(1, len(cf_rows))
        payload = {
            "schema_version": SP4_RULE_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "rule_id": "",
            "rule_version": rule_version,
            "decision_stage": bucket["decision_stage"],
            "abstract_state": bucket["abstract_state"],
            "action_policy": bucket["action_policy"],
            "applicability": bucket["applicability"],
            "support": {
                "n_candidates": len(bucket["candidates"]),
                "n_tasks": len(bucket["tasks"]),
                "n_entities": len(bucket["tasks"]),
                "task_ids": sorted(str(x) for x in bucket["tasks"]),
                "discovery_methods": sorted(str(x) for x in bucket["methods"] if x),
            },
            "statistics": {
                "n_cf": len(cf_rows),
                "win_rate": wins / n if cf_rows else 0.0,
                "harm_rate": harms / n if cf_rows else 0.0,
                "invalid_rate": invalids / n if cf_rows else 0.0,
                "tie_rate": ties / n if cf_rows else 0.0,
            },
            "source_hashes": {
                "candidates": sorted(str(x) for x in bucket["candidates"] if x)[:32],
            },
            "status": "deferred",
            "evidence_refs": [row.get("pair_id") for row in cf_rows[:20]],
        }
        payload["rule_id"] = make_rule_id(payload)
        rules.append(validate_memory_rule(payload))
    rules.sort(key=lambda item: item["rule_id"])
    return rules
