"""One-time frozen eval-set sampling. Later runs only verify hashes."""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_file, sha256_text
from .paths import PROTOCOL_VERSION, Workspace


def _load_json_records(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        loaded = json.loads(text)
        if not isinstance(loaded, list):
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"{path} is not a JSON list")
        records = loaded
    return records


def _webqsp_id(record: Mapping[str, Any]) -> str:
    value = record.get("QuestionId") or record.get("ID")
    if not value:
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, "WebQSP record missing QuestionId")
    return str(value)


def _cwq_id(record: Mapping[str, Any]) -> str:
    value = record.get("ID")
    if not value:
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, "CWQ record missing ID")
    return str(value)


def _webqsp_answers(record: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    ids: List[str] = []
    names: List[str] = []
    for parse in record.get("Parses") or []:
        for answer in parse.get("Answers") or []:
            arg = answer.get("AnswerArgument")
            name = answer.get("EntityName") or arg
            if arg:
                ids.append(str(arg))
            if name:
                names.append(str(name))
    return sorted(set(ids)), sorted(set(names))


def _normalize_webqsp(record: Mapping[str, Any], source_relpath: str, source_sha256: str) -> Dict[str, Any]:
    topic = record.get("topic_entity") or {}
    if not isinstance(topic, dict):
        topic = {}
    answer_ids, answer_names = _webqsp_answers(record)
    question = record.get("RawQuestion") or record.get("ProcessedQuestion") or ""
    item = {
        "task_id": _webqsp_id(record),
        "dataset": "webqsp",
        "question": question,
        "source_entities": sorted(topic.keys()),
        "source_entity_names": dict(topic),
        "answer_entity_ids": answer_ids,
        "normalized_answers": answer_names,
        "source_relpath": source_relpath,
        "source_file_sha256": source_sha256,
        "source_record_sha256": sha256_text(canonical_json(record)),
        "protocol_version": PROTOCOL_VERSION,
        "contains_oracle_fields": True,
    }
    return item


def _normalize_cwq(record: Mapping[str, Any], source_relpath: str, source_sha256: str) -> Dict[str, Any]:
    topic = record.get("topic_entity") or record.get("qid_topic_entity") or {}
    if not isinstance(topic, dict):
        topic = {}
    answer = record.get("answer")
    names = [str(answer)] if answer not in (None, "") else []
    item = {
        "task_id": _cwq_id(record),
        "dataset": "cwq",
        "question": record.get("question") or "",
        "source_entities": sorted(topic.keys()),
        "source_entity_names": dict(topic),
        "answer_entity_ids": [],
        "normalized_answers": names,
        "source_relpath": source_relpath,
        "source_file_sha256": source_sha256,
        "source_record_sha256": sha256_text(canonical_json(record)),
        "protocol_version": PROTOCOL_VERSION,
        "contains_oracle_fields": True,
    }
    return item


def _index_by_id(records: Sequence[Mapping[str, Any]], id_fn) -> Dict[str, Mapping[str, Any]]:
    indexed = {}
    duplicates = []
    for record in records:
        task_id = id_fn(record)
        if task_id in indexed:
            duplicates.append(task_id)
        indexed[task_id] = record
    if duplicates:
        raise ProtocolError(
            ViolationCode.SAMPLING_ERROR,
            "duplicate task ids in source",
            {"ids": sorted(set(duplicates))},
        )
    return indexed


def _shuffle_ids(ids: Sequence[str], seed: int) -> List[str]:
    ordered = sorted(ids)
    rng = random.Random(seed)
    shuffled = list(ordered)
    rng.shuffle(shuffled)
    return shuffled


def _slice_ids(shuffled: Sequence[str], start: int, end: int, name: str) -> List[str]:
    if end > len(shuffled):
        raise ProtocolError(
            ViolationCode.SAMPLING_ERROR,
            f"{name} slice [{start}, {end}) exceeds source size {len(shuffled)}",
        )
    chosen = list(shuffled[start:end])
    if len(chosen) != (end - start):
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"{name} expected {end - start} ids")
    if len(set(chosen)) != len(chosen):
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"{name} has duplicate ids")
    return chosen


def eval_set_paths(workspace: Workspace) -> Dict[str, Path]:
    root = workspace.artifacts_root / "datasets"
    return {
        "webqsp_smoke_20": root / "webqsp_smoke_20.jsonl",
        "webqsp_model_compare_150": root / "webqsp_model_compare_150.jsonl",
        "cwq_model_compare_50": root / "cwq_model_compare_50.jsonl",
    }


def manifest_path(workspace: Workspace) -> Path:
    return workspace.artifacts_root / "datasets" / "eval_set_manifest_v1.json"


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]], workspace: Workspace) -> str:
    lines = [canonical_json(row) for row in rows]
    text = "\n".join(lines) + ("\n" if lines else "")
    workspace.safe_write_text(path, text)
    return sha256_file(path)


def build_eval_sets(config: Mapping[str, Any], workspace: Workspace) -> Dict[str, Any]:
    spec = config.get("eval_sampling") or {}
    seed = spec.get("seed")
    if not isinstance(seed, int):
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, "eval_sampling.seed must be int")

    webqsp_rel = spec["webqsp_source"]
    cwq_rel = spec["cwq_source"]
    webqsp_path = workspace.assert_readable_input(webqsp_rel, root=workspace.data_root)
    cwq_path = workspace.assert_readable_input(cwq_rel, root=workspace.data_root)
    webqsp_sha = sha256_file(webqsp_path)
    cwq_sha = sha256_file(cwq_path)
    webqsp_records = _index_by_id(_load_json_records(webqsp_path), _webqsp_id)
    cwq_records = _index_by_id(_load_json_records(cwq_path), _cwq_id)

    webqsp_ids = _shuffle_ids(list(webqsp_records), seed)
    cwq_ids = _shuffle_ids(list(cwq_records), seed)

    plans = {
        "webqsp_smoke_20": {
            "dataset": "webqsp",
            "usage": "smoke",
            "n": 20,
            "ids": _slice_ids(webqsp_ids, 0, 20, "webqsp_smoke_20"),
            "source_relpath": f"data/{webqsp_rel}",
            "source_sha256": webqsp_sha,
        },
        "webqsp_model_compare_150": {
            "dataset": "webqsp",
            "usage": "model_compare",
            "n": 150,
            "ids": _slice_ids(webqsp_ids, 20, 170, "webqsp_model_compare_150"),
            "source_relpath": f"data/{webqsp_rel}",
            "source_sha256": webqsp_sha,
        },
        "cwq_model_compare_50": {
            "dataset": "cwq",
            "usage": "model_compare",
            "n": 50,
            "ids": _slice_ids(cwq_ids, 0, 50, "cwq_model_compare_50"),
            "source_relpath": f"data/{cwq_rel}",
            "source_sha256": cwq_sha,
        },
    }

    smoke_set = set(plans["webqsp_smoke_20"]["ids"])
    compare_set = set(plans["webqsp_model_compare_150"]["ids"])
    overlap = sorted(smoke_set & compare_set)
    if overlap:
        raise ProtocolError(
            ViolationCode.SAMPLING_ERROR,
            "WebQSP smoke and model-compare sets overlap",
            {"overlap": overlap},
        )

    paths = eval_set_paths(workspace)
    datasets_meta = []
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for name, plan in plans.items():
        if plan["dataset"] == "webqsp":
            rows = [
                {
                    **_normalize_webqsp(
                        webqsp_records[task_id],
                        plan["source_relpath"],
                        plan["source_sha256"],
                    ),
                    "usage": plan["usage"],
                    "eval_set": name,
                    "sample_seed": seed,
                }
                for task_id in plan["ids"]
            ]
        else:
            rows = [
                {
                    **_normalize_cwq(
                        cwq_records[task_id],
                        plan["source_relpath"],
                        plan["source_sha256"],
                    ),
                    "usage": plan["usage"],
                    "eval_set": name,
                    "sample_seed": seed,
                }
                for task_id in plan["ids"]
            ]
        if len(rows) != plan["n"]:
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"{name} size {len(rows)} != {plan['n']}")
        question_hashes = [row["source_record_sha256"] for row in rows]
        if len(set(question_hashes)) != len(question_hashes):
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"{name} has record hash collisions")
        file_hash = _write_jsonl(paths[name], rows, workspace)
        datasets_meta.append(
            {
                "name": name,
                "dataset": plan["dataset"],
                "usage": plan["usage"],
                "n": plan["n"],
                "task_ids": plan["ids"],
                "path": str(paths[name].relative_to(workspace.self_play_root)),
                "file_sha256": file_hash,
                "source_relpath": plan["source_relpath"],
                "source_sha256": plan["source_sha256"],
            }
        )

    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "method": spec.get("method", "shuffle_then_slice_without_replacement"),
        "seed": seed,
        "generated_at": generated_at,
        "frozen": True,
        "datasets": datasets_meta,
        "notes": [
            "Seed is used only for this one-time freeze.",
            "Later runs must verify these files and must not resample.",
            "WebQSP smoke and model-compare slices are disjoint.",
            "WebQSP and CWQ metrics must be reported separately.",
        ],
    }
    manifest["manifest_hash"] = canonical_hash(
        {
            "protocol_version": manifest["protocol_version"],
            "method": manifest["method"],
            "seed": manifest["seed"],
            "datasets": [
                {
                    "name": item["name"],
                    "n": item["n"],
                    "task_ids": item["task_ids"],
                    "file_sha256": item["file_sha256"],
                    "source_sha256": item["source_sha256"],
                }
                for item in datasets_meta
            ],
        }
    )
    workspace.safe_write_text(manifest_path(workspace), canonical_json(manifest) + "\n")
    return manifest


def verify_eval_sets(
    config: Mapping[str, Any],
    workspace: Workspace,
    *,
    allow_missing: bool = False,
) -> Dict[str, Any]:
    path = manifest_path(workspace)
    if not path.exists():
        if allow_missing:
            return {"status": "missing", "path": str(path)}
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"frozen eval-set manifest missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    spec = config.get("eval_sampling") or {}
    if manifest.get("seed") != spec.get("seed"):
        raise ProtocolError(
            ViolationCode.SAMPLING_ERROR,
            "frozen eval-set seed does not match config",
            {"manifest_seed": manifest.get("seed"), "config_seed": spec.get("seed")},
        )
    paths = eval_set_paths(workspace)
    expected_n = {
        "webqsp_smoke_20": 20,
        "webqsp_model_compare_150": 150,
        "cwq_model_compare_50": 50,
    }
    for item in manifest.get("datasets") or []:
        name = item["name"]
        file_path = paths[name]
        if not file_path.exists():
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"frozen dataset missing: {file_path}")
        actual_hash = sha256_file(file_path)
        if actual_hash != item["file_sha256"]:
            raise ProtocolError(
                ViolationCode.SAMPLING_ERROR,
                f"frozen dataset hash mismatch for {name}",
                {"expected": item["file_sha256"], "actual": actual_hash},
            )
        rows = _load_json_records(file_path)
        if len(rows) != expected_n[name] or len(rows) != item["n"]:
            raise ProtocolError(
                ViolationCode.SAMPLING_ERROR,
                f"{name} has {len(rows)} rows, expected {expected_n[name]}",
            )
        ids = [row["task_id"] for row in rows]
        if ids != item["task_ids"]:
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"{name} task id order mismatch")
        if len(set(ids)) != len(ids):
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"{name} contains duplicate task ids")
        source_path = workspace.clue_on_graph_root / item["source_relpath"]
        if not source_path.exists():
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"source file missing: {source_path}")
        source_hash = sha256_file(source_path)
        if source_hash != item["source_sha256"]:
            raise ProtocolError(
                ViolationCode.SAMPLING_ERROR,
                f"source file hash changed for {name}",
                {"expected": item["source_sha256"], "actual": source_hash},
            )
    return {"status": "ok", "manifest": manifest, "manifest_sha256": sha256_file(path)}


def ensure_eval_sets(config: Mapping[str, Any], workspace: Workspace) -> Dict[str, Any]:
    """Build once. If frozen files exist, only verify. Never overwrite."""
    path = manifest_path(workspace)
    if path.exists():
        return verify_eval_sets(config, workspace)
    return {"status": "built", "manifest": build_eval_sets(config, workspace)}
