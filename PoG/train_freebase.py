from tqdm import tqdm
import json
import os
import random

from utils import *
from freebase_func import *
from output_paths import (
    build_memory_output_path,
    default_experience_memory_output_path,
    init_run_output,
    update_run_meta,
)
from relation_memory import (
    TrainRelationMemoryBuffer,
    append_experience_memory,
    append_train_relation_memories,
    count_branch_types,
    count_relation_memory_labels,
    load_relation_memory,
    load_webqsp_train_episodes,
    make_experience_memory_item,
    normalize_train_followup_policy,
)


def get_one_data(datas, question_string, question):
    for data in datas:
        if data[question_string] == question:
            return [data]
    return []


def select_questions(datas, question_string, start, limit, question):
    """Select a subset of train questions."""
    if question:
        selected = get_one_data(datas, question_string, question)
        if not selected:
            raise ValueError(f"Question not found in dataset: {question}")
        return selected

    start = max(0, start)
    if limit >= 0:
        return datas[start:start + limit]
    if start > 0:
        return datas[start:]
    return datas


def execute_gold_step(entity_ids, relation, frontier_limit):
    next_entities = set()
    for entity_id in sorted(entity_ids):
        if not (str(entity_id).startswith("m.") or str(entity_id).startswith("g.")):
            continue
        for entity in entity_search(entity_id, relation, True):
            if str(entity).startswith("m.") or str(entity).startswith("g."):
                next_entities.add(entity)
    return sorted(next_entities)[:frontier_limit]


def _serialize_work_memory(memory_items):
    if not memory_items:
        return "{}"
    try:
        return json.dumps(memory_items, ensure_ascii=False, indent=4)
    except Exception:
        return str(memory_items)


def add_train_memory_args(parser):
    parser.add_argument("--relation_memory_output_path", type=str,
                        default="", help="Path to write relation_choice memory JSONL.")
    parser.add_argument("--relation_memory_type", type=str,
                        default="relation_choice", help="Subdirectory name under PoG/relation_memory/.")
    parser.add_argument("--train_memory_family", type=str,
                        choices=["relation_choice", "experience", "all"],
                        default="all", help="Which memory family to build in one train pass.")
    parser.add_argument("--evidence_state_memory_output_path", type=str,
                        default="", help="Path to write evidence_state memory JSONL.")
    parser.add_argument("--failure_reflection_memory_output_path", type=str,
                        default="", help="Path to write failure_reflection memory JSONL.")
    parser.add_argument("--correction_action_memory_output_path", type=str,
                        default="", help="Path to write correction_action memory JSONL.")
    parser.add_argument("--gold_frontier_limit", type=int,
                        default=50, help="Max gold frontier entities retained during train traversal.")
    parser.add_argument("--write_missed_positive", type=int,
                        default=1, help="Write missed_positive memory items when gold relation is in candidates but not selected.")
    parser.add_argument("--train_followup_policy", type=str,
                        default="stop_if_correct",
                        choices=["stop_if_correct", "coin_flip_correct_or_wrong"],
                        help="Training-time gate after relation_choice is correct.")


def _load_optional_memory_banks(args):
    relation_memory_bank = []
    if args.relation_memory_mode != "none" and args.relation_memory_path.strip():
        relation_memory_bank = load_relation_memory(args.relation_memory_path.strip())
        print(f"Loaded {len(relation_memory_bank)} relation memory items.")
    setattr(args, "relation_memory_bank", relation_memory_bank)

    if args.evidence_state_memory_path.strip():
        setattr(args, "evidence_state_memory_bank", load_relation_memory(args.evidence_state_memory_path.strip()))
    else:
        setattr(args, "evidence_state_memory_bank", [])
    if args.failure_reflection_memory_path.strip():
        setattr(args, "failure_reflection_memory_bank", load_relation_memory(args.failure_reflection_memory_path.strip()))
    else:
        setattr(args, "failure_reflection_memory_bank", [])
    if args.correction_action_memory_path.strip():
        setattr(args, "correction_action_memory_bank", load_relation_memory(args.correction_action_memory_path.strip()))
    else:
        setattr(args, "correction_action_memory_bank", [])


def _normalize_train_memory_family(args):
    family = str(getattr(args, "train_memory_family", "all") or "all").strip().lower()
    if family not in {"relation_choice", "experience", "all"}:
        raise ValueError("train_memory_family must be one of: relation_choice, experience, all")
    return family


def _init_relation_memory_path(args, planned_question_count, enabled):
    if not enabled:
        return ""
    memory_type = str(getattr(args, "relation_memory_type", "relation_choice") or "relation_choice").strip()
    path = args.relation_memory_output_path.strip() or build_memory_output_path(
        args,
        planned_question_count,
        memory_type=memory_type,
    )
    args.relation_memory_output_path = path
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    print(f"Writing relation_choice memory to: {path}")
    return path


def _init_experience_memory_paths(args, planned_question_count, enabled):
    if not enabled:
        return {}
    paths = {
        "evidence_state": args.evidence_state_memory_output_path.strip() or default_experience_memory_output_path(
            args, planned_question_count, "evidence_state"
        ),
        "failure_reflection": args.failure_reflection_memory_output_path.strip() or default_experience_memory_output_path(
            args, planned_question_count, "failure_reflection"
        ),
        "correction_action": args.correction_action_memory_output_path.strip() or default_experience_memory_output_path(
            args, planned_question_count, "correction_action"
        ),
    }
    args.evidence_state_memory_output_path = paths["evidence_state"]
    args.failure_reflection_memory_output_path = paths["failure_reflection"]
    args.correction_action_memory_output_path = paths["correction_action"]
    for path in paths.values():
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    print(f"Writing evidence_state memory to: {paths['evidence_state']}")
    print(f"Writing failure_reflection memory to: {paths['failure_reflection']}")
    print(f"Writing correction_action memory to: {paths['correction_action']}")
    return paths


def _append_experience_memories(paths, episode, relation_items):
    if not paths:
        return

    for item in relation_items:
        if not item.get("should_continue_followup"):
            continue

        gold_relation = str(item.get("gold_relation") or "").strip()
        branch_relation = str(item.get("branch_relation") or "").strip()
        picked_relations = [
            str(relation).strip()
            for relation in item.get("picked_relations", [])
            if str(relation).strip()
        ]
        selected_relation_names = [branch_relation] if branch_relation else picked_relations
        if not selected_relation_names:
            continue

        depth = int(item.get("depth") or 0)
        incoming_relation = str(item.get("incoming_relation") or "")
        previous_relations = list(item.get("previous_relations") or [])
        candidate_relations = list(item.get("candidate_relations") or [])
        retrieved_relations = list(item.get("retrieved_relations") or [])
        entity_labels = list(item.get("entity_labels") or [])
        branch_policy = str(item.get("branch_policy") or "")
        branch_type = str(item.get("branch_type") or "")
        branch_relation_source = str(item.get("branch_relation_source") or "")
        relation_choice_correct = bool(item.get("relation_choice_correct"))

        work_memory = _serialize_work_memory(
            [
                {
                    "depth": depth,
                    "incoming_relation": incoming_relation,
                    "previous_relations": previous_relations,
                    "retrieved_relations": retrieved_relations,
                    "selected_relations": selected_relation_names,
                    "branch_type": branch_type,
                    "branch_relation": branch_relation,
                    "relation_choice_correct": relation_choice_correct,
                }
            ]
        )
        knowledge_triplets = "; ".join(candidate_relations[:20])

        evidence_item = make_experience_memory_item(
            episode=episode,
            depth=depth,
            memory_type="evidence_state",
            entity_labels=entity_labels,
            incoming_relation=incoming_relation,
            previous_relations=previous_relations,
            memory_text=(
                f"Current relation-choice branch uses: {', '.join(selected_relation_names)}"
            ),
            work_memory=work_memory,
            knowledge_triplets=knowledge_triplets,
            branch_policy=branch_policy,
            branch_type=branch_type,
            branch_relation=branch_relation,
            branch_relation_source=branch_relation_source,
            should_continue_followup=True,
            relation_choice_correct=relation_choice_correct,
        )
        append_experience_memory(paths["evidence_state"], evidence_item)

        if gold_relation in selected_relation_names:
            continue

        failure_item = make_experience_memory_item(
            episode=episode,
            depth=depth,
            memory_type="failure_reflection",
            entity_labels=entity_labels,
            incoming_relation=incoming_relation,
            previous_relations=previous_relations,
            memory_text=(
                f"The current relation-choice branch misses the gold relation {gold_relation}."
            ),
            work_memory=work_memory,
            knowledge_triplets=knowledge_triplets,
            branch_policy=branch_policy,
            branch_type=branch_type,
            branch_relation=branch_relation,
            branch_relation_source=branch_relation_source,
            should_continue_followup=True,
            relation_choice_correct=relation_choice_correct,
            extra_fields={
                "failure_cause": "branch_relation_not_gold",
                "gold_relation": gold_relation,
            },
        )
        append_experience_memory(paths["failure_reflection"], failure_item)

        correction_item = make_experience_memory_item(
            episode=episode,
            depth=depth,
            memory_type="correction_action",
            entity_labels=entity_labels,
            incoming_relation=incoming_relation,
            previous_relations=previous_relations,
            memory_text=(
                f"Correct the relation-choice branch to include {gold_relation} and continue retrieval."
            ),
            work_memory=work_memory,
            knowledge_triplets=knowledge_triplets,
            branch_policy=branch_policy,
            branch_type=branch_type,
            branch_relation=branch_relation,
            branch_relation_source=branch_relation_source,
            should_continue_followup=True,
            relation_choice_correct=relation_choice_correct,
            extra_fields={
                "correction_trigger": "relation_choice_failure",
                "gold_relation": gold_relation,
                "selected_relations": selected_relation_names,
            },
        )
        append_experience_memory(paths["correction_action"], correction_item)


def run_train_memory_extraction(args, episodes, planned_question_count, model):
    if args.dataset.lower() != "webqsp":
        raise ValueError("memory train mode currently supports only --dataset webqsp")

    family = _normalize_train_memory_family(args)
    write_relation = family in {"relation_choice", "all"}
    write_experience = family in {"experience", "all"}
    branch_policy = normalize_train_followup_policy(getattr(args, "train_followup_policy", "stop_if_correct"))
    relation_memory_path = _init_relation_memory_path(args, planned_question_count, write_relation)
    experience_paths = _init_experience_memory_paths(args, planned_question_count, write_experience)

    for episode in tqdm(episodes):
        question = episode["RawQuestion"]
        topic_entity = dict(episode["topic_entity"])
        gold_path = list(episode["gold_relation_path"])
        sub_questions = "[]"
        current_frontier = sorted(topic_entity.keys())
        entid_name = dict(topic_entity)
        previous_relations = []
        incoming_relation = ""

        setattr(args, "current_topic_entity", topic_entity)
        setattr(args, "sentence_model", model)

        for hop_index, gold_relation in enumerate(gold_path):
            depth = hop_index + 1
            if not current_frontier:
                print(f"Stop train episode {episode.get('parse_id')}: empty gold frontier at depth {depth}")
                break

            next_frontier = execute_gold_step(current_frontier, gold_relation, args.gold_frontier_limit)
            hop_buffer = TrainRelationMemoryBuffer()
            for entity_id in current_frontier:
                entity_name = entid_name.get(entity_id)
                if not entity_name:
                    entity_name = id2entity_name_or_type(entity_id)
                    entid_name[entity_id] = entity_name

                setattr(args, "current_relation_depth", depth)
                setattr(args, "current_incoming_relation", incoming_relation)
                setattr(args, "current_previous_relations", list(previous_relations))

                try:
                    _retrieve_relations, _token_num, rel_trace = relation_search_prune(
                        entity_id,
                        sub_questions,
                        entity_name,
                        [],
                        -1,
                        question,
                        args,
                    )
                except Exception as exc:
                    print(f"relation_search_prune failed in train mode: {exc}")
                    continue

                relation_items = append_train_relation_memories(
                    hop_buffer,
                    episode=episode,
                    depth=depth,
                    entity_id=entity_id,
                    entity_name=entity_name,
                    incoming_relation=incoming_relation,
                    previous_relations=previous_relations,
                    gold_relation=gold_relation,
                    candidate_relations=rel_trace.get("candidate_relations", []),
                    retrieved_relations=rel_trace.get("retrieved_relations", []),
                    selected_relations=rel_trace.get("selected_relations", []),
                    llm_raw_output=rel_trace.get("llm_raw_output", ""),
                    write_missed_positive=bool(args.write_missed_positive),
                    branch_policy=branch_policy,
                    rng=random,
                )
                _append_experience_memories(experience_paths, episode, relation_items)

            if write_relation:
                hop_buffer.flush(relation_memory_path)

            for entity_id in next_frontier:
                if entity_id not in entid_name:
                    entid_name[entity_id] = id2entity_name_or_type(entity_id)
            current_frontier = next_frontier
            previous_relations.append(gold_relation)
            incoming_relation = gold_relation

    meta_updates = {
        "train_memory_family": family,
        "train_followup_policy": branch_policy,
    }
    if write_relation:
        label_counts = count_relation_memory_labels(relation_memory_path)
        branch_counts = count_branch_types(relation_memory_path)
        meta_updates.update(
            {
                "relation_memory_output_path": relation_memory_path,
                "relation_memory_type": str(getattr(args, "relation_memory_type", "relation_choice") or "relation_choice").strip(),
                "relation_memory_label_counts": label_counts,
                "relation_memory_branch_counts": branch_counts,
            }
        )
        print(
            "Relation memory label counts: "
            f"positive={label_counts['positive']}, "
            f"missed_positive={label_counts['missed_positive']}, "
            f"negative={label_counts['negative']}, "
            f"total={label_counts['total']}"
        )
        print(
            "Relation memory branch counts: "
            f"gold_branch={branch_counts.get('gold_branch', 0)}, "
            f"synthetic_wrong_branch={branch_counts.get('synthetic_wrong_branch', 0)}, "
            f"real_wrong_branch={branch_counts.get('real_wrong_branch', 0)}, "
            f"fallback_gold_branch={branch_counts.get('fallback_gold_branch', 0)}, "
            f"total={branch_counts.get('total', 0)}"
        )
    if write_experience:
        meta_updates.update(
            {
                "evidence_state_memory_output_path": experience_paths["evidence_state"],
                "failure_reflection_memory_output_path": experience_paths["failure_reflection"],
                "correction_action_memory_output_path": experience_paths["correction_action"],
                "experience_memory_types": ["evidence_state", "failure_reflection", "correction_action"],
            }
        )
    update_run_meta(meta_updates)
    print("Memory training finished.")


def run_train(args):
    args.run_mode = "train"
    args.split = "train"

    train_episodes = load_webqsp_train_episodes()
    selected_train_episodes = select_questions(
        train_episodes,
        "RawQuestion",
        args.start,
        args.limit,
        args.question.strip(),
    )
    planned_question_count = len(selected_train_episodes)
    run_output = init_run_output(
        args,
        planned_question_count=planned_question_count,
        resume_dir=args.run_dir.strip() or None,
    )
    model = SentenceTransformer('../msmarco-distilbert-base-tas-b')
    _load_optional_memory_banks(args)
    print(f"Selected train episodes: {len(selected_train_episodes)} / {len(train_episodes)}")
    run_train_memory_extraction(args, selected_train_episodes, planned_question_count, model)
