#!/usr/bin/env python3
"""M1 structural-memory checks: membership, fusion, ablations, optional run traces.

Does not call LLMs. Exit 0 only when all selected checks pass.
"""

from __future__ import annotations

import argparse
import os
import sys
import types
from typing import Any

from jsonl_io import iter_jsonl_records
from kg_memory_retrieval import (
    FROZEN_PATH_FULL_DIR,
    FROZEN_SCHEMA_FULL_DIR,
    apply_relation_kg_memory,
    bank_from_records,
    load_kg_memory_bank,
    should_use_kg_memory_at_stage,
)
from kg_structural_memory import (
    MEMORY_KIND_PATH_TEMPLATE,
    MEMORY_KIND_SCHEMA_PROFILE,
    SOURCE_PROTOCOL_PATH_PROBE,
    SOURCE_PROTOCOL_SCHEMA_SURVEY,
)


def fake_record(
    source_type: str,
    direction: str,
    relation: str,
    *,
    coverage: float,
    support: int,
    confidence: float,
    branching: float = 1.0,
    status: str = "validated",
) -> dict[str, Any]:
    return {
        "memory_id": f"kgm_{source_type}_{direction}_{relation}",
        "memory_kind": MEMORY_KIND_SCHEMA_PROFILE,
        "source_protocol": SOURCE_PROTOCOL_SCHEMA_SURVEY,
        "key": {
            "source_type": source_type,
            "direction": direction,
            "relation_path": [relation],
            "target_type": "",
        },
        "semantic": {"capability_text": f"{source_type} {direction} {relation}"},
        "statistics": {
            "validation_coverage": coverage,
            "validation_entity_support": support,
            "validation_n": 30,
            "median_branching": branching,
            "confidence": confidence,
            "endpoint_type_top": [{"type": "people.person", "count": 10}],
        },
        "status": status,
    }


def fake_args(bank, **overrides):
    args = types.SimpleNamespace(
        kg_memory_bank=bank,
        kg_memory_mode="relation",
        kg_memory_stages="relation",
        kg_memory_strategy="rerank",
        kg_memory_ablation="none",
        kg_memory_seed=42,
        kg_memory_top_k=6,
        kg_memory_min_confidence=0.0,
        kg_memory_validated_only=1,
        kg_memory_prompt_token_budget=600,
        kg_memory_semantic_weight=0.7,
        kg_memory_structure_weight=0.3,
        kg_memory_fusion="additive",
        kg_memory_use_tail_sem=1,
        sentence_model=None,
        LLM_type="gpt-3.5-turbo-0125",
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    bank.type_cache["m.test"] = ["people.person"]
    return args


def fake_path_record(
    source_type: str,
    direction: str,
    relation_path: list[str],
    *,
    coverage: float,
    support: int,
    confidence: float,
    branching: float = 1.0,
    status: str = "validated",
    target_type: str = "",
) -> dict[str, Any]:
    path = list(relation_path)
    return {
        "memory_id": f"kgm_path_{source_type}_{'_'.join(path)}",
        "memory_kind": MEMORY_KIND_PATH_TEMPLATE,
        "source_protocol": SOURCE_PROTOCOL_PATH_PROBE,
        "key": {
            "source_type": source_type,
            "direction": direction,
            "relation_path": path,
            "target_type": target_type,
        },
        "semantic": {"capability_text": f"{source_type} {' -> '.join(path)}"},
        "statistics": {
            "validation_coverage": coverage,
            "validation_entity_support": support,
            "validation_n": 20,
            "median_branching": branching,
            "confidence": confidence,
        },
        "status": status,
    }


def path_people_bank():
    records = [
        fake_path_record(
            "people.person",
            "outgoing",
            ["people.person.profession"],
            coverage=1.0,
            support=20,
            confidence=1.0,
            target_type="people.profession",
        ),
        fake_path_record(
            "people.person",
            "outgoing",
            ["people.person.profession", "people.profession.people_with_this_profession"],
            coverage=0.6,
            support=12,
            confidence=0.6,
            target_type="people.person",
        ),
        fake_path_record(
            "people.person",
            "outgoing",
            ["people.person.gender"],
            coverage=0.2,
            support=4,
            confidence=0.2,
        ),
        fake_path_record(
            "music.release_track",
            "outgoing",
            ["music.release_track.recording", "music.recording.releases"],
            coverage=1.0,
            support=20,
            confidence=1.0,
            target_type="music.album",
        ),
    ]
    return bank_from_records(records, path="fake-path", jsonl_path="fake-path")


def people_bank():
    records = [
        fake_record(
            "people.person",
            "outgoing",
            "people.person.gender",
            coverage=0.2,
            support=6,
            confidence=0.2,
        ),
        fake_record(
            "people.person",
            "outgoing",
            "people.person.profession",
            coverage=1.0,
            support=30,
            confidence=1.0,
        ),
        fake_record(
            "people.person",
            "outgoing",
            "people.person.date_of_birth",
            coverage=0.8,
            support=24,
            confidence=0.8,
        ),
        fake_record(
            "music.release_track",
            "incoming",
            "music.recording.tracks",
            coverage=1.0,
            support=30,
            confidence=1.0,
        ),
    ]
    return bank_from_records(records, path="fake", jsonl_path="fake")


def check_gate_off() -> list[str]:
    args = types.SimpleNamespace(kg_memory_mode="none", kg_memory_stages="relation")
    if should_use_kg_memory_at_stage(args, "relation"):
        return ["mode=none should not enable relation memory"]
    return []


def check_no_extra_or_drop() -> list[str]:
    bank = people_bank()
    args = fake_args(bank)
    candidates = [
        "people.person.gender",
        "people.person.profession",
        "people.person.nationality",
    ]
    ordered, _prompt, trace = apply_relation_kg_memory(
        question="what is the gender of X?",
        entity_id="m.test",
        retrieved_relations=candidates,
        head_relations=candidates,
        tail_relations=[],
        pre_relations=[],
        args=args,
    )
    errors = []
    if set(ordered) != set(candidates):
        errors.append(f"rerank changed membership: {ordered} vs {candidates}")
    if trace.get("added_relations") or trace.get("dropped_relations"):
        errors.append("trace recorded add/drop on a no-hard-filter path")
    if "people.person.nationality" not in ordered:
        errors.append("relation without memory hit was dropped")
    if trace.get("n_memory_hits") != 2:
        errors.append(f"expected 2 memory hits, got {trace.get('n_memory_hits')}")
    return errors


def check_question_not_dominated_by_frequency() -> list[str]:
    import kg_memory_retrieval as retrieval

    bank = people_bank()
    args = fake_args(bank)
    candidates = ["people.person.gender", "people.person.profession"]

    def fake_semantic(question, relations, model):
        return {
            "people.person.gender": 0.95,
            "people.person.profession": 0.05,
        }

    original = retrieval.relation_semantic_scores
    retrieval.relation_semantic_scores = fake_semantic
    try:
        ordered, _prompt, trace = apply_relation_kg_memory(
            question="what is the gender of X?",
            entity_id="m.test",
            retrieved_relations=candidates,
            head_relations=candidates,
            tail_relations=[],
            pre_relations=[],
            args=args,
        )
    finally:
        retrieval.relation_semantic_scores = original
    errors = []
    if ordered[0] != "people.person.gender":
        errors.append(
            "high question relevance lost to high-coverage relation: "
            f"{ordered} scores={trace.get('scores')}"
        )
    return errors


def _close_gap_order(fusion: str) -> tuple[list[str], dict]:
    import kg_memory_retrieval as retrieval

    bank = people_bank()
    args = fake_args(bank, kg_memory_fusion=fusion)
    candidates = [
        "people.person.nationality",
        "people.person.profession",
        "people.person.gender",
    ]

    def fake_semantic(question, relations, model):
        return {
            "people.person.nationality": 0.80,
            "people.person.profession": 0.72,
            "people.person.gender": 0.10,
        }

    original = retrieval.relation_semantic_scores
    retrieval.relation_semantic_scores = fake_semantic
    try:
        ordered, _prompt, trace = apply_relation_kg_memory(
            question="what nationality is X?",
            entity_id="m.test",
            retrieved_relations=candidates,
            head_relations=candidates,
            tail_relations=[],
            pre_relations=[],
            args=args,
        )
    finally:
        retrieval.relation_semantic_scores = original
    return ordered, trace


def check_additive_can_demote_unhit_gold() -> list[str]:
    ordered, trace = _close_gap_order("additive")
    errors = []
    if ordered[0] != "people.person.profession":
        errors.append(
            "additive close-gap case should let high-coverage profession outrank unhit nationality: "
            f"{ordered} scores={trace.get('scores')}"
        )
    return errors


def check_gated_protects_higher_semantic_miss() -> list[str]:
    ordered, trace = _close_gap_order("gated")
    errors = []
    if ordered[0] != "people.person.nationality":
        errors.append(
            "gated fusion should keep higher-semantic no-hit nationality above lower-semantic profession: "
            f"{ordered} scores={trace.get('scores')}"
        )
    if set(ordered) != {
        "people.person.nationality",
        "people.person.profession",
        "people.person.gender",
    }:
        errors.append(f"gated fusion changed membership: {ordered}")
    unprotected = trace.get("order_after_unprotected") or []
    if unprotected and unprotected[0] == "people.person.nationality":
        errors.append("close-gap fixture no longer demotes under additive order; gated test is vacuous")
    return errors


def check_shuffle_changes_pairing() -> list[str]:
    bank = people_bank()
    candidates = [
        "people.person.gender",
        "people.person.profession",
        "people.person.date_of_birth",
    ]
    real_args = fake_args(bank, kg_memory_ablation="none")
    shuffle_args = fake_args(bank, kg_memory_ablation="shuffle")
    _, _, real_trace = apply_relation_kg_memory(
        question="q",
        entity_id="m.test",
        retrieved_relations=candidates,
        head_relations=candidates,
        tail_relations=[],
        pre_relations=[],
        args=real_args,
    )
    _, _, shuffle_trace = apply_relation_kg_memory(
        question="q",
        entity_id="m.test",
        retrieved_relations=candidates,
        head_relations=candidates,
        tail_relations=[],
        pre_relations=[],
        args=shuffle_args,
    )
    real_struct = [row["structural"] for row in real_trace["scores"]]
    shuffle_struct = [row["structural"] for row in shuffle_trace["scores"]]
    if real_struct == shuffle_struct:
        return [f"shuffle did not change structural pairing: {real_struct}"]
    if sorted(real_struct) != sorted(shuffle_struct):
        return [f"shuffle changed the score multiset: {real_struct} vs {shuffle_struct}"]
    return []


def check_prompt_does_not_reorder() -> list[str]:
    bank = people_bank()
    args = fake_args(bank, kg_memory_strategy="prompt")
    candidates = ["people.person.gender", "people.person.profession"]
    ordered, prompt, trace = apply_relation_kg_memory(
        question="q",
        entity_id="m.test",
        retrieved_relations=candidates,
        head_relations=candidates,
        tail_relations=[],
        pre_relations=[],
        args=args,
    )
    errors = []
    if ordered != candidates:
        errors.append(f"prompt strategy reordered candidates: {ordered}")
    if "people.person.profession" not in prompt:
        errors.append("prompt strategy omitted structural evidence")
    if "not an answer" not in prompt.lower() and "not the answer" not in prompt.lower():
        errors.append("prompt missing the 'not the answer' caveat")
    if trace.get("order_before") != trace.get("order_after"):
        errors.append("prompt strategy recorded an order change")
    return errors


def check_path_two_hop_preferred() -> list[str]:
    bank = path_people_bank()
    stat = bank.lookup(["people.person"], "outgoing", "people.person.profession", 0.0, True)
    errors = []
    if stat is None:
        return ["path bank missing people.person.profession first hop"]
    if stat.hop_length != 2:
        errors.append(f"expected 2-hop stats for profession first hop, got hop_length={stat.hop_length} path={stat.relation_path}")
    if abs(stat.coverage - 0.6) > 1e-6:
        errors.append(f"2-hop coverage not used: {stat.coverage}")
    return errors


def check_path_no_extra_or_drop() -> list[str]:
    bank = path_people_bank()
    args = fake_args(bank)
    candidates = [
        "people.person.gender",
        "people.person.profession",
        "people.person.nationality",
    ]
    ordered, _prompt, trace = apply_relation_kg_memory(
        question="what is the profession of X?",
        entity_id="m.test",
        retrieved_relations=candidates,
        head_relations=candidates,
        tail_relations=[],
        pre_relations=[],
        args=args,
    )
    errors = []
    if set(ordered) != set(candidates):
        errors.append(f"path rerank changed membership: {ordered} vs {candidates}")
    if trace.get("added_relations") or trace.get("dropped_relations"):
        errors.append("path trace recorded add/drop")
    if "people.person.nationality" not in ordered:
        errors.append("unhit relation dropped under path memory")
    rows = [row for row in trace.get("scores") or [] if row.get("relation") == "people.person.profession"]
    if rows and int(rows[0].get("n_variants") or 0) != 2:
        errors.append(f"profession should expose both templates, got n_variants={rows[0].get('n_variants')}")
    return errors


def check_path_tail_semantics_discriminate() -> list[str]:
    """Two first hops with identical statistics must be split by their second hop."""
    import kg_memory_retrieval as retrieval

    records = [
        fake_path_record(
            "people.person",
            "outgoing",
            ["people.person.a_hop", "people.marriage.spouse"],
            coverage=0.8,
            support=16,
            confidence=0.8,
            target_type="people.person",
        ),
        fake_path_record(
            "people.person",
            "outgoing",
            ["people.person.b_hop", "location.location.containedby"],
            coverage=0.8,
            support=16,
            confidence=0.8,
            target_type="location.location",
        ),
    ]
    bank = bank_from_records(records, path="fake-path", jsonl_path="fake-path")
    args = fake_args(bank, kg_memory_fusion="gated")
    candidates = ["people.person.a_hop", "people.person.b_hop"]

    def fake_semantic(question, relations, model):
        return {rel: (1.0 if "spouse" in rel else 0.0) for rel in relations}

    original = retrieval.relation_semantic_scores
    retrieval.relation_semantic_scores = fake_semantic
    try:
        ordered, _prompt, trace = apply_relation_kg_memory(
            question="who did X marry?",
            entity_id="m.test",
            retrieved_relations=candidates,
            head_relations=candidates,
            tail_relations=[],
            pre_relations=[],
            args=args,
        )
    finally:
        retrieval.relation_semantic_scores = original
    struct = {row["relation"]: row["structural"] for row in trace.get("scores") or []}
    errors = []
    if struct.get("people.person.a_hop", 0) <= struct.get("people.person.b_hop", 0):
        errors.append(f"second hop did not change structural score: {struct}")
    if min(struct.values() or [0]) <= 0:
        errors.append(f"a memory hit must stay strictly positive: {struct}")
    if ordered[0] != "people.person.a_hop":
        errors.append(f"question-matching path not ranked first: {ordered}")
    return errors


def check_path_notail_ignores_second_hop() -> list[str]:
    """M2-notail: identical first-hop stats stay tied even if second hops differ."""
    import kg_memory_retrieval as retrieval

    records = [
        fake_path_record(
            "people.person",
            "outgoing",
            ["people.person.a_hop", "people.marriage.spouse"],
            coverage=0.8,
            support=16,
            confidence=0.8,
            target_type="people.person",
        ),
        fake_path_record(
            "people.person",
            "outgoing",
            ["people.person.b_hop", "location.location.containedby"],
            coverage=0.8,
            support=16,
            confidence=0.8,
            target_type="location.location",
        ),
    ]
    bank = bank_from_records(records, path="fake-path", jsonl_path="fake-path")
    args = fake_args(bank, kg_memory_fusion="gated", kg_memory_use_tail_sem=0)
    candidates = ["people.person.a_hop", "people.person.b_hop"]

    def fake_semantic(question, relations, model):
        return {rel: (1.0 if "spouse" in rel else 0.0) for rel in relations}

    original = retrieval.relation_semantic_scores
    retrieval.relation_semantic_scores = fake_semantic
    try:
        _ordered, _prompt, trace = apply_relation_kg_memory(
            question="who did X marry?",
            entity_id="m.test",
            retrieved_relations=candidates,
            head_relations=candidates,
            tail_relations=[],
            pre_relations=[],
            args=args,
        )
    finally:
        retrieval.relation_semantic_scores = original
    struct = {row["relation"]: row["structural"] for row in trace.get("scores") or []}
    tails = {row["relation"]: row.get("tail_semantic") for row in trace.get("scores") or []}
    errors = []
    if abs(struct.get("people.person.a_hop", -1) - struct.get("people.person.b_hop", -2)) > 1e-6:
        errors.append(f"notail should not split equal first hops: {struct}")
    if any(v is not None for v in tails.values()):
        errors.append(f"notail must not write tail_semantic: {tails}")
    return errors


def check_schema_scores_keep_m1_formula() -> list[str]:
    """M1 must not pick up the path tail weighting."""
    bank = people_bank()
    args = fake_args(bank)
    candidates = ["people.person.profession", "people.person.gender"]
    _ordered, _prompt, trace = apply_relation_kg_memory(
        question="what is the profession of X?",
        entity_id="m.test",
        retrieved_relations=candidates,
        head_relations=candidates,
        tail_relations=[],
        pre_relations=[],
        args=args,
    )
    errors = []
    for row in trace.get("scores") or []:
        stat = bank.lookup(["people.person"], "outgoing", row["relation"], 0.0, True)
        if stat is None:
            continue
        expected = round(stat.structural_score(explored=False), 4)
        if abs(float(row["structural"]) - expected) > 1e-6:
            errors.append(f"{row['relation']} structural {row['structural']} != M1 formula {expected}")
    return errors


def check_frozen_path_bank(path: str) -> list[str]:
    errors = []
    try:
        bank = load_kg_memory_bank(path)
    except Exception as exc:
        return [f"failed to load path bank {path}: {exc}"]
    if bank.n_records < 50:
        errors.append(f"too few compact first-hop keys: {bank.n_records}")
    if bank.n_validated < 30:
        errors.append(f"too few validated first-hop keys: {bank.n_validated}")
    if not bank.build_config_hash:
        errors.append("missing build_config_hash")
    stat = bank.lookup(["music.release_track"], "outgoing", "music.release_track.recording", 0.0, True)
    if stat is None:
        errors.append("missing music.release_track outgoing recording first hop")
    elif stat.hop_length < 1:
        errors.append(f"unexpected hop_length for recording: {stat.hop_length}")
    kinds = {item.memory_kind for item in bank.stats.values()}
    if MEMORY_KIND_PATH_TEMPLATE not in kinds:
        errors.append(f"path bank compact kinds={kinds}")
    return errors


def check_frozen_bank(path: str) -> list[str]:
    errors = []
    try:
        bank = load_kg_memory_bank(path)
    except Exception as exc:
        return [f"failed to load bank {path}: {exc}"]
    if bank.n_records < 100:
        errors.append(f"too few compact records: {bank.n_records}")
    if bank.n_validated < 50:
        errors.append(f"too few validated records: {bank.n_validated}")
    if not bank.build_config_hash:
        errors.append("missing build_config_hash")
    stat = bank.lookup(["music.release_track"], "incoming", "music.recording.tracks", 0.0, True)
    if stat is None:
        errors.append("missing music.release_track incoming music.recording.tracks")
    elif stat.coverage < 0.9:
        errors.append(f"unexpected coverage for recording.tracks: {stat.coverage}")
    return errors


def check_run_dir(path: str) -> list[str]:
    if os.path.isdir(path):
        trace_path = os.path.join(path, "pog_trace.jsonl")
        meta_path = os.path.join(path, "run_meta.json")
    else:
        trace_path = path
        meta_path = os.path.join(os.path.dirname(path), "run_meta.json")
    if not os.path.isfile(trace_path):
        return [f"missing trace: {trace_path}"]
    errors = []
    n_events = 0
    n_hits = 0
    n_order_changed = 0
    for record in iter_jsonl_records(trace_path):
        pog = record.get("pog_trace") or {}
        for depth in pog.get("depths") or []:
            for rel_trace in depth.get("relation_prune") or []:
                kgm = (rel_trace or {}).get("kg_memory") or {}
                if not kgm:
                    continue
                n_events += 1
                n_hits += int(kgm.get("n_memory_hits") or 0)
                before = kgm.get("order_before") or []
                after = kgm.get("order_after") or []
                sent = rel_trace.get("retrieved_relations") or rel_trace.get("candidate_relations_sent_to_llm") or []
                if set(before) != set(after):
                    errors.append("order_before/after membership changed")
                extra = set(after) - set(before)
                if extra:
                    errors.append(f"added relations: {sorted(extra)[:5]}")
                if kgm.get("added_relations") or kgm.get("dropped_relations"):
                    errors.append("hard filter markers present")
                if sent and set(sent) != set(after):
                    errors.append("LLM candidate list != order_after")
                if before != after:
                    n_order_changed += 1
            summary = ((depth.get("kg_memory") or {}).get("relation") or {})
            if summary and "n_events" not in summary:
                errors.append("depth kg_memory.relation missing n_events")
    if n_events == 0:
        errors.append("no kg_memory relation events in trace")
    if n_hits == 0:
        errors.append("no memory hits; type lookup or index likely broken")
    print(
        f"run_dir events={n_events} hits={n_hits} order_changed={n_order_changed} "
        f"meta={meta_path if os.path.isfile(meta_path) else 'missing'}"
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", default="", help="Optional real memory dir to load.")
    parser.add_argument("--run_dir", default="", help="Optional PoG run dir to check traces.")
    parser.add_argument("--skip_frozen", type=int, default=0)
    args = parser.parse_args()

    checks = [
        ("gate_off", check_gate_off),
        ("no_extra_or_drop", check_no_extra_or_drop),
        ("question_not_dominated_by_frequency", check_question_not_dominated_by_frequency),
        ("additive_can_demote_unhit_gold", check_additive_can_demote_unhit_gold),
        ("gated_protects_higher_semantic_miss", check_gated_protects_higher_semantic_miss),
        ("shuffle_changes_pairing", check_shuffle_changes_pairing),
        ("prompt_does_not_reorder", check_prompt_does_not_reorder),
        ("path_two_hop_preferred", check_path_two_hop_preferred),
        ("path_no_extra_or_drop", check_path_no_extra_or_drop),
        ("path_tail_semantics_discriminate", check_path_tail_semantics_discriminate),
        ("path_notail_ignores_second_hop", check_path_notail_ignores_second_hop),
        ("schema_scores_keep_m1_formula", check_schema_scores_keep_m1_formula),
    ]
    errors = []
    for name, fn in checks:
        hits = fn()
        status = "PASS" if not hits else "FAIL"
        print(f"[{status}] {name}")
        for item in hits:
            print(f"  - {item}")
        errors.extend(hits)

    bank_path = args.bank or FROZEN_SCHEMA_FULL_DIR
    if not args.skip_frozen:
        hits = check_frozen_bank(bank_path)
        status = "PASS" if not hits else "FAIL"
        print(f"[{status}] frozen_bank {bank_path}")
        for item in hits:
            print(f"  - {item}")
        errors.extend(hits)
        path_bank = FROZEN_PATH_FULL_DIR
        if os.path.isdir(path_bank):
            hits = check_frozen_path_bank(path_bank)
            status = "PASS" if not hits else "FAIL"
            print(f"[{status}] frozen_path_bank {path_bank}")
            for item in hits:
                print(f"  - {item}")
            errors.extend(hits)

    if args.run_dir:
        hits = check_run_dir(args.run_dir)
        status = "PASS" if not hits else "FAIL"
        print(f"[{status}] run_dir {args.run_dir}")
        for item in hits:
            print(f"  - {item}")
        errors.extend(hits)

    if errors:
        print(f"FAILED {len(errors)} check(s)")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
