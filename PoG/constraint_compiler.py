"""Question constraint extraction and lightweight Freebase linking for PoG.

This module intentionally does not read gold parses or gold SPARQL. It only
uses the question text, supplied topic entities, and optional Freebase lookups.
"""

from __future__ import annotations

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

US_STATES = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
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


def remove_topic_mentions(question: str, topic_entity: dict[str, str]) -> str:
    masked = question
    for name in sorted((topic_entity or {}).values(), key=lambda x: len(str(x)), reverse=True):
        if not name:
            continue
        pattern = re.compile(re.escape(str(name)), flags=re.IGNORECASE)
        masked = pattern.sub(" ", masked)
    return masked


def extract_candidate_mentions(question: str, topic_entity: dict[str, str]) -> list[dict[str, str]]:
    masked = remove_topic_mentions(question, topic_entity)
    lowered = normalize_text(masked)
    mentions: dict[str, dict[str, str]] = {}

    for state in sorted(US_STATES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(state) + r"\b", lowered):
            label = state.title()
            mentions[normalize_text(label)] = {"mention": label, "source": "state_lexicon"}

    for match in re.finditer(r"\b(?:[A-Z][A-Za-z0-9.'&-]*)(?:\s+[A-Z][A-Za-z0-9.'&-]*){0,4}\b", masked):
        raw = match.group(0).strip()
        norm = normalize_text(raw)
        if not norm or norm in MENTION_STOPWORDS:
            continue
        if all(part.lower() in MENTION_STOPWORDS for part in raw.split()):
            continue
        mentions[norm] = {"mention": raw, "source": "capitalized_span"}

    tokens = re.findall(r"[A-Za-z][A-Za-z.'&-]*", masked)
    for token in tokens:
        norm = normalize_text(token)
        if norm in MENTION_STOPWORDS or len(norm) <= 2:
            continue
        if norm in US_STATES:
            label = norm.title()
            mentions[normalize_text(label)] = {"mention": label, "source": "state_lexicon"}

    return list(mentions.values())


def build_name_alias_query(mention: str, limit: int) -> str:
    literal = sparql_escape_literal(mention)
    return f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?entity ?name ?source
WHERE {{
  {{
    ?entity ns:type.object.name "{literal}"@en .
    ?entity ns:type.object.name ?name .
    BIND("name" AS ?source)
  }}
  UNION
  {{
    ?entity ns:common.topic.alias "{literal}"@en .
    ?entity ns:type.object.name ?name .
    BIND("alias" AS ?source)
  }}
  FILTER(LANGMATCHES(LANG(?name), "en"))
}}
LIMIT {max(1, int(limit))}"""


def score_link_candidate(mention: str, candidate: dict[str, Any], topic_entity: dict[str, str]) -> float:
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
    return max(0.0, min(0.99, score))


def link_mention(
    mention: str,
    topic_entity: dict[str, str],
    args: Any,
    sparql_executor: Optional[SparqlExecutor],
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
    if sparql_executor is None:
        return None, [], "no_sparql_executor"

    limit = int(getattr(args, "constraint_link_top_k", 8))
    query = build_name_alias_query(mention, limit)
    try:
        bindings = sparql_executor(query)
    except Exception as exc:
        return None, [], repr(exc)

    candidates: list[dict[str, Any]] = []
    seen = set()
    for binding in bindings:
        mid = entity_from_binding(binding)
        if not is_mid(mid) or mid in seen:
            continue
        seen.add(mid)
        source = label_from_binding(binding, "source")
        item = {
            "mid": mid,
            "name": label_from_binding(binding, "name") or mention,
            "source": source,
        }
        item["confidence"] = score_link_candidate(mention, item, topic_entity)
        candidates.append(item)

    candidates.sort(key=lambda item: (-item["confidence"], item["name"], item["mid"]))
    threshold = float(getattr(args, "constraint_link_min_confidence", 0.65))
    best = candidates[0] if candidates and candidates[0]["confidence"] >= threshold else None
    return best, candidates, None


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


def compile_question_constraints(
    question: str,
    topic_entity: dict[str, str],
    args: Any,
    model: Any = None,
    sparql_executor: Optional[SparqlExecutor] = None,
) -> dict[str, Any]:
    """Compile executable constraints from question text.

    The sentence model parameter is accepted for future semantic ranking and to
    keep the call site stable; the current implementation uses deterministic
    rules plus Freebase name/alias lookup.
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

    mention_items = extract_candidate_mentions(question, topic_entity or {})
    entity_constraints: list[dict[str, Any]] = []
    unlinked_mentions: list[str] = []
    link_trace = []
    topic_mids = set(topic_entity or {})

    for mention_item in mention_items:
        mention = mention_item["mention"]
        best, candidates, error = link_mention(mention, topic_entity or {}, args, sparql_executor)
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

    entity_constraints.sort(key=lambda item: (-item.get("confidence", 0.0), item.get("name", ""), item.get("mid", "")))
    max_entity_constraints = int(getattr(args, "constraint_max_entity_constraints", 2))
    entity_constraints = entity_constraints[:max(0, max_entity_constraints)]

    compiled = {
        "enabled": True,
        "entity_constraints": entity_constraints,
        "time_constraints": parse_time_constraints(question, args),
        "order_constraints": parse_order_constraints(question),
        "unlinked_mentions": sorted(set(unlinked_mentions)),
        "trace": {
            "mentions": mention_items,
            "linking": link_trace,
            "prompt_context": "",
        },
    }
    compiled["trace"]["prompt_context"] = format_constraints_for_prompt(compiled)
    return compiled
