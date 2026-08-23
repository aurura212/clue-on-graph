#!/usr/bin/env python3
"""Freeze SP3 D0/D1/H discovery data. No LLM. No live KG."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from sp_memory.config import load_config
from sp_memory.hashing import canonical_json
from sp_memory.paths import Workspace
from sp_memory.sp3_sampling import freeze_discovery, verify_discovery


def main() -> int:
    workspace = Workspace.from_this_package()
    workspace.ensure_output_dirs()
    config_path = workspace.configs_root / "sp3_candidate_discovery_v1.json"
    config, config_hash, _ = load_config(config_path, workspace)
    result = freeze_discovery(workspace, config)
    verified = verify_discovery(workspace, config)
    payload = {
        "status": verified["status"],
        "config_sha256": config_hash,
        "manifest_hash": verified["manifest"]["manifest_hash"],
        "manifest_sha256": verified["manifest_sha256"],
        "datasets": verified["manifest"]["datasets"],
        "coverage_gaps": verified["manifest"].get("coverage_gaps") or [],
        "sampled_from_eval_sets": False,
    }
    print(canonical_json(payload))
    print("\nSP3 discovery freeze complete. Run scripts/run_sp3_discovery.py next.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
