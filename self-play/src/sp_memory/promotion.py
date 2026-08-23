"""Fixed-threshold promotion. Never silently drop failed rules."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence

from .hashing import canonical_hash, canonical_json
from .paths import PROTOCOL_VERSION
from .sp4_schemas import ALLOWED_RULE_STATUS, validate_memory_rule

PROMOTION_GATES = {
    "audit_pass_rate": 1.0,
    "min_discovery_tasks": 3,
    "min_cf_states": 5,
    "min_margin": 0.20,
    "max_harm_rate": 0.10,
    "min_v1_triggers": 5,
    "min_cost_drop": 0.10,
}


def _dominant(values: Sequence[str]) -> bool:
    if not values:
        return True
    counts = Counter(values)
    top = counts.most_common(1)[0][1]
    return top / len(values) >= 0.8


def evaluate_rule(
    rule: Mapping[str, Any],
    *,
    audit_pass_rate: float,
    v1: Mapping[str, Any],
    g3_or_sham_better: bool,
    config_frozen: bool,
) -> Dict[str, Any]:
    reasons: List[str] = []
    stats = rule.get("statistics") or {}
    support = rule.get("support") or {}
    win = float(stats.get("win_rate") or 0.0)
    harm = float(stats.get("harm_rate") or 0.0)
    invalid = float(stats.get("invalid_rate") or 0.0)
    n_cf = int(stats.get("n_cf") or 0)
    n_tasks = int(support.get("n_tasks") or 0)
    v1_triggers = int(v1.get("n_triggered") or 0)
    v1_success = float(v1.get("success_rate") or 0.0)
    v1_baseline = float(v1.get("baseline_success_rate") or 0.0)
    v1_cost = float(v1.get("mean_cost") or 0.0)
    v1_base_cost = float(v1.get("baseline_mean_cost") or 0.0)
    methods = list(support.get("discovery_methods") or [])

    if audit_pass_rate < PROMOTION_GATES["audit_pass_rate"]:
        reasons.append("audit_pass_rate")
    if n_tasks < PROMOTION_GATES["min_discovery_tasks"]:
        reasons.append("min_discovery_tasks")
    if n_cf < PROMOTION_GATES["min_cf_states"]:
        reasons.append("min_cf_states")
    if (win - harm) < PROMOTION_GATES["min_margin"]:
        reasons.append("margin")
    if harm > PROMOTION_GATES["max_harm_rate"]:
        reasons.append("harm_rate")
    if invalid > float(v1.get("baseline_invalid_rate") or invalid):
        reasons.append("invalid_above_baseline")
    if v1_triggers < PROMOTION_GATES["min_v1_triggers"]:
        reasons.append("v1_triggers")
    success_ok = v1_success > v1_baseline + 1e-9
    cost_ok = v1_success >= v1_baseline - 1e-9 and v1_base_cost > 0 and (v1_base_cost - v1_cost) / v1_base_cost >= PROMOTION_GATES["min_cost_drop"]
    if not (success_ok or cost_ok):
        reasons.append("v1_success_or_cost")
    if _dominant(list(support.get("task_ids") or [])[:1]) and n_tasks <= 1:
        reasons.append("dominated_by_single_task")
    if g3_or_sham_better:
        reasons.append("g3_or_sham_matches_or_better")
    if not config_frozen:
        reasons.append("config_not_frozen")
    if any(item == "random_critic" and len(methods) == 1 for item in methods):
        # random-only rules cannot be promoted as O0 self-play memory
        reasons.append("random_critic_only")

    if harm >= 0.25 and win < harm:
        status = "rejected_harmful"
    elif not reasons:
        status = "promoted"
    elif n_cf >= 3 and win > harm:
        status = "validated_candidate"
    else:
        status = "deferred"
    if status not in ALLOWED_RULE_STATUS:
        status = "deferred"
    out = dict(rule)
    out["status"] = status
    out["promotion_reasons"] = reasons
    out["promotion_gates"] = dict(PROMOTION_GATES)
    return validate_memory_rule(out)


def promote_rules(
    rules: Sequence[Mapping[str, Any]],
    *,
    audit_pass_rate: float,
    v1_by_rule: Mapping[str, Mapping[str, Any]],
    sham_better_ids: Sequence[str],
    config_frozen: bool,
) -> Dict[str, Any]:
    decisions = []
    for rule in rules:
        v1 = v1_by_rule.get(str(rule.get("rule_id")), {"n_triggered": 0, "success_rate": 0.0, "baseline_success_rate": 0.0, "mean_cost": 0.0, "baseline_mean_cost": 0.0, "baseline_invalid_rate": 1.0})
        decided = evaluate_rule(
            rule,
            audit_pass_rate=audit_pass_rate,
            v1=v1,
            g3_or_sham_better=str(rule.get("rule_id")) in set(sham_better_ids),
            config_frozen=config_frozen,
        )
        decisions.append(decided)
    promoted = [item for item in decisions if item["status"] == "promoted"]
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": "sp4-memory-manifest-v2",
        "n_rules": len(decisions),
        "n_promoted": len(promoted),
        "n_validated_candidate": sum(1 for item in decisions if item["status"] == "validated_candidate"),
        "n_rejected_harmful": sum(1 for item in decisions if item["status"] == "rejected_harmful"),
        "n_deferred": sum(1 for item in decisions if item["status"] == "deferred"),
        "rule_ids_promoted": [item["rule_id"] for item in promoted],
        "readonly": True,
        "gates": dict(PROMOTION_GATES),
    }
    manifest["manifest_hash"] = canonical_hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
    return {"decisions": decisions, "promoted": promoted, "manifest": manifest}
