#!/usr/bin/env python3
"""Build or verify the read-only input registry. Writes only under self-play/."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from sp_memory.config import load_config
from sp_memory.hashing import canonical_json
from sp_memory.paths import Workspace
from sp_memory.registry import build_input_registry, write_input_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    workspace = Workspace.from_this_package()
    config_path = Path(args.config) if args.config else None
    config, config_hash, loaded_path = load_config(config_path, workspace)
    registry = build_input_registry(config, workspace)
    out = write_input_registry(registry, workspace)
    print(canonical_json({"path": str(out), "content_hash": registry["content_hash"], "config_hash": config_hash, "config": str(loaded_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
