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
from reflection_memory import (
    ANSWER_DEPTH,
    reflection_memory_context,
)
from constraint_compiler import (
    format_constraints_for_prompt,
    is_constraint_pushdown_enabled,
    constraint_routing_mode,
    lookup_constraint_trace,
    select_prompt_constraints,
    select_search_constraints,
)
from constraint_runtime import (
    add_coverage,
    answer_gate_mode,
    append_constraint_prompt,
    covering_answer_names,
    covering_entity_ids,
    coverage_for,
    filter_cluster_chains_to_covering,
    filter_ent_rel_ent_dict_to_covering,
    keys_from_applied_constraints,
    merge_memory_conflicts,
    should_inject_constraint_prompt,
)
from typing import Any, Dict, List, Optional, Sequence, Tuple
import traceback
SPARQLPATH = "http://localhost:8890/sparql"  #your own IP and port
SPARQL_TIMEOUT = int(os.environ.get("SPARQL_TIMEOUT", "15"))

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
NUMERIC_RELATION_REGEX = r"(code|number|count|population|rank|value|amount|quantity|calling)"
BIND_RELATION_HINTS = (
    "district", "represented", "jurisdiction", "location", "country",
    "administrative", "place", "region", "state", "city", "county",
    "contained", "contains", "nationality", "religion", "language",
    "gender", "profession", "party", "office",
)

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
    sparql.setTimeout(SPARQL_TIMEOUT)
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
    sparql.setTimeout(SPARQL_TIMEOUT)
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


def count_one_hop_entities(entity: str, relation: str, head: bool) -> int:
    pattern = build_one_hop_base_pattern(entity, relation, head)
    query = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT (COUNT(DISTINCT ?tailEntity) AS ?c)
WHERE {{
  {pattern}
}}"""
    try:
        bindings = execurte_sparql(query)
    except Exception:
        return -1
    if not bindings:
        return 0
    try:
        return int(float(bindings[0].get("c", {}).get("value", 0) or 0))
    except (TypeError, ValueError):
        return 0


def sample_one_hop_entities(entity: str, relation: str, head: bool, limit: int = 3) -> List[str]:
    pattern = build_one_hop_base_pattern(entity, relation, head)
    query = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?tailEntity
WHERE {{
  {pattern}
}}
LIMIT {max(1, int(limit))}"""
    try:
        return execute_entity_search_query(query)
    except Exception:
        return []


def sample_one_hop_entity(entity: str, relation: str, head: bool) -> Optional[str]:
    ids = sample_one_hop_entities(entity, relation, head, limit=1)
    return ids[0] if ids else None


def sample_neighbor_relations(sample_id: str) -> List[str]:
    if not sample_id:
        return []
    try:
        return get_cvt_one_hop_relations(sample_id)
    except Exception:
        return []


def collect_neighbor_relations(sample_ids: Sequence[str]) -> List[str]:
    relations = []
    seen = set()
    for sample_id in sample_ids:
        for relation in sample_neighbor_relations(sample_id):
            if relation in seen:
                continue
            seen.add(relation)
            relations.append(relation)
    return relations


def parse_rank_value(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    date_value = normalize_literal_date(text)
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_value)
    if not match:
        return None
    year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return year * 10000 + month * 100 + day


def fetch_rank_values(entity_ids: Sequence[str], limit_per_entity: int = 8) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for entity_id in entity_ids:
        query = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT ?rel ?val
WHERE {{
  ns:{entity_id} ?rel ?val .
  FILTER(
    isNumeric(?val) ||
    REGEX(STR(?rel), "{NUMERIC_RELATION_REGEX}", "i") ||
    REGEX(STR(?rel), "{DATE_RELATION_REGEX}")
  )
}}
LIMIT {max(1, int(limit_per_entity))}"""
        try:
            bindings = execurte_sparql(query)
        except Exception:
            continue
        parsed_values = []
        for binding in bindings:
            parsed = parse_rank_value(binding.get("val", {}).get("value", ""))
            if parsed is not None:
                parsed_values.append(parsed)
        if parsed_values:
            values[str(entity_id)] = parsed_values[0]
    return values


def apply_local_rank(entity_ids: Sequence[str], order_constraints: Sequence[dict], max_n: int = 50) -> Tuple[List[str], bool]:
    entity_ids = [str(item) for item in entity_ids]
    if not order_constraints or not entity_ids or len(entity_ids) > max_n:
        return entity_ids, True
    values = fetch_rank_values(entity_ids)
    if not values:
        return entity_ids, True
    reverse = order_constraints[0].get("kind") == "max"
    ranked = sorted(values.keys(), key=lambda item: (values[item], item), reverse=reverse)
    limit = int(order_constraints[0].get("limit", 1) or 1)
    return ranked[:max(1, limit)], False


def score_bind_relation(relation: str, question: str, mentions: Sequence[str]) -> int:
    rel = str(relation).replace(".", " ").replace("_", " ").lower()
    rel_tokens = set(rel.split())
    question_tokens = set(re.findall(r"[a-z0-9]+", str(question or "").lower()))
    mention_tokens = set()
    for mention in mentions:
        mention_tokens.update(re.findall(r"[a-z0-9]+", str(mention or "").lower()))
    score = 0
    for hint in BIND_RELATION_HINTS:
        if hint in rel:
            score += 2
    score += len(rel_tokens & question_tokens)
    score += 2 * len(rel_tokens & mention_tokens)
    return score


def select_bind_predicates(
    neighbor_relations: Sequence[str],
    question: str,
    entity_constraints: Sequence[dict],
    limit: int = 2,
) -> List[str]:
    mentions = [str(item.get("mention") or item.get("name") or "") for item in entity_constraints]
    scored = []
    for relation in neighbor_relations:
        if abandon_rels(relation):
            continue
        score = score_bind_relation(relation, question, mentions)
        if score > 0:
            scored.append((score, relation))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [relation for _, relation in scored[:max(0, int(limit))]]


def rank_relation_regex(question: str) -> str:
    hint_terms = [term for term in ("calling", "code", "population", "rank", "number", "count", "amount", "quantity") if term in str(question or "").lower()]
    if hint_terms:
        return "(" + "|".join(re.escape(term) for term in hint_terms) + ")"
    return NUMERIC_RELATION_REGEX


def build_constraint_query(
    entity: str,
    relation: str,
    head: bool,
    entity_constraints: Sequence[dict],
    time_constraints: Sequence[dict],
    order_constraints: Sequence[dict],
    limit: Optional[int] = None,
    bind_predicates: Optional[Sequence[str]] = None,
    include_rank: bool = True,
    question: str = "",
) -> str:
    where_lines = [build_one_hop_base_pattern(entity, relation, head)]
    bind_predicates = [pred for pred in (bind_predicates or []) if pred]

    for index, constraint in enumerate(entity_constraints):
        mid = str(constraint.get("mid", "")).strip()
        if not is_mid(mid):
            continue
        if bind_predicates:
            unions = []
            for pred in bind_predicates:
                unions.append(f"{{ ?tailEntity ns:{pred} ns:{mid} . }}")
                unions.append(f"{{ ns:{mid} ns:{pred} ?tailEntity . }}")
            where_lines.append("{\n  " + "\n  UNION\n  ".join(unions) + "\n}")
        else:
            var = f"?constraintRelation{index}"
            where_lines.append(
                f"""{{
  {{ ?tailEntity {var} ns:{mid} . }}
  UNION
  {{ ns:{mid} {var} ?tailEntity . }}
}}"""
            )
            where_lines.append(
                "FILTER("
                f'!STRSTARTS(STR({var}), "http://rdf.freebase.com/ns/type.object.name") && '
                f'!STRSTARTS(STR({var}), "http://rdf.freebase.com/ns/common.") && '
                f'!STRSTARTS(STR({var}), "http://rdf.freebase.com/ns/freebase.")'
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
    if include_rank and order_constraints:
        direction = "ASC"
        if order_constraints[0].get("kind") == "max":
            direction = "DESC"
        numeric_regex = rank_relation_regex(question)
        where_lines.append(
            f"""OPTIONAL {{ ?tailEntity ?constraintOrderRelation ?constraintOrderValue .
  FILTER(
    REGEX(STR(?constraintOrderRelation), "{numeric_regex}", "i") ||
    REGEX(STR(?constraintOrderRelation), "{DATE_RELATION_REGEX}")
  )
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


def entity_search_with_constraints(entity, relation, head=True, question=None, args=None, subobjective_idx=None):
    if args is None or not is_constraint_pushdown_enabled(args):
        return entity_search(entity, relation, head)

    compiled = getattr(args, "current_constraints", {}) or {}
    cache = get_constraint_trace_cache(args)
    idx = subobjective_idx if subobjective_idx is not None else getattr(args, "current_subobjective_idx", None)
    routed = select_search_constraints(args, compiled, idx)
    entity_constraints = list(routed.get("entity_constraints", []))
    time_constraints = list(routed.get("time_constraints", []))
    order_constraints = list(routed.get("order_constraints", []))
    applied_constraints = entity_constraints + time_constraints + order_constraints
    # Prune/bucket look up by (entity, relation, head) only. routed_sig is not
    # part of that contract: this depth's e_list came from the latest search.
    cache_key = (entity, relation, bool(head))
    question = question or ""
    hub_threshold = int(getattr(args, "constraint_hub_threshold", 50))
    before_count = count_one_hop_entities(entity, relation, head)

    def cache_and_return(entity_ids, applied, reason, sparql="", bind_relation=None, extra=None, after_count=None):
        trace = pushdown_result(
            entity_ids,
            applied_constraints if applied else [],
            before_count if before_count >= 0 else len(entity_ids),
            after_count if after_count is not None else len(entity_ids),
            sparql,
            applied,
            reason,
        )
        trace["bind_relation"] = bind_relation or []
        trace["subobjective_idx"] = idx
        if extra:
            trace.update(extra)
        cache[cache_key] = trace
        return list(entity_ids)

    if not applied_constraints:
        unconstrained = entity_search(entity, relation, head)
        return cache_and_return(unconstrained, False, "no_compiled_constraints")

    small_bucket = 0 <= before_count < hub_threshold
    if small_bucket and not entity_constraints and not order_constraints:
        unconstrained = entity_search(entity, relation, head)
        return cache_and_return(
            unconstrained,
            False,
            "skip_small_bucket",
            extra={"before_count": before_count},
        )

    bind_predicates = []
    if entity_constraints:
        sample_ids = sample_one_hop_entities(entity, relation, head, limit=3)
        neighbor_relations = collect_neighbor_relations(sample_ids)
        bind_predicates = select_bind_predicates(neighbor_relations, question, entity_constraints)

    attempts: list[tuple[list[dict], list[dict], list[dict], Optional[list[str]], bool, str]] = []
    if entity_constraints and bind_predicates:
        attempts.append((entity_constraints[:1], time_constraints, order_constraints, bind_predicates, True, "bind_top_entity_with_time_rank"))
        attempts.append((entity_constraints[:1], time_constraints, [], bind_predicates, False, "bind_top_entity_with_time"))
        attempts.append((entity_constraints[:1], [], [], bind_predicates, False, "bind_top_entity_only"))
        if len(entity_constraints) > 1:
            attempts.append((entity_constraints, time_constraints, [], bind_predicates, False, "bind_all_entities_with_time"))
    if entity_constraints:
        attempts.append((entity_constraints[:1], time_constraints, order_constraints, None, True, "anyhop_top_entity_with_time_rank"))
        attempts.append((entity_constraints[:1], time_constraints, [], None, False, "anyhop_top_entity_with_time"))
        attempts.append((entity_constraints[:1], [], [], None, False, "anyhop_top_entity_only"))
    if time_constraints or order_constraints:
        attempts.append(([], time_constraints, order_constraints, None, True, "time_rank_only"))
        if time_constraints:
            attempts.append(([], time_constraints, [], None, False, "time_only"))

    errors = []
    rank_skipped = False
    last_query = ""
    for ent_cs, time_cs, order_cs, preds, include_rank, attempt_name in attempts:
        query = build_constraint_query(
            entity, relation, head, ent_cs, time_cs, order_cs,
            bind_predicates=preds, include_rank=include_rank, question=question,
        )
        last_query = query
        try:
            constrained = execute_entity_search_query(query)
        except Exception as exc:
            errors.append({"attempt": attempt_name, "error": repr(exc)})
            continue
        if constrained:
            local_rank_skipped = False
            if order_constraints and not include_rank and 0 < len(constrained) <= hub_threshold:
                constrained, local_rank_skipped = apply_local_rank(constrained, order_constraints, max_n=hub_threshold)
                rank_skipped = local_rank_skipped
            elif order_constraints and not include_rank:
                rank_skipped = True
            return cache_and_return(
                constrained,
                True,
                "",
                sparql=query,
                bind_relation=list(preds or []),
                extra={
                    "attempt": attempt_name,
                    "errors": errors,
                    "rank_skipped": rank_skipped,
                    "applied_constraints": ent_cs + time_cs + (order_cs if include_rank or not rank_skipped else []),
                },
            )
        errors.append({"attempt": attempt_name, "error": "empty_result"})
        if include_rank and order_cs:
            rank_skipped = True

    unconstrained = entity_search(entity, relation, head)
    return cache_and_return(
        unconstrained,
        False,
        "fallback_unconstrained",
        sparql=last_query,
        bind_relation=bind_predicates,
        extra={"errors": errors, "rank_skipped": rank_skipped},
    )


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
    return triples[:200]


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
    return triples[:200]


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
            if isinstance(exc, openai.APITimeoutError) or type(exc).__name__ == "APITimeoutError":
                return "", {'total': 0, 'input': 0, 'output': 0}, last_error
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
            names = sorted(relation_values[relation])
            if len(names) > 15:
                names = names[:15] + [f"...(+{len(relation_values[relation]) - 15} more)"]
            pieces.append(relation + ": " + ", ".join(names))
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
        relation_scan_ids = sorted(e_list)
        if len(relation_scan_ids) > 80:
            print(f"[cvt] sampling {80}/{len(relation_scan_ids)} CVTs for neighbor-relation scan on {rela}")
            relation_scan_ids = relation_scan_ids[:80]
        for cvt_id in relation_scan_ids:
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
                constraint_trace = lookup_constraint_trace(constraint_trace_cache, topic_e, rela, h_t == "head")
                is_cvt_bucket = (
                    len(e_list) > 10
                    and all(is_cvt_like_entity(eid, entid_name) for eid in e_list)
                )
                if is_cvt_bucket and not constraint_trace.get("pushdown_applied"):
                    expand_ids = sorted(e_list)
                    if len(expand_ids) > 80:
                        print(f"[cvt] skipping next-node expansion for {len(expand_ids) - 80} extra CVTs on {rela}")
                        expand_ids = expand_ids[:80]
                    for cvt_id in expand_ids:
                        per_cvt[cvt_id] = get_cvt_next_nodes(cvt_id)
                else:
                    per_cvt["__direct__"] = set(e_list)
                bucket_map[(topic_e, h_t, rela)] = per_cvt
    return bucket_map


def construct_relation_prune_prompt(question, sub_questions, entity_id, entity_name, total_relations, args, reflection_context=""):
    prompt = extract_relation_prompt + question + '\nSubobjectives: ' + str(sub_questions) + '\nTopic Entity: ' + entity_name + '\nRelations: '+ '; '.join(total_relations)
    constraint_context = ""
    if should_inject_constraint_prompt(args, "relation"):
        compiled = select_prompt_constraints(
            args,
            getattr(args, "current_constraints", {}) or {},
            getattr(args, "current_subobjective_idx", None),
        )
        constraint_context = format_constraints_for_prompt(compiled)
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
    combined_context = "\n\n".join(part for part in [memory_context, str(reflection_context or "").strip()] if part)
    if combined_context:
        prompt = combined_context + "\n\n" + prompt
    setattr(args, "current_relation_memory_context", memory_context)
    setattr(args, "current_relation_reflection_context", str(reflection_context or ""))
    setattr(args, "current_relation_constraint_context", constraint_context)
    return prompt



def collect_candidate_relations_without_llm(entity_id, pre_relations=None, pre_head=-1, args=None):
    """Collect and filter one-hop relation candidates without semantic ranking or an LLM call."""
    pre_relations = list(pre_relations or [])
    head_relations_raw = replace_relation_prefix(execurte_sparql(sparql_head_relations % entity_id))
    tail_relations_raw = replace_relation_prefix(execurte_sparql(sparql_tail_relations % entity_id))
    head_relations = list(head_relations_raw)
    tail_relations = list(tail_relations_raw)
    if args is None or getattr(args, "remove_unnecessary_rel", True):
        head_relations = [relation for relation in head_relations if not abandon_rels(relation)]
        tail_relations = [relation for relation in tail_relations if not abandon_rels(relation)]
    if pre_head:
        tail_relations = list(set(tail_relations) - set(pre_relations))
    else:
        head_relations = list(set(head_relations) - set(pre_relations))
    head_relations = sorted(set(head_relations))
    tail_relations = sorted(set(tail_relations))
    return {
        "head_relations_before_filter": sorted(head_relations_raw),
        "tail_relations_before_filter": sorted(tail_relations_raw),
        "head_relations": head_relations,
        "tail_relations": tail_relations,
        "candidate_relations": sorted(set(head_relations + tail_relations)),
    }


def relation_search_prune(entity_id, sub_questions, entity_name, pre_relations, pre_head, question, args, reflection_context=""):
    candidates = collect_candidate_relations_without_llm(
        entity_id, pre_relations, pre_head, args
    )
    head_relations = candidates["head_relations"]
    tail_relations = candidates["tail_relations"]
    total_relations = candidates["candidate_relations"]
    retrieved_relations = total_relations
    semantic_top_k = int(getattr(args, "relation_semantic_top_k", 20))
    if len(total_relations) > semantic_top_k:
        retrieved_relations = semantic_filter_relations(question, total_relations, args, top_k=semantic_top_k)

    prompt = construct_relation_prune_prompt(
        question, sub_questions, entity_id, entity_name, retrieved_relations, args,
        reflection_context=reflection_context,
    )
    result, token_num = run_llm(
        prompt, args.temperature_exploration, args.max_length,
        args.opeani_api_keys, args.LLM_type, False, False
    )
    try:
        flag, selected = select_relations(result, entity_id, head_relations, tail_relations)
    except Exception:
        flag, selected = False, []

    rel_trace = {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "head_relations_before_filter": candidates["head_relations_before_filter"],
        "tail_relations_before_filter": candidates["tail_relations_before_filter"],
        "candidate_relations": total_relations,
        "retrieved_relations": retrieved_relations,
        "candidate_relations_sent_to_llm": retrieved_relations,
        "selected_relations": [
            {"relation": item["relation"], "head": item["head"]} for item in (selected if flag else [])
        ],
        "llm_raw_output": result,
        "selection_success": bool(flag),
        "relation_memory_context": getattr(args, "current_relation_memory_context", ""),
        "reflection_memory_context": getattr(args, "current_relation_reflection_context", ""),
        "constraint_context": getattr(args, "current_relation_constraint_context", ""),
    }
    return (selected if flag else []), token_num, rel_trace

    
    
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
    prompt = append_constraint_prompt(prompt, args, "answer", covering_names=covering_answer_names(args, getattr(args, "current_entid_name", {}) or {}))
    chains = cluster_chain_of_entities
    covering_names = covering_answer_names(args, getattr(args, "current_entid_name", {}) or {})
    if answer_gate_mode(args) == "hard" and covering_names:
        chains = filter_cluster_chains_to_covering(cluster_chain_of_entities, covering_names)
    chain_prompt = '\n'.join([', '.join([str(x) for x in chain]) for sublist in chains for chain in sublist])
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


def entity_condition_prune(question, total_entities_id, total_relations, total_candidates, total_topic_entities, total_head, ent_rel_ent_dict, entid_name, name_entid, args, model, reflection_context=""):
    cur_call_time = 0
    cur_token = {'total': 0, 'input': 0, 'output': 0}
    setattr(args, "current_entity_reflection_context", str(reflection_context or ""))

    new_ent_rel_ent_dict = {}
    no_prune = ['time', 'number', 'date']
    filter_entities_id, filter_tops, filter_relations, filter_candidates, filter_head = [], [], [], [], []
    entity_prune_details = []
    constraint_trace_cache = getattr(args, "current_constraint_search_traces", {}) or {}
    bucket_next_nodes = build_bucket_next_nodes(ent_rel_ent_dict, entid_name, constraint_trace_cache)
    for topic_e, h_t_dict in sorted(ent_rel_ent_dict.items()):
        for h_t, r_e_dict in sorted(h_t_dict.items()):
            for rela, e_list in sorted(r_e_dict.items()):
                constraint_trace = lookup_constraint_trace(constraint_trace_cache, topic_e, rela, h_t == "head")
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
                        if reflection_context:
                            prompt = str(reflection_context).strip() + '\n\n' + prompt
                        sorted_e_list = [entid_name[e_id] for e_id in sorted(e_list)]
                        prompt += entid_name[topic_e] + ' ' + rela + ' ' + format_capped_list(sorted_e_list, 70)

                        cur_call_time += 1
                        result, token_num = run_llm(prompt, args.temperature_reasoning, args.max_length, args.opeani_api_keys, args.LLM_type, False, False)
                        for kk in token_num.keys():
                            cur_token[kk] += token_num[kk]

                        llm_raw = result
                        result = parse_list_output(result)

                        select_ent = sorted(result)
                        select_ent = [x for x in select_ent if x in sorted_e_list]

                dropped = sorted(set(candidates_before) - set(select_ent))
                parent_keys = coverage_for(args, topic_e)
                bucket_keys = set()
                if constraint_trace.get("pushdown_applied"):
                    bucket_keys = keys_from_applied_constraints(constraint_trace.get("applied_constraints"))
                child_keys = parent_keys | bucket_keys
                entity_prune_details.append({
                    "topic_entity": entid_name[topic_e],
                    "topic_entity_id": topic_e,
                    "head_or_tail": h_t,
                    "relation": rela,
                    "candidates_before_prune": candidates_before[:50] + (
                        [f"... +{len(candidates_before) - 50} more"] if len(candidates_before) > 50 else []
                    ),
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
                    "bind_relation": constraint_trace.get("bind_relation", []),
                    "fallback_reason": constraint_trace.get("fallback_reason", ""),
                    "satisfied_constraint_keys": sorted(child_keys),
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
                        child_id = name_entid[ent]
                        new_ent_rel_ent_dict[topic_e][h_t][rela].append(child_id)
                        add_coverage(args, child_id, child_keys)
                        filter_tops.append(entid_name[topic_e])
                        filter_relations.append(rela)
                        filter_candidates.append(ent)
                        filter_entities_id.append(child_id)
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


def update_memory(
    question, subquestions, ent_rel_ent_dict, entid_name, cluster_chain_of_entities,
    q_mem_f_path, args, correction_context="", memory_override=None, persist_memory=True,
):
    his_mem = read_question_memory(q_mem_f_path) if memory_override is None else str(memory_override or "")
    prompt = update_mem_prompt + question + '\nSubobjectives: '+str(subquestions)+'\nMemory: ' + his_mem
    if correction_context:
        prompt = str(correction_context).strip() + "\n\n" + prompt
    prompt = append_constraint_prompt(prompt, args, "memory", covering_names=covering_answer_names(args, entid_name))

    use_dict = ent_rel_ent_dict
    covering_ids = covering_entity_ids(args)
    if answer_gate_mode(args) != "off" and covering_ids:
        use_dict = filter_ent_rel_ent_dict_to_covering(ent_rel_ent_dict, covering_ids)

    chain_prompt = ''
    for topic_e, h_t_dict in sorted(use_dict.items()):
        for h_t, r_e_dict in sorted(h_t_dict.items()):
            for rela, e_list in sorted(r_e_dict.items()):
                sorted_e_list = [entid_name.get(e_id, e_id) for e_id in sorted(e_list)]
                chain_prompt += entid_name.get(topic_e, topic_e) + ' ' + rela + ' ' + format_capped_list(sorted_e_list, 70) + '\n'

    prefix = prompt + "\nKnowledge Triplets:\n"
    budget_prefix = maybe_prepend_reference_context(prefix, args, stage="memory")
    chain_prompt = truncate_knowledge_triplets_for_prompt(
        budget_prefix, chain_prompt, args.LLM_type, args.max_length,
    )
    prompt = maybe_prepend_reference_context(prefix + chain_prompt, args, stage="memory")
    response, token_num = run_llm(
        prompt, args.temperature_reasoning, args.max_length,
        args.opeani_api_keys, args.LLM_type, False, False
    )
    mem = extract_memory(response)
    mem, conflicts = merge_memory_conflicts(his_mem, mem, args, entid_name)
    print(mem)
    if persist_memory:
        os.makedirs(q_mem_f_path, exist_ok=True)
        with open(os.path.join(q_mem_f_path, 'mem'), 'w', encoding='utf-8') as f:
            f.write(mem)
    mem_trace = {
        "memory_before": his_mem,
        "memory_after": mem,
        "llm_raw_output": response,
        "knowledge_triplets_prompt": chain_prompt.strip(),
        "correction_context": str(correction_context or ""),
        "conflicts": conflicts,
    }
    return token_num, mem_trace



def reasoning(
    question, subquestions, ent_rel_ent_dict, entid_name, cluster_chain_of_entities,
    q_mem_f_path, args, restrict_to_covering: bool = False, reflection_context="",
    memory_override=None, return_trace=False,
):
    if memory_override is None:
        his_mem = read_question_memory(q_mem_f_path)
    else:
        his_mem = str(memory_override or "")

    use_dict = ent_rel_ent_dict
    covering_ids = covering_entity_ids(args)
    if restrict_to_covering and covering_ids:
        use_dict = filter_ent_rel_ent_dict_to_covering(ent_rel_ent_dict, covering_ids)
    chain_prompt = ''
    for topic_e, h_t_dict in sorted(use_dict.items()):
        for h_t, r_e_dict in sorted(h_t_dict.items()):
            for rela, e_list in sorted(r_e_dict.items()):
                sorted_e_list = [entid_name.get(e_id, e_id) for e_id in sorted(e_list)]
                chain_prompt += entid_name.get(topic_e, topic_e) + ', ' + rela + ', ' + format_capped_list(sorted_e_list, 70) + '\n'

    dynamic_context = str(reflection_context or "").strip()
    selected_items = []
    if not dynamic_context:
        current_idx = int(getattr(args, "current_subobjective_idx", 0) or 0)
        steps = parse_planning_steps(str(subquestions or ""))
        current_subobjective = steps[current_idx] if steps and current_idx < len(steps) else ""
        dynamic_context, selected_items = reflection_memory_context(
            getattr(args, "reflection_memory_bank", []), ANSWER_DEPTH, question, his_mem,
            chain_prompt, args, getattr(args, "sentence_model", None),
            current_subobjective=current_subobjective,
            entities=list(entid_name.values()), return_items=True,
        )
    setattr(args, "current_answer_depth_reflection_context", dynamic_context)
    setattr(args, "current_answer_depth_reflection_items", selected_items)

    covering_names = covering_answer_names(args, entid_name)
    prompt = build_answer_depth_prompt(dynamic_context) + question + '\nMemory: ' + his_mem
    prompt = append_constraint_prompt(prompt, args, "reasoning", covering_names=covering_names)
    if constraint_routing_mode(args) != "off":
        steps = parse_planning_steps(str(subquestions or ""))
        n_steps = max(1, len(steps) or 1)
        current_idx = int(getattr(args, "current_subobjective_idx", 0) or 0)
        prompt += (
            "\nSubobjectives: " + str(steps or subquestions)
            + f"\nCurrently working on subobjective {min(current_idx + 1, n_steps)}/{n_steps}."
            + '\nAlso include "Subobjective_Progress" in the JSON: the number of subobjectives already completed '
            "(0 if none; after completing the first subobjective output 1)."
        )
    prefix = prompt + "\nKnowledge Triplets:\n"
    budget_prefix = maybe_prepend_reference_context(prefix, args, stage="reasoning")
    chain_prompt = truncate_knowledge_triplets_for_prompt(
        budget_prefix, chain_prompt, args.LLM_type, args.max_length,
    )
    prompt = maybe_prepend_reference_context(prefix + chain_prompt, args, stage="reasoning")
    response, token_num = run_llm(
        prompt, args.temperature_reasoning, args.max_length,
        args.opeani_api_keys, args.LLM_type, False
    )
    print("Response from reasoning:", response)
    answer, reason, sufficient, progress = extract_reason_and_anwer(response)
    setattr(args, "last_subobjective_progress", progress)
    trace = {
        "llm_raw_output": response,
        "answer": answer,
        "reason": reason,
        "sufficient": sufficient,
        "subobjective_progress": progress,
        "memory": his_mem,
        "knowledge_triplets_prompt": chain_prompt.strip(),
        "reflection_memory_context": dynamic_context,
        "reflection_memory_items": selected_items,
    }
    if return_trace:
        return response, answer, sufficient, token_num, trace
    return response, answer, sufficient, token_num
