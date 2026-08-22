"""Fail-fast SP2-A guards: no LLM, no memory, no eval-set trajectories, no secrets."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from .errors import ProtocolError, ViolationCode
from .hashing import sha256_file
from .llm_guard import LLMCallGuard
from .paths import Workspace

SECRET_NAME_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API",
    "CREDENTIAL",
    "AUTHORIZATION",
    "COOKIE",
)
SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9\-._=]+|api[_-]?key\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
EVAL_TASK_RE = re.compile(r"\b(WebQTest-\d+|WebQTrn-\d+|WebQSP-\d+|CWQ\d+|ComplexWebQ-)")
MEMORY_PATH_MARKERS = ("candidate_experience", "formal_memory", "memory_store", "promoted_memory")


class MemoryAccessGuard:
    def __init__(self) -> None:
        self.reads = 0
        self.writes = 0

    def read(self, *args: Any, **kwargs: Any) -> Any:
        self.reads += 1
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            "SP2-A forbids memory read",
            {"call_count": self.reads},
        )

    def write(self, *args: Any, **kwargs: Any) -> Any:
        self.writes += 1
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            "SP2-A forbids memory write",
            {"call_count": self.writes},
        )


class Sp2aGuards:
    def __init__(self) -> None:
        self.llm = LLMCallGuard()
        self.memory = MemoryAccessGuard()
        self.oracle_label_in_action = 0
        self.eval_set_trajectory_uses = 0
        self.secret_hits: List[Dict[str, str]] = []

    def counts(self) -> Dict[str, int]:
        return {
            "llm_calls": self.llm.calls,
            "memory_reads": self.memory.reads,
            "memory_writes": self.memory.writes,
            "oracle_label_in_action": self.oracle_label_in_action,
            "eval_set_trajectory_uses": self.eval_set_trajectory_uses,
            "secret_hits": len(self.secret_hits),
        }

    def note_oracle_label(self) -> None:
        self.oracle_label_in_action += 1
        raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, "Oracle/test label entered an Action view")

    def note_eval_set_use(self, task_id: str) -> None:
        self.eval_set_trajectory_uses += 1
        raise ProtocolError(
            ViolationCode.REGISTRY_ERROR,
            "frozen eval-set task used to generate an SP2-A trajectory",
            {"task_id": task_id},
        )


def scan_text_for_secrets(text: str, *, path: str) -> List[Dict[str, str]]:
    hits = []
    if SECRET_VALUE_RE.search(text):
        hits.append({"path": path, "reason": "secret_value_pattern"})
    lowered = text.lower()
    if "authorization:" in lowered or "cookie:" in lowered:
        hits.append({"path": path, "reason": "auth_or_cookie_header"})
    return hits


def scan_config_for_secrets(config: Mapping[str, Any]) -> List[str]:
    banned = []
    for key in config:
        upper = str(key).upper()
        if any(marker in upper for marker in SECRET_NAME_MARKERS):
            banned.append(str(key))
    blob = json.dumps(config, ensure_ascii=False)
    if SECRET_VALUE_RE.search(blob):
        banned.append("<config-value-pattern>")
    return banned


def scan_paths_for_secrets(paths: Sequence[Path]) -> List[Dict[str, str]]:
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


def scan_eval_task_ids(text: str, banned_ids: Set[str]) -> List[str]:
    found = []
    for item in banned_ids:
        if item and item in text:
            found.append(item)
    for match in EVAL_TASK_RE.findall(text):
        if match not in found:
            found.append(match)
    return found


def assert_task_not_eval(task_id: str, banned_ids: Set[str], guards: Sp2aGuards) -> None:
    if task_id in banned_ids or EVAL_TASK_RE.search(task_id):
        guards.note_eval_set_use(task_id)


def load_exclusion_task_ids(workspace: Workspace) -> Set[str]:
    path = workspace.artifacts_root / "registries" / "benchmark_exclusion_registry_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") or []
    return {str(item.get("task_id")) for item in records if item.get("task_id")}


def snapshot_readonly_roots(workspace: Workspace) -> Dict[str, Any]:
    rows = []
    for name, root in (("data", workspace.data_root), ("cope_alias", workspace.cope_alias_root)):
        exists = root.exists()
        stat = root.stat() if exists else None
        rows.append(
            {
                "name": name,
                "path": str(root),
                "exists": exists,
                "mtime_ns": getattr(stat, "st_mtime_ns", None) if stat else None,
                "inode": getattr(stat, "st_ino", None) if stat else None,
            }
        )
    return {"roots": rows}


def source_mentions_memory(workspace: Workspace) -> List[Dict[str, str]]:
    hits = []
    root = workspace.src_root / "sp_memory"
    watched = {"live_environment.py", "kg_sparql.py", "recorded_io.py", "budget_ledger.py", "sp2a_checks.py"}
    for path in sorted(root.glob("*.py")):
        if path.name not in watched and not path.name.startswith("sp2a"):
            continue
        if path.name == "sp2a_guards.py":
            continue
        text = path.read_text(encoding="utf-8")
        for marker in ("artifacts/memory", "formal_memory.json", "candidate_experience.json"):
            if marker in text:
                hits.append({"file": path.name, "marker": marker})
    return hits
