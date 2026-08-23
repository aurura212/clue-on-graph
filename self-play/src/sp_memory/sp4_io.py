"""SP4 write-boundary helpers: atomic JSON/JSONL, hashes, NOT_GENERATED records."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .hashing import canonical_json, sha256_file, sha256_text
from .paths import PROTOCOL_VERSION, Workspace
from .sp2a_guards import scan_text_for_secrets

FORBIDDEN_BENCHMARK = (
    "artifacts/datasets/webqsp_smoke_20.jsonl",
    "artifacts/datasets/webqsp_model_compare_150.jsonl",
    "artifacts/datasets/cwq_model_compare_50.jsonl",
)


def assert_not_benchmark_path(path: Path | str) -> None:
    text = str(path).replace("\\", "/")
    for item in FORBIDDEN_BENCHMARK:
        if text.endswith(item) or f"/{item}" in text:
            raise ValueError(f"SP4 forbids reading or writing benchmark file {item}")


def atomic_write_text(workspace: Workspace, path: Path | str, text: str) -> Path:
    resolved = workspace.assert_writable(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".sp4-", suffix=".tmp", dir=str(resolved.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, resolved)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return resolved


def write_json(workspace: Workspace, path: Path | str, payload: Any) -> Path:
    return atomic_write_text(workspace, path, canonical_json(payload) + "\n")


def write_jsonl(workspace: Workspace, path: Path | str, rows: Sequence[Mapping[str, Any]]) -> Path:
    text = "".join(canonical_json(dict(row)) + "\n" for row in rows)
    return atomic_write_text(workspace, path, text)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def file_digest(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    return sha256_file(path)


def not_generated(relpath: str, reason: str) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "NOT_GENERATED",
        "path": relpath,
        "reason": reason,
    }


def write_not_generated(workspace: Workspace, path: Path | str, reason: str) -> Path:
    resolved = workspace.assert_writable(path)
    payload = not_generated(str(Path(path)), reason)
    return write_json(workspace, resolved, payload)


def secret_scan_paths(paths: Iterable[Path]) -> List[Dict[str, str]]:
    hits: List[Dict[str, str]] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        if path.suffix.lower() not in {".json", ".jsonl", ".txt", ".md", ".log"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        hits.extend(scan_text_for_secrets(text, path=str(path)))
    return hits


def sha256_blob(payload: Any) -> str:
    if isinstance(payload, str):
        return sha256_text(payload)
    return sha256_text(canonical_json(payload))
