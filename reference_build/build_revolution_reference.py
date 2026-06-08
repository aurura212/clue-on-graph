import argparse
import importlib.util
import json
import os
import re
import sys
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
REFERENCE_BUILD_DIR = Path(__file__).resolve().parent
PROMPT_REVOLUTION_DIR = REFERENCE_BUILD_DIR / "prompt_revolution"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.append(str(PROJECT_ROOT / "src"))


def load_llm_base() -> Dict[str, str]:
    config_path = PROJECT_ROOT / "src" / "config" / "config.py"
    spec = importlib.util.spec_from_file_location("clue_on_graph_config", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LLM_BASE


def load_module_from_path(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LLM_BASE = load_llm_base()
UTILS_MODULE = None

DEFAULT_INPUTS = {
    "cwq": [
        PROJECT_ROOT / "data" / "raw_train_set" / "ComplexWebQuestions_train.json",
        PROJECT_ROOT / "data" / "datasets" / "cwq_split.json",
    ],
    "webqsp": [
        PROJECT_ROOT / "data" / "raw_train_set" / "WebQSP.train.json",
        PROJECT_ROOT / "data" / "datasets" / "webqsp_split.jsonl",
    ],
    "grailqa": [
        PROJECT_ROOT / "data" / "raw_train_set" / "grailqa_v1.0_train.json",
        PROJECT_ROOT / "data" / "datasets" / "grailqa_split_1.json",
    ],
}

RELATION_PREFIX_PATTERN = re.compile(
    r"(?:ns:|kb:|http://rdf\.freebase\.com/ns/|:)([A-Za-z0-9_.]+)"
)
TYPE_OBJECT_PATTERN = re.compile(
    r"(?:ns:|kb:|http://rdf\.freebase\.com/ns/|:)type\.object\.type\s+"
    r"(?:ns:|kb:|http://rdf\.freebase\.com/ns/|:)([A-Za-z0-9_.]+)"
)

MID_PATTERN = re.compile(r"^[mg]\.[A-Za-z0-9_]+$")
SPARQL_ENTITY_PATTERN = re.compile(
    r"(?:ns:|kb:|http://rdf\.freebase\.com/ns/|:)([mg]\.[A-Za-z0-9_]+)"
)

SKIP_REL_PREFIXES = (
    "common.",
    "freebase.",
    "kg.",
    "rdf",
    "rdfs",
    "type.",
    "wikipedia.",
)


def normalize_dataset_name(dataset: str) -> str:
    lowered = dataset.lower()
    if lowered == "webqsp":
        return "webqsp"
    if lowered.startswith("cwq"):
        return "cwq"
    if lowered.startswith("grailqa"):
        return "grailqa"
    return lowered


def get_kgqa_dataset_name(dataset: str) -> str:
    dataset_key = normalize_dataset_name(dataset)
    if dataset_key == "webqsp":
        return "WebQSP"
    if dataset_key == "cwq":
        return "cwq"
    if dataset_key == "grailqa":
        return "grailqa"
    return dataset


def resolve_default_input(dataset: str) -> Path:
    for path in DEFAULT_INPUTS[normalize_dataset_name(dataset)]:
        if path.exists():
            return path
    tried = "\n".join(str(p) for p in DEFAULT_INPUTS[normalize_dataset_name(dataset)])
    raise FileNotFoundError(f"No default input file found. Tried:\n{tried}")


def default_input_candidates(dataset: str) -> List[Path]:
    dataset_key = normalize_dataset_name(dataset)
    return [path for path in DEFAULT_INPUTS[dataset_key] if path.exists()]


def load_dataset(path: Path) -> List[Dict[str, Any]]:
    if path.suffix == ".jsonl":
        data = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data.append(json.loads(line))
        return data

    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, dict) and "Questions" in raw:
        return raw["Questions"]
    if isinstance(raw, list):
        return raw
    raise ValueError(f"Unsupported dataset structure in {path}")


def clean_relation(rel: str) -> Optional[str]:
    rel = rel.strip().strip("<>").strip()
    if not rel:
        return None
    if MID_PATTERN.match(rel):
        return None
    if rel[0].isdigit():
        return None
    if rel.startswith(SKIP_REL_PREFIXES):
        return None
    if "." not in rel:
        return None
    return rel


def extract_relations_from_reasoning_path(path: Any) -> List[str]:
    if isinstance(path, list):
        if not path:
            return []
        path = path[0]
    if not isinstance(path, str):
        return []

    relations: List[str] = []
    seen = set()
    parts = [x.strip() for x in path.split("->")]
    for part in parts[1:]:
        rel = clean_relation(part)
        if rel and rel not in seen:
            relations.append(rel)
            seen.add(rel)
    return relations


def extract_relation_skeleton_from_sparql(sparql: Optional[str]) -> List[str]:
    """Extract relation tokens from SPARQL with the CoG-style regex strategy."""
    if not sparql:
        return []
    sparql_str = str(sparql)
    type_objects = set(TYPE_OBJECT_PATTERN.findall(sparql_str))
    rels = RELATION_PREFIX_PATTERN.findall(sparql_str)
    cleaned: List[str] = []
    seen = set()
    for rel in rels:
        if rel in type_objects:
            continue
        rel = clean_relation(rel)
        if rel and rel not in seen:
            cleaned.append(rel)
            seen.add(rel)
    return cleaned


def extract_entities_from_sparql(sparql: Optional[str]) -> Dict[str, str]:
    if not sparql:
        return {}
    entities: Dict[str, str] = {}
    for entity_id in SPARQL_ENTITY_PATTERN.findall(str(sparql)):
        entities.setdefault(entity_id, entity_id)
    return entities


def extract_relation_skeleton_from_graph_query(item: Dict[str, Any]) -> List[str]:
    graph_query = item.get("graph_query")
    if not isinstance(graph_query, dict):
        return []
    edges = graph_query.get("edges")
    if not isinstance(edges, list):
        return []

    rels: List[str] = []
    seen = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        rel = clean_relation(str(edge.get("relation", "")))
        if rel and rel not in seen:
            rels.append(rel)
            seen.add(rel)
    return rels


def extract_s_expression(item: Dict[str, Any]) -> Optional[str]:
    for key in ("SExpr", "s_expression", "sexpr"):
        value = item.get(key)
        if value and str(value).lower() != "null":
            return str(value)
    return None


def parse_webqsp_item(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, str], List[Dict[str, str]]]:
    question = item.get("RawQuestion") or item.get("Question") or item.get("ProcessedQuestion")
    sparql = None
    topic_entities: Dict[str, str] = {}
    answers: List[Dict[str, str]] = []

    parses = item.get("Parses")
    if isinstance(parses, list) and parses:
        parse = parses[0]
        sparql = parse.get("Sparql")
        mid = parse.get("TopicEntityMid")
        name = parse.get("TopicEntityName") or parse.get("PotentialTopicEntityMention")
        if mid and name:
            topic_entities[mid] = name
        for ans in parse.get("Answers", []):
            answers.append(
                {
                    "id": str(ans.get("AnswerArgument", "")),
                    "label": str(ans.get("EntityName") or ans.get("AnswerArgument", "")),
                }
            )

    if not topic_entities and item.get("TopicEntityID"):
        topic_entities[str(item["TopicEntityID"])] = str(item.get("TopicEntityName", ""))

    if not answers:
        answer_names = item.get("Answers", [])
        aliases = item.get("Aliases", [])
        for ans in answer_names:
            answers.append({"id": "", "label": str(ans)})
        for alias in aliases:
            if alias and alias != "None":
                answers.append({"id": "", "label": str(alias)})

    return question, sparql, topic_entities, answers


def parse_common_item(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, str], List[Dict[str, str]]]:
    question = item.get("question") or item.get("Question") or item.get("RawQuestion")
    sparql = item.get("sparql") or item.get("sparql_query")
    if not sparql and isinstance(item.get("graph_query"), dict):
        sparql = item["graph_query"].get("sparql")

    topic_entities: Dict[str, str] = {}
    if isinstance(item.get("topic_entity"), dict):
        topic_entities.update({str(k): str(v) for k, v in item["topic_entity"].items()})
    if item.get("TopicEntityID"):
        topic_entities[str(item["TopicEntityID"])] = str(item.get("TopicEntityName", ""))
    if isinstance(item.get("topics"), list):
        for topic in item["topics"]:
            uri = str(topic.get("uri", ""))
            mid = uri.split("/")[-1] if uri else ""
            labels = topic.get("label") or []
            label = labels[0] if isinstance(labels, list) and labels else str(topic.get("label", ""))
            if mid and label and mid.startswith(("m.", "g.")):
                topic_entities.setdefault(mid, str(label))
    if not topic_entities:
        topic_entities.update(extract_entities_from_sparql(sparql))

    answers: List[Dict[str, str]] = []
    raw_answers = item.get("answers", item.get("answer", []))
    if isinstance(raw_answers, str):
        raw_answers = [raw_answers]
    for ans in raw_answers or []:
        if isinstance(ans, str):
            answers.append({"id": "", "label": ans})
        elif isinstance(ans, dict):
            ans_id = ans.get("answer_id") or ans.get("answer_argument") or ans.get("id") or ""
            label_value = ans.get("answer") or ans.get("entity_name") or ans.get("answer_argument") or ans_id
            if isinstance(ans.get("label"), list) and ans["label"]:
                label_value = ans["label"][0]
            answers.append({"id": str(ans_id), "label": str(label_value)})

    return question, sparql, topic_entities, answers


def get_question_id(item: Dict[str, Any], index: int) -> str:
    for key in ("ID", "id", "qid", "QuestionId"):
        if key in item:
            return str(item[key])
    return f"sample_{index}"


def mask_question(question: str, topic_entities: Dict[str, str]) -> str:
    masked = question
    for label in sorted(topic_entities.values(), key=len, reverse=True):
        if label:
            masked = re.sub(re.escape(label), "[ENT]", masked, flags=re.IGNORECASE)
    return masked


def make_clue_reasoning(relations: Iterable[str]) -> List[str]:
    return [f"Follow relation `{rel}` as one step in the gold relation skeleton." for rel in relations]


def read_prompt_template(prompt_name: str) -> str:
    prompt_path = PROMPT_REVOLUTION_DIR / prompt_name
    with prompt_path.open("r", encoding="utf-8") as f:
        return f.read()


def extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"LLM output does not contain a JSON object: {text[:200]}")
    return json.loads(text[start:end + 1])


def normalize_answer_text(text: Any) -> str:
    return str(text).strip().replace(" ", "").lower()


def hit_answer_text(candidate: Any, answers: List[Dict[str, str]]) -> bool:
    candidate_text = normalize_answer_text(candidate)
    if not candidate_text:
        return False
    for answer in answers:
        for key in ("label", "id"):
            answer_text = normalize_answer_text(answer.get(key, ""))
            if answer_text and (candidate_text == answer_text or answer_text in candidate_text):
                return True
    return False


def path_to_serializable(path: List[Tuple[Any, Any, Any]]) -> List[List[str]]:
    return [[str(h), str(r), str(t)] for h, r, t in path]


def extract_tail_candidates(paths: List[List[Tuple[Any, Any, Any]]]) -> List[str]:
    candidates: List[str] = []
    for path in paths:
        if path:
            candidates.append(str(path[-1][-1]))
    return candidates


def parse_blueprint_relations(blueprint: Any) -> List[str]:
    if isinstance(blueprint, list):
        raw_items = blueprint
    elif isinstance(blueprint, str):
        raw_items = [x.strip() for x in blueprint.split("->")]
    else:
        return []

    relations: List[str] = []
    seen = set()
    for item in raw_items:
        text = str(item).strip().strip("`").strip()
        if not text:
            continue
        if "->" in text:
            parts = [x.strip() for x in text.split("->")]
            text_parts = parts[1:] if len(parts) > 1 else parts
        else:
            text_parts = [text]
        for part in text_parts:
            rel = clean_relation(part)
            if rel and rel not in seen:
                relations.append(rel)
                seen.add(rel)
    return relations


def topic_entity_label(entity: Dict[str, str]) -> str:
    label = str(entity.get("label") or entity.get("id") or "")
    return label


def verify_blind_with_direct_freebase(ref: Dict[str, Any], relations: List[str], options: argparse.Namespace) -> Dict[str, Any]:
    try:
        freebase_func = load_module_from_path(
            "clue_on_graph_freebase_func",
            PROJECT_ROOT / "utils" / "freebase_func.py",
        )
        instantiate_relation_path = freebase_func.instantiate_relation_path
    except Exception as exc:
        ref["blind"]["retrieval_result"] = []
        ref["blind"]["hit"] = False
        ref["blind"]["verification"] = {
            "status": "error",
            "reason": f"failed to import utils.freebase_func.instantiate_relation_path: {exc}",
        }
        return ref

    topic_entities = [
        entity for entity in ref.get("topic_entities", [])
        if str(entity.get("id", "")).startswith(("m.", "g."))
    ]
    retrieval_result = []
    all_tail_candidates: List[str] = []
    max_grounded_depth = 0
    failure_reasons: List[str] = []

    for entity in topic_entities:
        entity_id = str(entity["id"])
        label = topic_entity_label(entity)
        reasoning_path = ref["blind"].get("reasoning_paths", {}).get(label)
        entity_relations = extract_relations_from_reasoning_path(reasoning_path) if reasoning_path else relations
        reasoning_path = reasoning_path or (label + " -> " + " -> ".join(entity_relations))
        try:
            result_paths, cur_depth, cur_failures = instantiate_relation_path(
                entity_id,
                label,
                entity_relations,
                max_que=options.max_que,
                directions=options.direct_relation_directions,
            )
        except Exception as exc:
            result_paths = []
            cur_depth = 0
            cur_failures = [f"{label}: direct Freebase verification failed: {exc}"]

        tail_candidates = extract_tail_candidates(result_paths)
        all_tail_candidates.extend(tail_candidates)
        max_grounded_depth = max(max_grounded_depth, cur_depth)
        failure_reasons.extend(cur_failures)

        retrieval_result.append({
            "topic_entity": entity,
            "reasoning_path": reasoning_path,
            "relations": entity_relations,
            "status": "ok" if result_paths else "empty",
            "mode": "direct_freebase",
            "paths": [path_to_serializable(path) for path in result_paths[:options.max_saved_paths]],
            "tail_candidates": tail_candidates[:options.max_saved_candidates],
        })

    return finalize_kg_verification(
        ref,
        relations,
        retrieval_result,
        all_tail_candidates,
        max_grounded_depth,
        failure_reasons,
        options.max_saved_candidates,
    )


def finalize_kg_verification(
    ref: Dict[str, Any],
    relations: List[str],
    retrieval_result: List[Dict[str, Any]],
    all_tail_candidates: List[str],
    max_grounded_depth: int,
    failure_reasons: List[str],
    max_saved_candidates: int,
) -> Dict[str, Any]:
    hit = any(hit_answer_text(candidate, ref["answers"]) for candidate in all_tail_candidates)
    if hit:
        failure_type = "correct"
    elif not all_tail_candidates:
        failure_type = "no_instantiated_answer_candidate"
    elif max_grounded_depth < len(relations):
        failure_type = "path_not_fully_instantiated"
    else:
        failure_type = "instantiated_but_answer_mismatch"

    ref["blind"]["retrieval_result"] = retrieval_result
    ref["blind"]["hit"] = hit
    ref["blind"]["verification"] = {
        "status": "ok",
        "failure_type": failure_type,
        "predicted_relations": relations,
        "max_grounded_depth": max_grounded_depth,
        "target_depth": len(relations),
        "tail_candidates": all_tail_candidates[:max_saved_candidates],
        "failure_reasons": failure_reasons[:max_saved_candidates],
    }
    return ref


def verify_blind_with_kg(ref: Dict[str, Any], options: argparse.Namespace) -> Dict[str, Any]:
    relations = parse_blueprint_relations(ref["blind"].get("blueprint"))
    if not relations:
        ref["blind"]["retrieval_result"] = []
        ref["blind"]["hit"] = False
        ref["blind"]["verification"] = {
            "status": "skipped",
            "reason": "blind blueprint is empty or has no valid relations",
        }
        return ref

    topic_entities = [
        entity for entity in ref.get("topic_entities", [])
        if str(entity.get("id", "")).startswith(("m.", "g."))
    ]
    if not topic_entities:
        ref["blind"]["retrieval_result"] = []
        ref["blind"]["hit"] = False
        ref["blind"]["verification"] = {
            "status": "skipped",
            "reason": "no Freebase topic entity id is available",
        }
        return ref

    if options.kg_verify_mode == "direct":
        return verify_blind_with_direct_freebase(ref, relations, options)

    try:
        kg_instantiation = load_module_from_path(
            "clue_on_graph_kg_instantiation",
            PROJECT_ROOT / "src" / "kg_instantiation.py",
        )
        bfs_for_each_path = kg_instantiation.bfs_for_each_path
        relation_binding = kg_instantiation.relation_binding
    except Exception as exc:
        ref["blind"]["retrieval_result"] = []
        ref["blind"]["hit"] = False
        ref["blind"]["verification"] = {
            "status": "error",
            "reason": f"failed to import KG instantiation tools: {exc}",
        }
        return ref

    retrieval_result = []
    all_tail_candidates: List[str] = []
    max_grounded_depth = 0
    failure_reasons: List[str] = []

    for entity in topic_entities:
        entity_id = str(entity["id"])
        label = topic_entity_label(entity)
        reasoning_path = ref["blind"].get("reasoning_paths", {}).get(label)
        entity_relations = extract_relations_from_reasoning_path(reasoning_path) if reasoning_path else relations
        reasoning_path = reasoning_path or (label + " -> " + " -> ".join(entity_relations))
        init_reasoning_path = {label: reasoning_path}
        try:
            binded_relations = relation_binding(init_reasoning_path, topk=options.relation_binding_topk)
            sequential_relation_candidates = [
                binded_relations.get(relation, [relation])
                for relation in entity_relations
            ]
            result_paths, grounded_knowledge_current, ungrounded_neighbor_relation_dict, _ = bfs_for_each_path(
                entity_id,
                entity_relations,
                sequential_relation_candidates,
                options,
                options.max_que,
            )
        except Exception as exc:
            failure_reasons.append(f"{label}: KG verification failed: {exc}")
            retrieval_result.append({
                "topic_entity": entity,
                "reasoning_path": reasoning_path,
                "status": "error",
                "error": str(exc),
                "paths": [],
                "tail_candidates": [],
            })
            continue

        tail_candidates = extract_tail_candidates(result_paths)
        all_tail_candidates.extend(tail_candidates)
        if grounded_knowledge_current:
            max_grounded_depth = max(max_grounded_depth, max(x[-1] for x in grounded_knowledge_current))
        if not result_paths:
            failure_reasons.append(f"{label}: no instantiated path")

        retrieval_result.append({
            "topic_entity": entity,
            "reasoning_path": reasoning_path,
            "relations": entity_relations,
            "status": "ok",
            "paths": [path_to_serializable(path) for path in result_paths[:options.max_saved_paths]],
            "tail_candidates": tail_candidates[:options.max_saved_candidates],
            "ungrounded_neighbor_relations": {
                str(k): [str(x) for x in v[:options.max_saved_candidates]]
                for k, v in ungrounded_neighbor_relation_dict.items()
            },
        })

    return finalize_kg_verification(
        ref,
        relations,
        retrieval_result,
        all_tail_candidates,
        max_grounded_depth,
        failure_reasons,
        options.max_saved_candidates,
    )


def render_prompt(prompt_name: str, **kwargs: Any) -> str:
    template = read_prompt_template(prompt_name)
    safe_kwargs = {
        key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
        for key, value in kwargs.items()
    }
    for key, value in safe_kwargs.items():
        template = template.replace("{" + key + "}", value)
    return template


def call_prompt_json(
    prompt_name: str,
    options: argparse.Namespace,
    llm_calls: int,
    **kwargs: Any,
) -> Tuple[Dict[str, Any], int]:
    utils_module = load_utils_module()
    prompt = render_prompt(prompt_name, **kwargs)
    response, llm_calls = utils_module.run_llm(
        prompt,
        temperature=options.temperature,
        max_tokens=options.max_token,
        llm_calls=llm_calls,
        openai_api_keys=options.openai_api_keys,
        engine=options.LLM_type,
    )
    return extract_json_object(response), llm_calls


def load_utils_module():
    global UTILS_MODULE
    if UTILS_MODULE is None:
        UTILS_MODULE = load_module_from_path(
            "clue_on_graph_utils",
            PROJECT_ROOT / "utils" / "utils.py",
        )
    return UTILS_MODULE


def count_tokens_if_available(text: str) -> int:
    try:
        import tiktoken

        encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        return len(encoding.encode(text))
    except Exception:
        return 0


def kgqa_relation_extract(
    question: str,
    topic_entity: str,
    cand_relation: List[str],
    options: argparse.Namespace,
    input_token_cnt: int,
    output_token_cnt: int,
    llm_calls: int,
) -> Tuple[List[Tuple[Any, ...]], int, int, int]:
    utils_module = load_utils_module()
    prompt = (PROJECT_ROOT / "src" / "prompt_md" / "extract_relation.md").read_text(encoding="utf-8")
    relation_str = "; ".join(cand_relation)
    prompt_1 = (
        prompt % ""
        + "\nQuestion:" + question
        + "\nTopic Entity:" + topic_entity
        + "\nRelations" + relation_str
        + "\nAnswer:"
    )
    response, llm_calls = utils_module.run_llm(
        prompt_1,
        options.temperature,
        options.max_token,
        llm_calls,
        options.openai_api_keys,
        pipe=None,
        engine=options.LLM_type,
    )
    rel_with_score = re.findall(r"\([\s\S]*?\)", response, re.DOTALL)
    rel_score_tuple: List[Tuple[Any, ...]] = []
    for item in rel_with_score:
        try:
            parsed = eval(item)
            if isinstance(parsed, tuple):
                rel_score_tuple.append(parsed)
        except Exception:
            continue

    if options.count_token_cost:
        input_token_cnt += count_tokens_if_available(prompt_1)
        output_token_cnt += count_tokens_if_available(response)

    return rel_score_tuple, input_token_cnt, output_token_cnt, llm_calls


def kgqa_get_init_reasoning_path(
    question: str,
    topic_ent: List[str],
    options: argparse.Namespace,
    input_token_cnt: int,
    output_token_cnt: int,
    llm_calls: int,
    cand_relation: Optional[Dict[str, List[str]]] = None,
) -> Tuple[Dict[str, str], int, int, int]:
    utils_module = load_utils_module()
    dataset_name = get_kgqa_dataset_name(options.dataset).split("_")[0]
    if options.relation_check == 1:
        prompt_path = PROJECT_ROOT / "src" / "prompt_md" / f"{dataset_name}_init_with_relation.md"
        prompt_init_path = prompt_path.read_text(encoding="utf-8")
        prompt_init_path += (
            "Question: " + question
            + "\nTopic Entities:" + ", ".join(topic_ent)
            + "\nValuable Relations:" + str(cand_relation)
            + "\nThought:"
        )
    else:
        prompt_path = PROJECT_ROOT / "src" / "prompt_md" / f"{dataset_name}_init.md"
        prompt_init_path = prompt_path.read_text(encoding="utf-8")
        prompt_init_path += (
            "Question: " + question
            + "\nTopic Entities:" + ", ".join(topic_ent)
            + "\nThought:"
        )

    default_relation_path = {entity: entity for entity in topic_ent}
    init_reasoning_path = default_relation_path.copy()
    response = ""
    for _ in range(3):
        try:
            response, llm_calls = utils_module.run_llm(
                prompt_init_path,
                options.temperature,
                options.max_token_reasoning,
                llm_calls,
                openai_api_keys=options.openai_api_keys,
                pipe=None,
                engine=options.LLM_type,
            )
            response_dict = eval(response.split("Path:")[-1].strip())
            for key, value in response_dict.items():
                if isinstance(value, list) and value:
                    if isinstance(value[0], str):
                        init_reasoning_path[key] = value[0]
                    elif value[0]:
                        init_reasoning_path[key] = value[0][0]
            if isinstance(init_reasoning_path, dict):
                break
        except Exception as exc:
            init_reasoning_path = default_relation_path.copy()
            if options.verbose:
                print(f"initial reasoning path prediction failed: {exc}")
                print(response)

    if options.count_token_cost:
        input_token_cnt += count_tokens_if_available(prompt_init_path)
        output_token_cnt += count_tokens_if_available(response)

    return init_reasoning_path, input_token_cnt, output_token_cnt, llm_calls


def build_blind_with_kgqa_initial_prediction(
    ref: Dict[str, Any],
    options: argparse.Namespace,
    llm_calls: int,
) -> Tuple[Dict[str, Any], int]:
    """Generate blind paths with kgqa.py's initial prediction flow, without demos or reflection."""
    if options.openai_api_base:
        os.environ["OPENAI_API_BASE"] = options.openai_api_base
    utils_module = load_utils_module()
    origin_dataset = options.dataset
    options.dataset = get_kgqa_dataset_name(origin_dataset)
    question = ref["question"]
    topic_entities = ref["topic_entities"]
    topic_ent_list = [topic_entity_label(entity) for entity in topic_entities]
    topic_ent_relation = {label: [] for label in topic_ent_list}
    input_token_cnt = 0
    output_token_cnt = 0
    first_relations: Dict[str, List[str]] = {}

    try:
        if options.relation_check == 1:
            for entity in topic_entities:
                entity_id = str(entity.get("id", ""))
                label = topic_entity_label(entity)
                try:
                    neighbor_relations = utils_module.get_ent_one_hop_rel(entity_id)
                    rel_score_tuple, input_token_cnt, output_token_cnt, llm_calls = kgqa_relation_extract(
                        question,
                        label,
                        neighbor_relations,
                        options,
                        input_token_cnt,
                        output_token_cnt,
                        llm_calls,
                    )
                    selected_relations = [
                        item[0] for item in rel_score_tuple
                        if isinstance(item, tuple) and len(item) > 0 and isinstance(item[0], str)
                    ]
                    topic_ent_relation[label] = selected_relations
                    first_relations[label] = selected_relations
                except Exception as exc:
                    first_relations[label] = []
                    ref["blind"].setdefault("prediction_errors", []).append(
                        f"{label}: relation extraction failed: {exc}"
                    )

        reasoning_paths, input_token_cnt, output_token_cnt, llm_calls = kgqa_get_init_reasoning_path(
            question,
            topic_ent_list,
            options,
            input_token_cnt=input_token_cnt,
            output_token_cnt=output_token_cnt,
            llm_calls=llm_calls,
            cand_relation=topic_ent_relation,
        )
    except Exception as exc:
        ref["blind"].setdefault("prediction_errors", []).append(
            f"initial reasoning path prediction failed: {exc}"
        )
        reasoning_paths = {label: label for label in topic_ent_list}
    finally:
        options.dataset = origin_dataset

    if not isinstance(reasoning_paths, dict):
        ref["blind"].setdefault("prediction_errors", []).append(
            f"initial reasoning path prediction returned non-dict: {type(reasoning_paths)}"
        )
        reasoning_paths = {label: label for label in topic_ent_list}

    blueprint: List[str] = []
    seen = set()
    for path in reasoning_paths.values():
        for relation in extract_relations_from_reasoning_path(path):
            if relation not in seen:
                blueprint.append(relation)
                seen.add(relation)

    ref["blind"]["blueprint"] = blueprint
    ref["blind"]["reasoning_paths"] = reasoning_paths
    ref["blind"]["first_relations"] = first_relations
    ref["blind"]["clue_reasoning"] = [
        f"{label}: {path}" for label, path in reasoning_paths.items()
    ]
    ref["blind"]["answer_type"] = ref["blind"].get("answer_type", "unknown")
    ref["blind"]["constraints"] = ref["blind"].get("constraints", [])
    ref["blind"]["prediction_source"] = "src/kgqa.py:get_init_reasoning_path_without_reference_or_reflection"
    ref["blind"]["token_count"] = {
        "input_token": input_token_cnt,
        "output_token": output_token_cnt,
    }

    return ref, llm_calls


def maybe_enrich_with_prompt_revolution(
    ref: Dict[str, Any],
    options: argparse.Namespace,
    llm_calls: int,
) -> Tuple[Dict[str, Any], int]:
    topic_entities = ref["topic_entities"]

    if options.use_llm_gold_clue:
        result, llm_calls = call_prompt_json(
            "gold_clue_reasoning.md",
            options,
            llm_calls,
            question=ref["question"],
            topic_entities=topic_entities,
            blueprint=ref["gold"]["blueprint"],
        )
        clue_reasoning = result.get("clue_reasoning")
        if isinstance(clue_reasoning, list) and clue_reasoning:
            ref["gold"]["clue_reasoning"] = [str(x) for x in clue_reasoning]
            ref["gold"]["clue_reasoning_source"] = "prompt_revolution/gold_clue_reasoning.md"

    if options.use_llm_blind:
        ref, llm_calls = build_blind_with_kgqa_initial_prediction(ref, options, llm_calls)

    if options.verify_blind_kg:
        ref = verify_blind_with_kg(ref, options)

    if options.use_llm_evaluation:
        result, llm_calls = call_prompt_json(
            "evaluate_blind_reference.md",
            options,
            llm_calls,
            question=ref["question"],
            topic_entities=topic_entities,
            gold_blueprint=ref["gold"]["blueprint"],
            gold_clue_reasoning=ref["gold"]["clue_reasoning"],
            blind_blueprint=ref["blind"].get("blueprint", []),
            blind_clue_reasoning=ref["blind"].get("clue_reasoning", []),
            retrieval_result=ref["blind"].get("retrieval_result", []),
            hit=ref["blind"].get("hit"),
        )
        ref["evaluation"].update({
            "score": result.get("score"),
            "correct_reason": result.get("correct_reason", ""),
            "error_step": result.get("error_step", ""),
            "correction": result.get("correction", ""),
            "guardrail": result.get("guardrail", ""),
        })

    return ref, llm_calls


def build_reference_item(dataset: str, item: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    dataset_key = normalize_dataset_name(dataset)
    if dataset_key == "webqsp":
        question, sparql, topic_entities, answers = parse_webqsp_item(item)
    else:
        question, sparql, topic_entities, answers = parse_common_item(item)

    if not question:
        return None

    sparql_relations = extract_relation_skeleton_from_sparql(sparql)
    graph_relations = extract_relation_skeleton_from_graph_query(item)
    relations = sparql_relations or graph_relations
    if not relations:
        return None

    relation_path = " -> ".join(relations)
    return {
        "id": get_question_id(item, index),
        "dataset": dataset,
        "question": question,
        "masked_question": mask_question(question, topic_entities),
        "topic_entities": [
            {"id": entity_id, "label": label}
            for entity_id, label in topic_entities.items()
        ],
        "answers": answers,
        "gold": {
            "blueprint": relations,
            "relation_path": relation_path,
            "clue_reasoning": make_clue_reasoning(relations),
            "sparql": sparql,
            "s_expression": extract_s_expression(item),
            "source": "sparql_regex" if sparql_relations else "graph_query_edges",
        },
        "blind": {
            "blueprint": [],
            "clue_reasoning": [],
            "retrieval_result": [],
            "hit": None,
        },
        "evaluation": {
            "score": None,
            "correct_reason": "",
            "error_step": "",
            "correction": "",
            "guardrail": "",
        },
        "memory_item": {
            "title": "",
            "description": "",
            "content": "",
        },
    }


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, indent=4) + "\n")
            count += 1
    return count


def get_limit_suffix(limit: int) -> str:
    return "all" if limit < 0 else f"limit{limit}"


def get_default_output_file(dataset_key: str, limit: int) -> Path:
    return (
        PROJECT_ROOT
        / "data"
        / "revolution_reference"
        / f"{dataset_key}_reference_{get_limit_suffix(limit)}.jsonl"
    )


def build_references(
    dataset: str,
    input_file: Path,
    options: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    data = load_dataset(input_file)
    refs: List[Dict[str, Any]] = []
    llm_calls = 0
    stats = {
        "loaded": len(data),
        "processed": 0,
        "written": 0,
        "skipped_without_question": 0,
        "skipped_without_relation_skeleton": 0,
        "llm_calls": 0,
    }

    for index, item in enumerate(data):
        if options.limit >= 0 and stats["processed"] >= options.limit:
            break
        stats["processed"] += 1
        if not isinstance(item, dict):
            stats["skipped_without_question"] += 1
            continue
        ref = build_reference_item(dataset, item, index)
        if ref is None:
            question = item.get("question") or item.get("Question") or item.get("RawQuestion")
            if question:
                stats["skipped_without_relation_skeleton"] += 1
            else:
                stats["skipped_without_question"] += 1
            continue
        if uses_prompt_revolution(options) or options.verify_blind_kg:
            ref, llm_calls = maybe_enrich_with_prompt_revolution(ref, options, llm_calls)
        refs.append(ref)

    stats["written"] = len(refs)
    stats["llm_calls"] = llm_calls
    return refs, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a revolution-style reference base with gold relation skeletons."
    )
    parser.add_argument("--dataset", required=True, help="Dataset name: cwq, WebQSP, or grailqa.")
    parser.add_argument("--input_file", default="", help="Training file. Defaults to data/raw_train_set/*.")
    parser.add_argument(
        "--output_file",
        default="",
        help="Output JSONL path. Defaults to data/revolution_reference/{dataset}_reference_{limit}.jsonl.",
    )
    parser.add_argument("--limit", type=int, default=-1, help="Maximum number of source samples to process.")
    parser.add_argument("--use_llm_gold_clue", action="store_true", help="Call prompt_revolution/gold_clue_reasoning.md.")
    parser.add_argument(
        "--use_llm_blind",
        action="store_true",
        help="Generate blind initial paths with src/kgqa.py without reference examples or reflection.",
    )
    parser.add_argument("--use_llm_evaluation", action="store_true", help="Call prompt_revolution/evaluate_blind_reference.md.")
    parser.add_argument("--use_llm_memory", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--verify_blind_kg", action="store_true", help="Verify blind blueprint with KG retrieval.")
    parser.add_argument("--kg_verify_mode", choices=["direct", "instantiation"], default="direct")
    parser.add_argument(
        "--direct_relation_directions",
        nargs="+",
        choices=["forward", "backward"],
        default=["forward", "backward"],
    )
    parser.add_argument("--relation_binding_topk", type=int, default=5)
    parser.add_argument("--max_que", type=int, default=150)
    parser.add_argument("--max_saved_paths", type=int, default=10)
    parser.add_argument("--max_saved_candidates", type=int, default=20)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max_token", type=int, default=1024)
    parser.add_argument("--max_token_reasoning", type=int, default=2048)
    parser.add_argument("--relation_check", type=int, default=1)
    parser.add_argument("--count_token_cost", type=bool, default=True)
    parser.add_argument("--llm", type=str, choices=LLM_BASE.keys(), default="gpt35")
    parser.add_argument("--openai_api_keys", type=str, default="")
    parser.add_argument("--openai_api_base", type=str, default="")
    args = parser.parse_args()
    args.LLM_type = LLM_BASE[args.llm]
    if uses_prompt_revolution(args):
        if args.openai_api_base:
            os.environ["OPENAI_API_BASE"] = args.openai_api_base
        if "OPENAI_API_BASE" not in os.environ:
            raise ValueError("--openai_api_base or OPENAI_API_BASE is required when using prompt_revolution LLM calls.")
    return args


def uses_prompt_revolution(options: argparse.Namespace) -> bool:
    return any(
        [
            options.use_llm_gold_clue,
            options.use_llm_blind,
            options.use_llm_evaluation,
        ]
    )


def main() -> None:
    args = parse_args()
    dataset_key = normalize_dataset_name(args.dataset)
    input_files = (
        [Path(args.input_file).resolve()]
        if args.input_file
        else default_input_candidates(dataset_key)
    )
    if not input_files:
        resolve_default_input(dataset_key)

    output_file = (
        Path(args.output_file).resolve()
        if args.output_file
        else get_default_output_file(dataset_key, args.limit)
    )

    refs: List[Dict[str, Any]] = []
    stats: Dict[str, int] = {}
    input_file = input_files[0]
    last_error: Optional[Exception] = None
    for candidate in input_files:
        input_file = candidate
        try:
            refs, stats = build_references(args.dataset, candidate, args)
            break
        except JSONDecodeError as exc:
            last_error = exc
            if args.input_file:
                raise
            print(f"[Warning] Failed to parse {candidate}: {exc}. Trying next default input.")
    else:
        raise RuntimeError(f"Could not load any default input for {args.dataset}") from last_error

    write_jsonl(output_file, refs)

    print(json.dumps(
        {
            "dataset": args.dataset,
            "limit": args.limit,
            "input_file": str(input_file),
            "output_file": str(output_file),
            "stats": stats,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
