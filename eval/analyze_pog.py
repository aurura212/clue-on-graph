#!/usr/bin/env python3
"""
Stage-wise PoG diagnosis with optional LLM attribution over pog_trace.

Uses output jsonl with `pog_trace` + gold labels.
Results and traces live under PoG/result/<run_folder>/:
  - results.jsonl
  - pog_trace.jsonl
Run folder name: {config_tag}_n{question_count}_{YYYYMMDD_HHMMSS}
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from typing import Any

import openai

from utils import (
    align,
    dataset_align_key,
    dataset_type_field,
    exact_match,
    load_eval_aliases,
    prepare_dataset_for_eval,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
POG_DIR = os.path.join(PROJECT_ROOT, "PoG")
if POG_DIR not in sys.path:
    sys.path.insert(0, POG_DIR)
from jsonl_io import DEFAULT_JSONL_INDENT, format_jsonl_record

POG_DIAGNOSIS_DIR = os.path.join(PROJECT_ROOT, "pog_diagnosis")

_llm_api_path = os.path.join(PROJECT_ROOT, "utils", "llm_api.py")
_llm_api_spec = importlib.util.spec_from_file_location("clue_on_graph_llm_api", _llm_api_path)
_llm_api = importlib.util.module_from_spec(_llm_api_spec)
_llm_api_spec.loader.exec_module(_llm_api)
get_chat_completion_extra_kwargs = _llm_api.get_chat_completion_extra_kwargs
is_openai_compatible_engine = _llm_api.is_openai_compatible_engine

# ---------------------------------------------------------------------------
# PoG pipeline stages (ordered)
# ---------------------------------------------------------------------------
STAGES = [
    "decomposition",
    "relation_retrieval",
    "entity_retrieval",
    "entity_prune",
    "depth_exploration",
    "memory_update",
    "sufficiency_reasoning",
    "answer_generation",
    "cot_fallback",
]

FAILURE_LABELS = [
    "no_graph_exploration",
    "relation_miss",
    "entity_miss_given_relation",
    "insufficient_depth",
    "memory_contradiction_or_gap",
    "false_sufficiency",
    "generation_wrong_despite_evidence",
    "generation_without_evidence",
    "mid_entity_unresolved",
    "parse_or_format_error",
    "unknown",
]

SUCCESS_LABELS = [
    "evidence_complete_in_chains",
    "evidence_partial_parametric_fill",
    "parametric_only_no_chains",
    "multi_hop_path_completed",
]

LLM_STAGES = [
    "decomposition",
    "relation_retrieval",
    "entity_retrieval",
    "entity_prune",
    "depth_exploration",
    "memory_update",
    "sufficiency_reasoning",
    "answer_generation",
    "reverse_retrieval",
    "cot_fallback",
    "none",
]

RELATION_PREFIX_PATTERN = re.compile(
    r"(?:ns:|kb:|http://rdf\.freebase\.com/ns/|:)([A-Za-z0-9_.]+)"
)
TYPE_OBJECT_PATTERN = re.compile(
    r"(?:ns:|kb:|http://rdf\.freebase\.com/ns/|:)type\.object\.type\s+"
    r"(?:ns:|kb:|http://rdf\.freebase\.com/ns/|:)([A-Za-z0-9_.]+)"
)
MID_PATTERN = re.compile(r"^[mg]\.[A-Za-z0-9_]+$")
SKIP_REL_PREFIXES = (
    "common.",
    "freebase.",
    "kg.",
    "rdf",
    "rdfs",
    "type.",
    "wikipedia.",
)

SUCCESS_ATTRIBUTION_PROMPT = """You are an expert at explaining why a Knowledge Graph QA pipeline succeeded.

This question was answered CORRECTLY. Your job is ONLY to explain where PoG succeeded — do NOT analyze failures or speculate about mistakes.

PoG runs in depths. At each depth:
1) relation_prune (select relations from Freebase),
2) before_entity_prune / after_entity_prune (retrieve and LLM-prune entities),
3) memory_update,
4) evaluation (answer, sufficient, stop).

Given the question, gold answers, the CORRECT model prediction, gold relations, and pog_trace, determine:
- Which depth(s) provided the decisive evidence or reasoning step
- Which pipeline stage was most critical for success
- Per depth: what worked (relations found, entities kept, memory, evaluation)

Output ONLY valid JSON (success attribution only — no error fields):
{{
  "attribution_type": "success",
  "success_depths": [list of integers],
  "success_stage": one of {stages},
  "success_detail": "one concise sentence explaining why the answer is correct",
  "per_depth_notes": [
    {{"depth": 1, "stage": "stage name", "what_worked": "..."}}
  ]
}}

Stages: {stages}

Question: {question}
Gold answers: {gold_answers}
Gold relations: {gold_relations}
Model prediction (CORRECT): {prediction}
Final stop reason: {final_stop_reason}
Final stop depth: {final_stop_depth}
Heuristic pre-check: {heuristic_summary}

PoG trace:
{trace_json}
"""

FAILURE_ATTRIBUTION_PROMPT = """You are an expert at diagnosing Knowledge Graph QA pipeline failures.

This question was answered INCORRECTLY. Your job is ONLY to explain where PoG failed — do NOT analyze what went right or why the answer could be correct.

PoG runs in depths. At each depth:
1) relation_prune (select relations from Freebase),
2) before_entity_prune / after_entity_prune (retrieve and LLM-prune entities),
3) memory_update,
4) evaluation (answer, sufficient, stop).

Given the question, gold answers, the WRONG model prediction, gold relations, and pog_trace, determine:
- The earliest depth where a critical mistake happened
- Which pipeline stage caused the failure
- Per depth: what went wrong (wrong relation, pruned gold entity, bad memory, false sufficient, wrong final answer)

Output ONLY valid JSON (failure attribution only — no success fields):
{{
  "attribution_type": "failure",
  "error_depth": integer (earliest critical mistake depth),
  "error_stage": one of {stages},
  "error_detail": "one concise sentence explaining why the answer is wrong",
  "per_depth_notes": [
    {{"depth": 1, "stage": "stage name", "what_failed": "..."}}
  ]
}}

Stages: {stages}

Question: {question}
Gold answers: {gold_answers}
Gold relations: {gold_relations}
Model prediction (WRONG): {prediction}
Final stop reason: {final_stop_reason}
Final stop depth: {final_stop_depth}
Heuristic pre-check: {heuristic_summary}

PoG trace:
{trace_json}
"""


def resolve_path(path: str) -> str:
    if not path:
        return path
    if os.path.isabs(path):
        return os.path.normpath(path)
    if path.startswith("..") or path.startswith("./"):
        return os.path.normpath(os.path.join(os.getcwd(), path))
    return os.path.normpath(os.path.join(PROJECT_ROOT, path))


def diagnosis_run_tag(output_file: str) -> str:
    base = os.path.basename(output_file.rstrip("/"))
    if base in ("results.jsonl", "pog_trace.jsonl"):
        base = os.path.basename(os.path.dirname(output_file.rstrip("/")))
    if base.startswith("PoG_"):
        base = base[4:]
    return base


def default_diagnosis_paths(output_file: str) -> tuple[str, str]:
    run_tag = diagnosis_run_tag(output_file)
    run_dir = os.path.join(POG_DIAGNOSIS_DIR, run_tag)
    report = os.path.join(run_dir, f"{run_tag}.jsonl")
    summary = os.path.join(run_dir, f"{run_tag}_summary.json")
    return report, summary


def parse_results(results_str: str) -> dict[str, Any]:
    if not results_str:
        return {}
    start_i = results_str.find("{")
    if start_i == -1:
        return {"_raw": results_str}
    chunk = results_str[start_i:]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        pass
    for pattern in [
        r'"Answer":\s*"([^"]+)"',
        r'"Answer":\s*(\[[^\]]+\])',
    ]:
        m = re.search(pattern, chunk)
        if m:
            ans = m.group(1)
            try:
                ans = ast.literal_eval(ans)
            except (SyntaxError, ValueError):
                pass
            return {"A": {"Answer": ans, "Sufficient": None}, "_parse_fallback": True}
    return {"_raw": results_str}


def normalize_text(s: str) -> str:
    return str(s).strip().replace(" ", "").lower()


def text_hits(text: str, candidates: list[str]) -> list[str]:
    norm_text = normalize_text(text)
    hits = []
    for c in candidates:
        nc = normalize_text(c)
        if not nc:
            continue
        if nc in norm_text or norm_text in nc:
            hits.append(c)
    return hits


def flatten_triples(reasoning_chains: list) -> list[tuple[str, str, str]]:
    triples = []
    for depth_layer in reasoning_chains or []:
        for chain in depth_layer or []:
            for t in chain or []:
                if isinstance(t, (list, tuple)) and len(t) == 3:
                    triples.append((str(t[0]), str(t[1]), str(t[2])))
    return triples


def extract_chain_entities(triples: list[tuple[str, str, str]]) -> set[str]:
    ents = set()
    for h, _, t in triples:
        ents.add(h)
        ents.add(t)
    return ents


def extract_chain_relations(triples: list[tuple[str, str, str]]) -> set[str]:
    return {r for _, r, _ in triples}


def clean_relation(rel: str) -> str | None:
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


def extract_relations_from_sparql(sparql: str | None) -> list[str]:
    if not sparql:
        return []
    sparql_str = str(sparql)
    type_objects = set(TYPE_OBJECT_PATTERN.findall(sparql_str))
    rels = RELATION_PREFIX_PATTERN.findall(sparql_str)
    cleaned: list[str] = []
    seen: set[str] = set()
    for rel in rels:
        if rel in type_objects:
            continue
        rel = clean_relation(rel)
        if rel and rel not in seen:
            cleaned.append(rel)
            seen.add(rel)
    return cleaned


def extract_relations_from_graph_query(origin_data: dict) -> list[str]:
    graph_query = origin_data.get("graph_query")
    if not isinstance(graph_query, dict):
        return []
    edges = graph_query.get("edges")
    if not isinstance(edges, list):
        return []

    rels: list[str] = []
    seen: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        rel = clean_relation(str(edge.get("relation", "")))
        if rel and rel not in seen:
            rels.append(rel)
            seen.add(rel)
    return rels


def gold_relations(origin_data: dict, dataset_name: str = "webqsp") -> set[str]:
    key = dataset_align_key(dataset_name)
    if key == "webqsp":
        rels: set[str] = set()
        for parse in origin_data.get("Parses", []):
            chain = parse.get("InferentialChain") or []
            for rel in chain:
                rels.add(rel)
        return rels

    sparql = origin_data.get("sparql") or origin_data.get("sparql_query")
    if not sparql and isinstance(origin_data.get("graph_query"), dict):
        sparql = origin_data["graph_query"].get("sparql")
    sparql_rels = extract_relations_from_sparql(sparql)
    graph_rels = extract_relations_from_graph_query(origin_data)
    return set(sparql_rels or graph_rels)


def gold_answers_display(
    origin_data: dict,
    gold_with_aliases: list[str],
    dataset_name: str = "webqsp",
) -> list[str]:
    key = dataset_align_key(dataset_name)
    if key == "webqsp":
        answers: list[str] = []
        for parse in origin_data.get("Parses", []):
            for ans in parse.get("Answers", []):
                if ans.get("EntityName"):
                    answers.append(ans["EntityName"])
                elif ans.get("AnswerArgument"):
                    answers.append(ans["AnswerArgument"])
        return list(set(answers)) or list(gold_with_aliases)

    if key == "cwq":
        if "answers" in origin_data:
            raw = origin_data["answers"]
        else:
            raw = origin_data.get("answer")
        if isinstance(raw, list):
            return list(set(raw)) or list(gold_with_aliases)
        if raw is not None:
            return [raw]
        return list(gold_with_aliases)

    if key == "grailqa":
        answers = []
        for ans in origin_data.get("answer", []):
            if ans.get("entity_name"):
                answers.append(ans["entity_name"])
            elif ans.get("answer_argument"):
                answers.append(ans["answer_argument"])
        return list(set(answers)) or list(gold_with_aliases)

    return list(gold_with_aliases)


def question_metadata(origin_data: dict, dataset_name: str) -> dict[str, Any]:
    meta: dict[str, Any] = {"dataset": dataset_align_key(dataset_name)}
    type_field = dataset_type_field(dataset_name)
    if type_field:
        meta["question_type"] = origin_data.get(type_field)
    if "qid" in origin_data:
        meta["qid"] = origin_data["qid"]
    if "ID" in origin_data:
        meta["sample_id"] = origin_data["ID"]
    return meta


def load_mem_artifacts(mem_root: str, question: str) -> dict[str, Any]:
    qdir = os.path.join(mem_root, question[:255])
    out: dict[str, Any] = {"mem_dir": qdir, "subq": None, "mem": None}
    for name in ("subq", "mem"):
        path = os.path.join(qdir, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                out[name] = f.read().strip()
    return out


def parse_subq(subq_raw: str | None) -> list[str]:
    if not subq_raw:
        return []
    try:
        val = ast.literal_eval(subq_raw)
        if isinstance(val, list):
            return [str(x) for x in val]
    except (SyntaxError, ValueError):
        pass
    return [subq_raw]


def parse_mem(mem_raw: str | None) -> dict[str, str]:
    if not mem_raw:
        return {}
    start = mem_raw.find("{")
    end = mem_raw.rfind("}")
    if start == -1 or end == -1:
        return {"_raw": mem_raw}
    try:
        obj = json.loads(mem_raw[start : end + 1])
        if isinstance(obj, dict):
            return {str(k): str(v) for k, v in obj.items()}
    except json.JSONDecodeError:
        pass
    return {"_raw": mem_raw}


def infer_path_type(reasoning_chains: list, call_num: int, sufficient: str | None) -> str:
    if not reasoning_chains:
        if call_num <= 5:
            return "early_stop_or_cot"
        return "deep_search_exhausted"
    depths = len(reasoning_chains)
    if depths >= 2:
        return "multi_depth_graph"
    return "single_depth_graph"


def gold_in_entities(entities: set[str], gold_with_aliases: list[str]) -> list[str]:
    hits = []
    for g in gold_with_aliases:
        for e in entities:
            if exact_match(e, [g]):
                hits.append(g)
                break
    return list(set(hits))


def has_unresolved_mid(triples: list[tuple[str, str, str]], prediction: str) -> bool:
    if str(prediction).startswith("m."):
        return True
    for _, _, tail in triples:
        if tail.startswith("m.") and tail in str(prediction):
            return True
    return False


def check_memory_stage(
    mem_dict: dict[str, str],
    gold_with_aliases: list[str],
    prediction: str,
    triples: list[tuple[str, str, str]],
) -> dict[str, Any]:
    mem_text = " ".join(mem_dict.values()) if mem_dict else ""
    gold_in_mem = text_hits(mem_text, gold_with_aliases) if mem_text else []
    pred_in_mem = text_hits(mem_text, [str(prediction)]) if mem_text and prediction else []

    chain_text = " ".join(f"{h} {r} {t}" for h, r, t in triples)
    issues = []
    if mem_text and gold_with_aliases and not gold_in_mem and triples:
        gold_in_chain = gold_in_entities(extract_chain_entities(triples), gold_with_aliases)
        if gold_in_chain:
            issues.append("gold_in_chains_but_not_in_memory")
    if pred_in_mem and not gold_in_mem and prediction:
        issues.append("memory_has_prediction_not_gold")
    return {
        "gold_in_memory": gold_in_mem,
        "prediction_in_memory": bool(pred_in_mem),
        "memory_issues": issues,
        "has_memory_file": bool(mem_text),
    }


def diagnose_one(
    data: dict,
    origin_data: dict,
    gold_with_aliases: list[str],
    mem_artifacts: dict[str, Any],
    llm_config: dict[str, Any] | None = None,
    dataset_name: str = "webqsp",
) -> dict[str, Any]:
    question = data.get("RawQuestion") or data.get("question", "")
    parsed = parse_results(data.get("results", ""))
    a_block = parsed.get("A", parsed)
    if isinstance(a_block, dict):
        prediction = a_block.get("Answer", a_block.get("answer"))
        sufficient = a_block.get("Sufficient", a_block.get("Known"))
    else:
        prediction = parsed.get("Answer")
        sufficient = parsed.get("Sufficient")

    if isinstance(prediction, list):
        pred_list = [str(x) for x in prediction]
        pred_str = ", ".join(pred_list)
    else:
        pred_list = [str(prediction)] if prediction is not None else []
        pred_str = str(prediction) if prediction is not None else ""

    is_correct = False
    if pred_list and pred_list[0].lower() not in ("null", "none", ""):
        for p in pred_list:
            if exact_match(str(p), gold_with_aliases):
                is_correct = True
                break
    elif pred_str.lower() in ("null", "none", ""):
        is_correct = False
    else:
        is_correct = exact_match(pred_str, gold_with_aliases)

    triples = flatten_triples(data.get("reasoning_chains", []))
    chain_ents = extract_chain_entities(triples)
    chain_rels = extract_chain_relations(triples)
    g_rels = gold_relations(origin_data, dataset_name)
    g_answers_raw = gold_answers_display(origin_data, gold_with_aliases, dataset_name)

    gold_in_chain = gold_in_entities(chain_ents, gold_with_aliases)
    rel_overlap = chain_rels & g_rels if g_rels else set()
    rel_miss = bool(g_rels) and not rel_overlap

    subqs = parse_subq(mem_artifacts.get("subq"))
    if not subqs and data.get("pog_trace", {}).get("subquestions"):
        subqs = parse_subq(str(data["pog_trace"]["subquestions"]))
    mem_dict = parse_mem(mem_artifacts.get("mem"))
    pog_trace = data.get("pog_trace")
    mem_info = check_memory_stage(mem_dict, gold_with_aliases, pred_str, triples)

    path_type = infer_path_type(data.get("reasoning_chains", []), data.get("call_num", 0), sufficient)
    n_depths = len(data.get("reasoning_chains") or [])
    n_hops_gold = len(g_rels) if g_rels else 1

    stage_status: dict[str, str] = {}

    # decomposition — heuristic (optional artifact; not treated as hard failure)
    topic_names = list(origin_data.get("topic_entity", {}).values())
    subq_text = " ".join(subqs).lower()
    if subqs:
        topic_hit = any(tn.lower() in subq_text for tn in topic_names) if topic_names else True
        stage_status["decomposition"] = "ok" if topic_hit else "weak_subq"
    else:
        stage_status["decomposition"] = "no_subq_file"

    # relation
    if not triples:
        stage_status["relation_retrieval"] = "skipped_no_graph"
    elif rel_miss:
        stage_status["relation_retrieval"] = "fail_gold_relation_missing"
    elif rel_overlap == g_rels:
        stage_status["relation_retrieval"] = "ok_all_gold_relations"
    else:
        stage_status["relation_retrieval"] = "partial_some_gold_relations"

    # entity
    if not triples:
        stage_status["entity_retrieval"] = "skipped_no_graph"
        stage_status["entity_prune"] = "skipped_no_graph"
    elif rel_miss:
        stage_status["entity_retrieval"] = "unknown_relation_wrong"
        stage_status["entity_prune"] = "unknown_relation_wrong"
    elif gold_in_chain:
        stage_status["entity_retrieval"] = "ok_gold_entity_present"
        stage_status["entity_prune"] = "ok_gold_kept"
    else:
        stage_status["entity_retrieval"] = "fail_gold_entity_missing"
        stage_status["entity_prune"] = "fail_gold_pruned_or_not_retrieved"

    # depth
    if n_depths < n_hops_gold and not gold_in_chain:
        stage_status["depth_exploration"] = "fail_insufficient_depth"
    elif n_depths >= n_hops_gold:
        stage_status["depth_exploration"] = "ok_depth_reached"
    else:
        stage_status["depth_exploration"] = "partial_depth_but_gold_found" if gold_in_chain else "partial_depth"

    # memory
    if mem_info["has_memory_file"]:
        if mem_info["memory_issues"]:
            stage_status["memory_update"] = "fail_" + mem_info["memory_issues"][0]
        elif mem_info["gold_in_memory"]:
            stage_status["memory_update"] = "ok_gold_in_memory"
        else:
            stage_status["memory_update"] = "weak_gold_not_in_memory"
    else:
        stage_status["memory_update"] = "no_mem_file"

    # sufficiency / reasoning
    suff_yes = sufficient and str(sufficient).lower() in ("yes", "known")
    if suff_yes and not gold_in_chain and triples:
        stage_status["sufficiency_reasoning"] = "fail_false_sufficient"
    elif suff_yes and gold_in_chain:
        stage_status["sufficiency_reasoning"] = "ok_sufficient_with_evidence"
    elif not suff_yes and is_correct and not triples:
        stage_status["sufficiency_reasoning"] = "ok_conservative_but_correct_parametric"
    elif not suff_yes and not is_correct:
        stage_status["sufficiency_reasoning"] = "ok_insufficient_recognized"
    else:
        stage_status["sufficiency_reasoning"] = "unclear"

    # answer generation
    if parsed.get("_parse_fallback") or parsed.get("_raw"):
        stage_status["answer_generation"] = "fail_parse_error"
    elif is_correct and gold_in_chain:
        stage_status["answer_generation"] = "ok_from_evidence"
    elif is_correct and not gold_in_chain and not triples:
        stage_status["answer_generation"] = "ok_parametric_no_graph"
    elif is_correct and not gold_in_chain:
        stage_status["answer_generation"] = "ok_parametric_despite_missing_gold_in_chains"
    elif not is_correct and gold_in_chain:
        stage_status["answer_generation"] = "fail_wrong_despite_gold_in_chains"
    elif not is_correct and triples:
        stage_status["answer_generation"] = "fail_wrong_evidence_or_selection"
    else:
        stage_status["answer_generation"] = "fail_no_evidence"

    if not triples and path_type in ("early_stop_or_cot", "deep_search_exhausted"):
        stage_status["cot_fallback"] = "used"
    else:
        stage_status["cot_fallback"] = "not_used"

    # aggregate failure / success label
    if is_correct:
        if gold_in_chain and rel_overlap == g_rels and n_depths >= n_hops_gold:
            outcome_label = "evidence_complete_in_chains"
        elif gold_in_chain:
            outcome_label = "evidence_partial_parametric_fill"
        elif not triples:
            outcome_label = "parametric_only_no_chains"
        elif n_depths >= 2 and gold_in_chain:
            outcome_label = "multi_hop_path_completed"
        else:
            outcome_label = "evidence_partial_parametric_fill"
    else:
        if not triples:
            outcome_label = "no_graph_exploration"
        elif rel_miss:
            outcome_label = "relation_miss"
        elif not gold_in_chain and rel_overlap:
            outcome_label = "entity_miss_given_relation"
        elif n_depths < n_hops_gold:
            outcome_label = "insufficient_depth"
        elif mem_info["memory_issues"]:
            outcome_label = "memory_contradiction_or_gap"
        elif suff_yes and not gold_in_chain:
            outcome_label = "false_sufficiency"
        elif gold_in_chain:
            outcome_label = "generation_wrong_despite_evidence"
        elif has_unresolved_mid(triples, pred_str):
            outcome_label = "mid_entity_unresolved"
        elif triples:
            outcome_label = "generation_without_evidence"
        elif stage_status["answer_generation"] == "fail_parse_error":
            outcome_label = "parse_or_format_error"
        else:
            outcome_label = "unknown"

    # human-readable root cause (first failing stage in pipeline order)
    root_cause_stage = None
    root_cause_detail = None
    stage_fail_map = {
        "decomposition": lambda s: s == "weak_subq",
        "relation_retrieval": lambda s: s.startswith("fail"),
        "entity_retrieval": lambda s: s.startswith("fail"),
        "entity_prune": lambda s: s.startswith("fail"),
        "depth_exploration": lambda s: s.startswith("fail"),
        "memory_update": lambda s: s.startswith("fail"),
        "sufficiency_reasoning": lambda s: s.startswith("fail"),
        "answer_generation": lambda s: s.startswith("fail"),
    }
    if not is_correct:
        for stage in STAGES:
            if stage == "cot_fallback":
                continue
            status = stage_status.get(stage, "")
            checker = stage_fail_map.get(stage)
            if checker and checker(status):
                root_cause_stage = stage
                root_cause_detail = status
                break
        if root_cause_stage is None:
            root_cause_stage = outcome_label
            root_cause_detail = outcome_label

    success_reason = None
    if is_correct:
        if outcome_label == "evidence_complete_in_chains":
            success_reason = "Gold relation and entity retrieved; multi-hop depth sufficient; answer matches evidence."
        elif outcome_label == "parametric_only_no_chains":
            success_reason = "No graph chains saved; answer likely from LLM parametric knowledge (CoT / half_stop / exhausted search)."
        elif outcome_label == "multi_hop_path_completed":
            success_reason = "Multi-depth reasoning chains contain gold; generation selected correct answer."
        else:
            success_reason = "Partial graph evidence (or parametric fill) yielded correct answer."

    record = {
        "question": question,
        **question_metadata(origin_data, dataset_name),
        "is_correct": is_correct,
        "prediction": prediction,
        "gold_answers": g_answers_raw,
        "gold_relations": sorted(g_rels),
        "outcome_label": outcome_label,
        "root_cause_stage": root_cause_stage,
        "root_cause_detail": root_cause_detail,
        "success_reason": success_reason,
        "path_type": path_type,
        "call_num": data.get("call_num"),
        "depths_explored": n_depths,
        "gold_hops_expected": n_hops_gold,
        "gold_in_reasoning_chains": gold_in_chain,
        "relations_in_chains": sorted(chain_rels),
        "relation_overlap_with_gold": sorted(rel_overlap),
        "sufficient": sufficient,
        "reason": parsed.get("R"),
        "stage_status": stage_status,
        "memory": mem_info,
        "subquestions": subqs,
        "topic_entity": origin_data.get("topic_entity", {}),
        "n_triples_in_chains": len(triples),
        "has_pog_trace": bool(pog_trace),
        "pog_trace_summary": compact_trace_for_llm(pog_trace) if pog_trace else None,
    }

    if llm_config and llm_config.get("use_llm"):
        if pog_trace:
            heuristic = {
                "outcome_label": outcome_label,
                "root_cause_stage": root_cause_stage,
                "stage_status": stage_status,
                "is_correct_heuristic": is_correct,
            }
            try:
                record["llm_diagnosis"] = run_llm_diagnosis(
                    question=question,
                    gold_answers=g_answers_raw,
                    gold_relations=sorted(g_rels),
                    prediction=prediction,
                    pog_trace=pog_trace,
                    heuristic=heuristic,
                    is_correct=is_correct,
                    llm_type=llm_config["llm_type"],
                    api_key=llm_config["api_key"],
                    api_base=llm_config["api_base"],
                    temperature=llm_config.get("temperature", 0.0),
                    max_tokens=llm_config.get("max_tokens", 2048),
                    timeout=llm_config.get("timeout", 120.0),
                )
                if llm_config.get("sleep", 0):
                    time.sleep(llm_config["sleep"])
            except Exception as exc:
                record["llm_diagnosis"] = {
                    "llm_error": str(exc),
                    "llm_error_type": type(exc).__name__,
                }
        else:
            record["llm_diagnosis"] = {
                "llm_skipped": True,
                "reason": "Output record has no pog_trace; re-run PoG with updated main_freebase.py",
            }

    return record


def _truncate_list(items: list, limit: int = 20) -> list:
    if len(items) <= limit:
        return items
    return items[:limit] + [f"...(+{len(items) - limit} more)"]


def compact_trace_for_llm(pog_trace: dict[str, Any] | None) -> dict[str, Any]:
    if not pog_trace:
        return {}
    compact = {
        "subquestions": pog_trace.get("subquestions"),
        "topic_entity": pog_trace.get("topic_entity"),
        "final_stop_reason": pog_trace.get("final_stop_reason"),
        "final_stop_depth": pog_trace.get("final_stop_depth"),
        "depths": [],
    }
    for d in pog_trace.get("depths", []):
        rel_prune = []
        for rp in d.get("relation_prune", []):
            rel_prune.append({
                "entity": rp.get("entity_name"),
                "candidates_sent_to_llm": _truncate_list(rp.get("candidate_relations_sent_to_llm", []), 30),
                "selected_relations": rp.get("selected_relations", []),
                "selection_success": rp.get("selection_success"),
            })
        prune_details = []
        for pd in d.get("entity_prune_details", []):
            prune_details.append({
                "topic_entity": pd.get("topic_entity"),
                "relation": pd.get("relation"),
                "before": _truncate_list(pd.get("candidates_before_prune", []), 15),
                "after": pd.get("candidates_after_prune", []),
                "dropped": _truncate_list(pd.get("dropped_candidates", []), 10),
                "prune_method": pd.get("prune_method"),
            })
        ev = d.get("evaluation") or {}
        mem = d.get("memory_update") or {}
        compact["depths"].append({
            "depth": d.get("depth"),
            "topic_entities": d.get("topic_entities"),
            "relation_prune": rel_prune,
            "before_entity_prune": d.get("before_entity_prune"),
            "after_entity_prune": d.get("after_entity_prune"),
            "entity_prune_details": prune_details,
            "pruned_triples": _truncate_list(d.get("pruned_triples", []), 25),
            "entity_prune_success": d.get("entity_prune_success"),
            "memory_after": (mem.get("memory_after") or "")[:1500],
            "evaluation": {
                "answer": ev.get("answer"),
                "sufficient": ev.get("sufficient"),
                "stop": ev.get("stop"),
            },
            "reverse_retrieval": d.get("reverse_retrieval"),
            "stop_reason": d.get("stop_reason"),
        })
    if pog_trace.get("final_answer_generation"):
        compact["final_answer_generation"] = {
            "method": pog_trace["final_answer_generation"].get("method"),
        }
    return compact


def normalize_llm_attribution(parsed: dict[str, Any], is_correct: bool) -> dict[str, Any]:
    """Keep only success fields for correct cases, only error fields for wrong cases."""
    expected_type = "success" if is_correct else "failure"
    out = {
        "attribution_type": expected_type,
        "llm_raw_output": parsed.get("llm_raw_output"),
        "llm_parse_error": parsed.get("llm_parse_error", False),
        "llm_token": parsed.get("llm_token"),
    }
    if parsed.get("llm_parse_error"):
        return out

    if is_correct:
        out["success_depths"] = parsed.get("success_depths") or []
        out["success_stage"] = parsed.get("success_stage")
        out["success_detail"] = parsed.get("success_detail") or parsed.get("error_detail", "")
        out["per_depth_notes"] = parsed.get("per_depth_notes") or []
    else:
        out["error_depth"] = parsed.get("error_depth")
        out["error_stage"] = parsed.get("error_stage")
        out["error_detail"] = parsed.get("error_detail") or parsed.get("success_detail", "")
        out["per_depth_notes"] = parsed.get("per_depth_notes") or []
    return out


def run_llm_diagnosis(
    question: str,
    gold_answers: list[str],
    gold_relations: list[str],
    prediction: Any,
    pog_trace: dict[str, Any] | None,
    heuristic: dict[str, Any],
    is_correct: bool,
    llm_type: str,
    api_key: str,
    api_base: str,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: float = 120.0,
) -> dict[str, Any]:
    if not is_openai_compatible_engine(llm_type):
        raise ValueError(f"Unsupported LLM engine for diagnosis: {llm_type}")
    if not api_key:
        raise ValueError("API key required for LLM diagnosis (--openai_api_keys)")

    trace_compact = compact_trace_for_llm(pog_trace)
    prompt_template = SUCCESS_ATTRIBUTION_PROMPT if is_correct else FAILURE_ATTRIBUTION_PROMPT
    system_msg = (
        "You explain why a KGQA pipeline succeeded. Output success-attribution JSON only."
        if is_correct
        else "You diagnose KGQA pipeline failures. Output failure-attribution JSON only."
    )
    prompt = prompt_template.format(
        stages=", ".join(LLM_STAGES),
        question=question,
        gold_answers=json.dumps(gold_answers, ensure_ascii=False),
        gold_relations=json.dumps(gold_relations, ensure_ascii=False),
        prediction=json.dumps(prediction, ensure_ascii=False),
        final_stop_reason=trace_compact.get("final_stop_reason"),
        final_stop_depth=trace_compact.get("final_stop_depth"),
        heuristic_summary=json.dumps(heuristic, ensure_ascii=False),
        trace_json=json.dumps(trace_compact, ensure_ascii=False, indent=2),
    )
    client = openai.OpenAI(api_key=api_key, base_url=api_base, timeout=timeout)
    completion_kwargs = {
        "model": llm_type,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    completion_kwargs.update(get_chat_completion_extra_kwargs(llm_type))
    completion = client.chat.completions.create(**completion_kwargs)
    raw = completion.choices[0].message.content or ""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return normalize_llm_attribution({"llm_raw_output": raw, "llm_parse_error": True}, is_correct)
    try:
        parsed = json.loads(raw[start : end + 1])
        parsed["llm_raw_output"] = raw
        parsed["llm_parse_error"] = False
        parsed["llm_token"] = {
            "total": completion.usage.total_tokens,
            "input": completion.usage.prompt_tokens,
            "output": completion.usage.completion_tokens,
        }
        return normalize_llm_attribution(parsed, is_correct)
    except json.JSONDecodeError:
        return normalize_llm_attribution({"llm_raw_output": raw, "llm_parse_error": True}, is_correct)


def summarize(records: list[dict], dataset_name: str = "webqsp") -> dict[str, Any]:
    total = len(records)
    correct = sum(1 for r in records if r["is_correct"])
    wrong = total - correct

    wrong_labels = Counter(r["outcome_label"] for r in records if not r["is_correct"])
    correct_labels = Counter(r["outcome_label"] for r in records if r["is_correct"])
    root_stages = Counter(r["root_cause_stage"] for r in records if not r["is_correct"] and r["root_cause_stage"])

    stage_fail_counts = defaultdict(int)
    for r in records:
        if r["is_correct"]:
            continue
        for stage, status in r["stage_status"].items():
            if status.startswith("fail") or status == "weak_subq":
                stage_fail_counts[stage] += 1

    llm_error_depths = Counter(
        r.get("llm_diagnosis", {}).get("error_depth")
        for r in records
        if not r["is_correct"]
        and r.get("llm_diagnosis", {}).get("attribution_type") == "failure"
        and r.get("llm_diagnosis", {}).get("error_depth") is not None
    )
    llm_error_stages = Counter(
        r.get("llm_diagnosis", {}).get("error_stage")
        for r in records
        if not r["is_correct"]
        and r.get("llm_diagnosis", {}).get("attribution_type") == "failure"
        and r.get("llm_diagnosis", {}).get("error_stage")
    )
    llm_success_stages = Counter(
        r.get("llm_diagnosis", {}).get("success_stage")
        for r in records
        if r["is_correct"]
        and r.get("llm_diagnosis", {}).get("attribution_type") == "success"
        and r.get("llm_diagnosis", {}).get("success_stage")
    )
    llm_success_depths = Counter(
        depth
        for r in records
        if r["is_correct"]
        and r.get("llm_diagnosis", {}).get("attribution_type") == "success"
        for depth in (r.get("llm_diagnosis", {}).get("success_depths") or [])
    )

    by_type: dict[str, dict[str, Any]] = {}
    type_field = dataset_type_field(dataset_name)
    if type_field:
        buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})
        for r in records:
            bucket = r.get("question_type") or "unknown"
            buckets[bucket]["total"] += 1
            if r["is_correct"]:
                buckets[bucket]["correct"] += 1
        for bucket, counts in sorted(buckets.items()):
            total_n = counts["total"]
            correct_n = counts["correct"]
            by_type[bucket] = {
                "total": total_n,
                "correct": correct_n,
                "accuracy": correct_n / total_n if total_n else 0.0,
            }

    return {
        "dataset": dataset_align_key(dataset_name),
        "type_field": type_field or None,
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "accuracy": correct / total if total else 0,
        "wrong_outcome_labels": dict(wrong_labels.most_common()),
        "correct_outcome_labels": dict(correct_labels.most_common()),
        "wrong_root_cause_stages": dict(root_stages.most_common()),
        "wrong_stage_signal_counts": dict(sorted(stage_fail_counts.items(), key=lambda x: -x[1])),
        "llm_failure_attributed": sum(
            1 for r in records
            if not r["is_correct"] and r.get("llm_diagnosis", {}).get("attribution_type") == "failure"
        ),
        "llm_success_attributed": sum(
            1 for r in records
            if r["is_correct"] and r.get("llm_diagnosis", {}).get("attribution_type") == "success"
        ),
        "llm_error_depths": dict(llm_error_depths.most_common()),
        "llm_error_stages": dict(llm_error_stages.most_common()),
        "llm_success_stages": dict(llm_success_stages.most_common()),
        "llm_success_depths": dict(llm_success_depths.most_common()),
        "llm_skipped": sum(
            1 for r in records
            if r.get("llm_diagnosis", {}).get("llm_skipped")
        ),
        "llm_errors": sum(
            1 for r in records
            if r.get("llm_diagnosis", {}).get("llm_error")
            or r.get("llm_diagnosis", {}).get("llm_parse_error")
        ),
        "by_question_type": by_type,
    }


def main():
    parser = argparse.ArgumentParser(description="Stage-wise PoG error/success analysis")
    parser.add_argument(
        "--dataset",
        default="webqsp",
        choices=["cwq", "webqsp", "webqsp_split", "grailqa", "grailqa_split"],
        help="Dataset name (cwq / grailqa / webqsp and optional *_split variants)",
    )
    parser.add_argument("--output_file", required=True,
                        help="Run folder under PoG/result/, or path to results.jsonl (folder name or full path)")
    parser.add_argument(
        "--mem_tag",
        default="",
        help="mem_PoG subfolder name, e.g. webqsp_gpt-3.5-turbo-0125. Default: basename of output_file.",
    )
    parser.add_argument(
        "--out_report",
        default="",
        help="Output jsonl path (default: pog_diagnosis/<run_tag>/<run_tag>.jsonl)",
    )
    parser.add_argument(
        "--out_summary",
        default="",
        help="Summary json path (default: pog_diagnosis/<run_tag>/<run_tag>_summary.json)",
    )
    parser.add_argument("--filter", choices=["all", "wrong", "correct"], default="all")
    parser.add_argument("--limit", type=int, default=-1)
    parser.add_argument(
        "--use_llm",
        action="store_true",
        help="LLM attributes success (correct Q) or failure (wrong Q) separately per question",
    )
    parser.add_argument("--LLM_type", default="gpt-4o-mini")
    parser.add_argument("--openai_api_keys", default="")
    parser.add_argument("--openai_api_base", default="")
    parser.add_argument("--llm_temperature", type=float, default=0.0)
    parser.add_argument("--llm_max_tokens", type=int, default=2048)
    parser.add_argument("--llm_sleep", type=float, default=0.0, help="Seconds between LLM calls")
    parser.add_argument("--llm_timeout", type=float, default=120.0, help="Seconds before each LLM request times out")
    args = parser.parse_args()

    api_base = args.openai_api_base or os.environ.get("OPENAI_API_BASE", "")
    if args.openai_api_base:
        os.environ["OPENAI_API_BASE"] = args.openai_api_base
    elif args.use_llm and not api_base:
        raise ValueError("Set --openai_api_base or OPENAI_API_BASE when --use_llm")
    if args.use_llm and (not api_base.startswith("http://") and not api_base.startswith("https://")):
        raise ValueError(
            f"Invalid OPENAI_API_BASE: {api_base!r}. "
            "Use a full URL, e.g. https://api.deepseek.com or https://cn2us02.opapi.win/v1 "
            '(do not write "$https://..." in analyze.sh)'
        )

    llm_config = None
    if args.use_llm:
        api_key = args.openai_api_keys or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("Set --openai_api_keys or OPENAI_API_KEY when --use_llm")
        llm_config = {
            "use_llm": True,
            "llm_type": args.LLM_type,
            "api_key": api_key,
            "api_base": api_base,
            "temperature": args.llm_temperature,
            "max_tokens": args.llm_max_tokens,
            "sleep": args.llm_sleep,
            "timeout": args.llm_timeout,
        }
        print(
            f"LLM attribution enabled: model={args.LLM_type}, base={api_base}",
            flush=True,
        )
    else:
        print("LLM attribution disabled (pass --use_llm to enable)", flush=True)

    ground_truth, question_string, output_datas = prepare_dataset_for_eval(args.dataset, args.output_file)

    if not args.out_report or not args.out_summary:
        default_report, default_summary = default_diagnosis_paths(args.output_file)
        if not args.out_report:
            args.out_report = default_report
        if not args.out_summary:
            args.out_summary = default_summary

    mem_tag = args.mem_tag
    if not mem_tag:
        base = os.path.basename(args.output_file.rstrip("/"))
        if base in ("results.jsonl", "pog_trace.jsonl"):
            base = os.path.basename(os.path.dirname(args.output_file.rstrip("/")))
        if base.startswith("PoG_"):
            base = base[4:]
        mem_tag = base
    mem_root = resolve_path(os.path.join("mem_PoG", mem_tag))

    aname_dict, alias_dict, add_ans_alias_dict = load_eval_aliases(args.dataset)

    gt_by_q = {d[question_string]: d for d in ground_truth}

    all_records = []
    for i, data in enumerate(output_datas):
        if args.limit >= 0 and i >= args.limit:
            break
        q = data.get(question_string)
        if not q or q not in gt_by_q:
            continue
        origin = gt_by_q[q]
        try:
            answers, _ = align(
                args.dataset,
                question_string,
                data,
                ground_truth,
                aname_dict,
                alias_dict,
                add_ans_alias_dict,
            )
        except KeyError as exc:
            print(f"Skipping question (align failed): {exc}")
            continue
        mem_art = load_mem_artifacts(mem_root, q)
        all_records.append(
            diagnose_one(
                data,
                origin,
                answers,
                mem_art,
                llm_config=llm_config,
                dataset_name=args.dataset,
            )
        )
        if llm_config and (len(all_records) % 10 == 0 or len(all_records) == 1):
            print(f"Diagnosed {len(all_records)} questions (LLM on)", flush=True)

    summary = summarize(all_records, args.dataset)

    if args.filter == "wrong":
        write_records = [r for r in all_records if not r["is_correct"]]
    elif args.filter == "correct":
        write_records = [r for r in all_records if r["is_correct"]]
    else:
        write_records = all_records

    out_report = resolve_path(args.out_report)
    out_summary = resolve_path(args.out_summary)
    os.makedirs(os.path.dirname(out_report) or POG_DIAGNOSIS_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(out_summary) or POG_DIAGNOSIS_DIR, exist_ok=True)
    with open(out_report, "w", encoding="utf-8") as f:
        for rec in write_records:
            f.write(format_jsonl_record(rec, indent=DEFAULT_JSONL_INDENT))

    with open(out_summary, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, indent=2))
    print(f"\nWrote {len(write_records)} records -> {out_report}")
    print(f"Summary -> {out_summary}")
    if args.use_llm:
        print(
            f"LLM attribution: success={summary.get('llm_success_attributed', 0)}, "
            f"failure={summary.get('llm_failure_attributed', 0)}, "
            f"errors={summary.get('llm_errors', 0)}, "
            f"skipped={summary.get('llm_skipped', 0)}"
        )


if __name__ == "__main__":
    main()
