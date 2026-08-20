#!/usr/bin/env python3
"""V2-1 reflection evidence unit tests: gate, fallback, shuffle, B0 equivalence.

Does not call LLMs. Exit 0 only when all checks pass.
"""

from __future__ import annotations

import sys
import types
from typing import Any

from kg_memory_retrieval import attach_kg_memory_relation_events, should_use_kg_memory_at_stage
from reflection_structural_memory import (
    DEFAULT_EVIDENCE_PRODUCT_GATE,
    apply_ablation,
    build_reflection_event,
    compact_event_for_trace,
    evidence_score,
    format_evidence_summary,
    item_from_record,
    maybe_prepend_reflection_evidence,
    passes_evidence_gate,
    select_positive_interventions,
    utility_score,
)
from v2_protocol import (
    FORBIDDEN_REFLECTION_CONCLUSION_PHRASES,
    PROMPT_SECTION_TITLES,
    validate_reflection_event,
)


def fake_record(
    *,
    memory_id: str,
    relation: str,
    coverage: float,
    confidence: float,
    support: int = 10,
    branching: float = 2.0,
    status: str = "validated",
    witness: bool = True,
    source_type: str = "people.person",
    target_type: str = "people.profession",
) -> dict[str, Any]:
    evidence = {}
    if witness:
        evidence = {
            "witness_paths": [[relation]],
            "query_template_id": "schema_outgoing_relation",
            "query_hash": "hash_" + memory_id,
        }
    return {
        "memory_id": memory_id,
        "status": status,
        "key": {
            "source_type": source_type,
            "direction": "outgoing",
            "relation_path": [relation],
            "target_type": target_type,
        },
        "statistics": {
            "validation_coverage": coverage,
            "validation_entity_support": support,
            "median_branching": branching,
            "confidence": confidence,
        },
        "evidence": evidence,
    }


def reflection_args(**overrides: Any) -> types.SimpleNamespace:
    args = types.SimpleNamespace(
        kg_memory_mode="reflection",
        kg_memory_stages="reflection_judge,reflection_select",
        kg_memory_ablation="none",
        kg_memory_seed=42,
        kg_memory_bank=None,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _print_check(name: str, errors: list[str]) -> list[str]:
    status = "PASS" if not errors else "FAIL"
    print(f"[{status}] {name}")
    for item in errors:
        print(f"  - {item}")
    return errors


def check_positive_witness() -> list[str]:
    args = reflection_args()
    record = fake_record(
        memory_id="pos",
        relation="people.person.profession",
        coverage=0.9,
        confidence=0.8,
        witness=True,
    )
    event = build_reflection_event(
        stage="reflection_a",
        args=args,
        candidate_frontier=["Marc Chagall"],
        records=[record],
    )
    errors = validate_reflection_event(event, prefix="positive")
    if not event.get("prompt_visible_evidence") or not event.get("prompt_changed"):
        errors.append("witnessed validated route should produce a positive intervention")
    if event.get("n_positive_interventions") != 1:
        errors.append(f"expected 1 positive intervention, got {event.get('n_positive_interventions')}")
    items = event.get("evidence_items") or []
    if not items or not items[0].get("witness_replayable"):
        errors.append("positive item must be witness_replayable")
    if not passes_evidence_gate(items[0]):
        errors.append("positive item failed the evidence gate")
    text = event.get("prompt_text") or ""
    lowered = text.lower()
    for phrase in FORBIDDEN_REFLECTION_CONCLUSION_PHRASES:
        if phrase in lowered:
            errors.append(f"summary leaked decision phrase: {phrase}")
    for title in PROMPT_SECTION_TITLES:
        if title not in text:
            errors.append(f"missing section title: {title}")
    return errors


def check_unknown_without_witness() -> list[str]:
    args = reflection_args()
    record = fake_record(
        memory_id="unknown",
        relation="people.person.profession",
        coverage=0.9,
        confidence=0.8,
        witness=False,
    )
    event = build_reflection_event(
        stage="reflection_a",
        args=args,
        candidate_frontier=["Marc Chagall"],
        records=[record],
    )
    errors = validate_reflection_event(event, prefix="unknown")
    items = event.get("evidence_items") or []
    if items and items[0].get("witness_replayable"):
        errors.append("no-witness record must not be replayable")
    if event.get("n_positive_interventions"):
        errors.append("unknown/no-witness must not produce a positive intervention")
    if event.get("prompt_changed") or event.get("prompt_visible_evidence"):
        errors.append("unknown/no-witness must fall back to B0 prompt")
    grouped = event.get("grouped") or {}
    if grouped.get("unknown") == [] and items:
        errors.append("no-witness item should be grouped as unknown")
    return errors


def check_low_confidence_no_intervention() -> list[str]:
    args = reflection_args()
    record = fake_record(
        memory_id="lowconf",
        relation="people.person.profession",
        coverage=1.0,
        confidence=0.2,
        witness=True,
    )
    item = item_from_record(record, candidate_entity="x")
    errors = []
    if item["product"] >= DEFAULT_EVIDENCE_PRODUCT_GATE:
        errors.append(f"low confidence product {item['product']} should be below gate")
    if passes_evidence_gate(item):
        errors.append("low confidence must fail the evidence gate")
    event = build_reflection_event(
        stage="reflection_a",
        args=args,
        candidate_frontier=["x"],
        records=[record],
    )
    if event.get("prompt_visible_evidence") or event.get("n_positive_interventions"):
        errors.append("low confidence must not produce a positive intervention")
    return errors


def check_already_explored_not_recommended() -> list[str]:
    args = reflection_args()
    record = fake_record(
        memory_id="explored",
        relation="people.person.profession",
        coverage=1.0,
        confidence=1.0,
        witness=True,
    )
    event = build_reflection_event(
        stage="reflection_a",
        args=args,
        candidate_frontier=["x"],
        records=[record],
        already_explored_paths={("people.person.profession",)},
    )
    errors = []
    if event.get("n_positive_interventions"):
        errors.append("already-explored route must not be a positive intervention")
    grouped = event.get("grouped") or {}
    if not grouped.get("already_explored"):
        errors.append("already-explored route should be grouped as already_explored")
    if grouped.get("validated_unexplored"):
        errors.append("already-explored route was also listed as validated unexplored")
    if event.get("prompt_changed"):
        errors.append("only-explored evidence must fall back to B0 prompt")
    return errors


def check_score_and_branching_penalty() -> list[str]:
    errors = []
    expected = 0.5 * 0.8 * 1.0
    got = evidence_score(0.5, 0.8, 1.0)
    if abs(got - expected) > 1e-9:
        errors.append(f"evidence_score {got} != {expected}")
    if evidence_score(-1, 0.8, 1.0) != 0.0:
        errors.append("negative coverage should clip via max(0, coverage)")
    low = utility_score(0.4, 1.0)
    high = utility_score(0.4, 20.0)
    if not (high < low):
        errors.append(f"higher branching should lower utility: {high} vs {low}")
    if abs(low - (0.4 / 2.0)) > 1e-9:
        errors.append(f"utility_score low-branch {low} != 0.2")
    cheap = fake_record(
        memory_id="cheap",
        relation="people.person.profession",
        coverage=0.8,
        confidence=0.8,
        branching=1.0,
    )
    costly = fake_record(
        memory_id="costly",
        relation="people.person.gender",
        coverage=0.8,
        confidence=0.8,
        branching=20.0,
    )
    event = build_reflection_event(
        stage="reflection_b",
        args=reflection_args(),
        candidate_frontier=["a", "b"],
        entity_records=[("a", cheap), ("b", costly)],
    )
    by_id = {item["memory_id"]: item for item in event.get("evidence_items") or []}
    if by_id["cheap"]["utility_score"] <= by_id["costly"]["utility_score"]:
        errors.append("branching penalty did not rank the cheaper route higher")
    grouped = event.get("grouped") or {}
    if not grouped.get("high_cost"):
        errors.append("branching>=8 should be grouped as high_cost")
    return errors


def check_shuffle_and_irrelevant_structure() -> list[str]:
    records = [
        fake_record(memory_id="r1", relation="people.person.profession", coverage=0.9, confidence=0.9),
        fake_record(memory_id="r2", relation="people.person.gender", coverage=0.8, confidence=0.8),
        fake_record(memory_id="r3", relation="people.person.nationality", coverage=0.7, confidence=0.7),
    ]
    frontier = ["Marc Chagall", "Pablo Picasso"]
    real = build_reflection_event(
        stage="reflection_a",
        args=reflection_args(),
        candidate_frontier=frontier,
        records=records,
    )
    shuffled = build_reflection_event(
        stage="reflection_a",
        args=reflection_args(kg_memory_ablation="shuffle"),
        candidate_frontier=frontier,
        records=records,
    )
    irrelevant = build_reflection_event(
        stage="reflection_a",
        args=reflection_args(kg_memory_ablation="irrelevant"),
        candidate_frontier=frontier,
        records=records,
    )
    errors = []
    for name, event in (("real", real), ("shuffle", shuffled), ("irrelevant", irrelevant)):
        errors.extend(validate_reflection_event(event, prefix=name))
        if event.get("n_candidates") != len(frontier):
            errors.append(f"{name} changed candidate count")
        if event.get("n_evidence_items") != len(records):
            errors.append(f"{name} changed evidence item count")
        if list(event.get("candidate_frontier") or []) != frontier:
            errors.append(f"{name} changed candidate_frontier")
        text = event.get("prompt_text") or ""
        if event.get("prompt_visible_evidence"):
            for title in PROMPT_SECTION_TITLES:
                if title not in text:
                    errors.append(f"{name} missing section title {title}")
            if not text.startswith("Structural evidence (not a continue/stop/backtrack instruction):"):
                errors.append(f"{name} changed the evidence header")
    real_conf = [item["confidence"] for item in real["evidence_items"]]
    shuf_conf = [item["confidence"] for item in shuffled["evidence_items"]]
    if sorted(real_conf) != sorted(shuf_conf):
        errors.append("shuffle must permute numeric bags, not invent new scores")
    if shuf_conf == real_conf:
        # seed=42 should move at least one pairing for 3 distinct values
        real_prod = [item["product"] for item in real["evidence_items"]]
        shuf_prod = [item["product"] for item in shuffled["evidence_items"]]
        if shuf_prod == real_prod:
            errors.append("shuffle did not change evidence pairing")
    if any(item["relation_path"] == ["music.recording.tracks"] for item in real["evidence_items"]):
        errors.append("real evidence should not already be the irrelevant path")
    if not all(item["relation_path"] == ["music.recording.tracks"] for item in irrelevant["evidence_items"]):
        errors.append("irrelevant ablation must replace relation_path content")
    if irrelevant["n_evidence_items"] != real["n_evidence_items"]:
        errors.append("irrelevant ablation changed item count")
    # Ablation helper itself must not drop items.
    items = [item_from_record(record, candidate_entity="x") for record in records]
    shuffled_items = apply_ablation(items, "shuffle", 42)
    if len(shuffled_items) != len(items):
        errors.append("apply_ablation shuffle changed item count")
    return errors


def check_fallback_and_trace_attach() -> list[str]:
    errors = []
    none_args = reflection_args(kg_memory_mode="none", kg_memory_stages="relation")
    base = "B0 decision-A prefix"
    event = build_reflection_event(
        stage="reflection_a",
        args=none_args,
        candidate_frontier=["x"],
        records=[fake_record(memory_id="pos", relation="people.person.profession", coverage=1.0, confidence=1.0)],
    )
    if maybe_prepend_reflection_evidence(base, event) != base:
        errors.append("memory-off fallback changed the B0 prefix")
    if should_use_kg_memory_at_stage(none_args, "relation") or should_use_kg_memory_at_stage(none_args, "reflection_judge"):
        errors.append("memory-off still enabled a kg-memory stage")

    low = fake_record(memory_id="low", relation="people.person.profession", coverage=1.0, confidence=0.1)
    gated = build_reflection_event(
        stage="reflection_a",
        args=reflection_args(),
        candidate_frontier=["x"],
        records=[low],
    )
    if maybe_prepend_reflection_evidence(base, gated) != base:
        errors.append("gated-out evidence must keep the B0 prefix")

    depth = {
        "relation_prune": [],
        "reverse_retrieval": {
            "decision_a": {"add": False, "evidence": compact_event_for_trace(event)},
            "decision_b": {"invoked": False, "evidence": compact_event_for_trace(gated)},
        },
    }
    attach_kg_memory_relation_events(depth)
    kgm = depth.get("kg_memory") or {}
    if not kgm.get("reflection_judge"):
        errors.append("trace did not copy Decision A evidence into kg_memory.reflection_judge")
    if not kgm.get("reflection_select"):
        errors.append("trace did not copy Decision B evidence into kg_memory.reflection_select")
    errors.extend(validate_reflection_event(kgm["reflection_judge"], prefix="trace.reflection_judge"))
    return errors


def check_gate_helpers() -> list[str]:
    good = item_from_record(
        fake_record(memory_id="g", relation="people.person.profession", coverage=0.9, confidence=0.8),
        candidate_entity="x",
    )
    errors = []
    if not passes_evidence_gate(good):
        errors.append("validated witness item should pass the gate")
    if select_positive_interventions([good]) != [good]:
        errors.append("select_positive_interventions dropped a passing item")
    text = format_evidence_summary(
        {
            "validated_unexplored": [good],
            "unknown": [],
            "already_explored": [],
            "high_cost": [],
        }
    )
    if "continue = true" in text.lower():
        errors.append("format_evidence_summary leaked a conclusion")
    return errors


def main() -> int:
    checks = [
        ("positive_witness", check_positive_witness),
        ("unknown_without_witness", check_unknown_without_witness),
        ("low_confidence_no_intervention", check_low_confidence_no_intervention),
        ("already_explored_not_recommended", check_already_explored_not_recommended),
        ("score_and_branching_penalty", check_score_and_branching_penalty),
        ("shuffle_irrelevant_structure", check_shuffle_and_irrelevant_structure),
        ("fallback_and_trace_attach", check_fallback_and_trace_attach),
        ("gate_helpers", check_gate_helpers),
    ]
    errors: list[str] = []
    for name, fn in checks:
        errors.extend(_print_check(name, fn()))
    if errors:
        print(f"FAILED {len(errors)} check(s)")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
