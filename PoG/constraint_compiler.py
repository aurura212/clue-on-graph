"""Question constraint extraction and lightweight Freebase linking for PoG.

This module intentionally does not read gold parses or gold SPARQL. It only
uses the question text, supplied topic entities, and optional Freebase lookups.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional


SparqlExecutor = Callable[[str], list[dict[str, Any]]]


QUESTION_WORDS = {
    "who",
    "whom",
    "whose",
    "what",
    "which",
    "when",
    "where",
    "why",
    "how",
    "name",
    "find",
    "list",
    "give",
    "show",
    "tell",
}

MENTION_STOPWORDS = QUESTION_WORDS | {
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "for",
    "to",
    "by",
    "with",
    "and",
    "or",
    "from",
    "that",
    "this",
    "these",
    "those",
    "its",
    "his",
    "her",
    "their",
    "both",
    "also",
    "into",
    "about",
    "has",
    "have",
    "had",
    "does",
    "did",
    "do",
    "can",
    "could",
    "would",
    "should",
    "may",
    "might",
    "must",
    "will",
    "shall",
    "main",
    "whose",
    "spoken",
    "calling",
    "current",
    "currently",
    "now",
    "present",
    "latest",
    "earliest",
    "first",
    "last",
    "smallest",
    "largest",
    "oldest",
    "youngest",
    "highest",
    "lowest",
    "minimum",
    "maximum",
    "min",
    "max",
}

# Answer-type / role words are usually the thing being asked for, not a graph constraint.
# Applied only to unigrams so multi-word names like "New York" are still kept.
LOCATION_TYPE_HINTS = (
    "location.location",
    "location.us_state",
    "location.administrative_division",
    "location.country",
    "location.statistical_region",
    "location.dated_location",
    "government.governmental_jurisdiction",
    "base.aareas.schema.administrative_area",
)
ADMIN_TYPE_HINTS = (
    "location.us_state",
    "location.country",
    "location.administrative_division",
    "government.governmental_jurisdiction",
)
MAX_NGRAM_MENTIONS = 12
TYPE_UNIGRAMS = {
    "actor",
    "actress",
    "album",
    "author",
    "book",
    "capital",
    "child",
    "children",
    "city",
    "code",
    "codes",
    "college",
    "company",
    "composer",
    "countries",
    "country",
    "counties",
    "county",
    "date",
    "director",
    "film",
    "governor",
    "king",
    "language",
    "leader",
    "mayor",
    "member",
    "movie",
    "party",
    "person",
    "people",
    "place",
    "player",
    "president",
    "queen",
    "river",
    "school",
    "senator",
    "song",
    "state",
    "team",
    "time",
    "university",
    "wife",
    "year",
}


def is_constraint_pushdown_enabled(args: Any) -> bool:
    return str(getattr(args, "constraint_pushdown", "off")).lower() == "on"


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def sparql_escape_literal(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def normalize_mid(value: Any) -> str:
    value = str(value or "").strip()
    return value.replace("http://rdf.freebase.com/ns/", "")


def is_mid(value: Any) -> bool:
    value = normalize_mid(value)
    return value.startswith("m.") or value.startswith("g.")


def label_from_binding(binding: dict[str, Any], key: str = "name") -> str:
    value = binding.get(key, {}).get("value", "")
    return str(value)


def entity_from_binding(binding: dict[str, Any], key: str = "entity") -> str:
    return normalize_mid(binding.get(key, {}).get("value", ""))


def parse_time_constraints(question: str, args: Any) -> list[dict[str, Any]]:
    lowered = normalize_text(question)
    constraints: list[dict[str, Any]] = []
    asof_date = str(getattr(args, "constraint_asof_date", "2015-08-10"))

    if re.search(r"\b(current|currently|now|present)\b", lowered):
        constraints.append({
            "kind": "current",
            "start": asof_date,
            "end": asof_date,
            "asof_date": asof_date,
            "raw_text": "current",
        })

    range_seen = set()
    for match in re.finditer(r"\b(1[0-9]{3}|20[0-9]{2})\s*(?:-|to|through|until)\s*(1[0-9]{3}|20[0-9]{2})\b", lowered):
        start_year, end_year = match.group(1), match.group(2)
        key = (start_year, end_year)
        range_seen.add(start_year)
        range_seen.add(end_year)
        constraints.append({
            "kind": "range",
            "start": start_year + "-01-01",
            "end": end_year + "-12-31",
            "asof_date": "",
            "raw_text": match.group(0),
        })

    for match in re.finditer(r"\b(1[0-9]{3}|20[0-9]{2})\b", lowered):
        year = match.group(1)
        if year in range_seen:
            continue
        constraints.append({
            "kind": "year",
            "start": year + "-01-01",
            "end": year + "-12-31",
            "asof_date": "",
            "raw_text": year,
        })

    return constraints


def parse_order_constraints(question: str) -> list[dict[str, Any]]:
    lowered = normalize_text(question)
    patterns = [
        ("min", r"\b(smallest|lowest|least|minimum|min|earliest|oldest|first)\b"),
        ("max", r"\b(largest|highest|most|maximum|max|latest|newest|last)\b"),
    ]
    constraints = []
    for kind, pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            constraints.append({
                "kind": kind,
                "raw_text": match.group(1),
                "limit": 1,
            })
    return constraints


def parse_json_list(text: str) -> list[Any]:
    raw = str(text or "").strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    try:
        parsed = json.loads(raw)
    except Exception:
        try:
            parsed = eval(raw)
        except Exception:
            return []
    if isinstance(parsed, dict):
        parsed = parsed.get("constraints") or parsed.get("items") or [parsed]
    if not isinstance(parsed, list):
        return []
    return parsed


def time_constraint_from_llm_item(item: dict[str, Any], args: Any) -> Optional[dict[str, Any]]:
    asof_date = str(getattr(args, "constraint_asof_date", "2015-08-10"))
    mention = str(item.get("mention") or item.get("value") or "").strip()
    operator = normalize_text(item.get("operator", ""))
    value = str(item.get("value") or mention).strip()
    blob = normalize_text(" ".join([mention, operator, value]))
    if operator == "asof" or re.search(r"\b(current|currently|now|present)\b", blob):
        return {
            "kind": "current",
            "start": asof_date,
            "end": asof_date,
            "asof_date": asof_date,
            "raw_text": mention or "current",
        }
    range_match = re.search(r"(1[0-9]{3}|20[0-9]{2})\s*(?:-|to|through|until)\s*(1[0-9]{3}|20[0-9]{2})", blob)
    if operator == "range" or range_match:
        if not range_match:
            return None
        start_year, end_year = range_match.group(1), range_match.group(2)
        return {
            "kind": "range",
            "start": start_year + "-01-01",
            "end": end_year + "-12-31",
            "asof_date": "",
            "raw_text": mention or value,
        }
    year_match = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", blob)
    if year_match:
        year = year_match.group(1)
        return {
            "kind": "year",
            "start": year + "-01-01",
            "end": year + "-12-31",
            "asof_date": "",
            "raw_text": mention or year,
        }
    return None


def order_constraint_from_llm_item(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    operator = normalize_text(item.get("operator") or item.get("value") or item.get("mention") or "")
    if not re.search(r"\b(min|max|smallest|lowest|least|minimum|earliest|oldest|first|largest|highest|most|maximum|latest|newest|last)\b", operator):
        return None
    kind = "max" if re.search(r"\b(max|largest|highest|most|latest|newest|last)\b", operator) else "min"
    return {
        "kind": kind,
        "raw_text": str(item.get("mention") or item.get("value") or kind),
        "limit": 1,
    }


def merge_unique_constraints(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    merged = []
    for item in items:
        key = (
            item.get("kind"),
            item.get("start") or item.get("mid") or "",
            item.get("end") or item.get("mention") or item.get("raw_text") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def extract_constraints_with_llm(
    question: str,
    topic_entity: dict[str, str],
    args: Any,
) -> dict[str, Any]:
    """One LLM call to extract structured constraints. Never reads gold parses."""
    from prompt_list import constraint_extract_prompt
    from utils import run_llm

    topic_names = ", ".join(str(name) for name in (topic_entity or {}).values() if name) or "(none)"
    prompt = constraint_extract_prompt + topic_names + "\nQ: " + question + "\nOutput:\n"
    result = {
        "entity_mentions": [],
        "time_constraints": [],
        "order_constraints": [],
        "raw_output": "",
        "token_num": {"total": 0, "input": 0, "output": 0},
        "error": None,
        "source": "llm",
    }
    try:
        response, token_num = run_llm(
            prompt,
            args.temperature_reasoning,
            args.max_length,
            args.opeani_api_keys,
            args.LLM_type,
            False,
            False,
        )
    except Exception as exc:
        result["error"] = repr(exc)
        return result

    result["raw_output"] = response
    result["token_num"] = token_num or result["token_num"]
    parsed = parse_json_list(response)
    if not parsed:
        result["error"] = "empty_or_unparsed_llm_constraints"
        return result

    topic_norms = {normalize_text(name) for name in (topic_entity or {}).values()}
    entity_mentions = []
    time_constraints = []
    order_constraints = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        item_type = normalize_text(item.get("type", "entity"))
        mention = str(item.get("mention") or item.get("value") or "").strip()
        if item_type == "time":
            converted = time_constraint_from_llm_item(item, args)
            if converted:
                time_constraints.append(converted)
            continue
        if item_type == "rank":
            converted = order_constraint_from_llm_item(item)
            if converted:
                order_constraints.append(converted)
            continue
        if not mention or normalize_text(mention) in topic_norms:
            continue
        entity_mentions.append({"mention": mention, "source": "llm"})

    result["entity_mentions"] = entity_mentions
    result["time_constraints"] = merge_unique_constraints(time_constraints)
    result["order_constraints"] = merge_unique_constraints(order_constraints)
    if not entity_mentions and not time_constraints and not order_constraints:
        result["error"] = "llm_constraints_filtered_empty"
    return result


def remove_topic_mentions(question: str, topic_entity: dict[str, str]) -> str:
    masked = question
    for name in sorted((topic_entity or {}).values(), key=lambda x: len(str(x)), reverse=True):
        if not name:
            continue
        pattern = re.compile(re.escape(str(name)), flags=re.IGNORECASE)
        masked = pattern.sub(" ", masked)
    return masked


def strip_trailing_type_unigrams(raw: str) -> str:
    parts = re.sub(r"\s+", " ", str(raw or "").strip()).split()
    while len(parts) > 1 and normalize_text(parts[-1]) in TYPE_UNIGRAMS:
        parts.pop()
    return " ".join(parts)


def add_mention(mentions: dict[str, dict[str, str]], raw: str, source: str) -> None:
    raw = strip_trailing_type_unigrams(raw)
    raw = re.sub(r"\s+", " ", str(raw or "").strip())
    norm = normalize_text(raw)
    if not raw or not norm or norm in MENTION_STOPWORDS:
        return
    parts = [normalize_text(part) for part in raw.split()]
    if all(part in MENTION_STOPWORDS for part in parts):
        return
    if len(parts) == 1 and (norm in TYPE_UNIGRAMS or len(norm) <= 2):
        return
    if len(parts) > 1 and any(part in TYPE_UNIGRAMS for part in parts):
        return
    previous = mentions.get(norm)
    if previous is None or len(raw) > len(previous.get("mention", "")):
        mentions[norm] = {"mention": raw, "source": source}


def extract_candidate_mentions(question: str, topic_entity: dict[str, str]) -> list[dict[str, str]]:
    """Extract constraint mentions from arbitrary questions.

    No domain lexicons. Topic-entity strings are masked first, then we keep
    capitalized spans and content n-grams. Freebase linking decides which
    mentions are real entities.
    """
    masked = remove_topic_mentions(question, topic_entity)
    mentions: dict[str, dict[str, str]] = {}

    for match in re.finditer(r"\b(?:[A-Z][A-Za-z0-9.'&-]*)(?:\s+[A-Z][A-Za-z0-9.'&-]*){0,4}\b", masked):
        add_mention(mentions, match.group(0), "capitalized_span")

    tokens = re.findall(r"[A-Za-z][A-Za-z0-9.'&-]*", masked)
    content = []
    for token in tokens:
        norm = normalize_text(token)
        if not norm or norm in MENTION_STOPWORDS:
            continue
        content.append(token)

    max_n = min(4, len(content))
    for n in range(max_n, 0, -1):
        source = "content_ngram" if n > 1 else "content_unigram"
        for index in range(len(content) - n + 1):
            add_mention(mentions, " ".join(content[index:index + n]), source)

    ranked = []
    for item in mentions.values():
        token_count = len(item["mention"].split())
        if item.get("source") != "capitalized_span" and token_count > 2:
            continue
        ranked.append(item)
    ranked.sort(key=lambda item: (-len(item["mention"].split()), item["mention"].lower()))
    return ranked[:MAX_NGRAM_MENTIONS]


def build_exact_match_query(mention: str, limit: int, source: str = "name") -> str:
    literal = sparql_escape_literal(mention)
    if source == "alias":
        match_pattern = f'?entity ns:common.topic.alias ?matched .'
    else:
        match_pattern = f'?entity ns:type.object.name ?matched .'
    return f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?entity ?name
WHERE {{
  {match_pattern}
  FILTER(LCASE(STR(?matched)) = LCASE("{literal}"))
  FILTER(LANGMATCHES(LANG(?matched), "en"))
  ?entity ns:type.object.name ?name .
  FILTER(LANGMATCHES(LANG(?name), "en"))
}}
LIMIT {max(1, int(limit))}"""


def build_types_query(mids: list[str]) -> str:
    values = " ".join(f"ns:{mid}" for mid in mids if is_mid(mid))
    return f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT ?entity ?type
WHERE {{
  VALUES ?entity {{ {values} }}
  ?entity ns:type.object.type ?type .
}}"""


def collect_candidates_from_bindings(bindings: list[dict[str, Any]], mention: str, source: str) -> list[dict[str, Any]]:
    candidates = []
    seen = set()
    for binding in bindings:
        mid = entity_from_binding(binding)
        if not is_mid(mid) or mid in seen:
            continue
        seen.add(mid)
        candidates.append({
            "mid": mid,
            "name": label_from_binding(binding, "name") or mention,
            "source": source,
            "type": "",
        })
    return candidates


def attach_candidate_types(candidates: list[dict[str, Any]], sparql_executor: SparqlExecutor) -> None:
    mids = [item["mid"] for item in candidates]
    if not mids:
        return
    try:
        bindings = sparql_executor(build_types_query(mids))
    except Exception:
        return
    types_by_mid: dict[str, list[str]] = {}
    for binding in bindings:
        mid = entity_from_binding(binding)
        type_value = label_from_binding(binding, "type")
        if mid and type_value:
            types_by_mid.setdefault(mid, []).append(type_value)
    for item in candidates:
        item["type"] = " ".join(types_by_mid.get(item["mid"], []))


def score_link_candidate(
    mention: str,
    candidate: dict[str, Any],
    topic_entity: dict[str, str],
    question: str = "",
) -> float:
    mention_norm = normalize_text(mention)
    name_norm = normalize_text(candidate.get("name", ""))
    source = candidate.get("source", "")
    score = 0.45
    if name_norm == mention_norm:
        score += 0.35
    elif mention_norm and (mention_norm in name_norm or name_norm in mention_norm):
        score += 0.15
    if source == "name":
        score += 0.12
    if candidate.get("mid") in set(topic_entity or {}):
        score -= 0.5
    type_blob = normalize_text(candidate.get("type", ""))
    if any(hint in type_blob for hint in LOCATION_TYPE_HINTS):
        score += 0.2
    if any(hint in type_blob for hint in ADMIN_TYPE_HINTS):
        score += 0.15
    question_norm = normalize_text(question)
    if "language" in type_blob and any(token in question_norm for token in ("language", "spoken", "speak")):
        score += 0.2

    question_tokens = set(normalize_text(question).split()) - MENTION_STOPWORDS
    mention_tokens = set(mention_norm.split())
    extra_name_tokens = set(name_norm.split()) - mention_tokens - MENTION_STOPWORDS
    if extra_name_tokens:
        if extra_name_tokens & question_tokens:
            score += 0.06
        else:
            score -= min(0.18, 0.06 * len(extra_name_tokens))
    return max(0.0, min(0.99, score))


def link_mention(
    mention: str,
    topic_entity: dict[str, str],
    args: Any,
    sparql_executor: Optional[SparqlExecutor],
    question: str = "",
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
    if sparql_executor is None:
        return None, [], "no_sparql_executor"

    limit = max(8, int(getattr(args, "constraint_link_top_k", 8)) * 4)
    last_error = None
    candidates: list[dict[str, Any]] = []
    for source in ("name", "alias"):
        try:
            bindings = sparql_executor(build_exact_match_query(mention, limit, source=source))
        except Exception as exc:
            last_error = repr(exc)
            continue
        candidates = collect_candidates_from_bindings(bindings, mention, source)
        if candidates:
            break
    if not candidates:
        return None, [], last_error

    attach_candidate_types(candidates, sparql_executor)
    for item in candidates:
        item["confidence"] = score_link_candidate(mention, item, topic_entity, question)
    candidates.sort(key=lambda item: (-item["confidence"], item["name"], item["mid"]))
    threshold = float(getattr(args, "constraint_link_min_confidence", 0.65))
    best = candidates[0] if candidates and candidates[0]["confidence"] >= threshold else None
    return best, candidates[: int(getattr(args, "constraint_link_top_k", 8))], None


def format_constraints_for_prompt(compiled: dict[str, Any]) -> str:
    if not compiled:
        return ""
    parts = []
    entities = [
        f'{item.get("mention")} -> {item.get("name")} ({item.get("mid")})'
        for item in compiled.get("entity_constraints", [])
    ]
    if entities:
        parts.append("Linked entity constraints: " + "; ".join(entities))
    times = []
    for item in compiled.get("time_constraints", []):
        if item.get("kind") == "current":
            times.append(f'current/as of {item.get("asof_date")}')
        else:
            times.append(f'{item.get("raw_text")} [{item.get("start")}..{item.get("end")}]')
    if times:
        parts.append("Time constraints: " + "; ".join(times))
    orders = [f'{item.get("kind")} ({item.get("raw_text")})' for item in compiled.get("order_constraints", [])]
    if orders:
        parts.append("Order constraints: " + "; ".join(orders))
    unlinked = compiled.get("unlinked_mentions", [])
    if unlinked:
        parts.append("Unlinked constraint mentions: " + "; ".join(unlinked))
    return "\n".join(parts)


def prefer_longer_entity_constraints(constraints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop shorter mentions contained in a longer linked mention."""
    kept: list[dict[str, Any]] = []
    ordered = sorted(
        constraints,
        key=lambda item: (-len(str(item.get("mention", "")).split()), -item.get("confidence", 0.0)),
    )
    for item in ordered:
        mention = normalize_text(item.get("mention", ""))
        if any(mention != normalize_text(other.get("mention", "")) and mention in normalize_text(other.get("mention", "")) for other in kept):
            continue
        kept.append(item)
    return kept


def compile_question_constraints(
    question: str,
    topic_entity: dict[str, str],
    args: Any,
    model: Any = None,
    sparql_executor: Optional[SparqlExecutor] = None,
) -> dict[str, Any]:
    """Compile executable constraints from question text.

    LLM extraction is preferred; n-gram / regex extraction is the fallback.
    Mentions are linked through Freebase names/aliases. Gold parses are never read.
    """
    del model
    if not is_constraint_pushdown_enabled(args):
        return {
            "enabled": False,
            "entity_constraints": [],
            "time_constraints": [],
            "order_constraints": [],
            "unlinked_mentions": [],
            "trace": {"reason": "constraint_pushdown_off"},
        }

    use_llm = bool(getattr(args, "constraint_extract_llm", True))
    if use_llm:
        llm_extract = extract_constraints_with_llm(question, topic_entity or {}, args)
    else:
        llm_extract = {
            "entity_mentions": [],
            "time_constraints": [],
            "order_constraints": [],
            "raw_output": "",
            "token_num": {"total": 0, "input": 0, "output": 0},
            "error": "llm_extract_disabled",
            "source": "disabled",
        }
    mention_items = list(llm_extract.get("entity_mentions") or [])
    extract_source = "llm"
    if llm_extract.get("error"):
        mention_items = extract_candidate_mentions(question, topic_entity or {})
        extract_source = "ngram_fallback"

    entity_constraints: list[dict[str, Any]] = []
    unlinked_mentions: list[str] = []
    link_trace = []
    topic_mids = set(topic_entity or {})

    for mention_item in mention_items:
        mention = mention_item["mention"]
        best, candidates, error = link_mention(
            mention,
            topic_entity or {},
            args,
            sparql_executor,
            question,
        )
        link_trace.append({
            "mention": mention,
            "source": mention_item.get("source"),
            "candidates": candidates,
            "error": error,
            "selected": best,
        })
        if best and best.get("mid") not in topic_mids:
            entity_constraints.append({
                "mention": mention,
                "mid": best["mid"],
                "name": best.get("name") or mention,
                "confidence": best.get("confidence", 0.0),
                "source": best.get("source", mention_item.get("source", "")),
                "excluded_topic_entity": False,
            })
        else:
            unlinked_mentions.append(mention)

    entity_constraints = prefer_longer_entity_constraints(entity_constraints)
    entity_constraints.sort(key=lambda item: (-item.get("confidence", 0.0), -len(str(item.get("mention", "")).split()), item.get("name", ""), item.get("mid", "")))
    max_entity_constraints = int(getattr(args, "constraint_max_entity_constraints", 2))
    entity_constraints = entity_constraints[:max(0, max_entity_constraints)]

    time_constraints = list(llm_extract.get("time_constraints") or [])
    if not time_constraints:
        time_constraints = parse_time_constraints(question, args)
    order_constraints = list(llm_extract.get("order_constraints") or [])
    if not order_constraints:
        order_constraints = parse_order_constraints(question)

    compiled = {
        "enabled": True,
        "entity_constraints": entity_constraints,
        "time_constraints": time_constraints,
        "order_constraints": order_constraints,
        "unlinked_mentions": sorted(set(unlinked_mentions)),
        "trace": {
            "extract_source": extract_source,
            "llm_extract_error": llm_extract.get("error"),
            "llm_raw_output": llm_extract.get("raw_output", ""),
            "llm_token_num": llm_extract.get("token_num") or {"total": 0, "input": 0, "output": 0},
            "mentions": mention_items,
            "linking": link_trace,
            "prompt_context": "",
        },
    }
    compiled["trace"]["prompt_context"] = format_constraints_for_prompt(compiled)
    return compiled
