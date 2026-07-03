"""Relation-memory helpers for PoG train/test modes."""

from __future__ import annotations

import os
import random
import re
from typing import Any

from jsonl_io import append_jsonl_record
from reference_utils import mask_question_with_entities, read_jsonl_file, resolve_project_path


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEBQSP_TRAIN_PATH = os.path.join(PROJECT_ROOT, "data", "raw_train_set", "WebQSP.train.json")
CVT_LABEL = "[CVT_NODE]"
MEMORY_PROMPT_RELATION_LIMIT = 10


def normalize_mid(value: Any) -> str:
    if value is None:
        return ""
    value = str(value).strip().strip("<>")
    value = value.replace("http://rdf.freebase.com/ns/", "")
    if value.startswith("ns:"):
        value = value[3:]
    if value.startswith(":"):
        value = value[1:]
    return value


def is_mid(value: Any) -> bool:
    value = normalize_mid(value)
    return value.startswith("m.") or value.startswith("g.")


def parse_list_arg(value: Any, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[\s,]+", str(value).strip())
    return [str(item).strip() for item in items if str(item).strip()]


def should_use_relation_memory_at_stage(args: Any, stage: str) -> bool:
    if getattr(args, "relation_memory_mode", "none") != "prompt":
        return False
    stages = parse_list_arg(getattr(args, "relation_memory_stages", "relation"), ["relation"])
    stages = [stage_name.lower() for stage_name in stages]
    if not stages or "none" in stages:
        return False
    if "all" in stages:
        return stage == "relation"
    return stage in stages


def load_webqsp_train_episodes(path: str = WEBQSP_TRAIN_PATH) -> list[dict[str, Any]]:
    import json

    if path.endswith(".jsonl"):
        data = read_jsonl_file(path)
        if len(data) == 1 and isinstance(data[0], dict) and "Questions" in data[0]:
            data = data[0]
        else:
            wrapped = []
            for item in data:
                if isinstance(item, dict) and "Questions" in item:
                    wrapped.append(item)
            data = wrapped[0] if wrapped else {"Questions": []}
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

    episodes: list[dict[str, Any]] = []
    for question in data.get("Questions", []):
        question_id = question.get("QuestionId", "")
        raw_question = question.get("RawQuestion", "")
        for parse in question.get("Parses", []):
            chain = [str(rel) for rel in (parse.get("InferentialChain") or []) if str(rel).strip()]
            topic_mid = normalize_mid(parse.get("TopicEntityMid"))
            topic_name = parse.get("TopicEntityName") or parse.get("PotentialTopicEntityMention") or topic_mid
            if not raw_question or not chain or not is_mid(topic_mid):
                continue
            answers = []
            for answer in parse.get("Answers") or []:
                answer_id = normalize_mid(answer.get("AnswerArgument"))
                answers.append(
                    {
                        "answer_id": answer_id,
                        "answer": answer.get("EntityName") or answer_id,
                    }
                )
            episodes.append(
                {
                    "dataset": "webqsp",
                    "question_id": question_id,
                    "parse_id": parse.get("ParseId", ""),
                    "RawQuestion": raw_question,
                    "topic_entity": {topic_mid: topic_name},
                    "gold_relation_path": chain,
                    "constraints": list(parse.get("Constraints") or []),
                    "time": parse.get("Time"),
                    "order": parse.get("Order"),
                    "sparql": parse.get("Sparql", ""),
                    "gold_answers": answers,
                }
            )
    return episodes


def load_relation_memory(path: str) -> list[dict[str, Any]]:
    if not path:
        return []
    path = resolve_project_path(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Relation memory file not found: {path}")
    return read_jsonl_file(path)


def count_relation_memory_labels(path: str) -> dict[str, int]:
    counts = {
        "positive": 0,
        "missed_positive": 0,
        "negative": 0,
        "total": 0,
    }
    if not path or not os.path.exists(path):
        return counts
    for item in load_relation_memory(path):
        label = str(item.get("label", "")).strip()
        if label in counts:
            counts[label] += 1
        counts["total"] += 1
    return counts


def append_relation_memory(path: str, item: dict[str, Any], *, for_test: bool = True) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    record = export_test_memory_item(item) if for_test else dict(item)
    append_jsonl_record(path, record)


def normalize_entity_label(entity_id: str, entity_name: str | None = None) -> tuple[str, str]:
    label = str(entity_name or entity_id or "")
    if is_mid(label):
        return CVT_LABEL, "cvt_or_unnamed_mid"
    return label, "named_entity"


def entity_marker(entity_id: str, entity_name: str | None = None) -> str:
    _, node_type = normalize_entity_label(entity_id, entity_name)
    if node_type == "cvt_or_unnamed_mid":
        return entity_id
    label = str(entity_name or "").strip()
    return label or entity_id


def get_entity_labels(item: dict[str, Any]) -> list[str]:
    if "entity_labels" in item:
        labels = item.get("entity_labels") or []
        return [str(label).strip() for label in labels if str(label).strip()]
    legacy_label = str(item.get("entity_label", "")).strip()
    legacy_id = str(item.get("entity_id", "")).strip()
    if legacy_label == CVT_LABEL and legacy_id:
        return [legacy_id]
    if legacy_label:
        return [legacy_label]
    if legacy_id:
        return [legacy_id]
    return []


def build_question_key(question: str, topic_entity: dict[str, str] | None = None) -> str:
    return mask_question_with_entities(question, topic_entity or {})


def get_picked_relations(item: dict[str, Any]) -> list[str]:
    value = item.get("picked_relations")
    if value is None:
        value = item.get("candidate_relation", [])
    if isinstance(value, list):
        return [str(relation).strip() for relation in value if str(relation).strip()]
    relation = str(value or "").strip()
    return [relation] if relation else []


def format_picked_relations(picked_relations: str | list[str] | None) -> str:
    relations = picked_relations if isinstance(picked_relations, list) else get_picked_relations({"picked_relations": picked_relations})
    return "; ".join(relations) or "UNKNOWN"


def memory_matches_candidate_set(picked_relations: Any, candidate_set: set[str]) -> bool:
    relations = picked_relations if isinstance(picked_relations, list) else get_picked_relations({"picked_relations": picked_relations})
    return any(relation in candidate_set for relation in relations)


def memory_gold_matches_candidate_set(item: dict[str, Any], candidate_set: set[str]) -> bool:
    gold_relation = str(item.get("gold_relation", "")).strip()
    return bool(gold_relation and gold_relation in candidate_set)


def build_state_key(
    depth: int,
    incoming_relation: str,
    previous_relations: list[str],
    picked_relations: list[str] | None = None,
    candidate_relations: list[str] | None = None,
    entity_labels: list[str] | None = None,
    include_candidate_relations: bool = True,
) -> str:
    entity_sample = "; ".join((entity_labels or [])[:20])
    parts = [
        f"Depth: {depth}",
        f"Entities: {entity_sample or 'UNKNOWN'}",
        f"Incoming relation: {incoming_relation or 'NONE'}",
        f"Previous relations: {' -> '.join(previous_relations) if previous_relations else 'NONE'}",
        f"Picked relations: {format_picked_relations(picked_relations or [])}",
    ]
    if include_candidate_relations:
        relation_sample = "; ".join((candidate_relations or [])[:20])
        parts.append(f"Candidate relations: {relation_sample}")
    return "; ".join(parts) + ";"


def export_test_memory_item(item: dict[str, Any]) -> dict[str, Any]:
    exported = dict(item)
    exported.pop("candidate_relations", None)
    exported["state_key"] = build_state_key(
        depth=int(exported.get("depth") or 0),
        incoming_relation=str(exported.get("incoming_relation") or ""),
        previous_relations=list(exported.get("previous_relations") or []),
        picked_relations=get_picked_relations(exported),
        entity_labels=get_entity_labels(exported),
        include_candidate_relations=False,
    )
    return exported


def memory_merge_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("parse_id", ""),
        item.get("depth"),
        tuple(item.get("previous_relations") or []),
        item.get("incoming_relation", ""),
        tuple(get_picked_relations(item)),
        item.get("label", ""),
        item.get("gold_relation", ""),
    )


def merge_memory_items(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    merged_labels = sorted(set(get_entity_labels(base) + get_entity_labels(other)))
    merged_candidates = sorted(set(base.get("candidate_relations") or []) | set(other.get("candidate_relations") or []))
    merged_retrieved = sorted(set(base.get("retrieved_relations") or []) | set(other.get("retrieved_relations") or []))
    base["entity_labels"] = merged_labels
    base["candidate_relations"] = merged_candidates
    base["retrieved_relations"] = merged_retrieved
    base["gold_relation_in_candidates"] = bool(base.get("gold_relation_in_candidates")) or bool(other.get("gold_relation_in_candidates"))
    base["gold_relation_in_retrieved"] = bool(base.get("gold_relation_in_retrieved")) or bool(other.get("gold_relation_in_retrieved"))
    base["state_key"] = build_state_key(
        depth=int(base.get("depth") or 0),
        incoming_relation=str(base.get("incoming_relation") or ""),
        previous_relations=list(base.get("previous_relations") or []),
        picked_relations=get_picked_relations(base),
        candidate_relations=merged_candidates,
        entity_labels=merged_labels,
    )
    return base


class TrainRelationMemoryBuffer:
    def __init__(self) -> None:
        self._items: dict[tuple[Any, ...], dict[str, Any]] = {}

    def add(self, item: dict[str, Any]) -> None:
        key = memory_merge_key(item)
        if key in self._items:
            self._items[key] = merge_memory_items(self._items[key], item)
            return
        self._items[key] = item

    def flush(self, memory_output_path: str) -> None:
        if not self._items:
            return

        items = list(self._items.values())
        positive_items = [item for item in items if item.get("label") == "positive"]
        if positive_items:
            merged = positive_items[0]
            for item in positive_items[1:]:
                merged = merge_memory_items(merged, item)
            append_relation_memory(memory_output_path, merged)
            self._items.clear()
            return

        for item in items:
            append_relation_memory(memory_output_path, item)
        self._items.clear()


def make_memory_item(
    episode: dict[str, Any],
    depth: int,
    entity_labels: list[str],
    incoming_relation: str,
    previous_relations: list[str],
    picked_relations: list[str],
    gold_relation: str,
    label: str,
    selected_by_model: bool,
    gold_relation_in_candidates: bool,
    gold_relation_in_retrieved: bool,
    candidate_relations: list[str],
    retrieved_relations: list[str],
    llm_raw_output: str,
) -> dict[str, Any]:
    entity_labels = sorted({str(label).strip() for label in entity_labels if str(label).strip()})
    question = episode["RawQuestion"]
    question_key = build_question_key(question, episode.get("topic_entity", {}))
    state_key = build_state_key(
        depth=depth,
        incoming_relation=incoming_relation,
        previous_relations=previous_relations,
        picked_relations=picked_relations,
        candidate_relations=candidate_relations,
        entity_labels=entity_labels,
    )
    return {
        "dataset": episode.get("dataset", "webqsp"),
        "question_id": episode.get("question_id", ""),
        "parse_id": episode.get("parse_id", ""),
        "question": question,
        "masked_question": question_key,
        "depth": depth,
        "hop_index": depth - 1,
        "entity_labels": entity_labels,
        "incoming_relation": incoming_relation,
        "previous_relations": list(previous_relations),
        "picked_relations": list(picked_relations),
        "gold_relation": gold_relation,
        "label": label,
        "selected_by_model": selected_by_model,
        "gold_relation_in_candidates": gold_relation_in_candidates,
        "gold_relation_in_retrieved": gold_relation_in_retrieved,
        "candidate_relations": list(candidate_relations),
        "retrieved_relations": list(retrieved_relations),
        "question_key": question_key,
        "state_key": state_key,
        "llm_raw_output": llm_raw_output,
    }


def append_train_relation_memories(
    buffer: TrainRelationMemoryBuffer,
    episode: dict[str, Any],
    depth: int,
    entity_id: str,
    entity_name: str,
    incoming_relation: str,
    previous_relations: list[str],
    gold_relation: str,
    candidate_relations: list[str],
    retrieved_relations: list[str],
    selected_relations: list[dict[str, Any]],
    llm_raw_output: str,
    write_missed_positive: bool,
) -> None:
    selected_relation_names = sorted(
        {
            str(item.get("relation", "")).strip()
            for item in selected_relations
            if str(item.get("relation", "")).strip()
        }
    )
    gold_in_candidates = gold_relation in candidate_relations
    gold_in_retrieved = gold_relation in retrieved_relations
    gold_selected = gold_relation in selected_relation_names
    entity_labels = [entity_marker(entity_id, entity_name)]

    if gold_selected:
        buffer.add(
            make_memory_item(
                episode=episode,
                depth=depth,
                entity_labels=entity_labels,
                incoming_relation=incoming_relation,
                previous_relations=previous_relations,
                picked_relations=[gold_relation],
                gold_relation=gold_relation,
                label="positive",
                selected_by_model=True,
                gold_relation_in_candidates=gold_in_candidates,
                gold_relation_in_retrieved=gold_in_retrieved,
                candidate_relations=candidate_relations,
                retrieved_relations=retrieved_relations,
                llm_raw_output=llm_raw_output,
            ),
        )
        return

    if not selected_relation_names:
        return

    if gold_in_retrieved and write_missed_positive:
        label = "missed_positive"
    else:
        label = "negative"

    buffer.add(
        make_memory_item(
            episode=episode,
            depth=depth,
            entity_labels=entity_labels,
            incoming_relation=incoming_relation,
            previous_relations=previous_relations,
            picked_relations=selected_relation_names,
            gold_relation=gold_relation,
            label=label,
            selected_by_model=True,
            gold_relation_in_candidates=gold_in_candidates,
            gold_relation_in_retrieved=gold_in_retrieved,
            candidate_relations=candidate_relations,
            retrieved_relations=retrieved_relations,
            llm_raw_output=llm_raw_output,
        ),
    )


def current_state_key_from_args(args: Any, entity_id: str, entity_name: str, total_relations: list[str]) -> str:
    return build_state_key(
        depth=int(getattr(args, "current_relation_depth", 0) or 0),
        incoming_relation=getattr(args, "current_incoming_relation", "") or "",
        previous_relations=list(getattr(args, "current_previous_relations", []) or []),
        picked_relations=[],
        candidate_relations=total_relations,
        entity_labels=[entity_marker(entity_id, entity_name)],
    )


def format_relation_output_for_prompt(relations: list[str]) -> str:
    cleaned = [str(relation).strip() for relation in relations if str(relation).strip()]
    if not cleaned:
        return "[]"
    return "[" + ",".join(repr(relation) for relation in cleaned) + "]"


def candidate_relations_for_correct_memory_example(item: dict[str, Any], relation_limit: int) -> list[str]:
    gold_relation = str(item.get("gold_relation", "")).strip()
    if not gold_relation:
        return []
    relation_limit = MEMORY_PROMPT_RELATION_LIMIT if relation_limit <= 0 else min(relation_limit, MEMORY_PROMPT_RELATION_LIMIT)
    relation_pool = [
        str(relation).strip()
        for relation in (item.get("retrieved_relations") or [])
        if str(relation).strip()
    ]
    if gold_relation not in relation_pool:
        relation_pool.append(gold_relation)

    seen: set[str] = set()
    relations: list[str] = []
    for relation in relation_pool:
        if relation in seen:
            continue
        seen.add(relation)
        relations.append(relation)

    if len(relations) <= relation_limit:
        return shuffle_memory_candidate_relations(item, relations)

    limited = relations[:relation_limit]
    if gold_relation not in limited:
        limited[-1] = gold_relation
    return shuffle_memory_candidate_relations(item, limited)


def shuffle_memory_candidate_relations(item: dict[str, Any], relations: list[str]) -> list[str]:
    if len(relations) <= 1:
        return relations
    seed_text = "|".join(
        [
            str(item.get("question_id", "")),
            str(item.get("parse_id", "")),
            str(item.get("question", "")),
            str(item.get("gold_relation", "")),
            ";".join(relations),
        ]
    )
    shuffled = list(relations)
    random.Random(seed_text).shuffle(shuffled)
    return shuffled


def format_memory_example_for_prompt(item: dict[str, Any], relation_limit: int) -> list[str]:
    gold_relation = str(item.get("gold_relation", "")).strip()
    shown_relations = candidate_relations_for_correct_memory_example(item, relation_limit)
    if not gold_relation or gold_relation not in shown_relations:
        return []

    block = [
        f"Q: {item.get('question', '')}",
        f"Topic Entity: {'; '.join(get_entity_labels(item)) or 'UNKNOWN'}",
        f"Relations: {'; '.join(shown_relations)}",
        "The output is:",
        format_relation_output_for_prompt([gold_relation]),
    ]
    return block


def relation_memory_context(
    memory_bank: list[dict[str, Any]],
    question: str,
    entity_id: str,
    entity_name: str,
    total_relations: list[str],
    args: Any,
    model: Any,
) -> str:
    if not memory_bank or model is None:
        return ""

    candidate_set = set(total_relations)
    labels = set(parse_list_arg(getattr(args, "memory_labels", ""), ["positive", "missed_positive", "negative"]))
    filtered = [
        item for item in memory_bank
        if item.get("label") in labels
        and memory_gold_matches_candidate_set(item, candidate_set)
    ]
    if not filtered:
        return ""

    from sentence_transformers import util

    topic_entity = getattr(args, "current_topic_entity", {}) or {}
    question_key = build_question_key(question, topic_entity)
    state_key = current_state_key_from_args(args, entity_id, entity_name, total_relations)
    strategy = getattr(args, "memory_retrieval_strategy", "hybrid")
    state_weight = float(getattr(args, "memory_state_weight", 0.5))

    question_emb = model.encode(question_key)
    state_emb = model.encode(state_key)
    memory_question_emb = model.encode([item.get("question_key") or item.get("masked_question") or item.get("question", "") for item in filtered])
    memory_state_emb = model.encode([item.get("state_key", "") for item in filtered])
    question_scores = util.dot_score(question_emb, memory_question_emb)[0].cpu().tolist()
    state_scores = util.dot_score(state_emb, memory_state_emb)[0].cpu().tolist()

    scored = []
    for index, item in enumerate(filtered):
        if strategy == "question":
            score = question_scores[index]
        elif strategy == "state":
            score = state_scores[index]
        else:
            score = (1 - state_weight) * question_scores[index] + state_weight * state_scores[index]
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    top_k = max(0, int(getattr(args, "relation_memory_top_k", 4)))
    selected = scored[:top_k] if top_k else []
    if not selected:
        return ""

    relation_limit = max(0, int(getattr(args, "memory_candidate_relation_limit", 8)))
    token_budget = max(1, int(getattr(args, "memory_prompt_token_budget", 600)))
    lines = [
        "Use these previous relation-selection examples as weak guidance. Follow the current question and current Relations list.",
    ]
    for _score, item in selected:
        block = format_memory_example_for_prompt(item, relation_limit)
        if not block:
            continue
        candidate_lines = lines + block
        if estimate_token_count("\n".join(candidate_lines)) > token_budget:
            break
        lines.extend(block)
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def estimate_token_count(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))
