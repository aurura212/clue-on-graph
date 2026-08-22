"""Run manifest helpers. Never persist secrets or full environment dumps."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .hashing import sha256_file
from .paths import PROTOCOL_VERSION, Workspace
from .schemas import RunManifest, RunStatus

SECRET_NAME_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API",
    "CREDENTIAL",
    "AUTHORIZATION",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(prefix: str = "sp0") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def git_identity(repo_root: Path) -> tuple[Optional[str], Optional[bool]]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return commit, bool(porcelain.strip())
    except (OSError, subprocess.CalledProcessError):
        return None, None


def redact_env(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    source = env if env is not None else os.environ
    redacted = {}
    for key in sorted(source):
        upper = key.upper()
        if any(marker in upper for marker in SECRET_NAME_MARKERS):
            continue
        # Never copy values; only record names of non-secret variables that we actually need.
        # SP0 stores an allowlist of names, not values.
        if key in {"PWD", "USER", "HOME", "LANG", "LC_ALL"}:
            redacted[key] = "<omitted>"
    return redacted


def file_record(path: Path, *, role: str) -> Dict[str, Any]:
    exists = path.exists()
    record = {
        "path": str(path),
        "role": role,
        "exists": exists,
    }
    if exists and path.is_file():
        record["sha256"] = sha256_file(path)
        record["size"] = path.stat().st_size
    return record


class RunSession:
    def __init__(
        self,
        workspace: Workspace,
        *,
        run_id: Optional[str] = None,
        plan_version: str,
        config_hash: str,
        command: Optional[Sequence[str]] = None,
        seed: Optional[int] = None,
        model_metadata: Optional[Dict[str, Any]] = None,
        input_files: Optional[List[Dict[str, Any]]] = None,
        prefix: str = "sp0",
    ) -> None:
        self.workspace = workspace
        self.run_id = run_id or new_run_id(prefix)
        self.run_dir = workspace.assert_writable(workspace.runs_root / self.run_id)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        commit, dirty = git_identity(workspace.clue_on_graph_root)
        self.manifest = RunManifest(
            run_id=self.run_id,
            plan_version=plan_version,
            protocol_version=PROTOCOL_VERSION,
            git_commit=commit,
            git_dirty=dirty,
            command=list(command or sys.argv),
            config_hash=config_hash,
            input_files=list(input_files or []),
            seed=seed,
            model_metadata=dict(model_metadata or {"llm_called": False}),
            start_time=utc_now(),
            end_time=None,
            status=RunStatus.RUNNING,
            output_files=[],
            error=None,
        )
        self._write_manifest()

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.json"

    @property
    def result_path(self) -> Path:
        return self.run_dir / "sp0_check_result.json"

    @property
    def stdout_path(self) -> Path:
        return self.run_dir / "stdout_summary.txt"

    @property
    def stderr_path(self) -> Path:
        return self.run_dir / "stderr_summary.txt"

    def add_output(self, path: Path, role: str) -> None:
        self.manifest.output_files.append(file_record(path, role=role))

    def write_text(self, relative: str, text: str, role: str) -> Path:
        path = self.workspace.safe_write_text(self.run_dir / relative, text)
        self.add_output(path, role)
        return path

    def _write_manifest(self) -> None:
        payload = self.manifest.to_json()
        self.workspace.safe_write_text(self.manifest_path, payload + "\n")

    def finish(self, status: RunStatus, error: Optional[Dict[str, Any]] = None) -> RunManifest:
        self.manifest.status = status
        self.manifest.end_time = utc_now()
        self.manifest.error = error
        if self.manifest_path.exists():
            # refresh hash after writing outputs; include manifest itself last
            pass
        self._write_manifest()
        self.add_output(self.manifest_path, "manifest")
        self._write_manifest()
        return self.manifest
