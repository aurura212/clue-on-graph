#!/usr/bin/env python3
"""Build frozen eval sets once, or verify them. Never resample after freeze."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from sp_memory.config import load_config
from sp_memory.hashing import canonical_json
from sp_memory.paths import Workspace
from sp_memory.sampling import build_eval_sets, ensure_eval_sets, manifest_path, verify_eval_sets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--mode", choices=["build", "verify", "ensure"], default="ensure")
    args = parser.parse_args()
    workspace = Workspace.from_this_package()
    config, _, _ = load_config(Path(args.config) if args.config else None, workspace)
    if args.mode == "build":
        if manifest_path(workspace).exists():
            print("frozen eval sets already exist; refusing to rebuild", file=sys.stderr)
            return 2
        result = {"status": "built", "manifest": build_eval_sets(config, workspace)}
    elif args.mode == "verify":
        result = verify_eval_sets(config, workspace)
    else:
        result = ensure_eval_sets(config, workspace)
    print(canonical_json({"status": result.get("status", "ok"), "manifest_path": str(manifest_path(workspace))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
