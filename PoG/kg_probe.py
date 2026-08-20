"""SPARQL probe adapter for KG structural memory.

Wraps PoG ``execurte_sparql`` with disk cache, retries, and schema-survey helpers.
Does not open a separate HTTP client stack beyond the existing SPARQLPATH.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import traceback
from typing import Any, Callable

from SPARQLWrapper import JSON, SPARQLWrapper

from freebase_func import (
    SPARQLPATH,
    SPARQL_TIMEOUT,
    abandon_rels,
    execurte_sparql,
    relation_from_binding,
    replace_relation_prefix,
    sparql_head_relations,
    sparql_tail_relations,
)
from constraint_compiler import build_types_query, is_mid

NS = "http://rdf.freebase.com/ns/"
DEFAULT_MAX_RETRIES = 3
EXCLUDED_TYPE_EXACT = {"common.topic"}
EXCLUDED_TYPE_PREFIXES = ("type.", "un.", "base.", "user.", "freebase.", "common.")


def strip_ns(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith(NS):
        return text[len(NS) :]
    if text.startswith("ns:"):
        return text[3:]
    return text


def is_excluded_type(type_id: str) -> bool:
    tid = strip_ns(type_id)
    if not tid or tid in EXCLUDED_TYPE_EXACT:
        return True
    return tid.startswith(EXCLUDED_TYPE_PREFIXES)


def query_hash(sparql: str) -> str:
    return hashlib.sha256(sparql.strip().encode("utf-8")).hexdigest()


class KGProbe:
    def __init__(
        self,
        cache_dir: str,
        executor: Callable[[str], list[dict[str, Any]]] | None = None,
        endpoint: str = SPARQLPATH,
        timeout: int | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.cache_dir = cache_dir
        self.executor = executor or execurte_sparql
        self.endpoint = endpoint
        self.timeout = int(timeout if timeout is not None else SPARQL_TIMEOUT)
        self.max_retries = max(1, int(max_retries))
        self.stats = {
            "n_queries": 0,
            "n_cache_hits": 0,
            "n_retries": 0,
            "n_failures": 0,
        }
        os.makedirs(self.cache_dir, exist_ok=True)
        self._mediator_cache: dict[str, bool] = {}

    def _cache_path(self, sparql: str) -> str:
        return os.path.join(self.cache_dir, query_hash(sparql) + ".json")

    def _load_cache(self, sparql: str) -> list[dict[str, Any]] | None:
        path = self._cache_path(sparql)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload.get("bindings", [])

    def _save_cache(self, sparql: str, bindings: list[dict[str, Any]]) -> None:
        path = self._cache_path(sparql)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"bindings": bindings}, handle, ensure_ascii=False)
        os.replace(tmp, path)

    def query(self, sparql: str, timeout: int | None = None) -> list[dict[str, Any]]:
        self.stats["n_queries"] += 1
        cached = self._load_cache(sparql)
        if cached is not None:
            self.stats["n_cache_hits"] += 1
            return cached

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                if timeout is None or int(timeout) == self.timeout:
                    bindings = self.executor(sparql)
                else:
                    bindings = self._query_with_timeout(sparql, int(timeout))
                self._save_cache(sparql, bindings)
                return bindings
            except Exception as exc:
                last_error = exc
                self.stats["n_retries"] += 1
                traceback.print_exc()
                if attempt + 1 >= self.max_retries:
                    break
                time.sleep(2 ** attempt)

        self.stats["n_failures"] += 1
        raise RuntimeError(f"SPARQL query failed after {self.max_retries} attempts: {last_error}")

    def _query_with_timeout(self, sparql: str, timeout: int) -> list[dict[str, Any]]:
        wrapper = SPARQLWrapper(self.endpoint)
        wrapper.setQuery(sparql)
        wrapper.setReturnFormat(JSON)
        wrapper.setTimeout(timeout)
        results = wrapper.query().convert()
        return results.get("results", {}).get("bindings", [])

    def head_relations(self, entity_id: str) -> list[str]:
        bindings = self.query(sparql_head_relations % entity_id)
        relations = replace_relation_prefix(bindings)
        return sorted({rel for rel in relations if rel and not abandon_rels(rel)})

    def tail_relations(self, entity_id: str) -> list[str]:
        bindings = self.query(sparql_tail_relations % entity_id)
        relations = replace_relation_prefix(bindings)
        return sorted({rel for rel in relations if rel and not abandon_rels(rel)})

    def sample_entities_of_type(self, type_id: str, limit: int) -> list[str]:
        type_id = strip_ns(type_id)
        limit = max(1, int(limit))
        sparql = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?x
WHERE {{
  ?x ns:type.object.type ns:{type_id} .
  FILTER(STRSTARTS(STR(?x), "{NS}m.") || STRSTARTS(STR(?x), "{NS}g."))
}}
LIMIT {limit}"""
        bindings = self.query(sparql)
        out: list[str] = []
        seen = set()
        for binding in bindings:
            mid = strip_ns(binding.get("x", {}).get("value", ""))
            if not is_mid(mid) or mid in seen:
                continue
            seen.add(mid)
            out.append(mid)
        return out

    def sample_neighbors(self, entity_id: str, relation: str, outgoing: bool, limit: int) -> list[str]:
        limit = max(1, int(limit))
        if outgoing:
            pattern = f"ns:{entity_id} ns:{relation} ?n ."
        else:
            pattern = f"?n ns:{relation} ns:{entity_id} ."
        sparql = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?n
WHERE {{
  {pattern}
}}
LIMIT {limit}"""
        bindings = self.query(sparql)
        out: list[str] = []
        seen = set()
        for binding in bindings:
            nid = strip_ns(binding.get("n", {}).get("value", ""))
            if not nid or nid in seen:
                continue
            seen.add(nid)
            out.append(nid)
        return out

    def two_hop_exists(self, entity_id: str, relation1: str, relation2: str, first_outgoing: bool = True) -> bool:
        if first_outgoing:
            hop1 = f"ns:{entity_id} ns:{relation1} ?x ."
        else:
            hop1 = f"?x ns:{relation1} ns:{entity_id} ."
        sparql = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?y
WHERE {{
  {hop1}
  ?x ns:{relation2} ?y .
}}
LIMIT 1"""
        return bool(self.query(sparql))

    def entity_types(self, mids: list[str]) -> dict[str, list[str]]:
        clean = [mid for mid in mids if is_mid(mid)]
        types: dict[str, list[str]] = {mid: [] for mid in clean}
        if not clean:
            return types
        batch_size = 40
        for start in range(0, len(clean), batch_size):
            batch = clean[start : start + batch_size]
            sparql = build_types_query(batch)
            if "VALUES ?entity" not in sparql:
                continue
            bindings = self.query(sparql)
            for binding in bindings:
                entity = strip_ns(binding.get("entity", {}).get("value", ""))
                type_id = strip_ns(binding.get("type", {}).get("value", ""))
                if entity not in types or not type_id:
                    continue
                if type_id not in types[entity]:
                    types[entity].append(type_id)
        return types

    def is_mediator_type(self, type_id: str) -> bool:
        type_id = strip_ns(type_id)
        if not type_id:
            return False
        if type_id in self._mediator_cache:
            return self._mediator_cache[type_id]
        sparql = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT ?flag
WHERE {{
  ns:{type_id} ns:freebase.type_hints.mediator ?flag .
}}
LIMIT 1"""
        bindings = self.query(sparql)
        flag = False
        if bindings:
            raw = str(bindings[0].get("flag", {}).get("value", "")).strip().lower()
            flag = raw in {"true", "1", "true^^http://www.w3.org/2001/xmlschema#boolean"}
        self._mediator_cache[type_id] = flag
        return flag

    def frequent_types(self, top_k: int, sample_limit: int = 8000) -> list[tuple[str, int]]:
        grouped = self._try_type_group_by(top_k)
        if grouped:
            return grouped
        print("[kg_probe] GROUP BY type counts timed out or failed; falling back to entity sample.")
        return self._types_from_entity_sample(top_k, sample_limit)

    def _try_type_group_by(self, top_k: int) -> list[tuple[str, int]]:
        sparql = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT ?type (COUNT(?x) AS ?c)
WHERE {{
  ?x ns:type.object.type ?type .
}}
GROUP BY ?type
ORDER BY DESC(?c)
LIMIT {max(top_k * 3, 50)}"""
        try:
            bindings = self.query(sparql, timeout=max(self.timeout, 60))
        except Exception:
            traceback.print_exc()
            return []
        ranked: list[tuple[str, int]] = []
        for binding in bindings:
            type_id = strip_ns(binding.get("type", {}).get("value", ""))
            if is_excluded_type(type_id):
                continue
            try:
                count = int(float(binding.get("c", {}).get("value", 0) or 0))
            except (TypeError, ValueError):
                count = 0
            ranked.append((type_id, count))
            if len(ranked) >= top_k:
                break
        return ranked

    def _types_from_entity_sample(self, top_k: int, sample_limit: int) -> list[tuple[str, int]]:
        sparql = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?x
WHERE {{
  ?x ns:type.object.type ns:common.topic .
  FILTER(STRSTARTS(STR(?x), "{NS}m."))
}}
LIMIT {max(200, int(sample_limit))}"""
        bindings = self.query(sparql, timeout=max(self.timeout, 60))
        mids = []
        seen = set()
        for binding in bindings:
            mid = strip_ns(binding.get("x", {}).get("value", ""))
            if not is_mid(mid) or mid in seen:
                continue
            seen.add(mid)
            mids.append(mid)
        counts: dict[str, int] = {}
        type_map = self.entity_types(mids)
        for _mid, type_ids in type_map.items():
            for type_id in type_ids:
                if is_excluded_type(type_id):
                    continue
                counts[type_id] = counts.get(type_id, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:top_k]


def binding_relation(binding: dict[str, Any]) -> str:
    if "relation" in binding:
        return relation_from_binding(binding["relation"].get("value", ""))
    return ""
