"""Unified KG structural memory records, validation, JSONL I/O, and manifests."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Iterator

from jsonl_io import append_jsonl_record, format_jsonl_record, iter_jsonl_records

BUILDER_VERSION = "kgm-schema-v1"
PATH_BUILDER_VERSION = "kgm-path-v1"
MEMORY_KIND_SCHEMA_PROFILE = "schema_profile"
MEMORY_KIND_PATH_TEMPLATE = "path_template"
SOURCE_PROTOCOL_SCHEMA_SURVEY = "schema_survey"
SOURCE_PROTOCOL_PATH_PROBE = "path_probe"
ALLOWED_DIRECTIONS = {"outgoing", "incoming"}
ALLOWED_STATUS = {"validated", "low_support", "deprecated", "unknown_or_low_support"}
FORBIDDEN_KEYS = {
    "question",
    "answer",
    "RawQuestion",
    "Parses",
    "gold_path",
    "gold_sparql",
    "gold_relation",
    "gold_answer",
    "gold_answers",
}
FORBIDDEN_KEY_PREFIXES = ("gold_",)
REQUIRED_TOP_FIELDS = (
    "memory_id",
    "memory_kind",
    "source_protocol",
    "applicable_stages",
    "key",
    "semantic",
    "statistics",
    "evidence",
    "provenance",
    "status",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()


def make_memory_id(source_type: str, direction: str, relation: str) -> str:
    digest = hashlib.sha256(f"{source_type}|{direction}|{relation}".encode("utf-8")).hexdigest()[:16]
    return f"kgm_schema_{digest}"


def make_path_memory_id(source_type: str, direction: str, relation_path: list[str], target_type: str) -> str:
    joined = "|".join(relation_path)
    digest = hashlib.sha256(
        f"{source_type}|{direction}|{joined}|{target_type}".encode("utf-8")
    ).hexdigest()[:16]
    return f"kgm_path_{digest}"


def walk_forbidden_keys(obj: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_s = str(key)
            here = f"{path}.{key_s}" if path else key_s
            if key_s in FORBIDDEN_KEYS or key_s.startswith(FORBIDDEN_KEY_PREFIXES):
                hits.append(here)
            hits.extend(walk_forbidden_keys(value, here))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            hits.extend(walk_forbidden_keys(item, f"{path}[{idx}]"))
    return hits


def validate_record(record: dict[str, Any], require_schema_profile: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record is not an object"]
    gold_hits = walk_forbidden_keys(record)
    if gold_hits:
        errors.append("forbidden gold/benchmark fields: " + ", ".join(gold_hits[:8]))
    for field in REQUIRED_TOP_FIELDS:
        if field not in record:
            errors.append(f"missing field {field}")
    kind = record.get("memory_kind")
    protocol = record.get("source_protocol")
    if require_schema_profile:
        if kind != MEMORY_KIND_SCHEMA_PROFILE:
            errors.append(f"memory_kind must be {MEMORY_KIND_SCHEMA_PROFILE}")
        if protocol != SOURCE_PROTOCOL_SCHEMA_SURVEY:
            errors.append(f"source_protocol must be {SOURCE_PROTOCOL_SCHEMA_SURVEY}")
    elif kind == MEMORY_KIND_SCHEMA_PROFILE:
        if protocol != SOURCE_PROTOCOL_SCHEMA_SURVEY:
            errors.append(f"source_protocol must be {SOURCE_PROTOCOL_SCHEMA_SURVEY}")
    elif kind == MEMORY_KIND_PATH_TEMPLATE:
        if protocol != SOURCE_PROTOCOL_PATH_PROBE:
            errors.append(f"source_protocol must be {SOURCE_PROTOCOL_PATH_PROBE}")
    else:
        errors.append(f"unknown memory_kind {kind}")
    stages = record.get("applicable_stages")
    if not isinstance(stages, list) or "relation" not in stages:
        errors.append("applicable_stages must include relation")
    key = record.get("key") or {}
    if not isinstance(key, dict):
        errors.append("key must be an object")
    else:
        if not key.get("source_type"):
            errors.append("key.source_type missing")
        if key.get("direction") not in ALLOWED_DIRECTIONS:
            errors.append("key.direction must be outgoing or incoming")
        path = key.get("relation_path")
        if not isinstance(path, list) or not path:
            errors.append("key.relation_path must be a non-empty list")
        elif kind == MEMORY_KIND_PATH_TEMPLATE and not (1 <= len(path) <= 2):
            errors.append("path_template relation_path must have length 1 or 2")
    stats = record.get("statistics") or {}
    if not isinstance(stats, dict):
        errors.append("statistics must be an object")
    else:
        for field in (
            "discovery_entity_support",
            "validation_entity_support",
            "validation_coverage",
            "median_branching",
            "cvt_ratio",
            "confidence",
        ):
            if field not in stats:
                errors.append(f"statistics.{field} missing")
    if record.get("status") not in ALLOWED_STATUS:
        errors.append("status is invalid")
    provenance = record.get("provenance") or {}
    if not isinstance(provenance, dict) or not provenance.get("build_config_hash"):
        errors.append("provenance.build_config_hash missing")
    return errors


def assert_valid_record(record: dict[str, Any]) -> None:
    errors = validate_record(record)
    if errors:
        raise ValueError("invalid structural memory record: " + "; ".join(errors))


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def compute_confidence(validation_support: int, validation_n: int, min_support: int) -> float:
    if validation_n <= 0:
        return 0.0
    coverage = validation_support / float(validation_n)
    support_factor = min(1.0, validation_support / float(max(1, min_support)))
    return round(coverage * support_factor, 4)


def decide_status(
    validation_support: int,
    validation_coverage: float,
    min_support: int,
    min_coverage: float,
) -> str:
    if validation_support >= min_support and validation_coverage >= min_coverage:
        return "validated"
    return "low_support"


def make_schema_profile_record(
    *,
    source_type: str,
    direction: str,
    relation: str,
    discovery_n: int,
    validation_n: int,
    discovery_support: int,
    validation_support: int,
    branchings: list[float],
    endpoint_type_counts: dict[str, int],
    cvt_neighbors: int,
    typed_neighbors: int,
    positive_entity_ids: list[str],
    witness_path: list[str] | None,
    query_template_id: str,
    query_hash_value: str,
    endpoint_id: str,
    build_config_hash: str,
    min_support: int,
    min_coverage: float,
    built_at: str | None = None,
) -> dict[str, Any]:
    validation_coverage = (validation_support / float(validation_n)) if validation_n else 0.0
    cvt_ratio = (cvt_neighbors / float(typed_neighbors)) if typed_neighbors else 0.0
    status = decide_status(validation_support, validation_coverage, min_support, min_coverage)
    top_endpoints = sorted(endpoint_type_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
    record = {
        "memory_id": make_memory_id(source_type, direction, relation),
        "memory_kind": MEMORY_KIND_SCHEMA_PROFILE,
        "source_protocol": SOURCE_PROTOCOL_SCHEMA_SURVEY,
        "applicable_stages": ["relation"],
        "key": {
            "source_type": source_type,
            "direction": direction,
            "relation_path": [relation],
            "target_type": top_endpoints[0][0] if top_endpoints else "",
        },
        "semantic": {
            "relation_labels": [relation],
            "capability_text": f"{source_type} {direction} {relation}",
            "info_need_tags": [source_type.split(".")[0]] if "." in source_type else [source_type],
        },
        "statistics": {
            "discovery_entity_support": int(discovery_support),
            "discovery_n": int(discovery_n),
            "discovery_coverage": round(discovery_support / float(discovery_n), 4) if discovery_n else 0.0,
            "validation_entity_support": int(validation_support),
            "validation_n": int(validation_n),
            "validation_coverage": round(validation_coverage, 4),
            "median_branching": round(median(branchings), 4),
            "direction_consistency": 1.0,
            "cvt_ratio": round(cvt_ratio, 4),
            "confidence": compute_confidence(validation_support, validation_n, min_support),
            "endpoint_type_top": [{"type": tid, "count": cnt} for tid, cnt in top_endpoints],
        },
        "evidence": {
            "positive_entity_ids": list(positive_entity_ids[:8]),
            "witness_paths": [witness_path] if witness_path else [],
            "query_template_id": query_template_id,
            "query_hash": query_hash_value,
        },
        "provenance": {
            "kg": "freebase",
            "endpoint_id": endpoint_id,
            "builder_version": BUILDER_VERSION,
            "build_config_hash": build_config_hash,
            "built_at": built_at or utc_now(),
        },
        "status": status,
    }
    assert_valid_record(record)
    return record


def make_path_template_record(
    *,
    source_type: str,
    direction: str,
    relation_path: list[str],
    target_type: str,
    discovery_n: int,
    validation_n: int,
    discovery_support: int,
    validation_support: int,
    branchings: list[float],
    cvt_ratio: float,
    contains_cvt: bool,
    positive_entity_ids: list[str],
    witness_path: list[str] | None,
    query_template_id: str,
    query_hash_value: str,
    endpoint_id: str,
    build_config_hash: str,
    min_support: int,
    min_coverage: float,
    built_at: str | None = None,
) -> dict[str, Any]:
    validation_coverage = (validation_support / float(validation_n)) if validation_n else 0.0
    status = decide_status(validation_support, validation_coverage, min_support, min_coverage)
    path = [str(rel) for rel in relation_path]
    record = {
        "memory_id": make_path_memory_id(source_type, direction, path, target_type),
        "memory_kind": MEMORY_KIND_PATH_TEMPLATE,
        "source_protocol": SOURCE_PROTOCOL_PATH_PROBE,
        "applicable_stages": ["relation"],
        "key": {
            "source_type": source_type,
            "direction": direction,
            "relation_path": path,
            "target_type": target_type,
        },
        "semantic": {
            "relation_labels": path,
            "capability_text": f"{source_type} {' -> '.join(path)}"
            + (f" -> {target_type}" if target_type else ""),
            "info_need_tags": [source_type.split(".")[0]] if "." in source_type else [source_type],
        },
        "statistics": {
            "discovery_entity_support": int(discovery_support),
            "discovery_n": int(discovery_n),
            "discovery_coverage": round(discovery_support / float(discovery_n), 4) if discovery_n else 0.0,
            "validation_entity_support": int(validation_support),
            "validation_n": int(validation_n),
            "validation_coverage": round(validation_coverage, 4),
            "median_branching": round(median(branchings), 4),
            "direction_consistency": 1.0,
            "cvt_ratio": round(float(cvt_ratio), 4),
            "contains_cvt": bool(contains_cvt),
            "confidence": compute_confidence(validation_support, validation_n, min_support),
        },
        "evidence": {
            "positive_entity_ids": list(positive_entity_ids[:8]),
            "witness_paths": [witness_path] if witness_path else [],
            "query_template_id": query_template_id,
            "query_hash": query_hash_value,
        },
        "provenance": {
            "kg": "freebase",
            "endpoint_id": endpoint_id,
            "builder_version": PATH_BUILDER_VERSION,
            "build_config_hash": build_config_hash,
            "built_at": built_at or utc_now(),
        },
        "status": status,
    }
    assert_valid_record(record)
    return record


def iter_memory_records(path: str) -> Iterator[dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return
        yield  # pragma: no cover
    yield from iter_jsonl_records(path)


def append_memory_records(path: str, records: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    for record in records:
        assert_valid_record(record)
        append_jsonl_record(path, record)


def rewrite_jsonl(path: str, records: list[dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(format_jsonl_record(record))
    os.replace(tmp, path)


def drop_records_for_types(path: str, source_types: set[str]) -> None:
    if not path or not os.path.isfile(path) or not source_types:
        return
    kept = []
    for record in iter_jsonl_records(path):
        key = record.get("key") or {}
        if key.get("source_type") in source_types:
            continue
        kept.append(record)
    rewrite_jsonl(path, kept)


def build_index(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, dict[str, dict[str, list[str]]]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        memory_id = record.get("memory_id")
        key = record.get("key") or {}
        source_type = key.get("source_type")
        direction = key.get("direction")
        relation_path = key.get("relation_path") or []
        relation = relation_path[0] if relation_path else ""
        if not memory_id or not source_type or direction not in ALLOWED_DIRECTIONS or not relation:
            continue
        by_type.setdefault(source_type, {}).setdefault(direction, {}).setdefault(relation, []).append(memory_id)
        by_id[memory_id] = {
            "source_type": source_type,
            "direction": direction,
            "relation": relation,
            "status": record.get("status"),
            "confidence": (record.get("statistics") or {}).get("confidence"),
        }
    return {
        "by_source_type": by_type,
        "by_memory_id": by_id,
        "n_records": len(records),
        "n_validated": sum(1 for rec in records if rec.get("status") == "validated"),
    }


def write_index(path: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    index = build_index(records)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return index


def write_manifest(path: str, manifest: dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_config_payload(
    *,
    n_types: int,
    discovery_n: int,
    validation_n: int,
    seed: int,
    min_support: int,
    min_coverage: float,
    neighbor_sample_limit: int,
    endpoint: str,
    type_source: str,
    excluded_types: list[str],
) -> dict[str, Any]:
    return {
        "builder_version": BUILDER_VERSION,
        "n_types": int(n_types),
        "discovery_n": int(discovery_n),
        "validation_n": int(validation_n),
        "seed": int(seed),
        "min_validation_entity_support": int(min_support),
        "min_validation_coverage": float(min_coverage),
        "neighbor_sample_limit": int(neighbor_sample_limit),
        "endpoint": endpoint,
        "type_source": type_source,
        "excluded_types": list(excluded_types),
        "memory_kind": MEMORY_KIND_SCHEMA_PROFILE,
        "source_protocol": SOURCE_PROTOCOL_SCHEMA_SURVEY,
    }
