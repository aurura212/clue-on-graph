from SPARQLWrapper import SPARQLWrapper, JSON
from utils import *
import os
import random
from freebase_func import *
from prompt_list import *
import json
import time
import openai
import re
from sentence_transformers import util
from sentence_transformers import SentenceTransformer
from reference_utils import maybe_prepend_reference_context
from relation_memory import relation_memory_context, should_use_relation_memory_at_stage
from constraint_compiler import format_constraints_for_prompt, is_constraint_pushdown_enabled
from typing import Any, Dict, List, Optional, Sequence, Tuple
import traceback
SPARQLPATH = "http://localhost:8890/sparql"  #your own IP and port

# pre-defined sparqls
sparql_head_relations = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT DISTINCT ?relation\nWHERE {\n  ns:%s ?relation ?x .\n}"""
sparql_tail_relations = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT DISTINCT ?relation\nWHERE {\n  ?x ?relation ns:%s .\n}"""
sparql_tail_entities_extract = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tailEntity\nWHERE {\nns:%s ns:%s ?tailEntity .\n}""" 
sparql_head_entities_extract = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tailEntity\nWHERE {\n?tailEntity ns:%s ns:%s  .\n}"""
sparql_one_hop_head_triples = """PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?relation ?entity
WHERE {
  ns:%s ?relation ?entity .
}"""
sparql_one_hop_tail_triples = """PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?relation ?entity
WHERE {
  ?entity ?relation ns:%s .
}"""
sparql_one_hop_head_relations_for_entity = """PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?relation
WHERE {
  ns:%s ?relation ?entity .
}"""
sparql_one_hop_tail_relations_for_entity = """PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?relation
WHERE {
  ?entity ?relation ns:%s .
}"""
sparql_one_hop_head_entities_for_relation = """PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?entity
WHERE {
  ns:%s ns:%s ?entity .
}"""
sparql_one_hop_tail_entities_for_relation = """PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?entity
WHERE {
  ?entity ns:%s ns:%s .
}"""
sparql_id = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT DISTINCT ?tailEntity\nWHERE {\n  {\n    ?entity ns:type.object.name ?tailEntity .\n    FILTER(?entity = ns:%s)\n  }\n  UNION\n  {\n    ?entity <http://www.w3.org/2002/07/owl#sameAs> ?tailEntity .\n    FILTER(?entity = ns:%s)\n  }\n}"""

DATE_RELATION_REGEX = r"([.]from$|[.]to$|[.]start_date$|[.]end_date$|[.]start$|[.]end$)"
START_RELATION_REGEX = r"([.]from$|[.]start_date$|[.]start$)"
END_RELATION_REGEX = r"([.]to$|[.]end_date$|[.]end$)"

# def check_end_word(s):
#     words = [" ID", " code", " number", "instance of", "website", "URL", "inception", "image", " rate", " count"]
#     return any(s.endswith(word) for word in words)

def abandon_rels(relation):
    if relation.startswith("http://") or relation == "type.object.type" or relation == "type.object.name" or relation.startswith("common.") or relation.startswith("freebase.") or "sameAs" in relation:
        return True


def execurte_sparql(sparql_query):
    sparql = SPARQLWrapper(SPARQLPATH)
    sparql.setQuery(sparql_query)
    sparql.setReturnFormat(JSON)
    results = sparql.query().convert()
    # print(results["results"]["bindings"])
    return results["results"]["bindings"]


def replace_relation_prefix(relations):
    return [relation['relation']['value'].replace("http://rdf.freebase.com/ns/","") for relation in relations]

def replace_entities_prefix(entities):
    return [entity['tailEntity']['value'].replace("http://rdf.freebase.com/ns/","") for entity in entities]


def id2entity_name_or_type(entity_id):
    sparql_query = sparql_id % (entity_id, entity_id)
    sparql = SPARQLWrapper(SPARQLPATH)
    sparql.setQuery(sparql_query)
    sparql.setReturnFormat(JSON)
    results = sparql.query().convert()
    if len(results["results"]["bindings"])==0:
        return entity_id
    else:
        return results["results"]["bindings"][0]['tailEntity']['value']


TokenUsage = Dict[str, int]
NeighborTriple = Tuple[str, str, str]


def parse_list_output(result: str) -> List[str]:
    last_brace_l = result.rfind('[')
    last_brace_r = result.rfind(']')

    if last_brace_l < last_brace_r:
        result = result[last_brace_l:last_brace_r+1]

    try:
        parsed = eval(result.strip())
    except:
        parsed = result.strip().strip("[").strip("]").split(', ')
        parsed = [x.strip("'").strip('"') for x in parsed]

    if isinstance(parsed, str):
        parsed = [parsed]
    return [str(x).strip() for x in parsed if str(x).strip()]


def relation_from_binding(value: str) -> str:
    return value.replace("http://rdf.freebase.com/ns/", "")


def entity_from_binding(value: str) -> str:
    return value.replace("http://rdf.freebase.com/ns/", "")


def is_mid(value: Any) -> bool:
    value = str(value or "")
    return value.startswith("m.") or value.startswith("g.")


def normalize_literal_date(value: str) -> str:
    value = str(value or "")
    match = re.search(r"\d{4}(?:-\d{2})?(?:-\d{2})?", value)
    if not match:
        return value
    date_value = match.group(0)
    if re.fullmatch(r"\d{4}", date_value):
        return date_value + "-01-01"
    if re.fullmatch(r"\d{4}-\d{2}", date_value):
        return date_value + "-01"
    return date_value


def build_one_hop_base_pattern(entity: str, relation: str, head: bool) -> str:
    if head:
        return f"ns:{entity} ns:{relation} ?tailEntity ."
    return f"?tailEntity ns:{relation} ns:{entity} ."


def execute_entity_search_query(sparql_query: str) -> List[str]:
    bindings = execurte_sparql(sparql_query)
    return replace_entities_prefix(bindings)


def build_constraint_query(
    entity: str,
    relation: str,
    head: bool,
    entity_constraints: Sequence[dict],
    time_constraints: Sequence[dict],
    order_constraints: Sequence[dict],
    limit: Optional[int] = None,
) -> str:
    where_lines = [build_one_hop_base_pattern(entity, relation, head)]

    for constraint in entity_constraints:
        mid = str(constraint.get("mid", "")).strip()
        if not is_mid(mid):
            continue
        where_lines.append(
            f"""{{
  {{ ?tailEntity ?constraintRelation ns:{mid} . }}
  UNION
  {{ ns:{mid} ?constraintRelation ?tailEntity . }}
}}"""
        )
        where_lines.append(
            "FILTER("
            "!STRSTARTS(STR(?constraintRelation), STR(ns:type.object.name)) && "
            "!STRSTARTS(STR(?constraintRelation), STR(ns:common.)) && "
            "!STRSTARTS(STR(?constraintRelation), STR(ns:freebase.))"
            ")"
        )

    for time_constraint in time_constraints:
        start = normalize_literal_date(time_constraint.get("start", ""))
        end = normalize_literal_date(time_constraint.get("end", "")) or start
        if not start:
            continue
        where_lines.append(
            f"""OPTIONAL {{ ?tailEntity ?constraintStartRelation ?constraintStartRaw .
  FILTER(REGEX(STR(?constraintStartRelation), "{START_RELATION_REGEX}"))
  BIND(SUBSTR(STR(?constraintStartRaw), 1, 10) AS ?constraintStart)
}}"""
        )
        where_lines.append(
            f"""OPTIONAL {{ ?tailEntity ?constraintEndRelation ?constraintEndRaw .
  FILTER(REGEX(STR(?constraintEndRelation), "{END_RELATION_REGEX}"))
  BIND(SUBSTR(STR(?constraintEndRaw), 1, 10) AS ?constraintEnd)
}}"""
        )
        where_lines.append(
            f"""FILTER(
  (!BOUND(?constraintStart) || ?constraintStart <= "{end}") &&
  (!BOUND(?constraintEnd) || ?constraintEnd >= "{start}")
)"""
        )
        where_lines.append("FILTER(BOUND(?constraintStart) || BOUND(?constraintEnd))")

    order_clause = ""
    if order_constraints:
        direction = "ASC"
        if order_constraints[0].get("kind") == "max":
            direction = "DESC"
        where_lines.append(
            f"""OPTIONAL {{ ?tailEntity ?constraintOrderRelation ?constraintOrderValue .
  FILTER(REGEX(STR(?constraintOrderRelation), "{DATE_RELATION_REGEX}"))
}}"""
        )
        where_lines.append("FILTER(BOUND(?constraintOrderValue))")
        order_clause = f"ORDER BY {direction}(?constraintOrderValue)"
        limit = int(order_constraints[0].get("limit", 1) or 1)

    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    where_body = "\n  ".join(where_lines)
    return f"""PREFIX ns: <http://rdf.freebase.com/ns/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT DISTINCT ?tailEntity
WHERE {{
  {where_body}
}}
{order_clause}
{limit_clause}"""


def get_constraint_trace_cache(args: Any) -> Dict[Tuple[str, str, bool], dict]:
    cache = getattr(args, "current_constraint_search_traces", None)
    if cache is None:
        cache = {}
        setattr(args, "current_constraint_search_traces", cache)
    return cache


def pushdown_result(
    entity_ids: Sequence[str],
    applied_constraints: Sequence[dict],
    before_count: int,
    after_count: int,
    sparql_query: str,
    applied: bool,
    fallback_reason: str = "",
) -> dict:
    return {
        "entity_ids": list(entity_ids),
        "applied_constraints": list(applied_constraints),
        "before_count": before_count,
        "after_count": after_count,
        "sparql": sparql_query,
        "fallback_reason": fallback_reason,
        "applied": applied,
        "pushdown_applied": applied,
    }


def entity_search_with_constraints(entity, relation, head=True, question=None, args=None):
    del question
    if args is None or not is_constraint_pushdown_enabled(args):
        return entity_search(entity, relation, head)

    compiled = getattr(args, "current_constraints", {}) or {}
    cache = get_constraint_trace_cache(args)
    cache_key = (entity, relation, bool(head))
    unconstrained = entity_search(entity, relation, head)
    entity_constraints = list(compiled.get("entity_constraints", []))
    time_constraints = list(compiled.get("time_constraints", []))
    order_constraints = list(compiled.get("order_constraints", []))

    applied_constraints = entity_constraints + time_constraints + order_constraints
    if not applied_constraints:
        cache[cache_key] = pushdown_result(
            unconstrained, [], len(unconstrained), len(unconstrained), "", False, "no_compiled_constraints"
        )
        return unconstrained

    attempts: list[tuple[list[dict], list[dict], list[dict], str]] = []
    if entity_constraints:
        attempts.append((entity_constraints[:1], time_constraints, order_constraints, "top_entity_with_time_order"))
    if len(entity_constraints) > 1:
        attempts.append((entity_constraints, time_constraints, order_constraints, "all_entities_with_time_order"))
    if time_constraints or order_constraints:
        attempts.append(([], time_constraints, order_constraints, "time_order_only"))
    if entity_constraints:
        attempts.append((entity_constraints[:1], [], [], "top_entity_only"))

    errors = []
    for ent_cs, time_cs, order_cs, attempt_name in attempts:
        query = build_constraint_query(entity, relation, head, ent_cs, time_cs, order_cs)
        try:
            constrained = execute_entity_search_query(query)
        except Exception as exc:
            errors.append({"attempt": attempt_name, "error": repr(exc)})
            continue
        if constrained:
            trace = pushdown_result(
                constrained,
                ent_cs + time_cs + order_cs,
                len(unconstrained),
                len(constrained),
                query,
                True,
                "",
            )
            trace["attempt"] = attempt_name
            trace["errors"] = errors
            cache[cache_key] = trace
            return constrained
        errors.append({"attempt": attempt_name, "error": "empty_result"})

    trace = pushdown_result(
        unconstrained,
        applied_constraints,
        len(unconstrained),
        len(unconstrained),
        "",
        False,
        "fallback_unconstrained",
    )
    trace["errors"] = errors
    cache[cache_key] = trace
    return unconstrained


def get_cvt_one_hop_triples(entity_id: str) -> List[NeighborTriple]:
    triples = []
    bindings = execurte_sparql(sparql_one_hop_head_triples % (entity_id))
    for item in bindings:
        relation = relation_from_binding(item["relation"]["value"])
        if not abandon_rels(relation):
            triples.append(("head", relation, entity_from_binding(item["entity"]["value"])))

    bindings = execurte_sparql(sparql_one_hop_tail_triples % (entity_id))
    for item in bindings:
        relation = relation_from_binding(item["relation"]["value"])
        if not abandon_rels(relation):
            triples.append(("tail", relation, entity_from_binding(item["entity"]["value"])))

    triples.sort()
    return triples


def get_cvt_one_hop_relations(entity_id: str) -> List[str]:
    relations = []
    bindings = execurte_sparql(sparql_one_hop_head_relations_for_entity % (entity_id))
    for item in bindings:
        relation = relation_from_binding(item["relation"]["value"])
        if not abandon_rels(relation):
            relations.append(relation)

    bindings = execurte_sparql(sparql_one_hop_tail_relations_for_entity % (entity_id))
    for item in bindings:
        relation = relation_from_binding(item["relation"]["value"])
        if not abandon_rels(relation):
            relations.append(relation)

    return sorted(set(relations))


def get_cvt_selected_relation_triples(entity_id: str, selected_relations: Sequence[str]) -> List[NeighborTriple]:
    triples = []
    for relation in selected_relations:
        bindings = execurte_sparql(sparql_one_hop_head_entities_for_relation % (entity_id, relation))
        for item in bindings:
            triples.append(("head", relation, entity_from_binding(item["entity"]["value"])))

        bindings = execurte_sparql(sparql_one_hop_tail_entities_for_relation % (relation, entity_id))
        for item in bindings:
            triples.append(("tail", relation, entity_from_binding(item["entity"]["value"])))

    triples.sort()
    return triples


def ensure_entity_name(entity_id: str, entid_name: Dict[str, str], name_entid: Dict[str, str]) -> str:
    if entity_id not in entid_name:
        if entity_id.startswith("m.") or entity_id.startswith("g."):
            entid_name[entity_id] = id2entity_name_or_type(entity_id)
        else:
            entid_name[entity_id] = entity_id
        name_entid[entid_name[entity_id]] = entity_id
    return entid_name[entity_id]


def run_llm_with_retry(prompt: str, args: Any, temperature: float, retries: int = 3) -> Tuple[str, TokenUsage, Optional[str]]:
    last_error = None
    for attempt in range(max(1, retries)):
        try:
            result, token_num = run_llm(prompt, temperature, args.max_length, args.opeani_api_keys, args.LLM_type, False, False)
            return result, token_num, None
        except Exception as exc:
            last_error = repr(exc)
            traceback.print_exc()
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 8))

    return "", {'total': 0, 'input': 0, 'output': 0}, last_error


def make_cvt_evidence_text(
    cvt_id: str,
    topic_name: str,
    incoming_relation: str,
    selected_relations: Sequence[str],
    neighbor_triples: Sequence[NeighborTriple],
    entid_name: Dict[str, str],
    name_entid: Dict[str, str],
) -> Tuple[str, Dict[str, List[str]]]:
    pieces = [cvt_id, "incoming: " + topic_name + " " + incoming_relation]
    relation_values = {}
    for direction, relation, neighbor_id in neighbor_triples:
        if relation not in selected_relations:
            continue
        neighbor_name = ensure_entity_name(neighbor_id, entid_name, name_entid)
        if relation not in relation_values:
            relation_values[relation] = []
        if neighbor_name not in relation_values[relation]:
            relation_values[relation].append(neighbor_name)

    for relation in selected_relations:
        if relation in relation_values:
            pieces.append(relation + ": " + ", ".join(sorted(relation_values[relation])))
    return " | ".join(pieces), relation_values


def truncate_cvt_candidates(
    e_list: Sequence[str],
    intersection_keep: Sequence[str],
    top_k: int,
) -> Tuple[List[str], Dict[str, List[str]]]:
    """Order CVTs by intersection priority, then truncate to top_k.

    CVTs that also appear in other (topic_entity, relation) candidate sets are
    kept first (cross-relation corroboration); the remainder is truncated to
    top_k by stable sort. Returns the truncated id list and a small trace dict.
    """
    keep_set = set(intersection_keep)
    prioritized = [cvt_id for cvt_id in sorted(e_list) if cvt_id in keep_set]
    remainder = [cvt_id for cvt_id in sorted(e_list) if cvt_id not in keep_set]
    limit = max(0, int(top_k))
    if limit and len(remainder) > limit:
        remainder = remainder[:limit]
    truncated = sorted(set(prioritized) | set(remainder))
    return truncated, {
        "intersection_keep_count": len(prioritized),
        "remainder_count": len(remainder),
        "truncated_count": len(truncated),
    }


def cvt_neighbor_prune(
    question: str,
    topic_e: str,
    rela: str,
    e_list: Sequence[str],
    entid_name: Dict[str, str],
    name_entid: Dict[str, str],
    args: Any,
    intersection_keep: Optional[Sequence[str]] = None,
    intersection_trace: Optional[Dict[str, int]] = None,
    constraint_trace: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], Optional[str], str, int, TokenUsage, Dict[str, Any]]:
    cur_call_time = 0
    cur_token = {'total': 0, 'input': 0, 'output': 0}
    topic_name = entid_name[topic_e]
    max_fallback = int(getattr(args, "cvt_neighbor_fallback_top_k", 10))
    llm_retries = int(getattr(args, "cvt_neighbor_llm_retries", 3))
    cvt_entity_top_k = int(getattr(args, "cvt_entity_top_k", 30))
    intersection_keep = list(intersection_keep or [])
    intersection_trace = intersection_trace or {}
    constraint_trace = constraint_trace or {}
    constraint_pushdown_applied = bool(constraint_trace.get("pushdown_applied"))

    cvt_neighbor_relations = {}
    relation_counts = {}
    cvt_relation_llm_error = None
    cvt_entity_llm_error = None
    if not constraint_pushdown_applied:
        for cvt_id in sorted(e_list):
            relations = get_cvt_one_hop_relations(cvt_id)
            cvt_neighbor_relations[cvt_id] = relations
            for relation in relations:
                relation_counts[relation] = relation_counts.get(relation, 0) + 1

    cvt_selected_relations = []
    cvt_relation_llm_raw_output = None
    unique_neighbor_relations: List[str] = []
    candidate_relations_for_llm: List[str] = []
    filtered_e_list = list(e_list)
    if constraint_pushdown_applied:
        cvt_selected_relations = [
            item.get("mid") or item.get("raw_text") or item.get("kind", "")
            for item in constraint_trace.get("applied_constraints", [])
            if item
        ]
        prune_method = "constraint_pushdown_cvt"
    elif relation_counts:
        unique_neighbor_relations, candidate_relations_for_llm = prepare_cvt_neighbor_relations_for_llm(
            question,
            relation_counts,
            args,
        )
        relation_summary = [
            relation + " (covers " + str(relation_counts[relation]) + " CVT nodes)"
            for relation in candidate_relations_for_llm
        ]
        prompt = cvt_relation_prune_prompt + question
        prompt += "\nCurrent Incoming Triple: " + topic_name + " " + rela
        prompt += "\nCandidate Neighbor Relations: " + "; ".join(relation_summary)

        cur_call_time += 1
        result, token_num, cvt_relation_llm_error = run_llm_with_retry(prompt, args, args.temperature_reasoning, llm_retries)
        for kk in token_num.keys():
            cur_token[kk] += token_num[kk]
        cvt_relation_llm_raw_output = result
        parsed_relations = parse_list_output(result) if result else []
        relation_set = set(relation_counts.keys())
        normalized_relations = [rel.split(" (covers ")[0] for rel in parsed_relations]
        cvt_selected_relations = [rel for rel in normalized_relations if rel in relation_set]
        if not cvt_selected_relations:
            cvt_selected_relations = [
                relation for relation, _ in sorted(relation_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
            ]

        filtered_e_list = filter_cvts_by_relations(e_list, cvt_neighbor_relations, cvt_selected_relations)
        if not filtered_e_list:
            filtered_e_list = list(e_list)

    truncated_e_list, truncation_trace = truncate_cvt_candidates(
        filtered_e_list,
        intersection_keep,
        cvt_entity_top_k,
    )
    if not truncated_e_list:
        truncated_e_list = list(filtered_e_list)

    cvt_neighbor_evidence = {}
    candidate_evidence = []
    evidence_name_to_id = {}
    for cvt_id in sorted(truncated_e_list):
        if constraint_pushdown_applied:
            neighbor_triples = get_cvt_one_hop_triples(cvt_id)
        else:
            neighbor_triples = get_cvt_selected_relation_triples(cvt_id, cvt_selected_relations)
        evidence_text, relation_values = make_cvt_evidence_text(
            cvt_id,
            topic_name,
            rela,
            cvt_selected_relations if not constraint_pushdown_applied else [triple[1] for triple in neighbor_triples],
            neighbor_triples,
            entid_name,
            name_entid,
        )
        cvt_neighbor_evidence[cvt_id] = {
            "evidence_text": evidence_text,
            "selected_relation_neighbors": relation_values,
            "available_relations": cvt_neighbor_relations.get(cvt_id, []),
        }
        if relation_values:
            candidate_evidence.append(evidence_text)
            evidence_name_to_id[evidence_text] = cvt_id

    llm_raw = None
    if candidate_evidence:
        prompt = cvt_entity_prune_prompt + question
        prompt += "\nCandidate CVT Evidence: " + str(candidate_evidence)

        cur_call_time += 1
        result, token_num, cvt_entity_llm_error = run_llm_with_retry(prompt, args, args.temperature_reasoning, llm_retries)
        for kk in token_num.keys():
            cur_token[kk] += token_num[kk]
        llm_raw = result
        parsed_entities = parse_list_output(result) if result else []
        select_ids = []
        for item in parsed_entities:
            if item in e_list:
                select_ids.append(item)
            elif item in evidence_name_to_id:
                select_ids.append(evidence_name_to_id[item])
        select_ids = sorted(set(select_ids))
        if cvt_entity_llm_error:
            select_ids = sorted(evidence_name_to_id.values())
            prune_method = "cvt_neighbor_relation_prune_fallback_llm_error"
        elif not select_ids:
            select_ids = sorted(evidence_name_to_id.values())
            prune_method = "cvt_neighbor_relation_prune_fallback_all_evidence"
        else:
            prune_method = "cvt_neighbor_relation_prune"
    else:
        select_ids = sorted(e_list)[:max_fallback]
        prune_method = "cvt_neighbor_relation_prune_fallback_no_evidence"

    select_ent = [entid_name[ent_id] for ent_id in select_ids]
    cvt_trace = {
        "constraint_trace": constraint_trace,
        "constraint_pushdown_applied": constraint_pushdown_applied,
        "cvt_selected_relations": cvt_selected_relations,
        "cvt_unique_neighbor_relations": unique_neighbor_relations,
        "cvt_candidate_relations_sent_to_llm": candidate_relations_for_llm,
        "cvt_filtered_entity_count": len(filtered_e_list),
        "cvt_truncated_entity_count": len(truncated_e_list),
        "cvt_total_entity_count": len(e_list),
        "cvt_intersection_keep_count": truncation_trace["intersection_keep_count"],
        "cvt_intersection_id_keep_count": intersection_trace.get("cvt_id_keep", 0),
        "cvt_intersection_next_node_keep_count": intersection_trace.get("next_node_keep", 0),
        "cvt_intersection_combined_keep_count": intersection_trace.get("combined_keep", 0),
        "cvt_remainder_count": truncation_trace["remainder_count"],
        "cvt_entity_top_k": cvt_entity_top_k,
        "cvt_neighbor_evidence": cvt_neighbor_evidence,
        "cvt_relation_llm_raw_output": cvt_relation_llm_raw_output,
        "cvt_relation_llm_error": cvt_relation_llm_error,
        "cvt_entity_llm_error": cvt_entity_llm_error,
    }
    return select_ent, llm_raw, prune_method, cur_call_time, cur_token, cvt_trace
    


def select_relations(string, entity_id, head_relations, tail_relations):
    last_brace_l = string.rfind('[')
    last_brace_r = string.rfind(']')
    
    if last_brace_l < last_brace_r:
        string = string[last_brace_l:last_brace_r+1]

    relations=[]
    rel_list = eval(string.strip())
    for relation in rel_list:
        if relation in head_relations:
            relations.append({"entity": entity_id, "relation": relation, "head": True})
        elif relation in tail_relations:
            relations.append({"entity": entity_id, "relation": relation, "head": False})
    
    if not relations:
        return False, "No relations found"
    return True, relations



def semantic_filter_relations(question, total_relations, args, top_k=None):
    """Rank relations by semantic similarity to the question and keep top-k."""
    model = getattr(args, "sentence_model", None)
    if model is None or not total_relations:
        return list(total_relations)
    if top_k is None:
        top_k = int(getattr(args, "relation_semantic_top_k", 20))
    limit = min(max(1, top_k), len(total_relations))
    ranked, _ = retrieve_top_docs(question, total_relations, model, width=limit)
    return ranked


def prepare_cvt_neighbor_relations_for_llm(
    question: str,
    relation_counts: Dict[str, int],
    args: Any,
) -> Tuple[List[str], List[str]]:
    """Deduplicate CVT neighbor relations and semantically filter before LLM selection."""
    unique_relations = sorted(relation_counts.keys())
    semantic_top_k = int(getattr(args, "relation_semantic_top_k", 20))
    if len(unique_relations) > semantic_top_k:
        candidate_relations = semantic_filter_relations(
            question,
            unique_relations,
            args,
            top_k=semantic_top_k,
        )
    else:
        candidate_relations = unique_relations
    return unique_relations, candidate_relations


def filter_cvts_by_relations(
    e_list: Sequence[str],
    cvt_neighbor_relations: Dict[str, List[str]],
    selected_relations: Sequence[str],
) -> List[str]:
    selected = set(selected_relations)
    if not selected:
        return []
    return sorted(
        cvt_id
        for cvt_id in e_list
        if selected.intersection(cvt_neighbor_relations.get(cvt_id, []))
    )


def collect_cvt_intersection_keep(
    ent_rel_ent_dict: Dict[str, Dict[str, Dict[str, List[str]]]],
    topic_e: str,
    h_t: str,
    rela: str,
    e_list: Sequence[str],
    entid_name: Optional[Dict[str, str]] = None,
    bucket_next_nodes: Optional[Dict[Tuple[str, str, str], Dict[str, set]]] = None,
) -> Tuple[List[str], Dict[str, int]]:
    """CVT ids in e_list corroborated by other (topic_entity, relation) buckets.

    Two corroboration signals are combined:
    1. CVT id itself appears in another bucket's candidate set (shared intermediate node).
    2. The CVT's next-node neighbors (CVT, relation, next_node) appear in another
       bucket's next-node set -- i.e. two paths converge on the same downstream entity.

    Returns the priority-keep CVT id list and a small trace dict with per-signal counts.
    """
    current_set = set(e_list)
    if not current_set:
        return [], {"cvt_id_keep": 0, "next_node_keep": 0, "combined_keep": 0}

    other_ids: set = set()
    for other_topic, other_ht_dict in ent_rel_ent_dict.items():
        for other_ht, other_r_e_dict in other_ht_dict.items():
            for other_rela, other_e_list in other_r_e_dict.items():
                if other_topic == topic_e and other_ht == h_t and other_rela == rela:
                    continue
                other_ids.update(other_e_list)
    cvt_id_keep = current_set & other_ids

    next_node_keep: set = set()
    if bucket_next_nodes is not None:
        current_per_cvt = bucket_next_nodes.get((topic_e, h_t, rela), {})
        other_next_nodes: set = set()
        for key, per_cvt in bucket_next_nodes.items():
            if key == (topic_e, h_t, rela):
                continue
            for nodes in per_cvt.values():
                other_next_nodes.update(nodes)
        for cvt_id, nodes in current_per_cvt.items():
            if nodes & other_next_nodes:
                next_node_keep.add(cvt_id)

    combined = sorted(cvt_id_keep | next_node_keep)
    trace = {
        "cvt_id_keep": len(cvt_id_keep),
        "next_node_keep": len(next_node_keep),
        "combined_keep": len(combined),
    }
    return combined, trace


def is_cvt_like_entity(entity_id: str, entid_name: Dict[str, str]) -> bool:
    """Heuristic: entity has no readable Freebase name (name resolved back to the id)."""
    name = entid_name.get(entity_id, entity_id)
    return name == entity_id and entity_id.startswith("m.")


def get_cvt_next_nodes(cvt_id: str) -> set:
    """Return next-node entity ids reachable from a CVT in one hop."""
    triples = get_cvt_one_hop_triples(cvt_id)
    return {neighbor_id for _, _, neighbor_id in triples}


def build_bucket_next_nodes(
    ent_rel_ent_dict: Dict[str, Dict[str, Dict[str, List[str]]]],
    entid_name: Dict[str, str],
    constraint_trace_cache: Optional[Dict[Tuple[str, str, bool], dict]] = None,
) -> Dict[Tuple[str, str, str], Dict[str, set]]:
    """Precompute next-node sets for every (topic, head/tail, relation) bucket.

    For regular entity buckets the entities themselves are the next-nodes. For
    CVT buckets each CVT is expanded via one-hop SPARQL into its downstream
    entities. The result is cached once per depth iteration so that
    `collect_cvt_intersection_keep` can compute next-node intersections without
    reissuing SPARQL queries.
    """
    bucket_map: Dict[Tuple[str, str, str], Dict[str, set]] = {}
    for topic_e, h_t_dict in ent_rel_ent_dict.items():
        for h_t, r_e_dict in h_t_dict.items():
            for rela, e_list in r_e_dict.items():
                per_cvt: Dict[str, set] = {}
                constraint_trace = (constraint_trace_cache or {}).get((topic_e, rela, h_t == "head"), {})
                is_cvt_bucket = (
                    len(e_list) > 10
                    and all(is_cvt_like_entity(eid, entid_name) for eid in e_list)
                )
                if is_cvt_bucket and not constraint_trace.get("pushdown_applied"):
                    for cvt_id in e_list:
                        per_cvt[cvt_id] = get_cvt_next_nodes(cvt_id)
                else:
                    per_cvt["__direct__"] = set(e_list)
                bucket_map[(topic_e, h_t, rela)] = per_cvt
    return bucket_map


def construct_relation_prune_prompt(question, sub_questions, entity_id, entity_name, total_relations, args):
    prompt = extract_relation_prompt + question + '\nSubobjectives: ' + str(sub_questions) + '\nTopic Entity: ' + entity_name + '\nRelations: '+ '; '.join(total_relations)
    constraint_context = ""
    if is_constraint_pushdown_enabled(args):
        constraint_context = format_constraints_for_prompt(getattr(args, "current_constraints", {}) or {})
        if constraint_context:
            prompt += (
                "\nQuestion Constraints: " + constraint_context +
                "\nPrefer relations that can reach the answer or verify these entity, time, and ordering constraints."
            )
    prompt = maybe_prepend_reference_context(prompt, args, stage="relation")
    memory_context = ""
    if should_use_relation_memory_at_stage(args, "relation"):
        memory_context = relation_memory_context(
            getattr(args, "relation_memory_bank", []),
            question,
            entity_id,
            entity_name,
            total_relations,
            args,
            getattr(args, "sentence_model", None),
        )
        if memory_context:
            prompt = memory_context + "\n\n" + prompt
    setattr(args, "current_relation_memory_context", memory_context)
    setattr(args, "current_relation_constraint_context", constraint_context)
    return prompt


def relation_search_prune(entity_id, sub_questions, entity_name, pre_relations, pre_head, question, args):
    sparql_relations_extract_head = sparql_head_relations % (entity_id)
    head_relations = execurte_sparql(sparql_relations_extract_head)
    head_relations = replace_relation_prefix(head_relations)

    sparql_relations_extract_tail= sparql_tail_relations % (entity_id)
    tail_relations = execurte_sparql(sparql_relations_extract_tail)
    tail_relations = replace_relation_prefix(tail_relations)

    head_relations_raw = list(head_relations)
    tail_relations_raw = list(tail_relations)

    if args.remove_unnecessary_rel:
        head_relations = [relation for relation in head_relations if not abandon_rels(relation)]
        tail_relations = [relation for relation in tail_relations if not abandon_rels(relation)]

    if pre_head:
        tail_relations = list(set(tail_relations) - set(pre_relations))
    else:
        head_relations = list(set(head_relations) - set(pre_relations))

    head_relations = list(set(head_relations))
    tail_relations = list(set(tail_relations))
    total_relations = head_relations+tail_relations
    total_relations.sort()  # make sure the order in prompt is always equal

    retrieved_relations = total_relations
    semantic_top_k = int(getattr(args, "relation_semantic_top_k", 20))
    if len(total_relations) > semantic_top_k:
        retrieved_relations = semantic_filter_relations(question, total_relations, args, top_k=semantic_top_k)

    prompt = construct_relation_prune_prompt(question, sub_questions, entity_id, entity_name, retrieved_relations, args)
    result, token_num = run_llm(prompt, args.temperature_exploration, args.max_length, args.opeani_api_keys, args.LLM_type, False, False)
    flag, retrieve_relations = select_relations(result, entity_id, head_relations, tail_relations)

    rel_trace = {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "head_relations_before_filter": sorted(head_relations_raw),
        "tail_relations_before_filter": sorted(tail_relations_raw),
        "candidate_relations": total_relations,
        "retrieved_relations": retrieved_relations,
        "candidate_relations_sent_to_llm": retrieved_relations,
        "selected_relations": [
            {"relation": r["relation"], "head": r["head"]} for r in (retrieve_relations if flag else [])
        ],
        "llm_raw_output": result,
        "selection_success": bool(flag),
        "relation_memory_context": getattr(args, "current_relation_memory_context", ""),
        "constraint_context": getattr(args, "current_relation_constraint_context", ""),
    }

    if flag:
        return retrieve_relations, token_num, rel_trace
    else:
        return [], token_num, rel_trace
    
    
def entity_search(entity, relation, head=True):
    if head:
        tail_entities_extract = sparql_tail_entities_extract% (entity, relation)
        entities = execurte_sparql(tail_entities_extract)
    else:
        head_entities_extract = sparql_head_entities_extract% (relation, entity)
        entities = execurte_sparql(head_entities_extract)


    entity_ids = replace_entities_prefix(entities)
    return entity_ids


def provide_triple(entity_candidates_id, relation):
    entity_candidates = []
    for entity_id in entity_candidates_id:
        if entity_id.startswith("m."):
            entity_candidates.append(id2entity_name_or_type(entity_id))
        else:
            entity_candidates.append(entity_id)

    if len(entity_candidates) <= 1:
        return entity_candidates, entity_candidates_id


    ent_id_dict = dict(sorted(zip(entity_candidates, entity_candidates_id)))
    entity_candidates, entity_candidates_id = list(ent_id_dict.keys()), list(ent_id_dict.values())
    return entity_candidates, entity_candidates_id

    
def update_history(entity_candidates, ent_rel, entity_candidates_id, total_candidates, total_relations, total_entities_id, total_topic_entities, total_head):
    if len(entity_candidates) == 0:
        entity_candidates.append("[FINISH]")
        entity_candidates_id = ["[FINISH_ID]"]

    candidates_relation = [ent_rel['relation']] * len(entity_candidates)
    topic_entities = [ent_rel['entity']] * len(entity_candidates)
    head_num = [ent_rel['head']] * len(entity_candidates)
    total_candidates.extend(entity_candidates)
    total_relations.extend(candidates_relation)
    total_entities_id.extend(entity_candidates_id)
    total_topic_entities.extend(topic_entities)
    total_head.extend(head_num)
    return total_candidates, total_relations, total_entities_id, total_topic_entities, total_head


def half_stop(question, question_string, subquestions, cluster_chain_of_entities, depth, call_num, all_t, start_time, args, pog_trace=None):
    print("No new knowledge added during search depth %d, stop searching." % depth)
    call_num += 1
    answer, token_num = generate_answer(question, subquestions, cluster_chain_of_entities, args)

    for kk in token_num.keys():
        all_t[kk] += token_num[kk]

    if pog_trace is not None:
        pog_trace["final_stop_reason"] = "half_stop"
        pog_trace["final_stop_depth"] = depth
        if pog_trace["depths"]:
            pog_trace["depths"][-1]["stop_reason"] = pog_trace["depths"][-1].get("stop_reason") or "half_stop"
        pog_trace["final_answer_generation"] = {
            "method": "generate_answer",
            "llm_response": answer,
        }

    save_2_jsonl(question, question_string, answer, cluster_chain_of_entities, call_num, all_t, start_time, pog_trace=pog_trace)


def generate_answer(question, subquestions, cluster_chain_of_entities, args): 
    prompt = answer_prompt + question 
    chain_prompt = '\n'.join([', '.join([str(x) for x in chain]) for sublist in cluster_chain_of_entities for chain in sublist])
    prefix = prompt + "\nKnowledge Triplets: "
    budget_prefix = maybe_prepend_reference_context(prefix, args, stage="answer")
    chain_prompt = truncate_knowledge_triplets_for_prompt(
        budget_prefix, chain_prompt, args.LLM_type, args.max_length,
    )
    prompt = maybe_prepend_reference_context(prefix + chain_prompt, args, stage="answer")
    result, token_num = run_llm(prompt, args.temperature_reasoning, args.max_length, args.opeani_api_keys, args.LLM_type, False)
    return result, token_num


def if_topic_non_retrieve(string):
    try:
        float(string)
        return True
    except ValueError:
        return False
    
def is_all_digits(lst):
    for s in lst:
        if not s.isdigit():
            return False
    return True


def entity_condition_prune(question, total_entities_id, total_relations, total_candidates, total_topic_entities, total_head, ent_rel_ent_dict, entid_name, name_entid, args, model):
    cur_call_time = 0
    cur_token = {'total': 0, 'input': 0, 'output': 0}

    new_ent_rel_ent_dict = {}
    no_prune = ['time', 'number', 'date']
    filter_entities_id, filter_tops, filter_relations, filter_candidates, filter_head = [], [], [], [], []
    entity_prune_details = []
    constraint_trace_cache = getattr(args, "current_constraint_search_traces", {}) or {}
    bucket_next_nodes = build_bucket_next_nodes(ent_rel_ent_dict, entid_name, constraint_trace_cache)
    for topic_e, h_t_dict in sorted(ent_rel_ent_dict.items()):
        for h_t, r_e_dict in sorted(h_t_dict.items()):
            for rela, e_list in sorted(r_e_dict.items()):
                constraint_trace = constraint_trace_cache.get((topic_e, rela, h_t == "head"), {})
                prune_method = "llm"
                llm_raw = None
                cvt_trace = {
                    "constraint_trace": constraint_trace,
                    "constraint_pushdown_applied": bool(constraint_trace.get("pushdown_applied")),
                    "cvt_selected_relations": [],
                    "cvt_neighbor_evidence": {},
                    "cvt_relation_llm_raw_output": None,
                    "cvt_relation_llm_error": None,
                    "cvt_entity_llm_error": None,
                }
                candidates_before = [entid_name[e_id] for e_id in sorted(e_list)]

                if constraint_trace.get("pushdown_applied") and len(e_list) <= int(getattr(args, "constraint_auto_keep_top_k", 50)):
                    sorted_e_list = candidates_before
                    select_ent = sorted_e_list
                    prune_method = "constraint_pushdown_auto_keep"
                elif is_all_digits(e_list) or rela in no_prune or len(e_list) <= 1:
                    sorted_e_list = candidates_before
                    select_ent = sorted_e_list
                    prune_method = "skip_auto_keep"
                else:
                    if all(entid_name[item].startswith('m.') for item in e_list) and len(e_list) > 10:
                        sorted_e_list = [entid_name[e_id] for e_id in sorted(e_list)]
                        intersection_keep, intersection_trace = collect_cvt_intersection_keep(
                            ent_rel_ent_dict, topic_e, h_t, rela, e_list,
                            entid_name=entid_name, bucket_next_nodes=bucket_next_nodes,
                        )
                        select_ent, llm_raw, prune_method, cvt_call_time, cvt_token, cvt_trace = cvt_neighbor_prune(
                            question,
                            topic_e,
                            rela,
                            e_list,
                            entid_name,
                            name_entid,
                            args,
                            intersection_keep=intersection_keep,
                            intersection_trace=intersection_trace,
                            constraint_trace=constraint_trace,
                        )
                        cur_call_time += cvt_call_time
                        for kk in cvt_token.keys():
                            cur_token[kk] += cvt_token[kk]
                    else:
                        if len(e_list) > 70:
                            sorted_e_list = [entid_name[e_id] for e_id in e_list]
                            topn_entities, topn_scores = retrieve_top_docs(question, sorted_e_list, model, 70)
                            e_list = [name_entid[e_n] for e_n in topn_entities]
                            print('sentence:', topn_entities)
                            prune_method = "llm_after_embedding_top70"

                        prompt = prune_entity_prompt + question +'\nTriples: '
                        sorted_e_list = [entid_name[e_id] for e_id in sorted(e_list)]
                        prompt += entid_name[topic_e] + ' ' + rela + ' ' + str(sorted_e_list)

                        cur_call_time += 1
                        result, token_num = run_llm(prompt, args.temperature_reasoning, args.max_length, args.opeani_api_keys, args.LLM_type, False, False)
                        for kk in token_num.keys():
                            cur_token[kk] += token_num[kk]

                        llm_raw = result
                        result = parse_list_output(result)

                        select_ent = sorted(result)
                        select_ent = [x for x in select_ent if x in sorted_e_list]

                dropped = sorted(set(candidates_before) - set(select_ent))
                entity_prune_details.append({
                    "topic_entity": entid_name[topic_e],
                    "topic_entity_id": topic_e,
                    "head_or_tail": h_t,
                    "relation": rela,
                    "candidates_before_prune": candidates_before,
                    "candidates_after_prune": list(select_ent),
                    "dropped_candidates": dropped,
                    "prune_method": prune_method,
                    "llm_raw_output": llm_raw,
                    "cvt_selected_relations": cvt_trace["cvt_selected_relations"],
                    "cvt_neighbor_evidence": cvt_trace["cvt_neighbor_evidence"],
                    "cvt_relation_llm_raw_output": cvt_trace["cvt_relation_llm_raw_output"],
                    "cvt_relation_llm_error": cvt_trace["cvt_relation_llm_error"],
                    "cvt_entity_llm_error": cvt_trace["cvt_entity_llm_error"],
                    "constraint_trace": cvt_trace.get("constraint_trace", constraint_trace),
                    "constraint_pushdown_applied": bool(cvt_trace.get("constraint_pushdown_applied")),
                    "before_pushdown_count": constraint_trace.get("before_count"),
                    "after_pushdown_count": constraint_trace.get("after_count"),
                    "fallback_reason": constraint_trace.get("fallback_reason", ""),
                })

                if len(select_ent) == 0 or all(x == '' for x in select_ent):
                    continue

                if topic_e not in new_ent_rel_ent_dict.keys():
                    new_ent_rel_ent_dict[topic_e] = {}
                if h_t not in new_ent_rel_ent_dict[topic_e].keys():
                    new_ent_rel_ent_dict[topic_e][h_t] = {}
                if rela not in new_ent_rel_ent_dict[topic_e][h_t].keys():
                    new_ent_rel_ent_dict[topic_e][h_t][rela] = []
                
                for ent in select_ent:
                    if ent in sorted_e_list:
                        new_ent_rel_ent_dict[topic_e][h_t][rela].append(name_entid[ent])
                        filter_tops.append(entid_name[topic_e])
                        filter_relations.append(rela)
                        filter_candidates.append(ent)
                        filter_entities_id.append(name_entid[ent])
                        if h_t == 'head':
                            filter_head.append(True)
                        else:
                            filter_head.append(False)


    if len(filter_entities_id) == 0:
        return False, [], [], [], [], new_ent_rel_ent_dict, cur_call_time, cur_token, entity_prune_details


    cluster_chain_of_entities = [[(filter_tops[i], filter_relations[i], filter_candidates[i]) for i in range(len(filter_candidates))]]
    return True, cluster_chain_of_entities, filter_entities_id, filter_relations, filter_head, new_ent_rel_ent_dict, cur_call_time, cur_token, entity_prune_details

def add_pre_info(add_ent_list, depth_ent_rel_ent_dict, new_ent_rel_ent_dict, entid_name, name_entid, args):
    add_entities_id = sorted(add_ent_list)
    add_relations, add_head = [], []
    topic_ent = set()

    for cur_ent in add_entities_id:
        flag = 0
        for depth, ent_rel_ent_dict in depth_ent_rel_ent_dict.items():
            for topic_e, h_t_dict in ent_rel_ent_dict.items():
                for h_t, r_e_dict in h_t_dict.items():
                    for rela, e_list in r_e_dict.items():
                        if cur_ent in e_list:
                            if topic_e not in new_ent_rel_ent_dict.keys():
                                new_ent_rel_ent_dict[topic_e] = {}
                            if h_t not in new_ent_rel_ent_dict[topic_e].keys():
                                new_ent_rel_ent_dict[topic_e][h_t] = {}
                            if rela not in new_ent_rel_ent_dict[topic_e][h_t].keys():
                                new_ent_rel_ent_dict[topic_e][h_t][rela] = []
                            if cur_ent not in new_ent_rel_ent_dict[topic_e][h_t][rela]:
                                new_ent_rel_ent_dict[topic_e][h_t][rela].append(cur_ent)
                            
                            if not flag:
                                add_relations.append(rela)
                                if h_t == 'head':
                                    add_head.append(True)
                                else:
                                    add_head.append(False)
                                flag = 1


        if not flag:
            print('none pre relation')
            print(cur_ent)
            flag = 1
            add_head.append(-1)
            add_relations.append('')
            if cur_ent not in new_ent_rel_ent_dict.keys():
                new_ent_rel_ent_dict[cur_ent] = {}

    return add_entities_id, add_relations, add_head, new_ent_rel_ent_dict

def read_question_memory(q_mem_f_path):
    mem_path = os.path.join(q_mem_f_path, 'mem')
    if os.path.isfile(mem_path):
        with open(mem_path, 'r', encoding='utf-8') as f:
            return f.read()
    return ""


def update_memory(question, subquestions, ent_rel_ent_dict, entid_name, cluster_chain_of_entities, q_mem_f_path, args):
    his_mem = read_question_memory(q_mem_f_path)
    prompt = update_mem_prompt + question + '\nSubobjectives: '+str(subquestions)+'\nMemory: ' + his_mem

    chain_prompt = ''
    for topic_e, h_t_dict in sorted(ent_rel_ent_dict.items()):
        for h_t, r_e_dict in sorted(h_t_dict.items()):
            for rela, e_list in sorted(r_e_dict.items()):
                sorted_e_list = [entid_name[e_id] for e_id in sorted(e_list)]
                chain_prompt += entid_name[topic_e] + ' ' + rela + ' ' + str(sorted_e_list) + '\n'

    prefix = prompt + "\nKnowledge Triplets:\n"
    budget_prefix = maybe_prepend_reference_context(prefix, args, stage="memory")
    chain_prompt = truncate_knowledge_triplets_for_prompt(
        budget_prefix, chain_prompt, args.LLM_type, args.max_length,
    )
    prompt = maybe_prepend_reference_context(prefix + chain_prompt, args, stage="memory")

    response, token_num = run_llm(prompt, args.temperature_reasoning, args.max_length, args.opeani_api_keys, args.LLM_type, False, False)
    
    mem = extract_memory(response)
    print(mem)
    with open(q_mem_f_path+'/mem', 'w', encoding='utf-8') as f:
        f.write(mem)
    mem_trace = {
        "memory_before": his_mem,
        "memory_after": mem,
        "llm_raw_output": response,
        "knowledge_triplets_prompt": chain_prompt.strip(),
    }
    return token_num, mem_trace


def reasoning(question, subquestions, ent_rel_ent_dict, entid_name, cluster_chain_of_entities, q_mem_f_path, args):
    with open(q_mem_f_path+'/mem', 'r', encoding='utf-8') as f:
        his_mem = f.read()

    prompt = answer_depth_prompt + question + '\nMemory: ' + his_mem

    chain_prompt = ''

    for topic_e, h_t_dict in sorted(ent_rel_ent_dict.items()):
        for h_t, r_e_dict in sorted(h_t_dict.items()):
            for rela, e_list in sorted(r_e_dict.items()):
                sorted_e_list = [entid_name[e_id] for e_id in sorted(e_list)]
                chain_prompt += entid_name[topic_e] + ', ' + rela + ', ' + str(sorted_e_list) + '\n'

    prefix = prompt + "\nKnowledge Triplets:\n"
    budget_prefix = maybe_prepend_reference_context(prefix, args, stage="reasoning")
    chain_prompt = truncate_knowledge_triplets_for_prompt(
        budget_prefix, chain_prompt, args.LLM_type, args.max_length,
    )
    prompt = maybe_prepend_reference_context(prefix + chain_prompt, args, stage="reasoning")

    response, token_num = run_llm(prompt, args.temperature_reasoning, args.max_length, args.opeani_api_keys, args.LLM_type, False)
    print("Response from reasoning:", response)
    answer, reason, sufficient = extract_reason_and_anwer(response)
    return response, answer, sufficient, token_num

    



