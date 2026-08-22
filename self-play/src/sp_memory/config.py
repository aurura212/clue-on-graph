"""Load and hash the frozen SP0 protocol configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

from .hashing import canonical_hash, sha256_file
from .paths import PROTOCOL_VERSION, Workspace


REQUIRED_BUDGET_KEYS = (
    "max_depth",
    "max_steps",
    "max_kg_calls",
    "max_llm_calls",
    "max_critic_rounds",
    "max_frontier_size",
)


def default_config_path(workspace: Workspace | None = None) -> Path:
    ws = workspace or Workspace.from_this_package()
    return ws.configs_root / "sp0_protocol_v1.json"


def load_config(path: Path | None = None, workspace: Workspace | None = None) -> Tuple[Dict[str, Any], str, Path]:
    ws = workspace or Workspace.from_this_package()
    config_path = path or default_config_path(ws)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if raw.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError(
            f"config protocol_version {raw.get('protocol_version')!r} != {PROTOCOL_VERSION}"
        )
    budgets = raw.get("budgets") or {}
    missing = [key for key in REQUIRED_BUDGET_KEYS if key not in budgets]
    if missing:
        raise ValueError(f"config missing budget keys: {missing}")
    return raw, sha256_file(config_path), config_path


def config_canonical_hash(config: Dict[str, Any]) -> str:
    return canonical_hash(config)
