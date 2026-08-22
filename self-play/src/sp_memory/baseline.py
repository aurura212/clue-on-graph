"""Inventory of the original PoG baseline that already lives under self-play/."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .hashing import canonical_json, sha256_file
from .paths import PROTOCOL_VERSION, Workspace

BASELINE_FILES = [
    "main_freebase.py",
    "freebase_func.py",
    "utils.py",
    "prompt_list.py",
    "data_split.py",
    "pog_w.sh",
]


def collect_baseline_inventory(workspace: Workspace) -> Dict[str, Any]:
    files = []
    for relpath in BASELINE_FILES:
        path = workspace.self_play_root / relpath
        record = {
            "path": relpath,
            "exists": path.exists(),
        }
        if path.exists() and path.is_file():
            record["sha256"] = sha256_file(path)
            record["size"] = path.stat().st_size
        files.append(record)
    inventory = {
        "protocol_version": PROTOCOL_VERSION,
        "baseline_name": "original-pog-in-self-play",
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
        "entrypoints": {
            "main": "main_freebase.py",
            "cli_wrapper": "pog_w.sh",
            "kg_query": [
                "freebase_func.py:execurte_sparql",
                "freebase_func.py:entity_search",
                "freebase_func.py:relation_search_prune",
            ],
            "answer_parse": [
                "freebase_func.py:reasoning",
                "freebase_func.py:generate_answer",
            ],
            "dataset_loader": "utils.py:prepare_dataset",
            "prompts": "prompt_list.py",
        },
        "decision_points_for_future_memory": [
            {
                "name": "relation_selection",
                "site": "freebase_func.py:relation_search_prune",
                "planned_interface": "adapter + prompt/action score over visible relations",
            },
            {
                "name": "continue_stop",
                "site": "freebase_func.py:reasoning / main_freebase.py stop flag",
                "planned_interface": "adapter over Sufficient/Answer decision",
            },
            {
                "name": "backtrack_recovery",
                "site": "utils.py:if_finish_list / freebase_func.py:add_pre_info",
                "planned_interface": "adapter over reverse-entity recovery",
            },
        ],
        "notes": [
            "SP0 does not modify these baseline files.",
            "pog_w.sh may contain secrets; it is hashed in place and must not be copied into run artifacts.",
            "Self-Play memory is not implemented in these files.",
        ],
    }
    return inventory


def write_baseline_inventory(workspace: Workspace) -> Path:
    inventory = collect_baseline_inventory(workspace)
    path = workspace.artifacts_root / "protocol" / "pog_baseline_inventory_v1.json"
    workspace.safe_write_text(path, canonical_json(inventory) + "\n")
    return path


def baseline_file_hashes(workspace: Workspace) -> Dict[str, str]:
    hashes = {}
    for relpath in BASELINE_FILES:
        path = workspace.self_play_root / relpath
        if path.exists():
            hashes[relpath] = sha256_file(path)
    return hashes


def assert_baseline_unchanged(workspace: Workspace, expected: Dict[str, str]) -> List[str]:
    current = baseline_file_hashes(workspace)
    changed = []
    for relpath, digest in expected.items():
        if current.get(relpath) != digest:
            changed.append(relpath)
    return changed
