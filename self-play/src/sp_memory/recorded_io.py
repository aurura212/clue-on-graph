"""Desensitized recorded I/O writer and offline replayer for SP2-A live KG."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .hashing import canonical_hash, canonical_json, sha256_text
from .kg_sparql import BuiltRequest, NormalizedTarget, PhysicalExchange, PhysicalStatus, build_entity_search_request
from .paths import Workspace

RECORDED_IO_VERSION = "sp2a-recorded-io-v1"


def exchange_to_record(exchange: PhysicalExchange, *, task_id: str, step_id: str) -> Dict[str, Any]:
    request = exchange.request
    record = {
        "recorded_io_version": RECORDED_IO_VERSION,
        "record_id": exchange.physical_request_id,
        "task_id": task_id,
        "step_id": step_id,
        "logical_action_id": exchange.logical_action_id,
        "physical_request_id": exchange.physical_request_id,
        "endpoint_or_snapshot_id": request.endpoint,
        "query_kind": request.query_kind,
        "request_summary": {
            "method": request.method,
            "entity": request.entity,
            "relation": request.relation,
            "direction": request.direction,
            "head": request.head,
            "sparql_hash": sha256_text(request.sparql),
            "params": request.params_summary,
        },
        "sparql": request.sparql,
        "request_hash": request.request_hash,
        "response_hash": exchange.response_hash,
        "response_status": exchange.status.value,
        "http_status": exchange.http_status,
        "retry_index": exchange.retry_index,
        "elapsed_ms": exchange.elapsed_ms,
        "error_message": exchange.error_message,
        "canonical_targets": [item.to_dict() for item in exchange.targets],
        "bindings": exchange.bindings,
        "truncated": exchange.truncated,
        "network_used": exchange.network_used,
        "contains_secret": False,
        "allowed_uses": ["sp2a_replay", "audit"],
    }
    record["content_hash"] = canonical_hash({k: v for k, v in record.items() if k != "content_hash"})
    return record


def write_recorded_io(
    records: Iterable[Mapping[str, Any]],
    workspace: Workspace,
    *,
    relative: str,
) -> Path:
    payload = {
        "recorded_io_version": RECORDED_IO_VERSION,
        "records": list(records),
    }
    payload["bundle_hash"] = canonical_hash({"records": payload["records"]})
    path = workspace.assert_writable(workspace.self_play_root / relative)
    workspace.safe_write_text(path, canonical_json(payload) + "\n")
    return path


def load_recorded_io(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("recorded_io_version") != RECORDED_IO_VERSION:
        raise ValueError(f"unexpected recorded I/O version {payload.get('recorded_io_version')}")
    return payload


def index_records(payload: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("records") or []:
        key = str(item.get("request_hash") or "")
        if not key:
            continue
        # Keep the last successful-or-final attempt for a request hash+retry? Replay uses final per logical.
        out[f"{key}|{item.get('retry_index', 0)}"] = dict(item)
        out[key] = dict(item)
    return out


def replay_targets_from_record(record: Mapping[str, Any]) -> List[str]:
    targets = []
    for item in record.get("canonical_targets") or []:
        targets.append(str(item["value"]))
    return targets


def record_to_exchange(record: Mapping[str, Any], *, endpoint: str) -> PhysicalExchange:
    direction = record.get("request_summary", {}).get("direction")
    entity = record.get("request_summary", {}).get("entity")
    relation = record.get("request_summary", {}).get("relation")
    if record.get("query_kind") == "entity_search" and entity and relation and direction:
        built = build_entity_search_request(entity, relation, direction, endpoint=endpoint)
    else:
        built = BuiltRequest(
            query_kind=str(record.get("query_kind") or "entity_search"),
            entity=entity,
            relation=relation,
            direction=direction,
            head=record.get("request_summary", {}).get("head"),
            sparql=str(record.get("sparql") or ""),
            endpoint=endpoint,
            method="POST",
            request_hash=str(record.get("request_hash") or ""),
            params_summary=dict(record.get("request_summary", {}).get("params") or {}),
        )
    targets = [
        NormalizedTarget(
            value=str(item["value"]),
            source_location=str(item.get("source_location") or ""),
            binding_index=int(item.get("binding_index") or 0),
            term_type=str(item.get("term_type") or "unknown"),
        )
        for item in record.get("canonical_targets") or []
    ]
    return PhysicalExchange(
        physical_request_id=str(record.get("physical_request_id") or record.get("record_id")),
        logical_action_id=str(record.get("logical_action_id") or ""),
        request=built,
        retry_index=int(record.get("retry_index") or 0),
        status=PhysicalStatus(record.get("response_status")),
        http_status=record.get("http_status"),
        response_hash=str(record.get("response_hash") or ""),
        elapsed_ms=float(record.get("elapsed_ms") or 0.0),
        bindings=list(record.get("bindings") or []),
        targets=targets,
        error_message=str(record.get("error_message") or ""),
        truncated=bool(record.get("truncated")),
        network_used=False,
    )


def diff_replay(online: Mapping[str, Any], replayed: Mapping[str, Any], keys: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    compare_keys = list(
        keys
        or (
            "status",
            "canonical_targets",
            "logical_actions",
            "physical_requests",
            "retries",
            "state_id_after",
            "environment_status",
        )
    )
    diffs = []
    for key in compare_keys:
        left = online.get(key)
        right = replayed.get(key)
        if left != right:
            diffs.append({"key": key, "online": left, "replay": right})
    return diffs
