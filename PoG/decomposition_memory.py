"""Decomposition-memory helpers for PoG train/test modes."""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Any

from jsonl_io import append_jsonl_record
from reference_utils import mask_question_with_entities, read_jsonl_file, resolve_project_path


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def estimate_token_count(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))


def build_question_key(question: str, topic_entity: dict[str, str] | None = None) -> str:
    return mask_question_with_entities(question, topic_entity or {})


def parse_planning_steps(text: str) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(candidate)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except Exception:
                pass

    steps: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r"^[-*\d.)\s]+", "", line).strip()
        if line:
            steps.append(line.strip("'\""))
    return steps


def format_topic_entities(topic_entity: dict[str, str]) -> str:
    if not topic_entity:
        return "None"
    return "; ".join(f"{name} ({mid})" for mid, name in topic_entity.items())


def format_constraints(constraints: list[dict[str, Any]]) -> str:
    if not constraints:
        return ""
    return json.dumps(constraints, ensure_ascii=False)


def get_episode_question(episode: dict[str, Any]) -> str:
    return str(episode.get("RawQuestion") or episode.get("question") or "")


def optional_prompt_section(title: str, value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return f"\n{title}:\n{value}\n"


def build_gold_planning_prompt(episode: dict[str, Any]) -> str:
    optional_sections = "".join(
        [
            optional_prompt_section("Constraints", episode.get("constraints") or []),
            optional_prompt_section("Time", episode.get("time")),
            optional_prompt_section("Order", episode.get("order")),
        ]
    )
    return f"""You are building high-quality planning memory for knowledge graph question answering.

Given a question and its gold SPARQL query, generate the correct planning steps needed to answer the question over the knowledge graph.

Requirements:
1. Each step should correspond to one KG retrieval action, filter condition, ranking/time/order constraint, or final answer selection.
2. The steps should be concise and readable natural language.
3. Preserve the logic of the SPARQL query, including relation hops, constraints, ordering, limits, and time conditions.
4. Do not mention implementation details such as PREFIX lines.
5. Output only a Python list of strings. Do not output explanations or markdown.

Question:
{get_episode_question(episode)}

Topic Entities:
{format_topic_entities(episode.get("topic_entity", {}) or {})}

Gold SPARQL:
{episode.get("sparql", "")}

Answers:
{json.dumps(episode.get("gold_answers", []) or [], ensure_ascii=False)}
{optional_sections}"""


def make_decomposition_memory_item(
    episode: dict[str, Any],
    gold_subobjectives: list[str],
    llm_raw_output: str,
) -> dict[str, Any]:
    question = get_episode_question(episode)
    topic_entity = dict(episode.get("topic_entity") or {})
    return {
        "memory_type": "decomposition_plan",
        "dataset": episode.get("dataset", "webqsp"),
        "question_id": episode.get("question_id", ""),
        "parse_id": episode.get("parse_id", ""),
        "question": question,
        "masked_question": build_question_key(question, topic_entity),
        "topic_entity": topic_entity,
        "gold_sparql": episode.get("sparql", ""),
        "gold_relation_path": list(episode.get("gold_relation_path") or []),
        "gold_subobjectives": list(gold_subobjectives),
        "constraints": list(episode.get("constraints") or []),
        "time": episode.get("time"),
        "order": episode.get("order"),
        "gold_answers": list(episode.get("gold_answers") or []),
        "llm_raw_output": llm_raw_output,
    }


def append_decomposition_memory(path: str, item: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    append_jsonl_record(path, item)


def load_decomposition_memory(path: str) -> list[dict[str, Any]]:
    if not path:
        return []
    path = resolve_project_path(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Decomposition memory file not found: {path}")
    return read_jsonl_file(path)


def count_decomposition_memory(path: str) -> int:
    if not path or not os.path.exists(path):
        return 0
    return len(load_decomposition_memory(path))


def should_use_decomposition_memory(args: Any) -> bool:
    return getattr(args, "decomposition_memory_mode", "none") == "prompt"


def select_decomposition_memory_items(
    memory_bank: list[dict[str, Any]],
    question: str,
    topic_entity: dict[str, str],
    args: Any,
    model: Any,
) -> list[tuple[float, dict[str, Any]]]:
    if not memory_bank:
        return []
    top_k = min(max(0, int(getattr(args, "decomposition_memory_top_k", 4))), len(memory_bank))
    if top_k <= 0:
        return []
    if model is None:
        return [(0.0, item) for item in memory_bank[:top_k]]

    from sentence_transformers import util

    question_key = build_question_key(question, topic_entity)
    memory_questions = [item.get("masked_question") or item.get("question", "") for item in memory_bank]
    query_emb = model.encode(question_key)
    memory_emb = model.encode(memory_questions)
    scores = util.dot_score(query_emb, memory_emb)[0].cpu().tolist()
    ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [(scores[i], memory_bank[i]) for i in ranked_indices[:top_k]]


def format_memory_item_for_prompt(score: float, item: dict[str, Any]) -> str:
    lines = ["Q: " + str(item.get("question", ""))]
    topic_names = list((item.get("topic_entity") or {}).values())
    if topic_names:
        lines.append("Topic Entity: " + "; ".join(str(name) for name in topic_names))

    steps = [str(step).strip() for step in (item.get("gold_subobjectives") or []) if str(step).strip()]
    if steps:
        lines.append("Correct Planning Steps:")
        lines.extend(f"{index + 1}. {step}" for index, step in enumerate(steps))
        lines.append("Output: " + repr(steps))
    return "\n".join(lines)


def decomposition_memory_context(
    memory_bank: list[dict[str, Any]],
    question: str,
    topic_entity: dict[str, str],
    args: Any,
    model: Any,
) -> str:
    if not should_use_decomposition_memory(args):
        return ""
    selected = select_decomposition_memory_items(memory_bank, question, topic_entity, args, model)
    if not selected:
        return ""

    token_budget = max(1, int(getattr(args, "decomposition_memory_prompt_token_budget", 800)))
    lines = [
        "Here are related examples from training questions.",
        "Use their Output format and planning style as guidance, but answer the current question only.",
    ]
    for score, item in selected:
        block = format_memory_item_for_prompt(score, item)
        candidate = lines + ["", block]
        if estimate_token_count("\n".join(candidate)) > token_budget:
            break
        lines.extend(["", block])
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)
