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
from output_paths import init_run_output, load_processed_questions, get_current_run, default_relation_memory_output_path, update_run_meta
from output_paths import default_decomposition_memory_output_path, default_memory_output_dir
from output_paths import load_parse_ids_from_jsonl, filter_jsonl_by_parse_id, load_progress, append_progress
from output_paths import DECOMPOSITION_MEMORY_FILENAME, RELATION_MEMORY_FILENAME
from eval_run import run_post_test_evaluation
from relation_memory import (
    TrainRelationMemoryBuffer,
    append_train_relation_memories,
    count_relation_memory_labels,
    load_relation_memory,
    load_webqsp_train_episodes,
)
from constraint_compiler import (
    compile_question_constraints,
    format_constraints_for_prompt,
    constraint_routing_mode,
    select_search_constraints,
)
from constraint_runtime import (
    answer_gate_mode,
    answer_in_covering_set,
    apply_frontier_bias,
    covering_answer_names,
    reset_coverage_map,
)
from decomposition_memory import (
    append_decomposition_memory,
    build_gold_planning_prompt,
    count_decomposition_memory,
    load_decomposition_memory,
    make_decomposition_memory_item,
    parse_planning_steps,
)
import os
import pprint
import traceback

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
    parser.add_argument("--train_memory_family", type=str,
                        choices=["relation_choice", "decomposition", "all"],
                        default="relation_choice", help="Which memory family to build in train mode.")
    parser.add_argument("--relation_memory_type", type=str,
                        default="relation_choice", help="Compatibility tag for train scripts.")
    parser.add_argument("--train_followup_policy", type=str,
                        default="stop_if_correct", help="Compatibility tag for train scripts.")
    parser.add_argument("--decomposition_memory_mode", type=str, choices=["none", "prompt"],
                        default="none", help="How decomposition memory is used in test mode.")
    parser.add_argument("--decomposition_memory_path", type=str,
                        default="", help="Path to decomposition memory JSONL for test mode.")
    parser.add_argument("--decomposition_memory_output_path", type=str,
                        default="", help="Path to write decomposition memory JSONL in train mode.")
    parser.add_argument("--memory_output_dir", type=str,
                        default="", help="Per-run memory folder created under PoG/memory/. Holds decomposition_memory.jsonl, relation_memory.jsonl, progress.jsonl. Defaults to a timestamped folder.")
    parser.add_argument("--decomposition_memory_top_k", type=int,
                        default=4, help="Top-k decomposition memories to inject.")
    parser.add_argument("--decomposition_memory_prompt_token_budget", type=int,
                        default=800, help="Approximate token budget for decomposition memory prompt context.")
    parser.add_argument("--cvt_entity_top_k", type=int,
                        default=30, help="Max CVT candidates sent to the Step-2 entity LLM after intersection-priority filtering.")
    parser.add_argument("--constraint_pushdown", type=str, choices=["off", "on"],
                        default="off", help="Enable question constraint compilation and SPARQL pushdown in test and train modes.")
    parser.add_argument("--constraint_routing", type=str, choices=["off", "auto", "on"],
                        default="auto", help="Per-hop constraint routing from decomposition. off=all constraints every hop; auto=use routing with full-constraint fallback; on=routing required or skip SPARQL pushdown.")
    parser.add_argument("--constraint_asof_date", type=str,
                        default="2015-08-10", help="Snapshot date used for current/present time constraints.")
    parser.add_argument("--constraint_link_top_k", type=int,
                        default=8, help="Max Freebase name/alias candidates per constraint mention.")
    parser.add_argument("--constraint_link_min_confidence", type=float,
                        default=0.65, help="Minimum confidence for using a linked mention as a hard entity constraint.")
    parser.add_argument("--constraint_max_entity_constraints", type=int,
                        default=2, help="Max linked non-topic entity constraints used by pushdown.")
    parser.add_argument("--constraint_auto_keep_top_k", type=int,
                        default=50, help="Auto-keep already pushdown-filtered buckets up to this size before LLM pruning.")
    parser.add_argument("--constraint_hub_threshold", type=int,
                        default=50, help="Skip time-only pushdown when unconstrained neighbor count is below this threshold.")
    parser.add_argument("--constraint_prompt_stages", type=str,
                        default="relation,memory,reasoning,answer",
                        help="Stages that receive compiled question constraints in prompts.")
    parser.add_argument("--constraint_answer_gate", type=str, choices=["off", "soft", "hard"],
                        default="hard", help="Stop-search gate using constraint-covering provenance. off=disabled, soft=prompt only, hard=reject uncovered answers.")
    parser.add_argument("--decomposition_grounding_check", type=int,
                        default=1, help="Strip ungrounded quoted literals from subobjectives.")
    parser.add_argument("--decomposition_memory_mask_literals", type=int,
                        default=1, help="Mask topic names and quoted literals in decomposition memory examples.")
    parser.add_argument("--memory_conflict_policy", type=str, choices=["keep_both", "overwrite"],
                        default="keep_both", help="How to handle memory slot value changes when both mention constraint-covering entities.")
    parser.add_argument("--constraint_frontier_bias", type=int,
                        default=1, help="Prefer constraint-covering entities when building the next-hop frontier.")
    parser.add_argument("--decomposition_repair", type=str, choices=["off", "on"],
                        default="off", help="Optionally re-plan once after repeated insufficient reasoning. Default off.")


def should_train_relation_memory(args):
    return getattr(args, "train_memory_family", "relation_choice") in {"relation_choice", "all"}


def should_train_decomposition_memory(args):
    return getattr(args, "train_memory_family", "relation_choice") in {"decomposition", "all"}


def resolve_split(args):
    if args.split:
        return args.split
    return "train" if args.run_mode == "train" else "test"


def update_current_subobjective_idx(args, depth, sub_questions):
    """Set args.current_subobjective_idx for this hop.

    LLM Subobjective_Progress is a 1-based completed count; otherwise assume
    1 subobjective per hop: idx = min(depth - 1, n_steps - 1).
    """
    if isinstance(sub_questions, list):
        steps = [str(item).strip() for item in sub_questions if str(item).strip()]
    else:
        steps = parse_planning_steps(str(sub_questions or ""))
    n_steps = max(1, len(steps) or 1)
    progress = getattr(args, "last_subobjective_progress", None)
    try:
        progress = int(progress) if progress is not None else None
    except (TypeError, ValueError):
        progress = None
    if progress is not None:
        args.current_subobjective_idx = min(max(progress, 0), n_steps - 1)
    else:
        args.current_subobjective_idx = min(max(int(depth) - 1, 0), n_steps - 1)
    return args.current_subobjective_idx


def execute_gold_step(entity_ids, relation, frontier_limit, args=None, question=""):
    next_entities = set()
    for entity_id in sorted(entity_ids):
        if not (str(entity_id).startswith("m.") or str(entity_id).startswith("g.")):
            continue
        if args is not None and getattr(args, "constraint_pushdown", "off") == "on":
            entities = entity_search_with_constraints(entity_id, relation, True, question, args)
        else:
            entities = entity_search(entity_id, relation, True)
        for entity in entities:
            if str(entity).startswith("m.") or str(entity).startswith("g."):
                next_entities.add(entity)
    return sorted(next_entities)[:frontier_limit]


def run_combined_memory_train(args, run_output, episodes, model):
    if args.dataset.lower() != "webqsp":
        raise ValueError("train mode currently supports only --dataset webqsp")

    train_decomp = should_train_decomposition_memory(args)
    train_relation = should_train_relation_memory(args)
    if not (train_decomp or train_relation):
        raise ValueError(f"Unsupported train_memory_family: {args.train_memory_family}")

    memory_dir = args.memory_output_dir.strip() or default_memory_output_dir(args, len(episodes))
    args.memory_output_dir = memory_dir
    os.makedirs(memory_dir, exist_ok=True)

    decomposition_memory_path = (
        args.decomposition_memory_output_path.strip()
        or os.path.join(memory_dir, DECOMPOSITION_MEMORY_FILENAME)
    )
    relation_memory_path = (
        args.relation_memory_output_path.strip()
        or os.path.join(memory_dir, RELATION_MEMORY_FILENAME)
    )
    os.makedirs(os.path.dirname(os.path.abspath(decomposition_memory_path)), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(relation_memory_path)), exist_ok=True)

    print(f"Memory output dir: {memory_dir}")
    if train_decomp:
        print(f"Writing decomposition memory to: {decomposition_memory_path}")
    if train_relation:
        print(f"Writing relation memory to: {relation_memory_path}")
    if getattr(args, "constraint_pushdown", "off") == "on":
        print(f"Constraint pushdown enabled in train mode, as_of={args.constraint_asof_date}")

    done = load_progress(memory_dir)
    if done:
        print(f"Resuming: {len(done)} episode(s) already completed according to progress.jsonl")
    decomp_existing = load_parse_ids_from_jsonl(decomposition_memory_path) if train_decomp else set()
    rel_existing = load_parse_ids_from_jsonl(relation_memory_path) if train_relation else set()

    setattr(args, "relation_memory_bank", [])
    setattr(args, "sentence_model", model)

    decomp_written = 0
    for episode in tqdm(episodes, desc="train memory"):
        parse_id = str(episode.get("parse_id", ""))
        if parse_id and parse_id in done:
            continue

        if parse_id:
            if parse_id in decomp_existing:
                filter_jsonl_by_parse_id(decomposition_memory_path, parse_id)
                decomp_existing.discard(parse_id)
            if parse_id in rel_existing:
                filter_jsonl_by_parse_id(relation_memory_path, parse_id)
                rel_existing.discard(parse_id)

        question = episode["RawQuestion"]
        topic_entity = dict(episode.get("topic_entity", {}))
        setattr(args, "current_constraint_search_traces", {})
        reset_coverage_map(args)
        if args.constraint_pushdown == "on":
            constraints = compile_question_constraints(
                question,
                topic_entity,
                args,
                model,
                sparql_executor=execurte_sparql,
            )
            setattr(args, "current_constraints", constraints)
            prompt_context = format_constraints_for_prompt(constraints)
            if prompt_context:
                print(f"compiled constraints [{parse_id}]: {prompt_context}")
        else:
            setattr(args, "current_constraints", {})

        if train_decomp:
            prompt = build_gold_planning_prompt(episode)
            response, _token_num = run_llm(
                prompt,
                args.temperature_reasoning,
                args.max_length,
                args.opeani_api_keys,
                args.LLM_type,
                False,
                False,
            )
            gold_subobjectives = parse_planning_steps(response)
            if gold_subobjectives:
                append_decomposition_memory(
                    decomposition_memory_path,
                    make_decomposition_memory_item(
                        episode,
                        gold_subobjectives=gold_subobjectives,
                        llm_raw_output=response,
                    ),
                )
                decomp_written += 1
            else:
                print(f"Skip decomposition memory with empty plan: {parse_id}")

        if train_relation:
            gold_path = list(episode.get("gold_relation_path") or [])
            sub_questions = "[]"
            current_frontier = sorted(topic_entity.keys())
            entid_name = dict(topic_entity)
            previous_relations = []
            incoming_relation = ""

            setattr(args, "current_topic_entity", topic_entity)

            for hop_index, gold_relation in enumerate(gold_path):
                depth = hop_index + 1
                if not current_frontier:
                    print(f"Stop train episode {parse_id}: empty gold frontier at depth {depth}")
                    break

                setattr(args, "current_constraint_search_traces", {})
                next_frontier = execute_gold_step(
                    current_frontier,
                    gold_relation,
                    args.gold_frontier_limit,
                    args=args,
                    question=question,
                )
                if args.constraint_pushdown == "on":
                    traces = getattr(args, "current_constraint_search_traces", {}) or {}
                    for (entity_id, relation, head), trace in traces.items():
                        print(
                            "constraint pushdown "
                            f"parse={parse_id} entity={entity_id} relation={relation} head={head} "
                            f"before={trace.get('before_count')} after={trace.get('after_count')} "
                            f"applied={trace.get('pushdown_applied')} bind={trace.get('bind_relation')} "
                            f"attempt={trace.get('attempt', '')} reason={trace.get('fallback_reason', '')}"
                        )
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
                    )

                hop_buffer.flush(relation_memory_path)

                for entity_id in next_frontier:
                    if entity_id not in entid_name:
                        entid_name[entity_id] = id2entity_name_or_type(entity_id)
                current_frontier = next_frontier
                previous_relations.append(gold_relation)
                incoming_relation = gold_relation

        if parse_id:
            append_progress(memory_dir, parse_id)

    meta_updates = {
        "memory_output_dir": memory_dir,
        "constraint_pushdown": getattr(args, "constraint_pushdown", "off"),
        "constraint_asof_date": getattr(args, "constraint_asof_date", ""),
        "constraint_hub_threshold": getattr(args, "constraint_hub_threshold", 50),
    }
    if train_decomp:
        decomp_count = count_decomposition_memory(decomposition_memory_path)
        meta_updates["decomposition_memory_output_path"] = decomposition_memory_path
        meta_updates["decomposition_memory_count"] = decomp_count
        print(f"Decomposition memory training finished. written={decomp_written}, total={decomp_count}")
    if train_relation:
        label_counts = count_relation_memory_labels(relation_memory_path)
        meta_updates["relation_memory_output_path"] = relation_memory_path
        meta_updates["relation_memory_label_counts"] = label_counts
        print(
            "Relation memory label counts: "
            f"positive={label_counts['positive']}, "
            f"missed_positive={label_counts['missed_positive']}, "
            f"negative={label_counts['negative']}, "
            f"total={label_counts['total']}"
        )
        print("Relation memory training finished.")
    update_run_meta(meta_updates)

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
                total_in_dataset = len(train_episodes)
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
                if should_train_decomposition_memory(args) or should_train_relation_memory(args):
                    model = SentenceTransformer('../msmarco-distilbert-base-tas-b') if should_train_relation_memory(args) else None
                    run_combined_memory_train(args, run_output, selected_train_episodes, model)
                else:
                    raise ValueError(f"Unsupported train_memory_family: {args.train_memory_family}")
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
            decomposition_memory_bank = []
            if args.decomposition_memory_mode != "none" and args.decomposition_memory_path.strip():
                decomposition_memory_bank = load_decomposition_memory(args.decomposition_memory_path.strip())
                print(f"Loaded {len(decomposition_memory_bank)} decomposition memory items.")
            setattr(args, "decomposition_memory_bank", decomposition_memory_bank)
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

                topic_entity = data['topic_entity']
                setattr(args, "current_topic_entity", topic_entity)
                setattr(args, "current_constraint_search_traces", {})
                setattr(args, "current_subobjective_idx", 0)
                setattr(args, "last_subobjective_progress", None)
                setattr(args, "sub_constraint_routing", None)
                setattr(args, "resolved_constraint_routing", None)
                setattr(args, "constraint_routing_status", "off")
                reset_coverage_map(args)
                if args.constraint_pushdown == "on":
                    constraints = compile_question_constraints(
                        question,
                        topic_entity,
                        args,
                        model,
                        sparql_executor=execurte_sparql,
                    )
                    setattr(args, "current_constraints", constraints)
                    extract_tokens = (constraints.get("trace") or {}).get("llm_token_num") or {}
                    if any(extract_tokens.get(key, 0) for key in ("total", "input", "output")):
                        call_num += 1
                        for kk in extract_tokens.keys():
                            all_t[kk] += extract_tokens[kk]
                else:
                    setattr(args, "current_constraints", {})

                call_num += 1
                sub_questions, token_num = get_subquestions(q_mem_f_path, question, args)
                for kk in token_num.keys():
                    all_t[kk] += token_num[kk]

                cluster_chain_of_entities = []
                depth_ent_rel_ent_dict = {}
                reverse_rec = {'time': 0, 'ent': []}
                pog_trace = new_run_trace(sub_questions, topic_entity)
                pog_trace["constraints"] = getattr(args, "current_constraints", {}) or {}
                pog_trace["decomposition"] = {
                    "subquestions": sub_questions,
                    "memory_context": getattr(args, "current_decomposition_memory_context", ""),
                    "reference_context": getattr(args, "current_decomposition_reference_context", ""),
                    "llm_raw_output": getattr(args, "current_decomposition_raw_output", ""),
                    "grounding": getattr(args, "current_decomposition_grounding", {}),
                    "constraint_routing": getattr(args, "sub_constraint_routing", None),
                    "resolved_constraint_routing": getattr(args, "resolved_constraint_routing", None),
                    "constraint_routing_status": getattr(args, "constraint_routing_status", "off"),
                }

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
                    update_current_subobjective_idx(args, depth, sub_questions)
                    depth_record = new_depth_record(depth, topic_entity)
                    depth_record["subobjective_idx"] = getattr(args, "current_subobjective_idx", 0)
                    depth_record["active_constraints"] = select_search_constraints(
                        args,
                        getattr(args, "current_constraints", {}) or {},
                        getattr(args, "current_subobjective_idx", 0),
                    )
                    if constraint_routing_mode(args) != "off":
                        active = depth_record["active_constraints"]
                        print(
                            "constraint routing "
                            f"depth={depth} sub_idx={args.current_subobjective_idx} "
                            f"status={getattr(args, 'constraint_routing_status', '')} "
                            f"entities={len(active.get('entity_constraints') or [])} "
                            f"time={len(active.get('time_constraints') or [])} "
                            f"order={len(active.get('order_constraints') or [])}"
                        )
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
                            entity_candidates_id = entity_search_with_constraints(
                                ent_rel['entity'], ent_rel['relation'], True, question, args,
                                subobjective_idx=getattr(args, "current_subobjective_idx", None),
                            )
                        else:
                            head_or_tail = 'tail'
                            entity_candidates_id = entity_search_with_constraints(
                                ent_rel['entity'], ent_rel['relation'], False, question, args,
                                subobjective_idx=getattr(args, "current_subobjective_idx", None),
                            )
                        
                        if len(entity_candidates_id) == 0:
                            print('the relations without tail entity:', ent_rel)
                            continue

                        max_candidates = int(getattr(args, "max_candidates_per_relation", 200))
                        if max_candidates > 0 and len(entity_candidates_id) > max_candidates:
                            print(
                                f"[search] capping {ent_rel['relation']} candidates "
                                f"{len(entity_candidates_id)} -> {max_candidates}"
                            )
                            entity_candidates_id = entity_candidates_id[:max_candidates]

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
                    
                    pprint.pprint(summarize_name_dict(convert_dict_name(ent_rel_ent_dict, entid_name)))

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
                    depth_record["constraint_covering_entities"] = sorted(
                        name for name in covering_answer_names(args, entid_name)
                        if not str(name).startswith(("m.", "g."))
                    )
                    cluster_chain_of_entities.append(chain_of_entities)

                    call_num += cur_call_time
                    for kk in cur_token.keys():
                        all_t[kk] += cur_token[kk]

                    pprint.pprint(summarize_name_dict(convert_dict_name(new_ent_rel_ent_dict, entid_name)))
                    setattr(args, "current_entid_name", entid_name)
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

                        covering_names = covering_answer_names(args, entid_name)
                        gate_trace = {
                            "mode": answer_gate_mode(args),
                            "triggered": False,
                            "reasked": False,
                            "covering_names": sorted(name for name in covering_names if not str(name).startswith(("m.", "g."))),
                        }

                        if str(answer).lower() == 'null' or str(answer).lower() == 'none'  or str(answer).startswith('m.') or str(answer).startswith('[\"m.') or str(answer).startswith("['m.") or 'yes' not in str(sufficient).lower():
                            stop = False
                        else:
                            stop = True

                        if (
                            stop
                            and answer_gate_mode(args) == "hard"
                            and covering_names
                            and not answer_in_covering_set(answer, covering_names)
                        ):
                            gate_trace["triggered"] = True
                            gate_trace["rejected_answer"] = answer
                            call_num += 1
                            results2, answer2, sufficient2, token_num2 = reasoning(
                                question, sub_questions, new_ent_rel_ent_dict, entid_name,
                                cluster_chain_of_entities, q_mem_f_path, args,
                                restrict_to_covering=True,
                            )
                            for kk in token_num2.keys():
                                all_t[kk] += token_num2[kk]
                            gate_trace["reasked"] = True
                            gate_trace["reask_answer"] = answer2
                            gate_trace["reask_sufficient"] = sufficient2
                            uncovered = (
                                str(answer2).lower() in {"null", "none"}
                                or str(answer2).startswith("m.")
                                or "yes" not in str(sufficient2).lower()
                                or not answer_in_covering_set(answer2, covering_names)
                            )
                            if uncovered:
                                stop = False
                            else:
                                results, answer, sufficient = results2, answer2, sufficient2
                                stop = True

                        depth_record["evaluation"] = {
                            "llm_response": results,
                            "answer": answer,
                            "sufficient": sufficient,
                            "stop": stop,
                            "answer_gate": gate_trace,
                            "subobjective_progress": getattr(args, "last_subobjective_progress", None),
                            "subobjective_idx": getattr(args, "current_subobjective_idx", 0),
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
                                pprint.pprint(summarize_name_dict(convert_dict_name(ent_rel_ent_dict, entid_name)))
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
                                biased_ids = apply_frontier_bias(entities_id, args)
                                for entity in biased_ids:
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
        except Exception:
            print("Error occurred, retrying...")
            traceback.print_exc()
            time.sleep(5)
            continue
