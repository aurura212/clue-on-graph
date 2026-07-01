import glob
import json
import os
import random
from typing import Any

from sentence_transformers import util


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def resolve_project_path(path):
    if not path:
        return path
    if os.path.isabs(path):
        return path
    cwd_path = os.path.abspath(path)
    if os.path.exists(cwd_path):
        return cwd_path
    return os.path.join(PROJECT_ROOT, path)


def read_jsonl_file(file_path):
    data = []
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return data

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    except json.JSONDecodeError:
        pass

    try:
        for line in text.splitlines():
            line = line.strip()
            if line:
                data.append(json.loads(line))
        return data
    except json.JSONDecodeError:
        data = []

    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        obj, index = decoder.raw_decode(text, index)
        data.append(obj)
    return data


def get_dataset_prefix(dataset):
    dataset_lower = dataset.lower()
    if "webqsp" in dataset_lower:
        return "webqsp"
    if "cwq" in dataset_lower:
        return "cwq"
    if "grailqa" in dataset_lower:
        return "grailqa"
    return dataset.split("_")[0]


def get_reference_limit_suffix(limit):
    return "all" if limit < 0 else f"limit{limit}"


def get_default_revolution_reference_path(dataset, limit=-1):
    dataset_prefix = get_dataset_prefix(dataset)
    limit_suffix = get_reference_limit_suffix(limit)
    reference_dir = os.path.join(PROJECT_ROOT, "data", "revolution_reference")
    candidates = [
        os.path.join(reference_dir, f"{dataset_prefix}_reference_{limit_suffix}.jsonl"),
        os.path.join(reference_dir, f"{dataset_prefix.upper()}_reference_{limit_suffix}.jsonl"),
        os.path.join(reference_dir, f"{dataset_prefix}_reference.jsonl"),
        os.path.join(reference_dir, f"{dataset_prefix.upper()}_reference.jsonl"),
    ]
    if limit >= 0:
        limit_candidates = sorted(
            glob.glob(os.path.join(reference_dir, f"{dataset_prefix}_reference_limit{limit}*.jsonl")),
            key=os.path.getmtime,
            reverse=True,
        )
        candidates = limit_candidates + candidates
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def load_reference_bank(args):
    if getattr(args, "reference_mode", "none") == "none":
        return []
    reference_path = getattr(args, "reference_base_path", "") or get_default_revolution_reference_path(
        args.dataset,
        getattr(args, "reference_limit", -1),
    )
    reference_path = resolve_project_path(reference_path)
    if not os.path.exists(reference_path):
        raise FileNotFoundError(
            f"Revolution reference base not found: {reference_path}. "
            "Use --reference_base_path to provide an explicit path."
        )
    return read_jsonl_file(reference_path)


def mask_question_with_entities(question, topic_entity):
    masked_question = question
    entity_names = topic_entity.values() if isinstance(topic_entity, dict) else topic_entity
    for entity_name in entity_names:
        if entity_name:
            masked_question = masked_question.replace(str(entity_name), "[ENT]")
    return masked_question


def select_reference_items(reference_bank, question, topic_entity, args, model):
    if getattr(args, "reference_mode", "none") == "none" or not reference_bank:
        return []
    top_k = min(getattr(args, "reference_top_k", 4), len(reference_bank))
    if top_k <= 0:
        return []
    if getattr(args, "random_knowledge", 0) == 1:
        return random.sample(reference_bank, top_k)

    masked_question = mask_question_with_entities(question, topic_entity)
    cand_questions = [ref.get("masked_question") or ref.get("question", "") for ref in reference_bank]
    if model is None:
        return reference_bank[:top_k]

    query_emb = model.encode(masked_question)
    doc_emb = model.encode(cand_questions)
    scores = util.dot_score(query_emb, doc_emb)[0].cpu().tolist()
    ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [reference_bank[i] for i in ranked_indices[:top_k]]


def compact_path_string(path):
    if not path:
        return ""
    triples = path[0] if isinstance(path, list) and path and isinstance(path[0], list) else path
    if not triples:
        return ""
    parts = []
    for index, triple in enumerate(triples):
        if not isinstance(triple, list) or len(triple) != 3:
            continue
        head, relation, tail = triple
        if index == 0:
            parts.append(str(head))
        parts.extend([str(relation), str(tail)])
    return " -> ".join(parts)


def first_non_empty(values):
    if not values:
        return ""
    return str(values[0])


def format_correct_path(ref):
    gold_relation_path = ref.get("gold", {}).get("relation_path", "")
    topic_entities = ref.get("topic_entities", [])
    if topic_entities:
        label = topic_entities[0].get("label") or topic_entities[0].get("id") or "Topic Entity"
        return f"{label} -> {gold_relation_path}" if gold_relation_path else str(label)
    return gold_relation_path


def format_blind_reasoning_context(ref):
    lines = []
    blind = ref.get("blind", {})
    blind_reasoning_paths = blind.get("reasoning_paths", {})
    blind_clue_reasoning = blind.get("clue_reasoning", [])
    if blind_reasoning_paths:
        for label, path in blind_reasoning_paths.items():
            lines.append(f"Blind LLM Path ({label}): {path}")
    elif blind_clue_reasoning:
        lines.append("Blind LLM Reasoning: " + " | ".join([str(item) for item in blind_clue_reasoning]))

    retrieval_result = blind.get("retrieval_result", [])
    if retrieval_result:
        result = retrieval_result[0]
        if result.get("reasoning_path"):
            lines.append("Blind Retrieval Path: " + str(result.get("reasoning_path")))
        retrieved_path = compact_path_string(result.get("paths", []))
        if retrieved_path:
            path_label = "Complete Retrieved Path" if result.get("path_complete") else "Partial Retrieved Path"
            lines.append(path_label + ": " + retrieved_path)

    failure_reasons = blind.get("verification", {}).get("failure_reasons", [])
    if failure_reasons:
        lines.append("Failure: " + first_non_empty(failure_reasons))

    evaluation = ref.get("evaluation", {})
    if evaluation.get("correction"):
        lines.append("Correction: " + str(evaluation.get("correction", "")))
    return lines


def format_reference_item(ref, mode):
    lines = [
        "Reference Question: " + ref.get("question", ""),
        "Correct Path: " + format_correct_path(ref),
    ]
    if mode == "revolution":
        lines.extend(format_blind_reasoning_context(ref))
    return "\n".join([line for line in lines if line.strip()])


def format_gold_clue_reasoning(ref):
    clue_reasoning = ref.get("gold", {}).get("clue_reasoning", [])
    if not clue_reasoning:
        return ""
    if isinstance(clue_reasoning, str):
        return "Clue Reasoning: " + clue_reasoning
    steps = [str(step).strip() for step in clue_reasoning if str(step).strip()]
    if not steps:
        return ""
    numbered_steps = "\n".join(f"{index + 1}. {step}" for index, step in enumerate(steps))
    return "Clue Reasoning:\n" + numbered_steps


def format_decomposition_reference_item(ref):
    lines = [
        "Reference Question: " + ref.get("question", ""),
    ]
    clue_text = format_gold_clue_reasoning(ref)
    if clue_text:
        lines.append(clue_text)
    return "\n".join([line for line in lines if line.strip()])


def build_decomposition_reference_context(reference_bank, question, topic_entity, args, model):
    if getattr(args, "reference_mode", "none") == "none":
        return ""
    selected_refs = select_reference_items(reference_bank, question, topic_entity, args, model)
    if not selected_refs:
        return ""

    header = (
        "Reference cases selected from training questions. Each case contains a related question "
        "and its gold clue reasoning steps. Use these steps as guidance for decomposing the current "
        "question into subobjectives, but do not copy entities or answers from the references."
    )
    body = "\n\n".join(format_decomposition_reference_item(ref) for ref in selected_refs)
    return header + "\n" + body


def build_reference_context(reference_bank, question, topic_entity, args, model):
    mode = getattr(args, "reference_mode", "none")
    if mode == "none":
        return ""
    selected_refs = select_reference_items(reference_bank, question, topic_entity, args, model)
    if not selected_refs:
        return ""

    if mode == "cog":
        header = (
            "Reference cases selected from training questions. Each case contains a related question "
            "and its correct relation path. Use these paths as structural guidance for the current "
            "question, but do not copy entities or answers from the references."
        )
    elif mode == "revolution":
        header = (
            "Reference cases selected from training questions. Each case contains a related question, "
            "its correct relation path, and the blind LLM reasoning or retrieval feedback. Use them "
            "to avoid similar reasoning mistakes and to prefer relations that fit the current question. "
            "Do not copy entities or answers from the references."
        )
    else:
        raise ValueError(f"Unknown PoG reference_mode: {mode}")

    body = "\n\n".join(format_reference_item(ref, mode) for ref in selected_refs)
    return header + "\n" + body


def set_current_reference_context(args, context):
    setattr(args, "current_reference_context", context or "")


def set_current_decomposition_reference_context(args, context):
    setattr(args, "current_decomposition_reference_context", context or "")


def should_use_reference_at_stage(args, stage):
    if getattr(args, "reference_mode", "none") == "none":
        return False
    stages = getattr(args, "reference_stages", "relation")
    if stages is None:
        stages = "relation"
    if isinstance(stages, str):
        selected_stages = {
            item.strip().lower()
            for item in stages.replace(",", " ").split()
            if item.strip()
        }
    else:
        selected_stages = {str(item).strip().lower() for item in stages if str(item).strip()}
    if "none" in selected_stages:
        return False
    return "all" in selected_stages or stage in selected_stages


def maybe_prepend_reference_context(prompt, args, stage="relation"):
    if not should_use_reference_at_stage(args, stage):
        return prompt
    if stage == "decomposition":
        context = getattr(args, "current_decomposition_reference_context", "")
    else:
        context = getattr(args, "current_reference_context", "")
    if not context:
        return prompt
    return context + "\n\nCurrent task:\n" + prompt


def get_stages_suffix(stages: Any) -> str:
    if stages is None:
        stages = "relation"
    if isinstance(stages, str):
        selected_stages = [
            item.strip().lower()
            for item in stages.replace(",", " ").split()
            if item.strip()
        ]
    else:
        selected_stages = [str(item).strip().lower() for item in stages if str(item).strip()]
    if not selected_stages:
        selected_stages = ["relation"]
    return "-".join(selected_stages)


def get_reference_stages_suffix(args):
    return get_stages_suffix(getattr(args, "reference_stages", "relation"))


def format_reference_tag(args) -> str:
    mode = getattr(args, "reference_mode", "none")
    if mode == "none":
        return ""
    return (
        f"ref-{mode}_top{getattr(args, 'reference_top_k', 4)}_"
        f"{get_reference_limit_suffix(getattr(args, 'reference_limit', -1))}_"
        f"stages-{get_stages_suffix(getattr(args, 'reference_stages', 'relation'))}"
    )


def format_relation_memory_tag(args) -> str:
    mode = getattr(args, "relation_memory_mode", "none")
    if mode == "none":
        return ""
    return (
        f"mem-{mode}_top{getattr(args, 'relation_memory_top_k', 4)}_"
        f"{getattr(args, 'memory_retrieval_strategy', 'hybrid')}_"
        f"stages-{get_stages_suffix(getattr(args, 'relation_memory_stages', 'relation'))}"
    )


def get_output_file_tag(args):
    tag = f"{args.dataset}_{args.LLM_type}"
    run_mode = getattr(args, "run_mode", "test")
    if run_mode != "test":
        tag += f"_{run_mode}"
    reference_tag = format_reference_tag(args)
    if reference_tag:
        tag += f"_{reference_tag}"
    relation_memory_tag = format_relation_memory_tag(args)
    if relation_memory_tag:
        tag += f"_{relation_memory_tag}"
    return tag
