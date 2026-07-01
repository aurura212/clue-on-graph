from tqdm import tqdm
import argparse
from utils import *
from freebase_func import *
from reference_utils import (
    build_decomposition_reference_context,
    build_reference_context,
    load_reference_bank,
    set_current_decomposition_reference_context,
    set_current_reference_context,
)
from trace_utils import flatten_chain_triples, new_depth_record, new_run_trace, serialize_name_dict
from output_paths import (
    init_run_output,
    load_processed_questions,
    get_current_run,
    build_memory_output_path,
    default_relation_memory_output_path,
    default_experience_memory_output_path,
    update_run_meta,
)
from eval_run import run_post_test_evaluation
from relation_memory import (
    TrainRelationMemoryBuffer,
    append_train_relation_memories,
    count_relation_memory_labels,
    count_branch_types,
    load_relation_memory,
    load_webqsp_train_episodes,
    normalize_train_followup_policy,
    normalize_experience_memory_type,
    make_experience_memory_item,
    append_experience_memory,
)
import os
import pprint
import random

# os.environ['OPENAI_API_BASE'] = "https://cn2us02.opapi.win/v1"

def repeat_unanswer(dataset, datas, question_string, model_name):
    answered_set = set()
    new_data = []

    file_path = 'PoG_'+dataset+'_'+model_name+'.jsonl'
    with open(file_path, 'r', encoding='utf-8') as file:
        for line in file:
            data = json.loads(line) 
            answered_set.add(data[question_string])

    for x in datas:
        if x[question_string] not in answered_set:
            new_data.append(x)
    print(len(new_data))

    return new_data

def get_one_data(datas, question_string, question):
    for data in datas:
        if data[question_string] == question:
            return [data]
    return []


def select_questions(datas, question_string, start, limit, question):
    """Select a subset of questions for testing."""
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


def add_relation_memory_args(parser):
    parser.add_argument("--run_mode", type=str, choices=["test", "train"],
                        default="test", help="Run PoG in test mode or build relation memory in train mode.")
    parser.add_argument("--split", type=str, choices=["test", "train"],
                        default="", help="Dataset split. Defaults to train for run_mode=train and test otherwise.")
    parser.add_argument("--relation_memory_mode", type=str, choices=["none", "prompt"],
                        default="none", help="How relation memory is used.")
    parser.add_argument("--relation_memory_stages", type=str,
                        default="relation", help="Stages for relation memory: relation,memory,reasoning,answer,all,none.")
    parser.add_argument("--relation_memory_path", type=str,
                        default="", help="Path to relation memory JSONL for test mode (under PoG/relation_memory/ by default).")
    parser.add_argument("--relation_memory_output_path", type=str,
                        default="", help="Path to write relation memory JSONL in train mode (defaults to PoG/relation_memory/).")
    parser.add_argument("--relation_memory_type", type=str,
                        default="relation_choice",
                        help="Subdirectory name under PoG/relation_memory/ for train memory output.")
    parser.add_argument("--train_memory_family", type=str,
                        choices=["relation_choice", "experience"],
                        default="relation_choice",
                        help="Which memory family to build in train mode.")
    parser.add_argument("--evidence_state_memory_output_path", type=str,
                        default="", help="Path to write evidence_state memory JSONL in train mode.")
    parser.add_argument("--failure_reflection_memory_output_path", type=str,
                        default="", help="Path to write failure_reflection memory JSONL in train mode.")
    parser.add_argument("--correction_action_memory_output_path", type=str,
                        default="", help="Path to write correction_action memory JSONL in train mode.")
    parser.add_argument("--evidence_state_memory_path", type=str,
                        default="", help="Path to evidence_state memory JSONL in test mode.")
    parser.add_argument("--failure_reflection_memory_path", type=str,
                        default="", help="Path to failure_reflection memory JSONL in test mode.")
    parser.add_argument("--correction_action_memory_path", type=str,
                        default="", help="Path to correction_action memory JSONL in test mode.")
    parser.add_argument("--relation_memory_top_k", type=int,
                        default=4, help="Top-k relation memories to inject.")
    parser.add_argument("--memory_retrieval_strategy", type=str, choices=["question", "state", "hybrid"],
                        default="hybrid", help="Relation memory retrieval strategy.")
    parser.add_argument("--memory_state_weight", type=float,
                        default=0.5, help="State similarity weight for hybrid memory retrieval.")
    parser.add_argument("--memory_labels", type=str,
                        default="positive,missed_positive,negative", help="Allowed relation memory labels.")
    parser.add_argument("--memory_prompt_token_budget", type=int,
                        default=600, help="Approximate token budget for relation memory prompt context.")
    parser.add_argument("--memory_candidate_relation_limit", type=int,
                        default=8, help="Max candidate relations shown per memory item.")
    parser.add_argument("--gold_frontier_limit", type=int,
                        default=50, help="Max gold frontier entities retained during train traversal.")
    parser.add_argument("--write_missed_positive", type=int,
                        default=1, help="Write missed_positive memory items when gold relation is in candidates but not selected.")
    parser.add_argument("--relation_semantic_top_k", type=int,
                        default=20, help="Keep top-k relations after semantic similarity ranking when candidate count exceeds this value.")
    parser.add_argument("--train_followup_policy", type=str,
                        default="stop_if_correct",
                        choices=["stop_if_correct", "coin_flip_correct_or_wrong"],
                        help="Training-time gate after relation_choice is correct.")


def resolve_split(args):
    if args.split:
        return args.split
    return "train" if args.run_mode == "train" else "test"


def execute_gold_step(entity_ids, relation, frontier_limit):
    next_entities = set()
    for entity_id in sorted(entity_ids):
        if not (str(entity_id).startswith("m.") or str(entity_id).startswith("g.")):
            continue
        for entity in entity_search(entity_id, relation, True):
            if str(entity).startswith("m.") or str(entity).startswith("g."):
                next_entities.add(entity)
    return sorted(next_entities)[:frontier_limit]


def run_relation_memory_train(args, run_output, model):
    if args.dataset.lower() != "webqsp":
        raise ValueError("relation memory train mode currently supports only --dataset webqsp")

    branch_policy = normalize_train_followup_policy(getattr(args, "train_followup_policy", "stop_if_correct"))

    episodes = load_webqsp_train_episodes()
    total_in_dataset = len(episodes)
    episodes = select_questions(episodes, "RawQuestion", args.start, args.limit, args.question.strip())
    print(f"Selected train episodes: {len(episodes)} / {total_in_dataset}")

    memory_type = str(getattr(args, "relation_memory_type", "relation_choice") or "relation_choice").strip()
    memory_output_path = args.relation_memory_output_path.strip() or build_memory_output_path(
        args,
        len(episodes),
        memory_type=memory_type,
    )
    print(f"Writing relation memory to: {memory_output_path}")

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
        setattr(args, "relation_memory_bank", [])
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
                    retrieve_relations, token_num, rel_trace = relation_search_prune(
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

                candidate_relations = rel_trace.get("candidate_relations", [])
                retrieved_relations = rel_trace.get("retrieved_relations", [])
                selected_relations = rel_trace.get("selected_relations", [])
                append_train_relation_memories(
                    hop_buffer,
                    episode=episode,
                    depth=depth,
                    entity_id=entity_id,
                    entity_name=entity_name,
                    incoming_relation=incoming_relation,
                    previous_relations=previous_relations,
                    gold_relation=gold_relation,
                    candidate_relations=candidate_relations,
                    retrieved_relations=retrieved_relations,
                    selected_relations=selected_relations,
                    llm_raw_output=rel_trace.get("llm_raw_output", ""),
                    write_missed_positive=bool(args.write_missed_positive),
                    branch_policy=branch_policy,
                    rng=random,
                )

            hop_buffer.flush(memory_output_path)

            for entity_id in next_frontier:
                if entity_id not in entid_name:
                    entid_name[entity_id] = id2entity_name_or_type(entity_id)
            current_frontier = next_frontier
            previous_relations.append(gold_relation)
            incoming_relation = gold_relation

    label_counts = count_relation_memory_labels(memory_output_path)
    branch_counts = count_branch_types(memory_output_path)
    update_run_meta(
        {
            "relation_memory_output_path": memory_output_path,
            "relation_memory_type": memory_type,
            "relation_memory_label_counts": label_counts,
            "train_followup_policy": branch_policy,
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
    print("Relation memory training finished.")


def _resolve_memory_path(args, planned_question_count, memory_type):
    return args.relation_memory_output_path.strip() or build_memory_output_path(
        args,
        planned_question_count,
        memory_type=memory_type,
    )


def _serialize_work_memory(memory_items):
    if not memory_items:
        return "{}"
    try:
        import json

        return json.dumps(memory_items, ensure_ascii=False, indent=4)
    except Exception:
        return str(memory_items)


def _load_experience_memory_bank(args):
    banks = {
        "evidence_state": [],
        "failure_reflection": [],
        "correction_action": [],
    }
    if args.evidence_state_memory_path.strip():
        banks["evidence_state"] = load_relation_memory(args.evidence_state_memory_path.strip())
    if args.failure_reflection_memory_path.strip():
        banks["failure_reflection"] = load_relation_memory(args.failure_reflection_memory_path.strip())
    if args.correction_action_memory_path.strip():
        banks["correction_action"] = load_relation_memory(args.correction_action_memory_path.strip())
    return banks


def run_experience_memory_train(args, run_output, model):
    if args.dataset.lower() != "webqsp":
        raise ValueError("experience memory train mode currently supports only --dataset webqsp")

    episodes = load_webqsp_train_episodes()
    total_in_dataset = len(episodes)
    episodes = select_questions(episodes, "RawQuestion", args.start, args.limit, args.question.strip())
    print(f"Selected train episodes: {len(episodes)} / {total_in_dataset}")

    evidence_path = args.evidence_state_memory_output_path.strip() or default_experience_memory_output_path(
        args,
        len(episodes),
        "evidence_state",
    )
    failure_path = args.failure_reflection_memory_output_path.strip() or default_experience_memory_output_path(
        args,
        len(episodes),
        "failure_reflection",
    )
    correction_path = args.correction_action_memory_output_path.strip() or default_experience_memory_output_path(
        args,
        len(episodes),
        "correction_action",
    )
    print(f"Writing evidence_state memory to: {evidence_path}")
    print(f"Writing failure_reflection memory to: {failure_path}")
    print(f"Writing correction_action memory to: {correction_path}")

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
        setattr(args, "relation_memory_bank", [])
        setattr(args, "sentence_model", model)

        for hop_index, gold_relation in enumerate(gold_path):
            depth = hop_index + 1
            if not current_frontier:
                break

            next_frontier = execute_gold_step(current_frontier, gold_relation, args.gold_frontier_limit)
            hop_buffer = []
            for entity_id in current_frontier:
                entity_name = entid_name.get(entity_id) or id2entity_name_or_type(entity_id)
                entid_name[entity_id] = entity_name
                setattr(args, "current_relation_depth", depth)
                setattr(args, "current_incoming_relation", incoming_relation)
                setattr(args, "current_previous_relations", list(previous_relations))

                try:
                    retrieve_relations, _token_num, rel_trace = relation_search_prune(
                        entity_id,
                        sub_questions,
                        entity_name,
                        [],
                        -1,
                        question,
                        args,
                    )
                except Exception as exc:
                    print(f"relation_search_prune failed in experience memory train mode: {exc}")
                    continue

                if not retrieve_relations:
                    continue

                candidate_relations = rel_trace.get("candidate_relations", [])
                retrieved_relations = rel_trace.get("retrieved_relations", [])
                selected_relations = rel_trace.get("selected_relations", [])
                selected_relation_names = [
                    str(item.get("relation", "")).strip()
                    for item in selected_relations
                    if str(item.get("relation", "")).strip()
                ]
                if not selected_relation_names:
                    continue

                work_memory = _serialize_work_memory(
                    [
                        {
                            "depth": depth,
                            "incoming_relation": incoming_relation,
                            "previous_relations": list(previous_relations),
                            "retrieved_relations": retrieved_relations,
                            "selected_relations": selected_relation_names,
                        }
                    ]
                )
                knowledge_triplets = "; ".join(candidate_relations[:20])
                entity_labels = [entity_name]

                evidence_item = make_experience_memory_item(
                    episode=episode,
                    depth=depth,
                    memory_type="evidence_state",
                    entity_labels=entity_labels,
                    incoming_relation=incoming_relation,
                    previous_relations=previous_relations,
                    memory_text=(
                        f"Selected relations support the current path: {', '.join(selected_relation_names)}"
                    ),
                    work_memory=work_memory,
                    knowledge_triplets=knowledge_triplets,
                )
                append_experience_memory(evidence_path, evidence_item)
                hop_buffer.append(evidence_item)

                if gold_relation in selected_relation_names:
                    previous_relations.append(gold_relation)
                    incoming_relation = gold_relation
                    continue

                failure_item = make_experience_memory_item(
                    episode=episode,
                    depth=depth,
                    memory_type="failure_reflection",
                    entity_labels=entity_labels,
                    incoming_relation=incoming_relation,
                    previous_relations=previous_relations,
                    memory_text=(
                        f"The selected relation missed the gold relation {gold_relation}."
                    ),
                    work_memory=work_memory,
                    knowledge_triplets=knowledge_triplets,
                    extra_fields={
                        "failure_cause": "gold_relation_not_selected",
                        "gold_relation": gold_relation,
                    },
                )
                append_experience_memory(failure_path, failure_item)

                correction_item = make_experience_memory_item(
                    episode=episode,
                    depth=depth,
                    memory_type="correction_action",
                    entity_labels=entity_labels,
                    incoming_relation=incoming_relation,
                    previous_relations=previous_relations,
                    memory_text=(
                        f"Correct the relation choice to include {gold_relation} and continue retrieval."
                    ),
                    work_memory=work_memory,
                    knowledge_triplets=knowledge_triplets,
                    extra_fields={
                        "correction_trigger": "relation_choice_failure",
                        "gold_relation": gold_relation,
                        "selected_relations": selected_relation_names,
                    },
                )
                append_experience_memory(correction_path, correction_item)

            for entity_id in next_frontier:
                if entity_id not in entid_name:
                    entid_name[entity_id] = id2entity_name_or_type(entity_id)
            current_frontier = next_frontier
            previous_relations.append(gold_relation)
            incoming_relation = gold_relation

    update_run_meta(
        {
            "evidence_state_memory_output_path": evidence_path,
            "failure_reflection_memory_output_path": failure_path,
            "correction_action_memory_output_path": correction_path,
            "experience_memory_types": ["evidence_state", "failure_reflection", "correction_action"],
        }
    )
    print("Experience memory training finished.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=str,
                        default="cwq", help="choose the dataset.")
    parser.add_argument("--max_length", type=int,
                        default=4096, help="the max length of LLMs output.")
    parser.add_argument("--temperature_exploration", type=float,
                        default=0.3, help="the temperature in exploration stage.")
    parser.add_argument("--temperature_reasoning", type=float,
                        default=0.3, help="the temperature in reasoning stage.")
    parser.add_argument("--depth", type=int,
                        default=4, help="choose the search depth of PoG.")
    parser.add_argument("--remove_unnecessary_rel", type=bool,
                        default=True, help="whether removing unnecessary relations.")
    parser.add_argument("--LLM_type", type=str,
                        default="gpt-3.5-turbo-0125", help="base LLM model.")
    parser.add_argument("--opeani_api_keys", type=str,
                        default="", help="API key for OpenAI-compatible models (GPT, DeepSeek, etc.).")
    parser.add_argument("--openai_api_base", type=str, default="",
                        help="OpenAI-compatible API base URL, e.g. https://api.deepseek.com")
    parser.add_argument("--reference_mode", type=str, choices=["none", "cog", "revolution"],
                        default="none", help="PoG reference mode. cog uses related questions and correct paths; revolution also uses blind LLM reasoning and retrieval feedback.")
    parser.add_argument("--reference_base_path", type=str,
                        default="", help="Path to a revolution reference JSONL file.")
    parser.add_argument("--reference_limit", type=int,
                        default=-1, help="Reference file limit suffix. -1 selects the full reference file.")
    parser.add_argument("--reference_top_k", type=int,
                        default=4, help="Number of similar reference cases to inject.")
    parser.add_argument("--reference_stages", type=str,
                        default="relation", help="Stages that can use reference: relation, memory, reasoning, answer, cot, reverse, add_entity, decomposition, all, none. Separate multiple stages with spaces or commas. decomposition uses gold.clue_reasoning from the reference bank.")
    parser.add_argument("--random_knowledge", type=int,
                        default=0, help="Use random reference cases instead of similar-question retrieval.")
    parser.add_argument("--start", type=int, default=0,
                        help="Start index in the dataset (0-based). Applied before skipping already-processed questions.")
    parser.add_argument("--limit", type=int, default=-1,
                        help="Max number of questions to run. -1 means no limit.")
    parser.add_argument("--question", type=str, default="",
                        help="Run a single question by exact RawQuestion/question text. Overrides --start/--limit.")
    parser.add_argument("--run_dir", type=str, default="",
                        help="Resume an existing run under result/. Pass folder name (e.g. webqsp_..._n10_20250617_120000) or full path.")
    add_relation_memory_args(parser)
    args = parser.parse_args()
    if not args.split:
        args.split = "train" if args.run_mode == "train" else "test"
    if args.openai_api_base:
        os.environ["OPENAI_API_BASE"] = args.openai_api_base
    elif "OPENAI_API_BASE" not in os.environ:
        raise ValueError("OPENAI_API_BASE is required. Set it in the environment or pass --openai_api_base.")

    while True:
        try:
            processed_question = []
            split = resolve_split(args)
            args.split = split
            if args.run_mode == "train":
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
                if getattr(args, "train_memory_family", "relation_choice") == "experience":
                    if not args.evidence_state_memory_output_path.strip():
                        args.evidence_state_memory_output_path = default_experience_memory_output_path(
                            args, planned_question_count, "evidence_state"
                        )
                    if not args.failure_reflection_memory_output_path.strip():
                        args.failure_reflection_memory_output_path = default_experience_memory_output_path(
                            args, planned_question_count, "failure_reflection"
                        )
                    if not args.correction_action_memory_output_path.strip():
                        args.correction_action_memory_output_path = default_experience_memory_output_path(
                            args, planned_question_count, "correction_action"
                        )
                    for path in [
                        args.evidence_state_memory_output_path,
                        args.failure_reflection_memory_output_path,
                        args.correction_action_memory_output_path,
                    ]:
                        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                    run_experience_memory_train(args, run_output, model)
                else:
                    if not args.relation_memory_output_path.strip():
                        args.relation_memory_output_path = build_memory_output_path(
                            args,
                            planned_question_count,
                            memory_type=str(getattr(args, "relation_memory_type", "relation_choice") or "relation_choice").strip(),
                        )
                    if not os.path.exists(args.relation_memory_output_path):
                        os.makedirs(os.path.dirname(os.path.abspath(args.relation_memory_output_path)), exist_ok=True)
                    run_relation_memory_train(args, run_output, model)
                break

            datas, question_string = prepare_dataset(args.dataset)
            total_in_dataset = len(datas)
            datas = select_questions(
                datas, question_string, args.start, args.limit, args.question.strip()
            )
            planned_question_count = len(datas)
            if args.question.strip():
                print(f"Selected 1 question by --question (dataset size={total_in_dataset}).")
            elif args.start > 0 or args.limit >= 0:
                end = args.start + args.limit if args.limit >= 0 else total_in_dataset
                print(
                    f"Selected questions [{args.start}:{end}) -> {planned_question_count} / {total_in_dataset} "
                    f"(start={args.start}, limit={args.limit})."
                )

            run_output = init_run_output(
                args,
                planned_question_count=planned_question_count,
                resume_dir=args.run_dir.strip() or None,
            )

            processed_question = load_processed_questions(question_string)
            if processed_question:
                print("data_processed", len(processed_question))
                datas = [x for x in datas if x[question_string] not in processed_question]
                print(f"Remaining after skipping processed: {len(datas)}")
                if len(datas) == 0:
                    print("All questions have been processed.")
                    run_post_test_evaluation(args, run_output)
                    break
            #datas = datas[994:]
            #datas = repeat_unanswer(args.dataset, datas, question_string, args.LLM_type)
            # if findone:
            #     datas = get_one_data(datas, question_string, 'Which countries both contain the Delnita River and fall in Eastern Europe?')
            #     print(datas)
            model = SentenceTransformer('../msmarco-distilbert-base-tas-b')
            setattr(args, "sentence_model", model)
            relation_memory_bank = []
            if args.relation_memory_mode != "none" and args.relation_memory_path.strip():
                relation_memory_bank = load_relation_memory(args.relation_memory_path.strip())
                print(f"Loaded {len(relation_memory_bank)} relation memory items.")
            setattr(args, "relation_memory_bank", relation_memory_bank)
            experience_banks = _load_experience_memory_bank(args)
            setattr(args, "evidence_state_memory_bank", experience_banks["evidence_state"])
            setattr(args, "failure_reflection_memory_bank", experience_banks["failure_reflection"])
            setattr(args, "correction_action_memory_bank", experience_banks["correction_action"])
            reference_bank = load_reference_bank(args)
            if args.reference_mode != "none":
                print(f"Loaded {len(reference_bank)} reference cases for PoG reference_mode={args.reference_mode}.")
            part_q = False
            if part_q:
                q_set = []
                f = open('../eval/analysis_question', 'r', encoding='utf-8')
                for line in f.readlines():
                    q_set.append(line.strip())

            print("Start Running PoG on %s dataset." % args.dataset)

            for data in tqdm(datas):
                if part_q and data[question_string] not in q_set:
                    continue
                #try:
                start_time = time.time()
                call_num = 0
                all_t = {'total': 0, 'input': 0, 'output': 0}

                question = data[question_string]
                print('New question start:', question)
                setattr(args, "current_topic_entity", data.get('topic_entity', {}))
                reference_context = build_reference_context(
                    reference_bank,
                    question,
                    data.get('topic_entity', {}),
                    args,
                    model,
                )
                set_current_reference_context(args, reference_context)
                if reference_context:
                    print("PoG reference context:\n", reference_context)
                decomposition_reference_context = build_decomposition_reference_context(
                    reference_bank,
                    question,
                    data.get('topic_entity', {}),
                    args,
                    model,
                )
                set_current_decomposition_reference_context(args, decomposition_reference_context)
                if decomposition_reference_context:
                    print("PoG decomposition reference context:\n", decomposition_reference_context)
                q_mem_f_path = '../mem_PoG/'+run_output["run_folder_name"]+'/'+question[:255]
                if not os.path.exists(q_mem_f_path):
                    os.makedirs(q_mem_f_path)
                with open(q_mem_f_path+'/mem_PoG', 'w', encoding='utf-8') as f:
                    pass

                call_num += 1
                sub_questions, token_num = get_subquestions(q_mem_f_path, question, args)
                for kk in token_num.keys():
                    all_t[kk] += token_num[kk]

                topic_entity = data['topic_entity']
                cluster_chain_of_entities = []
                depth_ent_rel_ent_dict = {}
                reverse_rec = {'time': 0, 'ent': []}
                pog_trace = new_run_trace(sub_questions, topic_entity)

                entid_name = {}
                name_entid = {}
                for e_id, e_name in topic_entity.items():
                    entid_name[e_id] = e_name
                    name_entid[e_name] = e_id

                if len(topic_entity) == 0:
                    call_num += 1
                    results, token_num = generate_without_explored_paths(question, sub_questions, args)
                    for kk in token_num.keys():
                        all_t[kk] += token_num[kk]
                    
                    new_e_rev_list = [entid_name[x] for x in reverse_rec['ent']]
                    reverse_rec['ent'] = new_e_rev_list
                    pog_trace["final_stop_reason"] = "no_topic_entity_cot"
                    pog_trace["final_answer_generation"] = {
                        "method": "generate_without_explored_paths",
                        "llm_response": results,
                    }
                    save_2_jsonl(question, question_string, results, [], call_num, all_t, start_time, pog_trace=pog_trace)
                    continue

                pre_relations = []
                pre_heads= [-1] * len(topic_entity)
                flag_printed = False
                for depth in range(1, args.depth+1):
                    depth_record = new_depth_record(depth, topic_entity)
                    current_entity_relations_list = []
                    i=0
                    for entity in topic_entity:
                        if entity!="[FINISH_ID]":
                            setattr(args, "current_topic_entity", topic_entity)
                            call_num += 1
                            setattr(args, "current_relation_depth", depth)
                            setattr(args, "current_incoming_relation", "" if depth == 1 else (pre_relations[i] if i < len(pre_relations) else ""))
                            setattr(args, "current_previous_relations", list(pre_relations))
                            retrieve_relations, token_num, rel_trace = relation_search_prune(entity, sub_questions, topic_entity[entity], pre_relations, pre_heads[i], question, args)
                            depth_record["relation_prune"].append(rel_trace)
                            for kk in token_num.keys():
                                all_t[kk] += token_num[kk]
                            if entity.startswith("m.") == False and entity.startswith("g.") == False:
                                continue
                            current_entity_relations_list.extend(retrieve_relations)
                        i+=1
                    total_candidates = []
                    total_relations = []
                    total_entities_id = [] 
                    total_topic_entities = [] 
                    total_head = []

                    ent_rel_ent_dict = {} # e->head/tail->rel->ent
                    for ent_rel in current_entity_relations_list:
                        if ent_rel['entity'] not in ent_rel_ent_dict.keys():
                            ent_rel_ent_dict[ent_rel['entity']] = {}

                        if ent_rel['head']:
                            head_or_tail = 'head'
                            entity_candidates_id = entity_search(ent_rel['entity'], ent_rel['relation'], True)
                        else:
                            head_or_tail = 'tail'
                            entity_candidates_id = entity_search(ent_rel['entity'], ent_rel['relation'], False)
                        
                        if len(entity_candidates_id) == 0:
                            print('the relations without tail entity:', ent_rel)
                            continue

                        entity_candidates, entity_candidates_id = provide_triple(entity_candidates_id, ent_rel['relation'])

                        name_entid.update(dict(zip(entity_candidates, entity_candidates_id)))
                        entid_name.update(dict(zip(entity_candidates_id, entity_candidates)))

                        if head_or_tail not in ent_rel_ent_dict[ent_rel['entity']].keys():
                                ent_rel_ent_dict[ent_rel['entity']][head_or_tail] = {}

                        if ent_rel['relation'] not in ent_rel_ent_dict[ent_rel['entity']][head_or_tail].keys():
                            ent_rel_ent_dict[ent_rel['entity']][head_or_tail][ent_rel['relation']] = []

                        # store current entities into ent_rel_ent_dict
                        for retrive_ent in entity_candidates_id:
                            if retrive_ent not in ent_rel_ent_dict[ent_rel['entity']][head_or_tail][ent_rel['relation']]:
                                ent_rel_ent_dict[ent_rel['entity']][head_or_tail][ent_rel['relation']].append(retrive_ent)
                        
                        total_candidates, total_relations, total_entities_id, total_topic_entities, total_head = update_history(entity_candidates, ent_rel, entity_candidates_id, total_candidates, total_relations, total_entities_id, total_topic_entities, total_head)
                    
                    depth_ent_rel_ent_dict[depth] = ent_rel_ent_dict
                    depth_record["before_entity_prune"] = serialize_name_dict(convert_dict_name(ent_rel_ent_dict, entid_name))
                    
                    pprint.pprint(convert_dict_name(ent_rel_ent_dict, entid_name))

                    if len(total_candidates) == 0:
                        depth_record["stop_reason"] = "no_candidates_after_relation_entity_search"
                        pog_trace["depths"].append(depth_record)
                        new_e_rev_list = [entid_name[x] for x in reverse_rec['ent']]
                        reverse_rec['ent'] = new_e_rev_list
                        half_stop(question, question_string, sub_questions, cluster_chain_of_entities, depth, call_num, all_t, start_time, args, pog_trace=pog_trace)
                        flag_printed = True
                        break
                    
                    flag, chain_of_entities, entities_id, pre_relations, pre_heads, new_ent_rel_ent_dict, cur_call_time, cur_token, entity_prune_details = entity_condition_prune(question, total_entities_id, total_relations, total_candidates, total_topic_entities, total_head, ent_rel_ent_dict, entid_name, name_entid, args, model)
                    depth_record["after_entity_prune"] = serialize_name_dict(convert_dict_name(new_ent_rel_ent_dict, entid_name))
                    depth_record["entity_prune_details"] = entity_prune_details
                    depth_record["pruned_triples"] = flatten_chain_triples(chain_of_entities)
                    depth_record["entity_prune_success"] = flag
                    cluster_chain_of_entities.append(chain_of_entities)

                    call_num += cur_call_time
                    for kk in cur_token.keys():
                        all_t[kk] += cur_token[kk]

                    pprint.pprint(convert_dict_name(new_ent_rel_ent_dict, entid_name))
                    if flag:
                        call_num += 1
                        token_num, mem_trace = update_memory(question, sub_questions, new_ent_rel_ent_dict, entid_name, cluster_chain_of_entities, q_mem_f_path, args)
                        depth_record["memory_update"] = mem_trace
                        for kk in token_num.keys():
                            all_t[kk] += token_num[kk]

                        call_num += 1
                        results, answer, sufficient, token_num = reasoning(question, sub_questions, new_ent_rel_ent_dict, entid_name, cluster_chain_of_entities, q_mem_f_path, args)
                        for kk in token_num.keys():
                            all_t[kk] += token_num[kk]

                        if str(answer).lower() == 'null' or str(answer).lower() == 'none'  or str(answer).startswith('m.') or str(answer).startswith('[\"m.') or str(answer).startswith("['m.") or 'yes' not in str(sufficient).lower():
                            stop = False
                        else:
                            stop = True

                        depth_record["evaluation"] = {
                            "llm_response": results,
                            "answer": answer,
                            "sufficient": sufficient,
                            "stop": stop,
                        }

                        if stop:
                            print("PoG stoped at depth %d." % depth)
                            depth_record["stop_reason"] = "reasoning_sufficient"
                            pog_trace["depths"].append(depth_record)
                            pog_trace["final_stop_reason"] = "reasoning_sufficient"
                            pog_trace["final_stop_depth"] = depth
                            new_e_rev_list = [entid_name[x] for x in reverse_rec['ent']]
                            reverse_rec['ent'] = new_e_rev_list
                            save_2_jsonl(question, question_string, results, cluster_chain_of_entities, call_num, all_t, start_time, pog_trace=pog_trace)
                            flag_printed = True
                            break
                        else:
                            print("depth %d still not find the answer." % depth)
                            add_ent_list = []
                            reverse_trace = {"triggered": False, "add_entities": [], "judge_response": None, "select_response": None}
                            if reverse_rec['time']<5:
                                entities_id, add_ent_list, cur_call_time, cur_token = if_finish_list(question, entities_id, depth_ent_rel_ent_dict, entid_name, name_entid, q_mem_f_path, results, cluster_chain_of_entities, args, model)
                                call_num += cur_call_time
                                for kk in cur_token.keys():
                                    all_t[kk] += cur_token[kk]
                                add_ent_list = [ent for ent in add_ent_list if ent not in reverse_rec['ent']]
                                if add_ent_list:
                                    reverse_trace["triggered"] = True
                                    reverse_trace["add_entities"] = [entid_name.get(e, e) for e in add_ent_list]

                            depth_record["reverse_retrieval"] = reverse_trace
                            pog_trace["depths"].append(depth_record)

                            if add_ent_list:
                                reverse_rec['time'] += 1
                                reverse_rec['ent'] += add_ent_list

                                add_ent_list, add_pre_relations, add_pre_heads, new_ent_rel_ent_dict = add_pre_info(add_ent_list, depth_ent_rel_ent_dict, new_ent_rel_ent_dict, entid_name, name_entid, args)
                                pre_relations += add_pre_relations
                                pprint.pprint(convert_dict_name(ent_rel_ent_dict, entid_name))
                                pre_heads += add_pre_heads
                                entities_id += add_ent_list

                            if not entities_id or depth>5:
                                new_e_rev_list = [entid_name[x] for x in reverse_rec['ent']]
                                reverse_rec['ent'] = new_e_rev_list
                                half_stop(question, question_string, sub_questions, cluster_chain_of_entities, depth, call_num, all_t, start_time, args, pog_trace=pog_trace)
                                flag_printed = True
                                break
                            else:
                                topic_entity = {}
                                for entity in entities_id:
                                    if if_topic_non_retrieve(entity):
                                        continue
                                    if entity.startswith("m."):
                                        topic_entity[entity] = entid_name[entity]

                    else:
                        depth_record["stop_reason"] = "entity_prune_failed"
                        pog_trace["depths"].append(depth_record)
                        new_e_rev_list = [entid_name[x] for x in reverse_rec['ent']]
                        reverse_rec['ent'] = new_e_rev_list
                        half_stop(question, question_string, sub_questions, cluster_chain_of_entities, depth, call_num, all_t, start_time, args, pog_trace=pog_trace)
                        flag_printed = True
                        break
                
                if not flag_printed:
                    call_num += 1
                    results, token_num = generate_without_explored_paths(question, sub_questions, args)
                    for kk in token_num.keys():
                        all_t[kk] += token_num[kk]
                    
                    new_e_rev_list = [entid_name[x] for x in reverse_rec['ent']]
                    reverse_rec['ent'] = new_e_rev_list
                    pog_trace["final_stop_reason"] = "max_depth_cot_fallback"
                    pog_trace["final_answer_generation"] = {
                        "method": "generate_without_explored_paths",
                        "llm_response": results,
                    }
                    save_2_jsonl(question, question_string, results, [], call_num, all_t, start_time, pog_trace=pog_trace)
                '''except:
                    continue'''
            run_post_test_evaluation(args, run_output)
            break
        except:
            print("Error occurred, retrying...")
            time.sleep(5)
            continue
