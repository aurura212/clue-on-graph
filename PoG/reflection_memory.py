"""Reflection-memory training, validation, retrieval, and prompt formatting for PoG."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any, Iterable

from jsonl_io import iter_jsonl_records


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def mask_question_with_entities(question: str, topic_entities: dict[str, str]) -> str:
    masked = str(question or "")
    names = sorted(
        {str(name).strip() for name in (topic_entities or {}).values() if str(name).strip()},
        key=len, reverse=True,
    )
    for name in names:
        masked = re.sub(re.escape(name), "[TOPIC_ENTITY]", masked, flags=re.IGNORECASE)
    return masked


def resolve_project_path(path: str) -> str:
    path = os.path.expanduser(str(path or ""))
    return path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)


def read_jsonl_file(path: str) -> list[dict[str, Any]]:
    """Read pretty-printed multi-line JSONL. Do not parse line-by-line."""
    if not path or not os.path.exists(path):
        return []
    return list(iter_jsonl_records(path))

ANSWER_DEPTH = "answer_depth"
JUDGE_REVERSE = "judge_reverse"
ADD_ENTITY = "add_entity"
REFLECTION_MEMORY_TYPES = {ANSWER_DEPTH, JUDGE_REVERSE, ADD_ENTITY}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalise_answer(value: Any) -> str:
    text = _clean(value).lower()
    text = text.strip("[]{}()\"'")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value if _clean(item)]
    return [_clean(value)] if _clean(value) else []


def _unique(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def parse_reflection_stages(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        parts = value
    else:
        parts = re.split(r"[\s,]+", str(value or ""))
    aliases = {
        "answer": ANSWER_DEPTH,
        "reasoning": ANSWER_DEPTH,
        "reverse": JUDGE_REVERSE,
        "add_ent": ADD_ENTITY,
    }
    return {aliases.get(_clean(part).lower(), _clean(part).lower()) for part in parts if _clean(part)}


def should_use_reflection_memory_at_stage(args: Any, memory_type: str) -> bool:
    if getattr(args, "reflection_memory_mode", "none") != "prompt":
        return False
    stages = parse_reflection_stages(
        getattr(args, "reflection_memory_stages", "answer_depth,judge_reverse,add_entity")
    )
    if not stages or "none" in stages:
        return False
    return "all" in stages or memory_type in stages


def load_reflection_memory(path: str) -> list[dict[str, Any]]:
    if not path:
        return []
    resolved = resolve_project_path(path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Reflection memory file not found: {resolved}")
    return [
        item for item in read_jsonl_file(resolved)
        if item.get("memory_type") in REFLECTION_MEMORY_TYPES and item.get("verified") is True
    ]


def count_reflection_memory(path: str) -> dict[str, int]:
    counts = {ANSWER_DEPTH: 0, JUDGE_REVERSE: 0, ADD_ENTITY: 0, "total": 0}
    if not path or not os.path.exists(path):
        return counts
    for item in read_jsonl_file(path):
        memory_type = item.get("memory_type")
        if memory_type in REFLECTION_MEMORY_TYPES and item.get("verified") is True:
            counts[memory_type] += 1
            counts["total"] += 1
    return counts


def triplets_to_lines(triplets: Any) -> list[str]:
    if not triplets:
        return []
    if isinstance(triplets, str):
        return [line.strip() for line in triplets.splitlines() if line.strip()]
    lines: list[str] = []
    for triplet in triplets:
        if isinstance(triplet, str):
            if triplet.strip():
                lines.append(triplet.strip())
        elif isinstance(triplet, dict):
            head = triplet.get("head_name") or triplet.get("head") or triplet.get("topic_entity") or ""
            relation = triplet.get("relation") or ""
            tail = triplet.get("tail_name") or triplet.get("tail") or triplet.get("entities") or ""
            lines.append(f"{head}, {relation}, {tail}")
        elif isinstance(triplet, (list, tuple)) and len(triplet) >= 3:
            lines.append(f"{triplet[0]}, {triplet[1]}, {triplet[2]}")
    return _unique(lines)


def build_reflection_state_key(
    memory_type: str,
    memory: str,
    knowledge_triplets: Any,
    current_subobjective: str = "",
    entities: Any = None,
) -> str:
    triplet_lines = triplets_to_lines(knowledge_triplets)
    return " | ".join(
        part for part in [
            f"stage={memory_type}",
            f"subobjective={_clean(current_subobjective)}",
            f"entities={'; '.join(_as_list(entities))}",
            f"memory={_clean(memory)}",
            f"triplets={'; '.join(triplet_lines)}",
        ] if part.split("=", 1)[-1]
    )


def _json_output(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=4)


def format_reflection_example(item: dict[str, Any]) -> str:
    """Format only prompt-safe fields; training diagnostics and gold metadata never leak."""
    memory_type = item.get("memory_type")
    question = item.get("question", "")
    memory = item.get("memory", "")
    triplets = "\n".join(triplets_to_lines(item.get("knowledge_triplets")))
    if memory_type == ANSWER_DEPTH:
        return "\n".join([
            f"Q: {question}",
            f"Memory: {memory}",
            f"Knowledge Triplets: {triplets}",
            "Output:",
            _json_output(item.get("output", {})),
        ])
    if memory_type == JUDGE_REVERSE:
        return "\n".join([
            f"Q: {question}",
            f"Entities set to be retrieved: {item.get('entities_to_retrieve', [])}",
            f"Memory: {memory}",
            f"Knowledge Triplets: {triplets}",
            "Output:",
            _json_output(item.get("output", {})),
        ])
    if memory_type == ADD_ENTITY:
        return "\n".join([
            f"Q: {question}",
            f"Reason: {item.get('reason', '')}",
            f"Candidate Entities: {item.get('candidate_entities', [])}",
            f"Memory: {memory}",
            f"Output: {item.get('output', [])}",
        ])
    return ""


def _lexical_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[a-z0-9_.]+", str(left).lower()))
    right_tokens = set(re.findall(r"[a-z0-9_.]+", str(right).lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _embedding_scores(model: Any, query: str, documents: list[str]) -> list[float] | None:
    if model is None or not documents:
        return None
    try:
        from sentence_transformers import util

        query_embedding = model.encode(query)
        document_embeddings = model.encode(documents)
        return [float(score) for score in util.dot_score(query_embedding, document_embeddings)[0].cpu().tolist()]
    except Exception:
        return None


def reflection_memory_context(
    memory_bank: list[dict[str, Any]],
    memory_type: str,
    question: str,
    memory: str,
    knowledge_triplets: Any,
    args: Any,
    model: Any = None,
    *,
    current_subobjective: str = "",
    entities: Any = None,
    return_items: bool = False,
):
    if not should_use_reflection_memory_at_stage(args, memory_type):
        return ("", []) if return_items else ""
    filtered = [
        item for item in memory_bank
        if item.get("memory_type") == memory_type and item.get("verified") is True
    ]
    if not filtered:
        return ("", []) if return_items else ""

    topic_entities = getattr(args, "current_topic_entity", {}) or {}
    question_key = mask_question_with_entities(question, topic_entities)
    state_key = build_reflection_state_key(
        memory_type, memory, knowledge_triplets, current_subobjective, entities
    )
    memory_question_keys = [
        item.get("question_key") or item.get("masked_question") or item.get("question", "")
        for item in filtered
    ]
    memory_state_keys = [item.get("state_key", "") for item in filtered]
    question_scores = _embedding_scores(model, question_key, memory_question_keys)
    state_scores = _embedding_scores(model, state_key, memory_state_keys)
    if question_scores is None:
        question_scores = [_lexical_similarity(question_key, value) for value in memory_question_keys]
    if state_scores is None:
        state_scores = [_lexical_similarity(state_key, value) for value in memory_state_keys]

    strategy = getattr(args, "memory_retrieval_strategy", "hybrid")
    state_weight = float(getattr(args, "memory_state_weight", 0.5))
    scored: list[tuple[float, dict[str, Any]]] = []
    for index, item in enumerate(filtered):
        if strategy == "question":
            score = question_scores[index]
        elif strategy == "state":
            score = state_scores[index]
        else:
            score = (1.0 - state_weight) * question_scores[index] + state_weight * state_scores[index]
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    top_k = max(0, int(getattr(args, "reflection_memory_top_k", 1)))
    token_budget = max(1, int(getattr(args, "reflection_memory_prompt_token_budget", 500)))
    selected: list[dict[str, Any]] = []
    blocks: list[str] = []
    for _score, item in scored[:top_k]:
        block = format_reflection_example(item)
        if not block:
            continue
        candidate = "\n\n".join(blocks + [block])
        # A conservative dependency-free approximation; the prompt builder adds only a short header.
        if max(1, int(len(candidate) / 3.5)) > token_budget:
            break
        blocks.append(block)
        selected.append(item)
    context = "\n\n".join(blocks)
    if return_items:
        return context, selected
    return context


def _answer_matches(answer: Any, gold_answers: list[dict[str, Any]], visible_values: Iterable[Any]) -> bool:
    answer_candidates = [answer]
    if isinstance(answer, str):
        try:
            parsed = json.loads(answer.replace("'", '"'))
            if isinstance(parsed, list):
                answer_candidates.extend(parsed)
        except Exception:
            answer_candidates.extend(re.split(r"[,;|]", answer))
    answer_norms = {_normalise_answer(value) for value in answer_candidates if _normalise_answer(value)}
    answer_norms.discard("null")
    if not answer_norms:
        return False
    visible_norms = {_normalise_answer(value) for value in visible_values if _normalise_answer(value)}
    gold_norms: set[str] = set()
    for gold in gold_answers:
        gold_norms.add(_normalise_answer(gold.get("answer_id")))
        gold_norms.add(_normalise_answer(gold.get("answer")))
    # At least one returned answer must be both a legal gold answer and grounded in visible evidence.
    return bool(answer_norms & gold_norms & visible_norms)


def determine_expected_sufficient(hop: dict[str, Any], trace: dict[str, Any]) -> tuple[str, str]:
    if not hop.get("is_final"):
        return "No", "Null"
    visible_values = trace.get("visible_values", [])
    for gold in hop.get("gold_answers", []):
        for value in (gold.get("answer"), gold.get("answer_id")):
            if _normalise_answer(value) in {_normalise_answer(item) for item in visible_values}:
                return "Yes", _clean(value)
    return "No", "Null"


def _parse_memory_object(memory: Any) -> dict[str, Any] | None:
    text = str(memory or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except Exception:
        try:
            first = text.find("{")
            last = text.rfind("}")
            value = json.loads(text[first:last + 1]) if first >= 0 and last > first else None
        except Exception:
            value = None
    return value if isinstance(value, dict) else None


def _memory_grounding_check(trace: dict[str, Any], hop: dict[str, Any]) -> tuple[bool, list[str]]:
    """Require a structured memory that covers subobjective slots and visible triplet tokens."""
    memory = trace.get("memory_after", "")
    memory_obj = _parse_memory_object(memory)
    problems: list[str] = []
    subobjectives = list(hop.get("subobjectives", []))
    if memory_obj is None:
        problems.append("memory_not_json_object")
        return False, problems

    expected_slots = {str(index + 1) for index in range(len(subobjectives))}
    if expected_slots and not expected_slots.issubset({str(key) for key in memory_obj}):
        problems.append("memory_missing_subobjective_slots")

    memory_norm = _normalise_answer(json.dumps(memory_obj, ensure_ascii=False))
    triplet_lines = triplets_to_lines(trace.get("knowledge_triplets", []))
    for line in triplet_lines:
        parts = [part.strip() for part in line.split(",", 2)]
        if len(parts) < 3:
            continue
        head_norm = _normalise_answer(parts[0])
        tail_norm = _normalise_answer(parts[2])
        if head_norm and head_norm not in memory_norm:
            problems.append("memory_missing_visible_head")
            break
        # The tail can contain a compact list. Requiring one meaningful token keeps
        # paraphrased memories valid while rejecting memories unrelated to evidence.
        tail_tokens = [token for token in tail_norm.split() if len(token) > 2]
        if tail_tokens and not any(token in memory_norm.split() for token in tail_tokens):
            problems.append("memory_missing_visible_tail")
            break
    return not problems, _unique(problems)


def diagnose_gold_hop_round(trace: dict[str, Any], hop: dict[str, Any]) -> dict[str, Any]:
    selected_relations = {
        item.get("relation") for item in trace.get("relation_selection", {}).get("selected_relations", [])
    }
    relation_ok = hop.get("gold_relation") in selected_relations
    selected_entity_ids = set(trace.get("entity_selection", {}).get("selected_entity_ids", []))
    gold_next_ids = set(hop.get("gold_next_entity_ids", []))
    entity_ok = bool(gold_next_ids & selected_entity_ids)
    memory_ok, memory_problems = _memory_grounding_check(trace, hop)

    expected_sufficient, expected_answer = determine_expected_sufficient(hop, trace)
    actual_sufficient = _clean(trace.get("answer_depth", {}).get("sufficient")).lower()
    actual_answer = trace.get("answer_depth", {}).get("answer")
    if expected_sufficient == "Yes":
        sufficient_ok = actual_sufficient == "yes" and _answer_matches(
            actual_answer, hop.get("gold_answers", []), trace.get("visible_values", [])
        )
    else:
        sufficient_ok = actual_sufficient == "no" and _normalise_answer(actual_answer) in {"", "null"}

    reverse = trace.get("reverse", {})
    reverse_invoked = bool(reverse.get("invoked"))
    need_backtrack = not (relation_ok and entity_ok)
    if expected_sufficient == "Yes" and relation_ok and entity_ok:
        reverse_ok = not reverse_invoked
    elif not reverse_invoked:
        reverse_ok = False
    else:
        actual_add = bool(reverse.get("add"))
        if need_backtrack:
            expected_ids = set(hop.get("gold_start_entity_ids", []))
            actual_ids = set(reverse.get("added_entity_ids", []))
            reverse_ok = actual_add and bool(expected_ids & actual_ids)
        else:
            reverse_ok = not actual_add

    errors: list[str] = []
    if not relation_ok:
        errors.append("relation")
    if not entity_ok:
        errors.append("entity")
    if not memory_ok:
        errors.append("memory")
    if not sufficient_ok:
        errors.append("sufficient")
    if not reverse_ok:
        errors.append("backtrack")
    return {
        "success": not errors,
        "errors": errors,
        "relation_ok": relation_ok,
        "entity_ok": entity_ok,
        "memory_ok": memory_ok,
        "memory_problems": memory_problems,
        "sufficient_ok": sufficient_ok,
        "reverse_ok": reverse_ok,
        "expected_sufficient": expected_sufficient,
        "expected_answer": expected_answer,
        "expected_add": "Yes" if need_backtrack else "No",
    }


MEMORY_SLOT_NOTE_SEP = " | Note: "
INCOMPLETE_MEMORY_SENTENCE = "It is not mentioned, and I also don't know."


def strip_memory_slot_notes(memory: str) -> str:
    """Drop training notes before a memory item is written as a few-shot."""
    obj = _parse_memory_object(memory)
    if not isinstance(obj, dict) or not obj:
        return memory
    cleaned: dict[str, str] = {}
    for key, value in obj.items():
        text = str(value)
        if MEMORY_SLOT_NOTE_SEP in text:
            text = text.split(MEMORY_SLOT_NOTE_SEP, 1)[0].rstrip()
        cleaned[str(key)] = text
    return json.dumps(cleaned, ensure_ascii=False, indent=4)


def _visible_triplet_sentence(lines: list[str]) -> str:
    """Phrase visible triplets like the update_memory few-shot, without dumping a JSON template."""
    facts = [str(line).strip() for line in lines if str(line).strip()]
    if not facts:
        return INCOMPLETE_MEMORY_SENTENCE
    if len(facts) == 1:
        return f"The triplets provide the information that {facts[0]}."
    return "The triplets provide the information that " + "; ".join(facts) + "."


def build_corrected_memory(
    subobjectives: list[str],
    current_hop_index: int,
    knowledge_triplets: Any,
    slot_note: str = "",
) -> str:
    """Rebuild memory from visible evidence only; no hidden gold answer or triplet is used."""
    lines = triplets_to_lines(knowledge_triplets)
    note = _clean(slot_note)
    visible_sentence = _visible_triplet_sentence(lines)
    corrected: dict[str, str] = {}
    for index, subobjective in enumerate(subobjectives):
        slot = str(index + 1)
        if index < current_hop_index:
            body = f"This subobjective is already completed: {_clean(subobjective)}."
        elif index == current_hop_index:
            body = visible_sentence
        else:
            body = INCOMPLETE_MEMORY_SENTENCE
        if note and index == current_hop_index:
            body = body + MEMORY_SLOT_NOTE_SEP + note
        corrected[slot] = body
    if not corrected:
        body = visible_sentence
        if note:
            body = body + MEMORY_SLOT_NOTE_SEP + note
        corrected["1"] = body
    return json.dumps(corrected, ensure_ascii=False, indent=4)


def _hop_slot_note(diagnosis: dict[str, Any], hop: dict[str, Any]) -> str:
    """Semantic trainer hints for the current hop slot; used by reasoning/reverse, not update_memory."""
    errors = diagnosis.get("errors") or []
    parts: list[str] = []
    if "sufficient" in errors:
        if diagnosis.get("expected_sufficient") == "Yes":
            parts.append(
                "This hop's evidence is enough to answer; Sufficient=Yes with an answer grounded in the facts."
            )
        else:
            parts.append(
                "This hop's evidence is not enough to answer; Sufficient=No and Answer=Null."
            )
    if "backtrack" in errors:
        if diagnosis.get("expected_add") == "Yes":
            names = hop.get("gold_start_entity_names") or []
            extra = ": " + ", ".join(str(name) for name in names) + "." if names else "."
            parts.append("Need the hop-start entity again" + extra)
        else:
            parts.append("The correct next entity is already in the frontier; Add=No.")
    return " ".join(parts)


def _memory_schema_example(subobjectives: list[str], problems: list[str] | None = None) -> str:
    """Natural-language trainer hint for update_memory; do not dump a JSON object to copy."""
    steps = [_clean(item) for item in (subobjectives or []) if _clean(item)]
    problems = [str(item) for item in (problems or [])]
    lines = [
        "Write Memory in the same style as the example: one short sentence per subobjective.",
        (
            "If the current triplets support a subobjective, say what they show. "
            f'If they do not, write "{INCOMPLETE_MEMORY_SENTENCE}"'
        ),
        "Do not invent names or answers that are absent from the current Knowledge Triplets.",
    ]
    if steps:
        listed = "; ".join(steps)
        lines.append(f"Keep a sentence for every subobjective in order: {listed}.")
    if "memory_missing_subobjective_slots" in problems:
        lines.append("The previous Memory omitted a subobjective; do not drop any of them.")
    if "memory_not_json_object" in problems:
        lines.append("Keep the same Memory layout as the example, with one sentence per subobjective.")
    if "memory_missing_visible_head" in problems or "memory_missing_visible_tail" in problems:
        lines.append("The Memory must mention the entities that appear in the current triplets.")
    return " ".join(lines)


def _answer_depth_schema_example(sufficient: str, answer: str) -> str:
    answer_json = json.dumps("Null" if str(sufficient).lower() != "yes" else (answer or ""), ensure_ascii=False)
    reason = (
        "The visible evidence answers the question."
        if str(sufficient).lower() == "yes"
        else "The visible evidence does not yet answer the question."
    )
    return (
        "Output exactly this JSON shape. A must be an object with Sufficient and Answer; "
        "do not put the answer in A as a string; do not include Add.\n"
        "{\n"
        '    "A": {\n'
        f'        "Sufficient": "{sufficient}",\n'
        f"        \"Answer\": {answer_json}\n"
        "    },\n"
        f'    "R": "{reason}"\n'
        "}"
    )


def _reverse_schema_example(add: str) -> str:
    return (
        'Output exactly this JSON shape (must include "Add" and "Reason" only):\n'
        "{\n"
        f'    "Add": "{add}",\n'
        '    "Reason": "Explain whether a historical entity must be added."\n'
        "}"
    )


def _append_stage_line(stages: dict[str, list[str]], stage: str, line: str) -> None:
    header = "Training correction: solve the current hop again using only currently observed candidates and triplets."
    bucket = stages.setdefault(stage, [header])
    if line not in bucket:
        bucket.append(line)


def build_next_round_state(
    state: dict[str, Any],
    trace: dict[str, Any],
    diagnosis: dict[str, Any],
    hop: dict[str, Any],
) -> dict[str, Any]:
    next_state = deepcopy(state)
    visible_triplets = list(hop.get("prior_knowledge_triplets", [])) + triplets_to_lines(
        trace.get("knowledge_triplets", [])
    )
    next_state["memory"] = build_corrected_memory(
        hop.get("subobjectives", []),
        hop.get("hop_index", 0),
        visible_triplets,
        slot_note=_hop_slot_note(diagnosis, hop),
    )
    errors = diagnosis.get("errors") or []
    stages: dict[str, list[str]] = {}
    if "relation" in errors:
        _append_stage_line(
            stages,
            "relation",
            f"Select relation {hop.get('gold_relation')} because it is the verified relation for this hop.",
        )
    if "entity" in errors:
        observed_names = [
            name for entity_id, name in zip(
                trace.get("entity_selection", {}).get("candidate_entity_ids", []),
                trace.get("entity_selection", {}).get("candidate_entities", []),
            ) if entity_id in set(hop.get("gold_next_entity_ids", []))
        ]
        if observed_names:
            _append_stage_line(
                stages,
                "entity",
                "Keep the observed candidate branch: " + ", ".join(_unique(observed_names)) + ".",
            )
        else:
            _append_stage_line(
                stages,
                "entity",
                "The correct branch was not retained; return to the current hop start and redo relation/entity selection.",
            )
    if "memory" in errors:
        stages["memory"] = [
            _memory_schema_example(
                hop.get("subobjectives") or [],
                diagnosis.get("memory_problems") or [],
            )
        ]
    if "sufficient" in errors:
        expected_sufficient = diagnosis.get("expected_sufficient") or "No"
        expected_answer = diagnosis.get("expected_answer") or "Null"
        stages["answer"] = [_answer_depth_schema_example(expected_sufficient, expected_answer)]
    if "backtrack" in errors:
        expected_add = "Yes" if diagnosis.get("expected_add") == "Yes" else "No"
        stages["reverse"] = [_reverse_schema_example(expected_add)]
        if expected_add == "Yes":
            names = hop.get("gold_start_entity_names") or []
            stages["add"] = [
                "From Candidate Entities, select only: " + ", ".join(str(name) for name in names) + "."
                if names else "From Candidate Entities, select only the hop-start entity.",
            ]
        else:
            stages["add"] = [
                "Do not add extra historical entities. If this step runs, output an empty list.",
            ]
    next_state["stage_corrections"] = {stage: "\n".join(lines) for stage, lines in stages.items()}
    next_state["correction_context"] = "\n\n".join(
        next_state["stage_corrections"][stage]
        for stage in ("relation", "entity", "memory", "answer", "reverse", "add")
        if next_state["stage_corrections"].get(stage)
    )
    return next_state


def _common_memory_fields(hop: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    question = hop.get("question", "")
    topic_entities = hop.get("topic_entities", {})
    memory = strip_memory_slot_notes(trace.get("memory_after", ""))
    triplets = triplets_to_lines(trace.get("knowledge_triplets", []))
    current_subobjective = hop.get("current_subobjective", "")
    return {
        "dataset": hop.get("dataset", "webqsp"),
        "question_id": hop.get("question_id", ""),
        "parse_id": hop.get("parse_id", ""),
        "question": question,
        "masked_question": mask_question_with_entities(question, topic_entities),
        "subobjectives": list(hop.get("subobjectives", [])),
        "current_subobjective": current_subobjective,
        "depth": int(hop.get("depth", hop.get("hop_index", 0) + 1)),
        "topic_entities": dict(topic_entities),
        "memory": memory,
        "knowledge_triplets": triplets,
        "question_key": mask_question_with_entities(question, topic_entities),
        "state_key": build_reflection_state_key(
            "", memory, triplets, current_subobjective, trace.get("next_entity_names", [])
        ),
        "verified": True,
    }


def _build_stage_memory_bundle(
    hop: dict[str, Any],
    trace: dict[str, Any],
    diagnosis: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build only stage-level positive examples from one real, visible trace."""
    if not diagnosis.get("memory_ok"):
        return []
    common = _common_memory_fields(hop, trace)
    bundle: list[dict[str, Any]] = []

    if diagnosis.get("sufficient_ok"):
        answer_trace = trace.get("answer_depth", {})
        answer_item = dict(common)
        answer_item.update({
            "memory_type": ANSWER_DEPTH,
            "state_key": build_reflection_state_key(
                ANSWER_DEPTH, common["memory"], common["knowledge_triplets"],
                common["current_subobjective"], trace.get("next_entity_names", [])
            ),
            "output": {
                "A": {
                    "Sufficient": "Yes" if _clean(answer_trace.get("sufficient")).lower() == "yes" else "No",
                    "Answer": answer_trace.get("answer") or "Null",
                },
                "R": answer_trace.get("reason", ""),
            },
        })
        bundle.append(answer_item)

    reverse = trace.get("reverse", {})
    if reverse.get("invoked") and diagnosis.get("reverse_ok"):
        judge_item = dict(common)
        judge_item.update({
            "memory_type": JUDGE_REVERSE,
            "entities_to_retrieve": list(reverse.get("entities_to_retrieve", [])),
            "state_key": build_reflection_state_key(
                JUDGE_REVERSE, common["memory"], common["knowledge_triplets"],
                common["current_subobjective"], reverse.get("entities_to_retrieve", [])
            ),
            "output": {
                "Add": "Yes" if reverse.get("add") else "No",
                "Reason": reverse.get("reason", ""),
            },
        })
        bundle.append(judge_item)
        if reverse.get("add") and reverse.get("add_prompt_invoked"):
            add_item = dict(common)
            add_item.update({
                "memory_type": ADD_ENTITY,
                "reason": reverse.get("reason", ""),
                "candidate_entities": list(reverse.get("candidate_entities", [])),
                "state_key": build_reflection_state_key(
                    ADD_ENTITY, common["memory"], common["knowledge_triplets"],
                    common["current_subobjective"], reverse.get("candidate_entities", [])
                ),
                "output": list(reverse.get("added_entity_names", [])),
            })
            bundle.append(add_item)
    return bundle


def _reflection_memory_identity(item: dict[str, Any]) -> tuple[str, str, str, str]:
    """One slot per question hop and reflection stage; Memory wording is not part of identity."""
    return (
        str(item.get("parse_id") or ""),
        str(item.get("memory_type") or ""),
        str(item.get("depth", "")),
        str(item.get("current_subobjective") or ""),
    )


def upsert_reflection_memory_items(
    existing: list[dict[str, Any]] | None = None,
    incoming: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Keep insertion order of first-seen identities; later items replace earlier ones."""
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in list(existing or []) + list(incoming or []):
        merged[_reflection_memory_identity(item)] = item
    return list(merged.values())


def build_verified_hop_memory_bundle(
    hop: dict[str, Any],
    traces: dict[str, Any] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """After a hop converges, keep the latest positive example for each stage."""
    trace_list = traces if isinstance(traces, list) else [traces]
    bundle: list[dict[str, Any]] = []
    for trace in trace_list:
        diagnosis = trace.get("diagnosis") or diagnose_gold_hop_round(trace, hop)
        bundle = upsert_reflection_memory_items(
            bundle, _build_stage_memory_bundle(hop, trace, diagnosis)
        )
    return bundle


def run_gold_hop_round(
    hop: dict[str, Any],
    state: dict[str, Any],
    args: Any,
    model: Any,
    relation_memory_example: str = "",
) -> dict[str, Any]:
    """Execute one real PoG relation/entity/memory/reasoning/reverse round for a gold hop."""
    from freebase_func import (
        entity_condition_prune,
        entity_search_with_constraints,
        id2entity_name_or_type,
        reasoning,
        relation_search_prune,
        update_memory,
    )
    from utils import if_finish_list

    question = hop["question"]
    subobjectives = hop.get("subobjectives", [])
    entid_name = dict(hop.get("entity_names", {}))
    name_entid = {name: entity_id for entity_id, name in entid_name.items()}
    start_ids = list(hop.get("gold_start_entity_ids", []))
    corrections = dict(state.get("stage_corrections") or {})
    relation_context = "\n\n".join(
        part for part in [relation_memory_example, corrections.get("relation", "")] if part
    )

    relation_traces: list[dict[str, Any]] = []
    selected_relations: list[dict[str, Any]] = []
    token_totals = {"total": 0, "input": 0, "output": 0}
    for entity_id in start_ids:
        entity_name = entid_name.get(entity_id) or id2entity_name_or_type(entity_id)
        entid_name[entity_id] = entity_name
        name_entid[entity_name] = entity_id
        relations, token_num, rel_trace = relation_search_prune(
            entity_id, subobjectives, entity_name, [], -1, question, args,
            reflection_context=relation_context,
        )
        relation_traces.append(rel_trace)
        selected_relations.extend(relations)
        for key in token_totals:
            token_totals[key] += token_num.get(key, 0)

    ent_rel_ent_dict: dict[str, Any] = {entity_id: {} for entity_id in start_ids}
    total_entities_id: list[str] = []
    total_relations: list[str] = []
    total_candidates: list[str] = []
    total_topic_entities: list[str] = []
    total_head: list[bool] = []
    candidate_entity_ids: list[str] = []
    candidate_entity_names: list[str] = []
    for selected in selected_relations:
        parent = selected["entity"]
        relation = selected["relation"]
        head = bool(selected["head"])
        children = entity_search_with_constraints(parent, relation, head, question, args)
        direction = "head" if head else "tail"
        ent_rel_ent_dict.setdefault(parent, {}).setdefault(direction, {}).setdefault(relation, [])
        for child in children:
            child = str(child)
            child_name = entid_name.get(child)
            if not child_name:
                child_name = id2entity_name_or_type(child) if child.startswith(("m.", "g.")) else child
            entid_name[child] = child_name
            name_entid.setdefault(child_name, child)
            ent_rel_ent_dict[parent][direction][relation].append(child)
            total_entities_id.append(child)
            total_relations.append(relation)
            total_candidates.append(child_name)
            total_topic_entities.append(parent)
            total_head.append(head)
            candidate_entity_ids.append(child)
            candidate_entity_names.append(child_name)

    entity_result = entity_condition_prune(
        question, total_entities_id, total_relations, total_candidates,
        total_topic_entities, total_head, ent_rel_ent_dict, entid_name,
        name_entid, args, model, reflection_context=corrections.get("entity", ""),
    )
    (entity_flag, cluster_chains, selected_entity_ids, selected_pre_relations,
     selected_pre_heads, selected_dict, call_count, entity_tokens, entity_details) = entity_result
    for key in token_totals:
        token_totals[key] += entity_tokens.get(key, 0)

    memory_tokens, memory_trace = update_memory(
        question, subobjectives, selected_dict, entid_name, cluster_chains,
        state.get("memory_dir", ""), args,
        correction_context=corrections.get("memory", ""),
        memory_override=state.get("memory", ""),
        persist_memory=False,
    )
    for key in token_totals:
        token_totals[key] += memory_tokens.get(key, 0)

    reasoning_result = reasoning(
        question, subobjectives, selected_dict, entid_name, cluster_chains,
        state.get("memory_dir", ""), args,
        reflection_context=state.get("answer_depth_context", ""),
        memory_override=memory_trace.get("memory_after", ""),
        return_trace=True,
        correction_context=corrections.get("answer", ""),
    )
    response, answer, sufficient, reasoning_tokens, reasoning_trace = reasoning_result
    for key in token_totals:
        token_totals[key] += reasoning_tokens.get(key, 0)

    reverse_trace: dict[str, Any] = {"invoked": False}
    if _clean(sufficient).lower() != "yes":
        finish_result = if_finish_list(
            question, selected_entity_ids, {hop.get("depth", 1): ent_rel_ent_dict},
            entid_name, name_entid, state.get("memory_dir", ""), response,
            cluster_chains, args, model,
            memory_override=memory_trace.get("memory_after", ""),
            judge_reverse_context=state.get("judge_reverse_context", ""),
            add_entity_context=state.get("add_entity_context", ""),
            judge_correction=corrections.get("reverse", ""),
            add_correction=corrections.get("add", ""),
            return_trace=True,
        )
        _new_ids, _added_ids, reverse_calls, reverse_tokens, reverse_trace = finish_result
        for key in token_totals:
            token_totals[key] += reverse_tokens.get(key, 0)
        call_count += reverse_calls

    selected_entity_names = [entid_name.get(entity_id, entity_id) for entity_id in selected_entity_ids]
    triplets = triplets_to_lines(memory_trace.get("knowledge_triplets_prompt", ""))
    visible_values = selected_entity_ids + selected_entity_names + [
        entid_name.get(entity_id, entity_id) for entity_id in start_ids
    ]
    return {
        "round": state.get("round", 1),
        "relation_selection": {
            "traces": relation_traces,
            "selected_relations": [
                {"entity": item.get("entity"), "relation": item.get("relation"), "head": item.get("head")}
                for item in selected_relations
            ],
            "candidate_relations": _unique(
                relation for trace in relation_traces for relation in trace.get("candidate_relations", [])
            ),
            "llm_raw_outputs": [trace.get("llm_raw_output", "") for trace in relation_traces],
        },
        "entity_selection": {
            "success": bool(entity_flag),
            "candidate_entity_ids": candidate_entity_ids,
            "candidate_entities": candidate_entity_names,
            "selected_entity_ids": list(selected_entity_ids),
            "selected_entities": selected_entity_names,
            "details": entity_details,
        },
        "memory_before": memory_trace.get("memory_before", ""),
        "memory_after": memory_trace.get("memory_after", ""),
        "memory_trace": memory_trace,
        "knowledge_triplets": triplets,
        "visible_values": _unique(visible_values),
        "next_entity_names": selected_entity_names,
        "answer_depth": reasoning_trace,
        "reverse": reverse_trace,
        "call_count": call_count + len(start_ids) + 2,
        "token_num": token_totals,
        "training_correction_context": state.get("correction_context", ""),
        "stage_corrections": corrections,
    }


def train_gold_hop_reflection(
    hop: dict[str, Any],
    args: Any,
    model: Any,
    relation_memory_example: str = "",
    max_rounds: int | None = None,
) -> dict[str, Any]:
    max_rounds = max(1, int(max_rounds or getattr(args, "max_reflection_rounds", 3)))
    state = {
        "round": 1,
        "memory": hop.get("initial_memory", ""),
        "memory_dir": hop.get("memory_dir", ""),
        "correction_context": "",
        "stage_corrections": {},
    }
    traces: list[dict[str, Any]] = []
    for round_index in range(max_rounds):
        state["round"] = round_index + 1
        trace = run_gold_hop_round(hop, state, args, model, relation_memory_example)
        diagnosis = diagnose_gold_hop_round(trace, hop)
        trace["diagnosis"] = diagnosis
        traces.append(trace)
        if diagnosis["success"]:
            return {
                "success": True,
                "rounds": round_index + 1,
                "trace": trace,
                "traces": traces,
                "memory_bundle": build_verified_hop_memory_bundle(hop, traces),
            }
        state = build_next_round_state(state, trace, diagnosis, hop)
    return {
        "success": False,
        "rounds": max_rounds,
        "trace": traces[-1] if traces else {},
        "traces": traces,
        "memory_bundle": [],
    }
