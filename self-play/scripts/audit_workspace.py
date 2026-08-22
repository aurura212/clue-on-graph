#!/usr/bin/env python3
"""Audit that experimental writes stay inside self-play/ and inputs are unchanged."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from sp_memory.baseline import baseline_file_hashes
from sp_memory.config import load_config
from sp_memory.hashing import canonical_json
from sp_memory.paths import Workspace, WorkspaceBoundaryError
from sp_memory.registry import build_input_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    workspace = Workspace.from_this_package()
    config, _, _ = load_config(Path(args.config) if args.config else None, workspace)
    registry = build_input_registry(config, workspace)
    illegal = [
        workspace.data_root / "AUDIT_SHOULD_FAIL.txt",
        workspace.cope_alias_root / "AUDIT_SHOULD_FAIL.txt",
        workspace.pog_root / "AUDIT_SHOULD_FAIL.txt",
        Path("/tmp/sp0_audit_outside.txt"),
        workspace.self_play_root / ".." / "data" / "AUDIT_SHOULD_FAIL.json",
    ]
    rejected = 0
    for path in illegal:
        try:
            workspace.assert_writable(path)
        except WorkspaceBoundaryError:
            rejected += 1
    report = {
        "baseline_hashes": baseline_file_hashes(workspace),
        "input_files": {item["relative_path"]: item["sha256"] for item in registry["files"]},
        "illegal_paths": len(illegal),
        "rejected": rejected,
        "reject_rate": rejected / len(illegal),
    }
    out = workspace.logs_root / "workspace_audit.json"
    workspace.safe_write_text(out, canonical_json(report) + "\n")
    print(canonical_json({"path": str(out), "reject_rate": report["reject_rate"]}))
    return 0 if rejected == len(illegal) else 1


if __name__ == "__main__":
    raise SystemExit(main())
