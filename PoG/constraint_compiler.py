"""Question constraint extraction and lightweight Freebase linking for PoG.

This module intentionally does not read gold parses or gold SPARQL. It only
uses the question text, supplied topic entities, and optional Freebase lookups.
"""

from __future__ import annotations

import json
import re
import sys
import time
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


def constraint_routing_mode(args: Any) -> str:
    """Return off/auto/on. Train mode and disabled pushdown always yield off."""
    if not is_constraint_pushdown_enabled(args):
        return "off"
    if str(getattr(args, "run_mode", "test")).lower() == "train":
        return "off"
    mode = str(getattr(args, "constraint_routing", "auto") or "auto").lower()
    if mode not in {"off", "auto", "on"}:
        return "auto"
    return mode


def is_constraint_routing_enabled(args: Any) -> bool:
    return constraint_routing_mode(args) in {"auto", "on"}


def lookup_constraint_trace(cache: Optional[dict], entity: str, relation: str, head: bool) -> dict:
    """Prune-facing lookup: (entity, relation, head) only. Do not require routed_sig.

    Search may still store a 4-tuple key for hop-specific memoization. Prefer the
    exact 3-tuple, otherwise the last 4-tuple with the same prefix.
    """
    if not cache:
        return {}
    key3 = (entity, relation, bool(head))
    hit = cache.get(key3)
    if isinstance(hit, dict):
        return hit
    matches = []
    for key, trace in cache.items():
        if not isinstance(key, tuple) or len(key) < 3 or not isinstance(trace, dict):
            continue
        if key[0] == entity and key[1] == relation and bool(key[2]) == bool(head):
            matches.append(trace)
    return matches[-1] if matches else {}


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def sparql_escape_literal(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def sparql_literal_variants(mention: str) -> list[str]:
    """Case variants that emulate LCASE matching while keeping the object index usable."""
    variants = (mention, mention.lower(), mention.upper(), mention.title(), mention.capitalize())
    return list(dict.fromkeys(variants))


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
    predicate = "ns:common.topic.alias" if source == "alias" else "ns:type.object.name"
    values = " ".join(
        f'"{sparql_escape_literal(v)}"@en' for v in sparql_literal_variants(mention)
    )
    return f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?entity ?name
WHERE {{
  VALUES ?matched {{ {values} }}
  ?entity {predicate} ?matched .
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
        started = time.perf_counter()
        try:
            bindings = sparql_executor(build_exact_match_query(mention, limit, source=source))
        except Exception as exc:
            elapsed = time.perf_counter() - started
            last_error = repr(exc)
            print(
                f"[constraint_link] mention={mention!r} source={source} "
                f"failed after {elapsed:.1f}s: {last_error}",
                file=sys.stderr,
            )
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


def format_constraints_for_prompt(
    compiled: dict[str, Any],
    subobjective_idx: Optional[int] = None,
    resolved_routing: Optional[list[dict[str, Any]]] = None,
) -> str:
    if not compiled:
        return ""
    if subobjective_idx is not None:
        routing = resolved_routing if resolved_routing is not None else compiled.get("resolved_routing")
        if routing:
            subset = get_constraints_for_subobjective(routing, subobjective_idx, compiled)
            compiled = {
                "entity_constraints": subset.get("entity_constraints") or [],
                "time_constraints": subset.get("time_constraints") or [],
                "order_constraints": subset.get("order_constraints") or [],
                "unlinked_mentions": [],
            }
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
            "constraint_key_map": {},
            "resolved_routing": None,
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
        "resolved_routing": None,
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
    compiled["constraint_key_map"] = build_constraint_key_map(compiled)
    compiled["trace"]["prompt_context"] = format_constraints_for_prompt(compiled)
    return compiled


def _empty_constraint_subset() -> dict[str, list]:
    return {
        "entity_constraints": [],
        "time_constraints": [],
        "order_constraints": [],
    }


def format_available_constraint_keys(compiled: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    compiled = compiled or {}
    for item in compiled.get("entity_constraints") or []:
        mention = str(item.get("mention") or item.get("name") or "").strip()
        if mention:
            keys.append("entity:" + mention)
    for item in compiled.get("time_constraints") or []:
        kind = str(item.get("kind") or item.get("raw_text") or "").strip()
        if kind:
            keys.append("time:" + kind)
    for item in compiled.get("order_constraints") or []:
        kind = str(item.get("kind") or "").strip()
        if kind:
            keys.append("rank:" + kind)
    return keys


def build_constraint_key_map(compiled: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map prompt-facing keys (entity:Ohio, time:current, rank:min) to compiled items."""
    key_map: dict[str, dict[str, Any]] = {}
    compiled = compiled or {}
    for item in compiled.get("entity_constraints") or []:
        mention = str(item.get("mention") or item.get("name") or "").strip()
        if mention:
            key_map.setdefault("entity:" + mention, item)
        name = str(item.get("name") or "").strip()
        if name:
            key_map.setdefault("entity:" + name, item)
        mid = str(item.get("mid") or "").strip()
        if mid:
            key_map.setdefault("entity:" + mid, item)
    for item in compiled.get("time_constraints") or []:
        kind = str(item.get("kind") or "").strip()
        if kind:
            key_map.setdefault("time:" + kind, item)
        raw = str(item.get("raw_text") or "").strip()
        if raw:
            key_map.setdefault("time:" + raw, item)
    for item in compiled.get("order_constraints") or []:
        kind = str(item.get("kind") or "").strip()
        if kind:
            key_map.setdefault("rank:" + kind, item)
            key_map.setdefault("order:" + kind, item)
        raw = str(item.get("raw_text") or "").strip()
        if raw:
            key_map.setdefault("rank:" + raw, item)
    return key_map


def parse_routing_key(key: str) -> tuple[str, str]:
    text = str(key or "").strip()
    if not text:
        return "", ""
    if ":" in text:
        prefix, body = text.split(":", 1)
        prefix = prefix.strip().lower()
        body = body.strip()
        if prefix in {"entity", "time", "rank", "order"}:
            if prefix == "order":
                prefix = "rank"
            return prefix, body
    return "", text


def _partial_text_match(needle: str, haystack: str) -> bool:
    needle = normalize_text(needle)
    haystack = normalize_text(haystack)
    if not needle or not haystack:
        return False
    if needle == haystack:
        return True
    if len(needle) < 2:
        return False
    return needle in haystack or haystack in needle


def _match_entity_constraint(key_body: str, entity_constraints: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    needle = normalize_text(key_body)
    if not needle:
        return None
    for item in entity_constraints or []:
        if normalize_text(item.get("mention", "")) == needle:
            return item
    for item in entity_constraints or []:
        if _partial_text_match(needle, item.get("name", "")) or _partial_text_match(needle, item.get("mention", "")):
            return item
        if normalize_text(item.get("mid", "")) == needle:
            return item
    return None


def _match_time_constraint(key_body: str, time_constraints: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    needle = normalize_text(key_body)
    if not needle:
        return None
    if needle in {"current", "currently", "now", "present", "asof"}:
        for item in time_constraints or []:
            if item.get("kind") == "current":
                return item
    for item in time_constraints or []:
        if normalize_text(item.get("kind", "")) == needle:
            return item
    for item in time_constraints or []:
        if (
            _partial_text_match(needle, item.get("raw_text", ""))
            or needle in normalize_text(item.get("start", ""))
            or needle in normalize_text(item.get("asof_date", ""))
        ):
            return item
    return None


def _match_order_constraint(key_body: str, order_constraints: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    needle = normalize_text(key_body)
    if not needle:
        return None
    if re.search(r"\b(max|largest|highest|most|maximum|latest|newest|last)\b", needle):
        kind = "max"
    elif re.search(r"\b(min|smallest|lowest|least|minimum|earliest|oldest|first)\b", needle):
        kind = "min"
    else:
        kind = needle
    for item in order_constraints or []:
        if normalize_text(item.get("kind", "")) == kind:
            return item
        if _partial_text_match(needle, item.get("raw_text", "")):
            return item
    return None


def resolve_constraint_keys(
    routing_keys: Optional[list[Any]],
    compiled_constraints: dict[str, Any],
) -> dict[str, Any]:
    """Map text keys such as entity:Ohio to a structured constraint subset.

    Unmatched keys are omitted (no-match fallback) and recorded in unresolved_keys.
    Matching is case-insensitive: exact mention first, then partial name/mention.
    """
    compiled = compiled_constraints or {}
    entity_constraints = list(compiled.get("entity_constraints") or [])
    time_constraints = list(compiled.get("time_constraints") or [])
    order_constraints = list(compiled.get("order_constraints") or [])
    key_map = compiled.get("constraint_key_map") or build_constraint_key_map(compiled)

    matched_entity: list[dict[str, Any]] = []
    matched_time: list[dict[str, Any]] = []
    matched_order: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for raw_key in routing_keys or []:
        key = str(raw_key or "").strip()
        if not key:
            continue
        prefix, body = parse_routing_key(key)
        item = None
        exact = key_map.get(key) or key_map.get(prefix + ":" + body if prefix else "")
        if exact is None:
            lowered = normalize_text(key)
            for map_key, map_item in key_map.items():
                if normalize_text(map_key) == lowered:
                    exact = map_item
                    break
        if exact is not None:
            item = exact
            if not prefix:
                if item in entity_constraints or item.get("mid"):
                    prefix = "entity"
                elif item.get("kind") in {"current", "year", "range"} or item.get("start") or item.get("asof_date"):
                    prefix = "time"
                else:
                    prefix = "rank"
        elif prefix == "entity" or (not prefix and body):
            item = _match_entity_constraint(body or key, entity_constraints)
            if item is not None:
                prefix = "entity"
        if item is None and prefix == "time":
            item = _match_time_constraint(body, time_constraints)
        if item is None and prefix == "rank":
            item = _match_order_constraint(body, order_constraints)
        if item is None and prefix == "entity":
            item = _match_entity_constraint(body, entity_constraints)
        if item is None and not prefix:
            item = (
                _match_entity_constraint(body or key, entity_constraints)
                or _match_time_constraint(body or key, time_constraints)
                or _match_order_constraint(body or key, order_constraints)
            )
            if item is not None:
                if item in entity_constraints or item.get("mid"):
                    prefix = "entity"
                elif item.get("kind") in {"current", "year", "range"} or item.get("start") or item.get("asof_date"):
                    prefix = "time"
                else:
                    prefix = "rank"

        if item is None:
            unresolved.append(key)
            continue
        if prefix == "entity":
            matched_entity.append(item)
        elif prefix == "time":
            matched_time.append(item)
        else:
            matched_order.append(item)

    return {
        "entity_constraints": merge_unique_constraints(matched_entity),
        "time_constraints": merge_unique_constraints(matched_time),
        "order_constraints": merge_unique_constraints(matched_order),
        "unresolved_keys": unresolved,
    }


def parse_subobjective_routing(text: str) -> Optional[list[dict[str, Any]]]:
    """Parse [{step, constraints}, ...] from LLM output. None if the old string-list format."""
    parsed = parse_json_list(text)
    if not parsed:
        return None
    if not all(isinstance(item, dict) and (item.get("step") or item.get("subobjective")) for item in parsed):
        return None
    routing: list[dict[str, Any]] = []
    for item in parsed:
        step = str(item.get("step") or item.get("subobjective") or "").strip()
        if not step:
            continue
        keys = item.get("constraints")
        if keys is None:
            keys = []
        if isinstance(keys, str):
            keys = [keys] if keys.strip() else []
        if not isinstance(keys, list):
            keys = []
        routing.append({
            "step": step,
            "constraints": [str(key).strip() for key in keys if str(key).strip()],
        })
    return routing or None


_HOP_EXPAND_RE = re.compile(r"\b(expand|retrieve|search|find)\b", flags=re.I)
_HOP_FILTER_RE = re.compile(r"\b(filter(?:\s+(?:out|to|the))?|only include)\b", flags=re.I)
_HOP_SELECT_RE = re.compile(
    r"\b(select the distinct|select the answer|select answer|as the answer|final answer|select the entity)\b",
    flags=re.I,
)


def _routing_keys(item: dict[str, Any]) -> list[str]:
    keys = item.get("constraints") or item.get("keys") or []
    if isinstance(keys, str):
        return [keys] if keys.strip() else []
    if not isinstance(keys, list):
        return []
    return [str(key).strip() for key in keys if str(key).strip()]


def _is_rank_step(keys: list[str]) -> bool:
    return any(normalize_text(key).startswith("rank:") for key in keys)


def _is_select_step(step: str, keys: list[str]) -> bool:
    if _is_rank_step(keys):
        return False
    text = str(step or "")
    if _HOP_EXPAND_RE.search(text):
        return False
    if _HOP_SELECT_RE.search(text):
        return True
    return bool(re.match(r"^select\b", text, flags=re.I))


def _is_pure_filter_step(step: str, keys: list[str]) -> bool:
    if _is_rank_step(keys):
        return False
    text = str(step or "")
    if not _HOP_FILTER_RE.search(text):
        return False
    if re.search(r"\bexpand\b", text, flags=re.I):
        return False
    return True


def normalize_hop_routing(routing: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Drop standalone Filter/Select steps; merge their keys onto the previous hop."""
    if not routing:
        return []
    hops: list[dict[str, Any]] = []
    pending_keys: list[str] = []
    for item in routing:
        step = str(item.get("step") or "").strip()
        keys = _routing_keys(item)
        if not step:
            pending_keys.extend(keys)
            continue
        if _is_select_step(step, keys) or _is_pure_filter_step(step, keys):
            pending_keys.extend(keys)
            continue
        merged = list(keys)
        if pending_keys:
            merged = pending_keys + merged
            pending_keys = []
        hops.append({"step": step, "constraints": merged})
    if pending_keys and hops:
        hops[-1]["constraints"] = hops[-1].get("constraints") or []
        hops[-1]["constraints"] = list(hops[-1]["constraints"]) + pending_keys
    elif pending_keys and not hops:
        hops = [{"step": str(routing[0].get("step") or "").strip(), "constraints": pending_keys}]
        hops = [item for item in hops if item["step"]]
    for item in hops:
        seen = set()
        unique = []
        for key in item.get("constraints") or []:
            if key in seen:
                continue
            seen.add(key)
            unique.append(key)
        item["constraints"] = unique
    return hops or list(routing)


def resolve_subobjective_routing(
    routing: Optional[list[dict[str, Any]]],
    compiled_constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for item in routing or []:
        keys = list(item.get("constraints") or item.get("keys") or [])
        subset = resolve_constraint_keys(keys, compiled_constraints)
        resolved.append({
            "step": str(item.get("step") or ""),
            "keys": keys,
            "constraints": keys,
            "entity_constraints": subset.get("entity_constraints") or [],
            "time_constraints": subset.get("time_constraints") or [],
            "order_constraints": subset.get("order_constraints") or [],
            "unresolved_keys": subset.get("unresolved_keys") or [],
            "resolved": True,
        })
    return resolved


def get_constraints_for_subobjective(
    resolved_routing: Optional[list[dict[str, Any]]],
    sub_idx: Optional[int],
    compiled_constraints: Optional[dict[str, Any]] = None,
) -> dict[str, list]:
    """Return {entity,time,order}_constraints for a 0-based subobjective index."""
    empty = _empty_constraint_subset()
    if not resolved_routing:
        return empty
    try:
        idx = int(sub_idx) if sub_idx is not None else 0
    except (TypeError, ValueError):
        idx = 0
    idx = max(0, min(idx, len(resolved_routing) - 1))
    item = resolved_routing[idx]
    if item.get("resolved"):
        return {
            "entity_constraints": list(item.get("entity_constraints") or []),
            "time_constraints": list(item.get("time_constraints") or []),
            "order_constraints": list(item.get("order_constraints") or []),
        }
    keys = item.get("constraints") or item.get("keys") or []
    subset = resolve_constraint_keys(keys, compiled_constraints or {})
    return {
        "entity_constraints": subset.get("entity_constraints") or [],
        "time_constraints": subset.get("time_constraints") or [],
        "order_constraints": subset.get("order_constraints") or [],
    }


def _constraint_identity(item: dict[str, Any], kind: str) -> tuple:
    if kind == "entity":
        return ("entity", str(item.get("mid") or normalize_text(item.get("mention") or "")))
    if kind == "time":
        return ("time", str(item.get("kind") or ""), str(item.get("start") or ""), str(item.get("asof_date") or ""))
    return ("rank", str(item.get("kind") or ""))


def assigned_constraint_ids(resolved_routing: Optional[list[dict[str, Any]]]) -> set[tuple]:
    assigned: set[tuple] = set()
    for item in resolved_routing or []:
        for entity in item.get("entity_constraints") or []:
            assigned.add(_constraint_identity(entity, "entity"))
        for time_item in item.get("time_constraints") or []:
            assigned.add(_constraint_identity(time_item, "time"))
        for order in item.get("order_constraints") or []:
            assigned.add(_constraint_identity(order, "rank"))
    return assigned


def unassigned_compiled_constraints(
    resolved_routing: Optional[list[dict[str, Any]]],
    compiled_constraints: Optional[dict[str, Any]],
) -> dict[str, list]:
    """Compiled constraints the LLM never attached to any subobjective.

    Those must stay active on every hop. Dropping them (e.g. forgetting
    entity:Ohio) turns time-only pushdown into a hub explosion.
    """
    compiled = compiled_constraints or {}
    assigned = assigned_constraint_ids(resolved_routing)
    entities = [
        item for item in (compiled.get("entity_constraints") or [])
        if _constraint_identity(item, "entity") not in assigned
    ]
    times = [
        item for item in (compiled.get("time_constraints") or [])
        if _constraint_identity(item, "time") not in assigned
    ]
    orders = [
        item for item in (compiled.get("order_constraints") or [])
        if _constraint_identity(item, "rank") not in assigned
    ]
    return {
        "entity_constraints": entities,
        "time_constraints": times,
        "order_constraints": orders,
    }


def merge_constraint_subsets(*subsets: dict[str, list]) -> dict[str, list]:
    entities: list[dict[str, Any]] = []
    times: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    for subset in subsets:
        if not subset:
            continue
        entities.extend(subset.get("entity_constraints") or [])
        times.extend(subset.get("time_constraints") or [])
        orders.extend(subset.get("order_constraints") or [])
    return {
        "entity_constraints": merge_unique_constraints(entities),
        "time_constraints": merge_unique_constraints(times),
        "order_constraints": merge_unique_constraints(orders),
    }


def get_pending_constraints(
    resolved_routing: Optional[list[dict[str, Any]]],
    sub_idx: Optional[int],
    compiled_constraints: Optional[dict[str, Any]] = None,
) -> dict[str, list]:
    """Union constraints from the current subobjective through the last one.

    Decomposition often puts entity/time filters on later 'filter' steps while
    earlier steps are unconstrained retrieves. Applying only the current step
    then leaves hub relations (e.g. governmental_body.members) unfiltered and
    explodes the frontier. Pending/lookahead keeps those later filters active
    until Subobjective_Progress advances past them.

    Constraints the LLM never assigned to any step stay active on every hop.
    """
    empty = _empty_constraint_subset()
    pending = empty
    if resolved_routing:
        try:
            idx = int(sub_idx) if sub_idx is not None else 0
        except (TypeError, ValueError):
            idx = 0
        idx = max(0, min(idx, len(resolved_routing) - 1))
        entities: list[dict[str, Any]] = []
        times: list[dict[str, Any]] = []
        orders: list[dict[str, Any]] = []
        for i in range(idx, len(resolved_routing)):
            part = get_constraints_for_subobjective(resolved_routing, i, compiled_constraints)
            entities.extend(part.get("entity_constraints") or [])
            times.extend(part.get("time_constraints") or [])
            orders.extend(part.get("order_constraints") or [])
        pending = {
            "entity_constraints": merge_unique_constraints(entities),
            "time_constraints": merge_unique_constraints(times),
            "order_constraints": merge_unique_constraints(orders),
        }
    unassigned = unassigned_compiled_constraints(resolved_routing, compiled_constraints)
    return merge_constraint_subsets(pending, unassigned)


def select_search_constraints(
    args: Any,
    compiled: Optional[dict[str, Any]] = None,
    subobjective_idx: Optional[int] = None,
) -> dict[str, list]:
    """Constraint subset used for SPARQL pushdown at the current hop."""
    compiled = compiled if compiled is not None else (getattr(args, "current_constraints", None) or {})
    full = {
        "entity_constraints": list(compiled.get("entity_constraints") or []),
        "time_constraints": list(compiled.get("time_constraints") or []),
        "order_constraints": list(compiled.get("order_constraints") or []),
    }
    mode = constraint_routing_mode(args)
    if mode == "off":
        return full
    routing = getattr(args, "resolved_constraint_routing", None)
    if routing is None:
        routing = compiled.get("resolved_routing")
    if not routing:
        if mode == "on":
            return _empty_constraint_subset()
        return full
    idx = subobjective_idx if subobjective_idx is not None else getattr(args, "current_subobjective_idx", 0)
    pending = get_pending_constraints(routing, idx, compiled)
    if pending["entity_constraints"] or pending["time_constraints"] or pending["order_constraints"]:
        return pending
    if mode == "auto" and (full["entity_constraints"] or full["time_constraints"] or full["order_constraints"]):
        return full
    return pending


def select_prompt_constraints(
    args: Any,
    compiled: Optional[dict[str, Any]] = None,
    subobjective_idx: Optional[int] = None,
) -> dict[str, list]:
    """Constraint subset injected into hop-local prompts.

    Unlike SPARQL selection, `on` with missing routing still shows the full
    compiled constraints as a reminder.
    """
    compiled = compiled if compiled is not None else (getattr(args, "current_constraints", None) or {})
    full = {
        "entity_constraints": list(compiled.get("entity_constraints") or []),
        "time_constraints": list(compiled.get("time_constraints") or []),
        "order_constraints": list(compiled.get("order_constraints") or []),
        "unlinked_mentions": list(compiled.get("unlinked_mentions") or []),
    }
    mode = constraint_routing_mode(args)
    if mode == "off":
        return full
    routing = getattr(args, "resolved_constraint_routing", None)
    if routing is None:
        routing = compiled.get("resolved_routing")
    if not routing:
        return full
    idx = subobjective_idx if subobjective_idx is not None else getattr(args, "current_subobjective_idx", 0)
    subset = get_pending_constraints(routing, idx, compiled)
    if not (subset["entity_constraints"] or subset["time_constraints"] or subset["order_constraints"]):
        subset = {
            "entity_constraints": full["entity_constraints"],
            "time_constraints": full["time_constraints"],
            "order_constraints": full["order_constraints"],
        }
    subset["unlinked_mentions"] = []
    return subset

