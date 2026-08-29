"""Episode-level orchestration for PoG decomposition, relation, and reflection memory training."""

from __future__ import annotations

import os
from typing import Any

from tqdm import tqdm

from decomposition_memory import (
    build_gold_planning_prompt,
    count_decomposition_memory,
    make_decomposition_memory_item,
    parse_planning_steps,
)
from jsonl_io import append_jsonl_record
from output_paths import (
    DECOMPOSITION_MEMORY_FILENAME,
    FAILED_HOP_TRACES_FILENAME,
    REFLECTION_MEMORY_FILENAME,
    RELATION_MEMORY_FILENAME,
    append_progress,
    default_memory_output_dir,
    filter_jsonl_by_parse_id,
    load_parse_ids_from_jsonl,
    load_progress,
    update_run_meta,
)
from relation_memory import (
    count_relation_memory_labels,
    format_memory_example_for_prompt,
    make_gold_relation_memory_item,
)
from reflection_memory import (
    build_corrected_memory,
    count_reflection_memory,
    train_gold_hop_reflection,
)


def should_train_relation_memory(args: Any) -> bool:
    return getattr(args, "train_memory_family", "relation_choice") in {"relation_choice", "all"}


def should_train_decomposition_memory(args: Any) -> bool:
    return getattr(args, "train_memory_family", "relation_choice") in {"decomposition", "all"}


def should_train_reflection_memory(args: Any) -> bool:
    return getattr(args, "train_memory_family", "relation_choice") in {"reflection", "all"}


def generate_train_subobjectives(episode: dict[str, Any], args: Any) -> tuple[list[str], str, dict[str, int]]:
    """Generate the train plan once; reflection/relation memories reuse the same real subobjectives."""
    from utils import run_llm

    prompt = build_gold_planning_prompt(episode)
    response, token_num = run_llm(
        prompt,
        args.temperature_reasoning,
        args.max_length,
        args.opeani_api_keys,
        args.LLM_type,
        False,
        False,
    )
    return parse_planning_steps(response), response, token_num


def validate_train_subobjectives(
    subobjectives: list[str], gold_relation_path: list[str]
) -> tuple[list[str], dict[str, Any]]:
    """Ensure every gold hop has an aligned non-empty subobjective without discarding an LLM plan."""
    cleaned = [str(item).strip() for item in subobjectives if str(item).strip()]
    generated_count = len(cleaned)
    if not cleaned:
        cleaned = [f"Retrieve the information needed through relation {relation}." for relation in gold_relation_path]
    if len(cleaned) < len(gold_relation_path):
        cleaned.extend(
            f"Continue the current reasoning through relation {gold_relation_path[index]}."
            for index in range(len(cleaned), len(gold_relation_path))
        )
    elif len(cleaned) > len(gold_relation_path) and gold_relation_path:
        # Preserve all generated content by merging overflow steps into the last aligned hop.
        cleaned = cleaned[: len(gold_relation_path) - 1] + ["; ".join(cleaned[len(gold_relation_path) - 1 :])]
    return cleaned, {
        "valid": bool(cleaned) and len(cleaned) == len(gold_relation_path),
        "generated_count": generated_count,
        "aligned_count": len(cleaned),
        "gold_hop_count": len(gold_relation_path),
        "used_fallback": generated_count == 0,
    }


def align_subobjectives_to_gold_hops(
    subobjectives: list[str], gold_relation_path: list[str]
) -> list[dict[str, Any]]:
    return [
        {
            "hop_index": index,
            "depth": index + 1,
            "gold_relation": relation,
            "subobjective": subobjectives[index] if index < len(subobjectives) else "",
        }
        for index, relation in enumerate(gold_relation_path)
    ]


def _is_mid(value: Any) -> bool:
    return str(value).startswith(("m.", "g."))


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").split()).strip("[]{}()\"'")


def execute_gold_step(
    entity_ids: list[str],
    relation: str,
    frontier_limit: int,
    args: Any | None = None,
    question: str = "",
) -> dict[str, Any]:
    """Execute a gold relation without an LLM, trying the forward direction then the real reverse edge."""
    from freebase_func import entity_search, entity_search_with_constraints

    edges: list[dict[str, Any]] = []
    next_entities: list[str] = []
    for entity_id in sorted(set(entity_ids)):
        if not _is_mid(entity_id):
            continue
        search = entity_search_with_constraints if (
            args is not None and getattr(args, "constraint_pushdown", "off") == "on"
        ) else None
        if search:
            children = search(entity_id, relation, True, question, args)
        else:
            children = entity_search(entity_id, relation, True)
        head = True
        if not children:
            if search:
                children = search(entity_id, relation, False, question, args)
            else:
                children = entity_search(entity_id, relation, False)
            head = False
        for child in children:
            child = str(child)
            edges.append({
                "parent_entity_id": entity_id,
                "child_entity_id": child,
                "relation": relation,
                "head": head,
                "direction": "forward" if head else "reverse",
            })
            if child not in next_entities:
                next_entities.append(child)
    return {
        "relation": relation,
        "edges": edges,
        "next_entities": next_entities[: max(1, int(frontier_limit))],
    }


def _entity_name(entity_id: str, cache: dict[str, str]) -> str:
    if entity_id in cache:
        return cache[entity_id]
    if not _is_mid(entity_id):
        cache[entity_id] = str(entity_id)
        return cache[entity_id]
    from freebase_func import id2entity_name_or_type

    cache[entity_id] = id2entity_name_or_type(entity_id)
    return cache[entity_id]


def _gold_answer_values(episode: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for answer in episode.get("gold_answers", []):
        for value in (answer.get("answer_id"), answer.get("answer")):
            norm = _normalise(value)
            if norm:
                values.add(norm)
    return values


def prepare_gold_hops_and_relation_memories(
    episode: dict[str, Any],
    subobjectives: list[str],
    args: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Traverse the gold path, collect relation candidates without LLM, then answer-filter hops backward."""
    from freebase_func import collect_candidate_relations_without_llm

    question = episode["RawQuestion"]
    gold_path = list(episode.get("gold_relation_path") or [])
    topic_entities = dict(episode.get("topic_entity", {}))
    entity_names = dict(topic_entities)
    frontier = sorted(topic_entities)
    raw_hops: list[dict[str, Any]] = []
    relation_items: list[dict[str, Any]] = []
    prior_triplets: list[str] = []

    for hop_index, gold_relation in enumerate(gold_path):
        if not frontier:
            break
        candidate_union: list[str] = []
        entity_candidate_details: list[dict[str, Any]] = []
        for entity_id in frontier:
            entity_name = _entity_name(entity_id, entity_names)
            candidates = collect_candidate_relations_without_llm(
                entity_id, [], -1, args,
            )
            entity_candidate_details.append({
                "entity_id": entity_id,
                "entity_name": entity_name,
                **candidates,
            })
            for relation in candidates["candidate_relations"]:
                if relation not in candidate_union:
                    candidate_union.append(relation)

        step = execute_gold_step(
            frontier, gold_relation, getattr(args, "gold_frontier_limit", 50), args, question
        )
        for edge in step["edges"]:
            edge["parent_entity_name"] = _entity_name(edge["parent_entity_id"], entity_names)
            edge["child_entity_name"] = _entity_name(edge["child_entity_id"], entity_names)
        next_frontier = list(step["next_entities"])
        if hop_index < len(gold_path) - 1:
            next_frontier = [entity_id for entity_id in next_frontier if _is_mid(entity_id)]

        current_subobjective = subobjectives[hop_index] if hop_index < len(subobjectives) else ""
        relation_item = make_gold_relation_memory_item(
            episode=episode,
            depth=hop_index + 1,
            subobjectives=subobjectives,
            current_subobjective=current_subobjective,
            entity_ids=frontier,
            entity_names=entity_names,
            incoming_relation=gold_path[hop_index - 1] if hop_index else "",
            previous_relations=gold_path[:hop_index],
            gold_relation=gold_relation,
            candidate_relations=candidate_union,
            entity_candidate_details=entity_candidate_details,
        )
        if relation_item.get("verified"):
            relation_items.append(relation_item)
        raw_hops.append({
            "dataset": episode.get("dataset", "webqsp"),
            "question_id": episode.get("question_id", ""),
            "parse_id": episode.get("parse_id", ""),
            "question": question,
            "topic_entities": topic_entities,
            "subobjectives": list(subobjectives),
            "current_subobjective": current_subobjective,
            "hop_index": hop_index,
            "depth": hop_index + 1,
            "gold_relation": gold_relation,
            "gold_answers": list(episode.get("gold_answers", [])),
            "gold_start_entity_ids": list(frontier),
            "all_next_entity_ids": list(next_frontier),
            "edges": list(step["edges"]),
            "entity_names": entity_names,
            "is_final": hop_index == len(gold_path) - 1,
            "prior_knowledge_triplets": list(prior_triplets),
            "initial_memory": build_corrected_memory(subobjectives, hop_index, prior_triplets),
            "relation_memory_item": relation_item,
        })
        prior_triplets.extend(
            f"{edge['parent_entity_name']}, {gold_relation}, {edge['child_entity_name']}"
            for edge in step["edges"]
        )
        frontier = next_frontier

    if not gold_path:
        return [], relation_items
    if len(raw_hops) != len(gold_path):
        for hop in raw_hops:
            hop["reflection_eligible"] = False
        return raw_hops, relation_items

    answer_values = _gold_answer_values(episode)
    final_hop = raw_hops[-1]
    viable_children = {
        edge["child_entity_id"] for edge in final_hop["edges"]
        if _normalise(edge["child_entity_id"]) in answer_values
        or _normalise(edge["child_entity_name"]) in answer_values
    }
    viable = viable_children
    for hop in reversed(raw_hops):
        kept_edges = [edge for edge in hop["edges"] if edge["child_entity_id"] in viable]
        hop["gold_edges"] = kept_edges
        hop["gold_next_entity_ids"] = sorted({edge["child_entity_id"] for edge in kept_edges})
        hop["gold_next_entity_names"] = sorted({edge["child_entity_name"] for edge in kept_edges})
        hop["gold_start_entity_ids"] = sorted({edge["parent_entity_id"] for edge in kept_edges})
        hop["gold_start_entity_names"] = sorted({edge["parent_entity_name"] for edge in kept_edges})
        hop["reflection_eligible"] = bool(kept_edges) and bool(
            hop.get("relation_memory_item", {}).get("verified")
        )
        viable = set(hop["gold_start_entity_ids"])
    return raw_hops, relation_items


def _append_records(path: str, records: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    for record in records:
        append_jsonl_record(path, record, indent=0)


def commit_episode_memory_bundle(
    *,
    memory_dir: str,
    parse_id: str,
    decomposition_path: str,
    relation_path: str,
    reflection_path: str,
    failed_path: str,
    decomposition_items: list[dict[str, Any]],
    relation_items: list[dict[str, Any]],
    reflection_items: list[dict[str, Any]],
    failed_items: list[dict[str, Any]],
) -> None:
    """Append every episode file first; progress is the final commit marker."""
    _append_records(decomposition_path, decomposition_items)
    _append_records(relation_path, relation_items)
    _append_records(reflection_path, reflection_items)
    _append_records(failed_path, failed_items)
    if parse_id:
        append_progress(memory_dir, parse_id)


def run_combined_memory_train(args: Any, run_output: dict[str, Any], episodes: list[dict[str, Any]], model: Any) -> None:
    if args.dataset.lower() != "webqsp":
        raise ValueError("train mode currently supports only --dataset webqsp")
    train_decomp = should_train_decomposition_memory(args)
    train_relation = should_train_relation_memory(args)
    train_reflection = should_train_reflection_memory(args)
    if not (train_decomp or train_relation or train_reflection):
        raise ValueError(f"Unsupported train_memory_family: {args.train_memory_family}")

    memory_dir = args.memory_output_dir.strip() or default_memory_output_dir(args, len(episodes))
    args.memory_output_dir = memory_dir
    os.makedirs(memory_dir, exist_ok=True)
    decomposition_path = args.decomposition_memory_output_path.strip() or os.path.join(memory_dir, DECOMPOSITION_MEMORY_FILENAME)
    relation_path = args.relation_memory_output_path.strip() or os.path.join(memory_dir, RELATION_MEMORY_FILENAME)
    reflection_path = args.reflection_memory_output_path.strip() or os.path.join(memory_dir, REFLECTION_MEMORY_FILENAME)
    failed_path = os.path.join(memory_dir, FAILED_HOP_TRACES_FILENAME)

    print(f"Memory output dir: {memory_dir}")
    if train_decomp:
        print(f"Writing decomposition memory to: {decomposition_path}")
    if train_relation:
        print(f"Writing relation memory to: {relation_path}")
    if train_reflection:
        print(f"Writing reflection memory to: {reflection_path}")

    done = load_progress(memory_dir)
    all_paths = [decomposition_path, relation_path, reflection_path, failed_path]
    existing = {path: load_parse_ids_from_jsonl(path) for path in all_paths}
    setattr(args, "relation_memory_bank", [])
    setattr(args, "reflection_memory_bank", [])
    setattr(args, "sentence_model", model)

    decomp_written = 0
    failed_hops = 0
    for episode in tqdm(episodes, desc="train memory"):
        parse_id = str(episode.get("parse_id", ""))
        if parse_id and parse_id in done:
            continue
        # Remove orphaned records from a previously interrupted, uncommitted episode.
        if parse_id:
            for path in all_paths:
                if parse_id in existing[path]:
                    filter_jsonl_by_parse_id(path, parse_id)
                    existing[path].discard(parse_id)

        gold_path = list(episode.get("gold_relation_path") or [])
        subobjectives, raw_plan, _plan_tokens = generate_train_subobjectives(episode, args)
        subobjectives, subobjective_validation = validate_train_subobjectives(subobjectives, gold_path)
        aligned_subobjectives = align_subobjectives_to_gold_hops(subobjectives, gold_path)

        decomposition_items: list[dict[str, Any]] = []
        if train_decomp and subobjective_validation["valid"]:
            item = make_decomposition_memory_item(
                episode, gold_subobjectives=subobjectives, llm_raw_output=raw_plan
            )
            item["subobjective_validation"] = subobjective_validation
            item["aligned_gold_hops"] = aligned_subobjectives
            decomposition_items.append(item)
            decomp_written += 1

        hops, relation_items_in_memory = prepare_gold_hops_and_relation_memories(
            episode, subobjectives, args
        )
        relation_items = relation_items_in_memory if train_relation else []
        reflection_items: list[dict[str, Any]] = []
        failed_items: list[dict[str, Any]] = []
        if train_reflection:
            for hop in hops:
                if not hop.get("reflection_eligible"):
                    failed_items.append({
                        "dataset": episode.get("dataset", "webqsp"),
                        "question_id": episode.get("question_id", ""),
                        "parse_id": parse_id,
                        "depth": hop.get("depth"),
                        "gold_relation": hop.get("gold_relation"),
                        "failure": "gold_hop_cannot_be_verified_to_answer",
                        "traces": [],
                    })
                    failed_hops += 1
                    continue
                relation_example = "\n".join(
                    format_memory_example_for_prompt(
                        hop["relation_memory_item"],
                        int(getattr(args, "memory_candidate_relation_limit", 8)),
                    )
                )
                result = train_gold_hop_reflection(
                    hop, args, model, relation_example,
                    max_rounds=getattr(args, "max_reflection_rounds", 3),
                )
                if result["success"]:
                    reflection_items.extend(result["memory_bundle"])
                else:
                    failed_items.append({
                        "dataset": episode.get("dataset", "webqsp"),
                        "question_id": episode.get("question_id", ""),
                        "parse_id": parse_id,
                        "depth": hop.get("depth"),
                        "gold_relation": hop.get("gold_relation"),
                        "failure": "max_reflection_rounds_exceeded",
                        "rounds": result.get("rounds", 0),
                        "traces": result.get("traces", []),
                    })
                    failed_hops += 1

        commit_episode_memory_bundle(
            memory_dir=memory_dir,
            parse_id=parse_id,
            decomposition_path=decomposition_path,
            relation_path=relation_path,
            reflection_path=reflection_path,
            failed_path=failed_path,
            decomposition_items=decomposition_items,
            relation_items=relation_items,
            reflection_items=reflection_items,
            failed_items=failed_items,
        )

    meta_updates: dict[str, Any] = {
        "memory_output_dir": memory_dir,
        "constraint_pushdown": getattr(args, "constraint_pushdown", "off"),
        "constraint_asof_date": getattr(args, "constraint_asof_date", ""),
        "constraint_hub_threshold": getattr(args, "constraint_hub_threshold", 50),
    }
    if train_decomp:
        meta_updates.update({
            "decomposition_memory_output_path": decomposition_path,
            "decomposition_memory_count": count_decomposition_memory(decomposition_path),
        })
        print(f"Decomposition memory training finished. written={decomp_written}")
    if train_relation:
        counts = count_relation_memory_labels(relation_path)
        meta_updates.update({
            "relation_memory_output_path": relation_path,
            "relation_memory_label_counts": counts,
        })
        print(f"Relation memory training finished. total={counts['total']}")
    if train_reflection:
        counts = count_reflection_memory(reflection_path)
        meta_updates.update({
            "reflection_memory_output_path": reflection_path,
            "reflection_memory_counts": counts,
            "failed_reflection_hops": failed_hops,
        })
        print(f"Reflection memory training finished. total={counts['total']}, failed_hops={failed_hops}")
    update_run_meta(meta_updates)
