"""Per-task PoG working memory isolated to the current run scratch directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import ProtocolError, ViolationCode
from .hashing import sha256_text
from .paths import Workspace

FORBIDDEN_MEMORY_MARKERS = (
    "candidate_experience",
    "promoted_memory",
    "formal_memory",
    "memory_store",
    "artifacts/memory",
)


class PogWorkingMemory:
    def __init__(self, workspace: Workspace, run_dir: Path, task_id: str) -> None:
        self.workspace = workspace
        self.task_id = task_id
        self.dir = workspace.assert_writable(run_dir / "scratch" / task_id)
        self.mem_path = self.dir / "pog_working_memory"
        self.subq_path = self.dir / "subq"
        self.events: List[Dict[str, Any]] = []

    def _reject_forbidden(self, path: Path) -> None:
        text = str(path)
        for marker in FORBIDDEN_MEMORY_MARKERS:
            if marker in text:
                raise ProtocolError(
                    ViolationCode.WORKSPACE_BOUNDARY,
                    "refusing Self-Play Experience Memory path",
                    {"path": text, "marker": marker},
                )

    def create_empty(self) -> None:
        self._reject_forbidden(self.mem_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.workspace.safe_write_text(self.mem_path, "")
        self.events.append({"op": "create", "task_id": self.task_id, "path": str(self.mem_path), "bytes": 0})

    def read(self) -> str:
        self._reject_forbidden(self.mem_path)
        text = self.mem_path.read_text(encoding="utf-8") if self.mem_path.exists() else ""
        self.events.append(
            {
                "op": "read",
                "task_id": self.task_id,
                "path": str(self.mem_path),
                "bytes": len(text.encode("utf-8")),
                "sha256": sha256_text(text) if text else sha256_text(""),
            }
        )
        return text

    def write(self, text: str) -> None:
        self._reject_forbidden(self.mem_path)
        payload = text or ""
        self.workspace.safe_write_text(self.mem_path, payload)
        self.events.append(
            {
                "op": "write",
                "task_id": self.task_id,
                "path": str(self.mem_path),
                "bytes": len(payload.encode("utf-8")),
                "sha256": sha256_text(payload),
            }
        )

    def write_subquestions(self, text: str) -> None:
        self._reject_forbidden(self.subq_path)
        self.workspace.safe_write_text(self.subq_path, text or "")
        self.events.append(
            {
                "op": "write_subq",
                "task_id": self.task_id,
                "path": str(self.subq_path),
                "bytes": len((text or "").encode("utf-8")),
            }
        )

    def close(self) -> None:
        self.events.append({"op": "close", "task_id": self.task_id, "path": str(self.mem_path)})

    def audit(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "path": str(self.mem_path),
            "events": list(self.events),
            "create_count": sum(1 for item in self.events if item["op"] == "create"),
            "read_count": sum(1 for item in self.events if item["op"] == "read"),
            "write_count": sum(1 for item in self.events if item["op"] == "write"),
        }
