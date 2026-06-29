"""Relation-memory helpers for PoG train/test modes."""

from __future__ import annotations

import os
import re
from typing import Any

from jsonl_io import append_jsonl_record
from reference_utils import mask_question_with_entities, read_jsonl_file, resolve_project_path


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEBQSP_TRAIN_PATH = os.path.join(PROJECT_ROOT, "data", "raw_train_set", "WebQSP.train.json")
CVT_LABEL = "[CVT_NODE]"


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


def append_relation_memory(path: str, item: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    append_jsonl_record(path, item)


def normalize_entity_label(entity_id: str, entity_name: str | None = None) -> tuple[str, str]:
    label = str(entity_name or entity_id or "")
    if is_mid(label):
        return CVT_LABEL, "cvt_or_unnamed_mid"
    return label, "named_entity"


def build_question_key(question: str, topic_entity: dict[str, str] | None = None) -> str:
    return mask_question_with_entities(question, topic_entity or {})


def build_state_key(
    depth: int,
    node_type: str,
    incoming_relation: str,
    previous_relations: list[str],
    candidate_relation: str = "",
    candidate_relations: list[str] | None = None,
) -> str:
    relation_sample = "; ".join((candidate_relations or [])[:20])
    return (
        f"Depth: {depth}; "
        f"Node type: {node_type}; "
        f"Incoming relation: {incoming_relation or 'NONE'}; "
        f"Previous relations: {' -> '.join(previous_relations) if previous_relations else 'NONE'}; "
        f"Candidate relation: {candidate_relation or 'UNKNOWN'}; "
        f"Candidate relations: {relation_sample}"
    )


def make_memory_item(
    episode: dict[str, Any],
    depth: int,
    entity_id: str,
    entity_name: str,
    incoming_relation: str,
    previous_relations: list[str],
    candidate_relation: str,
    gold_relation: str,
    label: str,
    selected_by_model: bool,
    gold_relation_in_candidates: bool,
    candidate_relations: list[str],
    llm_raw_output: str,
) -> dict[str, Any]:
    entity_label, node_type = normalize_entity_label(entity_id, entity_name)
    question = episode["RawQuestion"]
    question_key = build_question_key(question, episode.get("topic_entity", {}))
    state_key = build_state_key(
        depth=depth,
        node_type=node_type,
        incoming_relation=incoming_relation,
        previous_relations=previous_relations,
        candidate_relation=candidate_relation,
        candidate_relations=candidate_relations,
    )
    return {
        "dataset": episode.get("dataset", "webqsp"),
        "question_id": episode.get("question_id", ""),
        "parse_id": episode.get("parse_id", ""),
        "question": question,
        "masked_question": question_key,
        "depth": depth,
        "hop_index": depth - 1,
        "entity_id": entity_id,
        "entity_label": entity_label,
        "node_type": node_type,
        "incoming_relation": incoming_relation,
        "previous_relations": list(previous_relations),
        "candidate_relation": candidate_relation,
        "gold_relation": gold_relation,
        "label": label,
        "selected_by_model": selected_by_model,
        "gold_relation_in_candidates": gold_relation_in_candidates,
        "candidate_relations": list(candidate_relations),
        "question_key": question_key,
        "state_key": state_key,
        "llm_raw_output": llm_raw_output,
    }


def current_state_key_from_args(args: Any, entity_id: str, entity_name: str, total_relations: list[str]) -> str:
    _, node_type = normalize_entity_label(entity_id, entity_name)
    return build_state_key(
        depth=int(getattr(args, "current_relation_depth", 0) or 0),
        node_type=node_type,
        incoming_relation=getattr(args, "current_incoming_relation", "") or "",
        previous_relations=list(getattr(args, "current_previous_relations", []) or []),
        candidate_relation="",
        candidate_relations=total_relations,
    )


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
        and item.get("candidate_relation") in candidate_set
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
        "Relation Memory:",
        "Use these retrieved training memories as weak relation-selection priors. Each memory keeps its label.",
    ]
    for score, item in selected:
        shown_relations = (item.get("candidate_relations") or [])[:relation_limit]
        candidate_text = "; ".join(shown_relations)
        block = [
            f"- Label: {item.get('label', '')}",
            f"  Training question: {item.get('question', '')}",
            f"  Relation: {item.get('candidate_relation', '')}",
            (
                "  State: "
                f"depth={item.get('depth', '')}; "
                f"node_type={item.get('node_type', '')}; "
                f"incoming_relation={item.get('incoming_relation', '')}; "
                f"previous_relations={item.get('previous_relations', [])}"
            ),
        ]
        if candidate_text:
            block.append(f"  Candidate relations shown: {candidate_text}")
        block.append(f"  Retrieval score: {score:.4f}")
        candidate_lines = lines + block
        if estimate_token_count("\n".join(candidate_lines)) > token_budget:
            break
        lines.extend(block)
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def estimate_token_count(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))
