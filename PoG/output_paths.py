"""Manage PoG run output directories under result/."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from reference_utils import get_output_file_tag
from jsonl_io import iter_jsonl_records

RESULT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "result")

_RUN_OUTPUT: dict[str, Any] | None = None


def get_current_run() -> dict[str, Any] | None:
    return _RUN_OUTPUT


def _resolve_run_dir(run_dir: str) -> str:
    run_dir = run_dir.strip()
    if os.path.isabs(run_dir):
        return run_dir
    if os.path.isdir(run_dir):
        return os.path.abspath(run_dir)
    under_result = os.path.join(RESULT_ROOT, run_dir)
    if os.path.isdir(under_result):
        return os.path.abspath(under_result)
    return os.path.abspath(under_result)


def build_run_folder_name(config_tag: str, question_count: int, timestamp: str | None = None) -> str:
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{config_tag}_n{question_count}_{ts}"


def init_run_output(
    args,
    planned_question_count: int,
    resume_dir: str | None = None,
) -> dict[str, Any]:
    """Create or resume a run directory under result/."""
    global _RUN_OUTPUT
    if _RUN_OUTPUT is not None:
        return _RUN_OUTPUT

    config_tag = get_output_file_tag(args)
    os.makedirs(RESULT_ROOT, exist_ok=True)

    if resume_dir:
        run_dir = _resolve_run_dir(resume_dir)
        if not os.path.isdir(run_dir):
            raise FileNotFoundError(f"Run directory not found: {run_dir}")
        run_folder_name = os.path.basename(run_dir.rstrip("/"))
    else:
        run_folder_name = build_run_folder_name(config_tag, planned_question_count)
        run_dir = os.path.join(RESULT_ROOT, run_folder_name)
        os.makedirs(run_dir, exist_ok=True)

    run_output = {
        "run_dir": run_dir,
        "run_folder_name": run_folder_name,
        "config_tag": config_tag,
        "planned_question_count": planned_question_count,
        "results_path": os.path.join(run_dir, "results.jsonl"),
        "trace_path": os.path.join(run_dir, "pog_trace.jsonl"),
        "meta_path": os.path.join(run_dir, "run_meta.json"),
    }

    meta = {
        "config_tag": config_tag,
        "run_folder_name": run_folder_name,
        "planned_question_count": planned_question_count,
        "dataset": getattr(args, "dataset", ""),
        "LLM_type": getattr(args, "LLM_type", ""),
        "start": getattr(args, "start", 0),
        "limit": getattr(args, "limit", -1),
        "question": getattr(args, "question", ""),
        "depth": getattr(args, "depth", 4),
        "run_mode": getattr(args, "run_mode", "test"),
        "split": getattr(args, "split", ""),
        "reference_mode": getattr(args, "reference_mode", "none"),
        "relation_memory_mode": getattr(args, "relation_memory_mode", "none"),
        "relation_memory_stages": getattr(args, "relation_memory_stages", "relation"),
        "relation_memory_path": getattr(args, "relation_memory_path", ""),
        "relation_memory_output_path": getattr(args, "relation_memory_output_path", ""),
        "memory_retrieval_strategy": getattr(args, "memory_retrieval_strategy", "hybrid"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "results_file": "results.jsonl",
        "trace_file": "pog_trace.jsonl",
    }
    if not os.path.exists(run_output["meta_path"]):
        with open(run_output["meta_path"], "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=4)

    _RUN_OUTPUT = run_output
    print(f"PoG run output dir: {run_dir}")
    return run_output


def load_processed_questions(question_string: str) -> list[str]:
    run = get_current_run()
    if not run or not os.path.exists(run["results_path"]):
        return []
    processed = []
    for data in iter_jsonl_records(run["results_path"]):
        processed.append(data[question_string])
    return processed


def load_run_records(question_string: str) -> list[dict[str, Any]]:
    """Load results and merge pog_trace from separate trace file."""
    run = get_current_run()
    if run:
        results_path = run["results_path"]
        trace_path = run["trace_path"]
    else:
        raise ValueError("No active run output configured")

    records: dict[str, dict[str, Any]] = {}
    if os.path.exists(results_path):
        for data in iter_jsonl_records(results_path):
            records[data[question_string]] = data

    if os.path.exists(trace_path):
        for data in iter_jsonl_records(trace_path):
            q = data.get(question_string) or data.get("question")
            if q in records:
                records[q]["pog_trace"] = data.get("pog_trace")

    return list(records.values())
