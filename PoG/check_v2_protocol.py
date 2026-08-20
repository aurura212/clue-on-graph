#!/usr/bin/env python3
"""V2-0 protocol audit: denominators, timeout/retry, evidence order, B0 equivalence.

Does not call LLMs. Does not run random150_v1. Exit 0 only when all checks pass.
"""

from __future__ import annotations

import inspect
import os
import sys
import types
from typing import Any

import output_paths
import utils
from eval_slices import FROZEN_HARD150_V1, FROZEN_RANDOM150_V1, load_questions_file
from kg_memory_retrieval import should_use_kg_memory_at_stage
from reflection_structural_memory import (
    build_reflection_event,
    compact_event_for_trace,
    maybe_prepend_reflection_evidence,
)
from v2_protocol import (
    DEFAULT_GOLD_PATH,
    REFLECTION_EVIDENCE_AFTER_SEMANTIC_FILTER,
    SLICE_ROLES,
    V2_KG_MEMORY_SEED,
    V2_MAX_LENGTH,
    V2_RELATION_SEMANTIC_TOP_K,
    check_reflection_does_not_call_semantic_filter,
    check_semantic_filter_before_relation_memory,
    frozen_llm_policy,
    live_llm_policy_from_utils,
    slice_denominator_report,
    slice_role,
    validate_reflection_event,
)


HERE = os.path.dirname(os.path.abspath(__file__))


def _print_check(name: str, errors: list[str]) -> list[str]:
    status = "PASS" if not errors else "FAIL"
    print(f"[{status}] {name}")
    for item in errors:
        print(f"  - {item}")
    return errors


def check_llm_policy() -> list[str]:
    frozen = frozen_llm_policy()
    live = live_llm_policy_from_utils()
    errors = []
    if live["llm_request_timeout_sec"] != frozen["llm_request_timeout_sec"]:
        errors.append(
            f"timeout live={live['llm_request_timeout_sec']} frozen={frozen['llm_request_timeout_sec']}"
        )
    if live["llm_max_retries"] != frozen["llm_max_retries"]:
        errors.append(f"retries live={live['llm_max_retries']} frozen={frozen['llm_max_retries']}")
    if utils.LLM_REQUEST_TIMEOUT != frozen["llm_request_timeout_sec"]:
        errors.append("utils.LLM_REQUEST_TIMEOUT does not match frozen policy")
    if utils.LLM_MAX_RETRIES != frozen["llm_max_retries"]:
        errors.append("utils.LLM_MAX_RETRIES does not match frozen policy")
    if not REFLECTION_EVIDENCE_AFTER_SEMANTIC_FILTER:
        errors.append("V2 requires reflection evidence after semantic_filter_relations")
    src = inspect.getsource(utils)
    if "LLM_REQUEST_TIMEOUT = float(os.environ.get(\"OPENAI_TIMEOUT\", \"180\"))" not in src:
        errors.append("utils.py timeout default is not OPENAI_TIMEOUT=180")
    if "LLM_MAX_RETRIES = int(os.environ.get(\"OPENAI_MAX_RETRIES\", \"5\"))" not in src:
        errors.append("utils.py retry default is not OPENAI_MAX_RETRIES=5")
    return errors


def check_argparse_and_run_meta() -> list[str]:
    errors = []
    main_path = os.path.join(HERE, "main_freebase.py")
    with open(main_path, encoding="utf-8") as handle:
        main_src = handle.read()
    if "default=40" not in main_src or "relation_semantic_top_k" not in main_src:
        errors.append("main_freebase.py must default relation_semantic_top_k to 40")
    if "--max_length" in main_src and "default=4096" not in main_src:
        errors.append("main_freebase.py must default max_length to 4096")
    args = types.SimpleNamespace(
        max_length=V2_MAX_LENGTH,
        temperature_exploration=0.3,
        temperature_reasoning=0.3,
        depth=4,
        relation_semantic_top_k=V2_RELATION_SEMANTIC_TOP_K,
        kg_memory_seed=V2_KG_MEMORY_SEED,
        kg_memory_mode="none",
    )
    meta = output_paths.build_run_meta_from_args(args, "tag", "folder", 150)
    for key in ("llm_request_timeout_sec", "llm_max_retries", "max_length", "relation_semantic_top_k", "kg_memory_seed"):
        if key not in meta:
            errors.append(f"run_meta missing {key}")
    if float(meta.get("llm_request_timeout_sec") or 0) != 180.0 and os.environ.get("OPENAI_TIMEOUT", "180") == "180":
        errors.append(f"run_meta timeout {meta.get('llm_request_timeout_sec')} != 180")
    if int(meta.get("llm_max_retries") or 0) != 5 and os.environ.get("OPENAI_MAX_RETRIES", "5") == "5":
        errors.append(f"run_meta retries {meta.get('llm_max_retries')} != 5")
    if int(meta.get("max_length") or 0) != V2_MAX_LENGTH:
        errors.append(f"run_meta max_length {meta.get('max_length')} != {V2_MAX_LENGTH}")
    if int(meta.get("relation_semantic_top_k") or 0) != V2_RELATION_SEMANTIC_TOP_K:
        errors.append("run_meta relation_semantic_top_k != 40")
    return errors


def check_slice_roles_and_denominators() -> tuple[list[str], dict[str, Any]]:
    errors = []
    if slice_role("hard150_v1") != "development_stress":
        errors.append("hard150_v1 role is not development_stress")
    if slice_role("random150_v1") != "capability_eval":
        errors.append("random150_v1 role is not capability_eval")
    sid_h, qs_h = load_questions_file(FROZEN_HARD150_V1)
    sid_r, qs_r = load_questions_file(FROZEN_RANDOM150_V1)
    if sid_h != "hard150_v1" or len(qs_h) != 150:
        errors.append(f"hard150_v1 loaded {sid_h} n={len(qs_h)}")
    if sid_r != "random150_v1" or len(qs_r) != 150:
        errors.append(f"random150_v1 loaded {sid_r} n={len(qs_r)}")
    if not os.path.isfile(DEFAULT_GOLD_PATH):
        errors.append(f"missing WebQSP gold: {DEFAULT_GOLD_PATH}")
        return errors, {}
    report = slice_denominator_report(DEFAULT_GOLD_PATH)
    hard = report["hard150_v1"]
    rand = report["random150_v1"]
    if hard["n_all"] != 150:
        errors.append(f"hard150 n_all={hard['n_all']}")
    if hard["n_relation_valid"] != 143:
        errors.append(
            f"hard150 n_relation_valid={hard['n_relation_valid']} expected 143; "
            f"missing={hard['missing_gold_first_hop_questions']}"
        )
    if hard["n_missing_gold_first_hop"] != 7:
        errors.append(f"hard150 missing gold first hop != 7: {hard['n_missing_gold_first_hop']}")
    if rand["n_all"] != 150:
        errors.append(f"random150 n_all={rand['n_all']}")
    return errors, report


def check_evidence_order() -> list[str]:
    errors = check_semantic_filter_before_relation_memory(os.path.join(HERE, "freebase_func.py"))
    errors.extend(check_reflection_does_not_call_semantic_filter(os.path.join(HERE, "utils.py")))
    return errors


def _positive_record() -> dict[str, Any]:
    return {
        "memory_id": "kgm_people_person_profession",
        "status": "validated",
        "key": {
            "source_type": "people.person",
            "direction": "outgoing",
            "relation_path": ["people.person.profession"],
            "target_type": "people.profession",
        },
        "statistics": {
            "validation_coverage": 0.9,
            "validation_entity_support": 20,
            "median_branching": 2.0,
            "confidence": 0.8,
        },
        "evidence": {
            "witness_paths": [["people.person.profession"]],
            "query_template_id": "schema_outgoing_relation",
            "query_hash": "abc123",
        },
    }


def check_memory_off_equals_b0() -> list[str]:
    errors = []
    none_args = types.SimpleNamespace(
        kg_memory_mode="none",
        kg_memory_stages="relation",
        kg_memory_ablation="none",
        kg_memory_seed=42,
        kg_memory_bank=None,
    )
    if should_use_kg_memory_at_stage(none_args, "relation"):
        errors.append("mode=none must not enable relation memory")
    if should_use_kg_memory_at_stage(none_args, "reflection_judge"):
        errors.append("mode=none must not enable reflection_judge")
    if should_use_kg_memory_at_stage(none_args, "reflection_select"):
        errors.append("mode=none must not enable reflection_select")
    base = "JUDGE PREFIX // B0"
    event = build_reflection_event(
        stage="reflection_a",
        args=none_args,
        candidate_frontier=["Marc Chagall"],
        records=[_positive_record()],
    )
    errors.extend(validate_reflection_event(event, prefix="mode=none event"))
    if event.get("prompt_changed") or event.get("prompt_visible_evidence") or event.get("prompt_text"):
        errors.append("mode=none must not emit reflection evidence text")
    if maybe_prepend_reflection_evidence(base, event) != base:
        errors.append("mode=none prepend changed the B0 Decision A prefix")
    compact = compact_event_for_trace(event)
    errors.extend(validate_reflection_event(compact, prefix="mode=none compact"))

    reflection_args = types.SimpleNamespace(
        kg_memory_mode="reflection",
        kg_memory_stages="relation",
        kg_memory_ablation="none",
        kg_memory_seed=42,
    )
    if should_use_kg_memory_at_stage(reflection_args, "relation"):
        errors.append("mode=reflection must not enable first-hop relation rerank")
    if not should_use_kg_memory_at_stage(reflection_args, "reflection_judge"):
        errors.append("mode=reflection should enable Decision A even with leftover stages=relation")
    if not should_use_kg_memory_at_stage(reflection_args, "reflection_select"):
        errors.append("mode=reflection should enable Decision B even with leftover stages=relation")

    relation_args = types.SimpleNamespace(kg_memory_mode="relation", kg_memory_stages="relation")
    if not should_use_kg_memory_at_stage(relation_args, "relation"):
        errors.append("mode=relation should still enable first-hop for archived M1")
    if should_use_kg_memory_at_stage(relation_args, "reflection_judge"):
        errors.append("mode=relation must not enable reflection")
    return errors


def check_slice_not_evaluated() -> list[str]:
    errors = []
    if SLICE_ROLES.get("random150_v1") != "capability_eval":
        errors.append("random150_v1 must remain capability_eval / unevaluated in V2-0")
    result_root = os.path.join(HERE, "result")
    if os.path.isdir(result_root):
        for name in os.listdir(result_root):
            if "random150" in name.lower():
                errors.append(f"found a random150 run dir, V2-0 must not evaluate it: {name}")
    return errors


def main() -> int:
    all_errors: list[str] = []
    all_errors.extend(_print_check("llm_timeout_retry_max_token", check_llm_policy()))
    all_errors.extend(_print_check("argparse_and_run_meta", check_argparse_and_run_meta()))
    denom_errors, report = check_slice_roles_and_denominators()
    all_errors.extend(_print_check("slice_roles_and_denominators", denom_errors))
    if report:
        hard = report["hard150_v1"]
        rand = report["random150_v1"]
        print(
            f"  hard150_v1 n_all={hard['n_all']} n_relation_valid={hard['n_relation_valid']} "
            f"missing={hard['n_missing_gold_first_hop']}"
        )
        for question in hard.get("missing_gold_first_hop_questions") or []:
            print(f"    missing gold first hop: {question}")
        print(
            f"  random150_v1 n_all={rand['n_all']} n_relation_valid={rand['n_relation_valid']} "
            f"missing={rand['n_missing_gold_first_hop']}"
        )
    all_errors.extend(_print_check("semantic_filter_vs_reflection_order", check_evidence_order()))
    all_errors.extend(_print_check("memory_off_equals_b0", check_memory_off_equals_b0()))
    all_errors.extend(_print_check("random150_not_evaluated", check_slice_not_evaluated()))
    if all_errors:
        print(f"FAILED {len(all_errors)} check(s)")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
