"""Input file registry and benchmark exclusion registry."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_file, sha256_text
from .paths import PROTOCOL_VERSION, Workspace
from .schemas import ExclusionRecord


def _mtime_iso(path: Path) -> str:
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_input_registry(
    config: Mapping[str, Any],
    workspace: Workspace,
) -> Dict[str, Any]:
    spec = config.get("input_registry") or {}
    include = spec.get("include") or []
    exclude = spec.get("exclude") or []
    if not include:
        raise ProtocolError(ViolationCode.REGISTRY_ERROR, "input_registry.include is empty")

    files = []
    for item in include:
        root_name = item["root"]
        relpath = item["relpath"]
        if root_name == "data":
            root = workspace.data_root
        elif root_name == "cope_alias":
            root = workspace.cope_alias_root
        else:
            raise ProtocolError(ViolationCode.REGISTRY_ERROR, f"unknown input root {root_name}")
        path = workspace.assert_readable_input(relpath, root=root)
        if not path.is_file():
            raise ProtocolError(ViolationCode.REGISTRY_ERROR, f"registered input missing: {path}")
        files.append(
            {
                "relative_path": f"{root_name}/{relpath}",
                "source_root": root_name,
                "relpath": relpath,
                "size": path.stat().st_size,
                "mtime": _mtime_iso(path),
                "sha256": sha256_file(path),
                "usage_tag": item.get("usage_tag"),
                "contains_benchmark_question_answer_or_alias": bool(
                    item.get("contains_benchmark_question_answer_or_alias")
                ),
                "allowed_uses": list(item.get("allowed_uses") or []),
            }
        )

    files.sort(key=lambda row: row["relative_path"])
    registry = {
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "include_count": len(files),
        "exclude": [
            {
                "root": item["root"],
                "relpath": item["relpath"],
                "reason": item.get("reason", ""),
            }
            for item in exclude
        ],
        "files": files,
    }
    registry["content_hash"] = canonical_hash(
        {"files": files, "exclude": registry["exclude"], "protocol_version": PROTOCOL_VERSION}
    )
    return registry


def write_input_registry(
    registry: Mapping[str, Any],
    workspace: Workspace,
    *,
    filename: str = "input_registry_v1.json",
) -> Path:
    path = workspace.artifacts_root / "registries" / filename
    workspace.safe_write_text(path, canonical_json(registry) + "\n")
    return path


def load_input_registry(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def registries_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return left.get("content_hash") == right.get("content_hash")


def validate_exclusion_registry(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    parsed = [ExclusionRecord.from_dict(item) for item in records]
    seen_ids = {}
    seen_hashes = {}
    conflicts = []
    for item in parsed:
        key = (item.dataset, item.split, item.task_id)
        if key in seen_ids:
            conflicts.append({"type": "duplicate_task_id", "key": list(key)})
        seen_ids[key] = item
        hash_key = (item.dataset, item.normalized_question_hash)
        previous = seen_hashes.get(hash_key)
        if previous and previous.task_id != item.task_id:
            conflicts.append(
                {
                    "type": "question_hash_collision",
                    "hash": item.normalized_question_hash,
                    "task_ids": [previous.task_id, item.task_id],
                }
            )
        seen_hashes[hash_key] = item
    if conflicts:
        raise ProtocolError(
            ViolationCode.REGISTRY_ERROR,
            "exclusion registry has duplicates or hash collisions",
            {"conflicts": conflicts},
        )
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "records": [item.to_dict() for item in parsed],
        "count": len(parsed),
    }
    payload["content_hash"] = canonical_hash(payload["records"])
    return payload


def write_exclusion_registry(
    payload: Mapping[str, Any],
    workspace: Workspace,
    *,
    filename: str = "benchmark_exclusion_registry_v1.json",
) -> Path:
    path = workspace.artifacts_root / "registries" / filename
    workspace.safe_write_text(path, canonical_json(payload) + "\n")
    return path
