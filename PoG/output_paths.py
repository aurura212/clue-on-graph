"""Manage PoG run output directories under result/."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from reference_utils import get_output_file_tag
from jsonl_io import iter_jsonl_records

RESULT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "result")
RELATION_MEMORY_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "relation_memory")
DECOMPOSITION_MEMORY_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decomposition_memory")

_META_PRESERVE_KEYS = ("evaluation", "relation_memory_label_counts", "decomposition_memory_count")


def build_run_meta_from_args(
    args,
    config_tag: str,
    run_folder_name: str,
    planned_question_count: int,
) -> dict[str, Any]:
    openai_api_base = getattr(args, "openai_api_base", "") or os.environ.get("OPENAI_API_BASE", "")
    return {
        "config_tag": config_tag,
        "run_folder_name": run_folder_name,
        "planned_question_count": planned_question_count,
        "dataset": getattr(args, "dataset", ""),
        "run_mode": getattr(args, "run_mode", "test"),
        "split": getattr(args, "split", ""),
        "start": getattr(args, "start", 0),
        "limit": getattr(args, "limit", -1),
        "question": getattr(args, "question", ""),
        "max_length": getattr(args, "max_length", 4096),
        "temperature_exploration": getattr(args, "temperature_exploration", 0.3),
        "temperature_reasoning": getattr(args, "temperature_reasoning", 0.3),
        "depth": getattr(args, "depth", 4),
        "remove_unnecessary_rel": getattr(args, "remove_unnecessary_rel", True),
        "LLM_type": getattr(args, "LLM_type", ""),
        "openai_api_base": openai_api_base,
        "reference_mode": getattr(args, "reference_mode", "none"),
        "reference_base_path": getattr(args, "reference_base_path", ""),
        "reference_limit": getattr(args, "reference_limit", -1),
        "reference_top_k": getattr(args, "reference_top_k", 4),
        "reference_stages": getattr(args, "reference_stages", "relation"),
        "random_knowledge": getattr(args, "random_knowledge", 0),
        "relation_memory_mode": getattr(args, "relation_memory_mode", "none"),
        "relation_memory_stages": getattr(args, "relation_memory_stages", "relation"),
        "relation_memory_path": getattr(args, "relation_memory_path", ""),
        "relation_memory_output_path": getattr(args, "relation_memory_output_path", ""),
        "relation_memory_top_k": getattr(args, "relation_memory_top_k", 4),
        "train_memory_family": getattr(args, "train_memory_family", "relation_choice"),
        "decomposition_memory_mode": getattr(args, "decomposition_memory_mode", "none"),
        "decomposition_memory_path": getattr(args, "decomposition_memory_path", ""),
        "decomposition_memory_output_path": getattr(args, "decomposition_memory_output_path", ""),
        "decomposition_memory_top_k": getattr(args, "decomposition_memory_top_k", 4),
        "decomposition_memory_prompt_token_budget": getattr(args, "decomposition_memory_prompt_token_budget", 800),
        "memory_retrieval_strategy": getattr(args, "memory_retrieval_strategy", "hybrid"),
        "memory_state_weight": getattr(args, "memory_state_weight", 0.5),
        "memory_labels": getattr(args, "memory_labels", "positive,missed_positive,negative"),
        "memory_prompt_token_budget": getattr(args, "memory_prompt_token_budget", 600),
        "memory_candidate_relation_limit": getattr(args, "memory_candidate_relation_limit", 8),
        "relation_semantic_top_k": getattr(args, "relation_semantic_top_k", 20),
        "gold_frontier_limit": getattr(args, "gold_frontier_limit", 50),
        "write_missed_positive": getattr(args, "write_missed_positive", 1),
        "run_dir": getattr(args, "run_dir", ""),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "results_file": "results.jsonl",
        "trace_file": "pog_trace.jsonl",
    }


def write_run_meta(args, run_output: dict[str, Any], planned_question_count: int) -> None:
    meta_path = run_output["meta_path"]
    existing: dict[str, Any] = {}
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    meta = build_run_meta_from_args(
        args,
        config_tag=run_output["config_tag"],
        run_folder_name=run_output["run_folder_name"],
        planned_question_count=planned_question_count,
    )
    if "created_at" in existing:
        meta["created_at"] = existing["created_at"]
    for key in _META_PRESERVE_KEYS:
        if key in existing:
            meta[key] = existing[key]
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=4)

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


def build_relation_memory_filename(
    config_tag: str,
    question_count: int,
    timestamp: str | None = None,
) -> str:
    return f"{build_run_folder_name(config_tag, question_count, timestamp)}.jsonl"


def default_relation_memory_output_path(args, planned_question_count: int) -> str:
    os.makedirs(RELATION_MEMORY_ROOT, exist_ok=True)
    return os.path.join(
        RELATION_MEMORY_ROOT,
        build_relation_memory_filename(get_output_file_tag(args), planned_question_count),
    )


def default_decomposition_memory_output_path(args, planned_question_count: int) -> str:
    os.makedirs(DECOMPOSITION_MEMORY_ROOT, exist_ok=True)
    return os.path.join(
        DECOMPOSITION_MEMORY_ROOT,
        build_relation_memory_filename(get_output_file_tag(args), planned_question_count),
    )


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

    write_run_meta(args, run_output, planned_question_count)

    _RUN_OUTPUT = run_output
    print(f"PoG run output dir: {run_dir}")
    return run_output


def update_run_meta(updates: dict[str, Any]) -> None:
    run = get_current_run()
    if not run or not run.get("meta_path"):
        return
    meta_path = run["meta_path"]
    meta: dict[str, Any] = {}
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    meta.update(updates)
    meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=4)


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
