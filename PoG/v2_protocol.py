"""V2 frozen protocol: slice roles, LLM timeout/retry, denominators, evidence order.

Does not call LLMs.
"""

from __future__ import annotations

import os
from typing import Any

from eval_slices import FROZEN_HARD150_V1, FROZEN_RANDOM150_V1, load_questions_file

# Frozen V2 LLM policy. Must match Phase 0 / hard150 B0 scripts, not argparse leftovers.
V2_MAX_LENGTH = 4096
V2_LLM_REQUEST_TIMEOUT_SEC = 180.0
V2_LLM_MAX_RETRIES = 5
V2_TEMPERATURE_EXPLORATION = 0.3
V2_TEMPERATURE_REASONING = 0.3
V2_DEPTH = 4
V2_RELATION_SEMANTIC_TOP_K = 40
V2_KG_MEMORY_SEED = 42
V2_TIMEOUT_ENV = "OPENAI_TIMEOUT"
V2_RETRIES_ENV = "OPENAI_MAX_RETRIES"

# V2-0: reflection evidence is attached only after SPARQL + semantic_filter_relations.
# relation_search_prune: SPARQL -> abandon -> semantic_filter -> optional relation memory.
# if_finish_list / Decision A/B run later and must not call semantic_filter_relations.
REFLECTION_EVIDENCE_AFTER_SEMANTIC_FILTER = True

DEFAULT_GOLD_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "WebQSP.json")
)

SLICE_ROLES = {
    "hard150_v1": "development_stress",
    "random150_v1": "capability_eval",
}

FORBIDDEN_REFLECTION_CONCLUSION_PHRASES = (
    "continue = true",
    "continue=true",
    "stop = true",
    "stop=true",
    "backtrack =",
    "backtrack=",
)

REFLECTION_EVENT_REQUIRED_KEYS = (
    "stage",
    "memory_mode",
    "candidate_frontier",
    "evidence_items",
    "prompt_visible_evidence",
    "prompt_text",
    "prompt_changed",
    "timeout",
    "retry_count",
    "semantic_filter_already_applied",
    "n_candidates",
    "n_evidence_items",
    "llm_decision",
    "selected_entity",
    "post_decision_new_triples",
    "post_decision_found_answer",
)

REFLECTION_ITEM_REQUIRED_KEYS = (
    "memory_id",
    "applicability",
    "coverage",
    "confidence",
    "branching",
    "witness_replayable",
)

PROMPT_SECTION_TITLES = (
    "validated unexplored routes",
    "unknown routes",
    "already explored routes",
    "high-cost / high-branching routes",
)


def frozen_llm_policy() -> dict[str, Any]:
    return {
        "max_length": V2_MAX_LENGTH,
        "llm_request_timeout_sec": V2_LLM_REQUEST_TIMEOUT_SEC,
        "llm_max_retries": V2_LLM_MAX_RETRIES,
        "temperature_exploration": V2_TEMPERATURE_EXPLORATION,
        "temperature_reasoning": V2_TEMPERATURE_REASONING,
        "depth": V2_DEPTH,
        "relation_semantic_top_k": V2_RELATION_SEMANTIC_TOP_K,
        "kg_memory_seed": V2_KG_MEMORY_SEED,
        "timeout_env": V2_TIMEOUT_ENV,
        "retries_env": V2_RETRIES_ENV,
        "timeout_env_default": "180",
        "retries_env_default": "5",
        "reflection_evidence_after_semantic_filter": REFLECTION_EVIDENCE_AFTER_SEMANTIC_FILTER,
    }


def slice_role(slice_id: str) -> str:
    return SLICE_ROLES.get(str(slice_id or ""), "")


def load_hard150_questions() -> list[str]:
    _sid, questions = load_questions_file(FROZEN_HARD150_V1)
    return questions


def load_random150_questions() -> list[str]:
    _sid, questions = load_questions_file(FROZEN_RANDOM150_V1)
    return questions


def load_gold_first_hops(gold_path: str | None = None) -> dict[str, set[str]]:
    from analyze_kg_memory_run import load_webqsp_gold

    gold = load_webqsp_gold(gold_path or DEFAULT_GOLD_PATH)
    return {question: set(row.get("first_hops") or []) for question, row in gold.items()}


def relation_valid_denominator(questions: list[str], gold_first_hops: dict[str, set[str]]) -> dict[str, Any]:
    """Full n vs questions that have a gold first-hop InferentialChain.

    On hard150, LOG-047 used n=150 and n_with_gold_first_hop=143.
    Missing hops are WebQSP annotation gaps (null/empty InferentialChain), not memory bugs.
    """
    n = len(questions)
    missing = [q for q in questions if not gold_first_hops.get(q)]
    n_with = n - len(missing)
    return {
        "n_all": n,
        "n_relation_valid": n_with,
        "n_missing_gold_first_hop": len(missing),
        "missing_gold_first_hop_questions": missing,
        "note": (
            "n_all is the slice denominator for EM/F1. n_relation_valid is questions with at "
            "least one WebQSP InferentialChain first hop; first-hop recall uses this smaller "
            "denominator. Missing gold hops are dataset gaps, not memory bugs."
        ),
    }


def slice_denominator_report(gold_path: str | None = None) -> dict[str, Any]:
    gold = load_gold_first_hops(gold_path)
    return {
        "hard150_v1": relation_valid_denominator(load_hard150_questions(), gold),
        "random150_v1": relation_valid_denominator(load_random150_questions(), gold),
    }


def live_llm_policy_from_utils() -> dict[str, Any]:
    import utils

    return {
        "max_length_runner_default": V2_MAX_LENGTH,
        "llm_request_timeout_sec": float(utils.LLM_REQUEST_TIMEOUT),
        "llm_max_retries": int(utils.LLM_MAX_RETRIES),
        "timeout_env_value": os.environ.get(V2_TIMEOUT_ENV, "180"),
        "retries_env_value": os.environ.get(V2_RETRIES_ENV, "5"),
    }


def validate_reflection_event(event: dict[str, Any], *, prefix: str = "event") -> list[str]:
    errors = []
    if not isinstance(event, dict):
        return [f"{prefix}: not an object"]
    for key in REFLECTION_EVENT_REQUIRED_KEYS:
        if key not in event:
            errors.append(f"{prefix}: missing {key}")
    if event.get("semantic_filter_already_applied") is not True:
        errors.append(f"{prefix}: semantic_filter_already_applied must be True for V2")
    items = event.get("evidence_items") or []
    if not isinstance(items, list):
        errors.append(f"{prefix}: evidence_items is not a list")
        return errors
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{prefix} evidence_items[{idx}]: not an object")
            continue
        for key in REFLECTION_ITEM_REQUIRED_KEYS:
            if key not in item:
                errors.append(f"{prefix} evidence_items[{idx}]: missing {key}")
    return errors


def source_function_body(path: str, func_name: str) -> str:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    needle = f"def {func_name}("
    start = text.find(needle)
    if start < 0:
        return ""
    rest = text[start:]
    nxt = rest.find("\ndef ", 1)
    return rest if nxt < 0 else rest[:nxt]


def check_semantic_filter_before_relation_memory(freebase_func_path: str) -> list[str]:
    body = source_function_body(freebase_func_path, "relation_search_prune")
    errors = []
    if not body:
        return ["relation_search_prune not found"]
    i_sem = body.find("semantic_filter_relations(")
    i_mem = body.find("apply_relation_kg_memory(")
    if i_sem < 0:
        errors.append("relation_search_prune does not call semantic_filter_relations")
    if i_mem < 0:
        errors.append("relation_search_prune does not call apply_relation_kg_memory")
    if i_sem >= 0 and i_mem >= 0 and i_sem > i_mem:
        errors.append("semantic_filter_relations must run before apply_relation_kg_memory")
    return errors


def check_reflection_does_not_call_semantic_filter(utils_path: str) -> list[str]:
    body = source_function_body(utils_path, "if_finish_list")
    if not body:
        return ["if_finish_list not found"]
    errors = []
    if "semantic_filter_relations(" in body:
        errors.append("if_finish_list must not call semantic_filter_relations")
    if "apply_relation_kg_memory(" in body:
        errors.append("if_finish_list must not rerank first-hop relations")
    if "build_reflection_event(" not in body:
        errors.append("if_finish_list must construct Decision A/B reflection evidence")
    return errors
