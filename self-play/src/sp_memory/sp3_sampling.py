"""Freeze independent SP3 D0/D1/H discovery data. Never samples frozen eval sets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_file
from .paths import PROTOCOL_VERSION, Workspace
from .question_normalization import normalized_question_hash
from .sampling import (
    _load_json_records,
    _normalize_webqsp,
    _shuffle_ids,
    _webqsp_answers,
    _webqsp_id,
    eval_set_paths,
)
from .sp2b_checks import load_registry

GENERATOR_VERSION = "sp3-discovery-v1"
DISCOVERY_SEED = 20260822


def discovery_paths(workspace: Workspace) -> Dict[str, Path]:
    root = workspace.artifacts_root / "datasets"
    return {
        "d0": root / "sp3_discovery_d0_12.jsonl",
        "d1": root / "sp3_discovery_d1_60.jsonl",
        "h": root / "sp3_discovery_holdout_20.jsonl",
        "manifest": root / "sp3_discovery_manifest_v1.json",
        "registry": workspace.artifacts_root / "registries" / "sp3_discovery_registry_v1.json",
        "exclusion": workspace.artifacts_root / "registries" / "sp3_exclusion_registry_v1.json",
        "exposure": workspace.artifacts_root / "registries" / "sp3_exposure_registry_v1.json",
    }


def _looks_literal(argument: str, answer_type: str) -> bool:
    if str(answer_type).lower() == "value":
        return True
    text = str(argument or "")
    return bool(text[:4].isdigit() and ("-" in text or text.isdigit()))


def classify_webqsp(record: Mapping[str, Any]) -> Dict[str, Any]:
    chains = []
    answers = []
    sparql = ""
    for parse in record.get("Parses") or []:
        chain = parse.get("InferentialChain") or []
        if chain:
            chains.append([str(item) for item in chain])
        if not sparql:
            sparql = str(parse.get("Sparql") or "")
        answers.extend(parse.get("Answers") or [])
    hop = min((len(item) for item in chains), default=0)
    empty = not answers
    literal = any(_looks_literal(item.get("AnswerArgument"), item.get("AnswerType") or "") for item in answers)
    if empty:
        question_type = "empty_result"
        coverage = "empty_result_or_early_stop"
    elif literal:
        question_type = "literal"
        coverage = "literal_or_answer_submission"
    elif hop >= 2:
        question_type = "two_hop"
        coverage = "two_hop_or_consecutive_state_update"
    else:
        question_type = "one_hop"
        coverage = "one_hop_entity_relation"
    return {
        "hop_count": hop,
        "question_type": question_type,
        "coverage": coverage,
        "witness_paths": chains[:1],
        "logical_query": sparql,
        "allow_multihop": hop >= 2,
        "answer_type": "empty" if empty else ("literal" if literal else "entity"),
    }


def load_banned(workspace: Workspace) -> Dict[str, Set[str]]:
    banned_ids: Set[str] = set()
    banned_q: Set[str] = set()
    banned_topics: Set[str] = set()
    exclusion = json.loads(
        (workspace.artifacts_root / "registries" / "benchmark_exclusion_registry_v1.json").read_text(encoding="utf-8")
    )
    for item in exclusion.get("records") or []:
        if item.get("task_id"):
            banned_ids.add(str(item["task_id"]))
        if item.get("normalized_question_hash"):
            banned_q.add(str(item["normalized_question_hash"]))
        banned_topics.update(str(mid) for mid in (item.get("topic_entities") or []) if mid)
    for name, path in eval_set_paths(workspace).items():
        for row in _load_json_records(path):
            banned_ids.add(str(row["task_id"]))
            banned_q.add(normalized_question_hash(str(row.get("question") or "")))
            banned_topics.update(str(mid) for mid in (row.get("source_entities") or []) if mid)
    for rel in (
        "artifacts/registries/sp2b_b0_manual_tasks_v1.json",
        "artifacts/registries/sp2b_b1_development_tasks_v1.json",
    ):
        try:
            registry = load_registry(workspace, rel)
        except FileNotFoundError:
            continue
        for task in registry.get("tasks") or []:
            banned_q.add(normalized_question_hash(str(task.get("question") or "")))
            banned_topics.update(str(mid) for mid in (task.get("source_entities") or []) if mid)
            banned_topics.update(str(mid) for mid in (task.get("topic_entity") or {}) if mid)
    return {"task_ids": banned_ids, "questions": banned_q, "topics": banned_topics}


def _eligible_rows(
    records: Mapping[str, Mapping[str, Any]],
    *,
    source_relpath: str,
    source_sha256: str,
    banned: Mapping[str, Set[str]],
) -> List[Dict[str, Any]]:
    rows = []
    for task_id, record in records.items():
        if task_id in banned["task_ids"]:
            continue
        question = str(record.get("RawQuestion") or record.get("ProcessedQuestion") or "")
        qh = normalized_question_hash(question)
        if qh in banned["questions"]:
            continue
        topic = record.get("topic_entity") or {}
        if not isinstance(topic, dict) or not topic:
            continue
        if set(topic) & banned["topics"]:
            continue
        meta = classify_webqsp(record)
        normalized = _normalize_webqsp(record, source_relpath, source_sha256)
        answer_ids, answer_names = _webqsp_answers(record)
        row = {
            **normalized,
            "topic_entity": dict(topic),
            "logical_query": meta["logical_query"],
            "witness_paths": meta["witness_paths"],
            "hop_count": meta["hop_count"],
            "question_type": meta["question_type"],
            "coverage": meta["coverage"],
            "allow_multihop": meta["allow_multihop"],
            "answer_type": meta["answer_type"],
            "normalized_question_hash": qh,
            "task_generator_version": GENERATOR_VERSION,
            "sample_seed": DISCOVERY_SEED,
            "verifier_rule": "empty_or_abstain" if meta["answer_type"] == "empty" else "exact_id_or_name",
        }
        row["answer_entity_ids"] = answer_ids
        row["normalized_answers"] = answer_names
        rows.append(row)
    return rows


def _pick_stratified(pool: List[Dict[str, Any]], spec: Sequence[Tuple[str, int]], used: Set[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    chosen: List[Dict[str, Any]] = []
    gaps: List[str] = []
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for row in pool:
        if row["task_id"] in used:
            continue
        by_type.setdefault(row["question_type"], []).append(row)
    for question_type, count in spec:
        available = [row for row in by_type.get(question_type, []) if row["task_id"] not in used]
        take = available[:count]
        if len(take) < count:
            gaps.append(f"{question_type}: wanted {count} got {len(take)}")
        for row in take:
            used.add(row["task_id"])
            chosen.append(row)
    if len(chosen) < sum(item[1] for item in spec):
        needed = sum(item[1] for item in spec) - len(chosen)
        filler = [row for row in pool if row["task_id"] not in used][:needed]
        if len(filler) < needed:
            gaps.append(f"filler: wanted {needed} got {len(filler)}")
        for row in filler:
            used.add(row["task_id"])
            chosen.append(row)
    return chosen, gaps


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]], workspace: Workspace) -> str:
    text = "\n".join(canonical_json(row) for row in rows) + ("\n" if rows else "")
    workspace.safe_write_text(path, text)
    return sha256_file(path)


def _oracle_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "answer_entity_ids": list(row.get("answer_entity_ids") or []),
        "normalized_answers": list(row.get("normalized_answers") or []),
        "logical_query": str(row.get("logical_query") or ""),
        "witness_paths": list(row.get("witness_paths") or []),
        "verifier_rule": str(row.get("verifier_rule") or "exact_id_or_name"),
        "oracle_version": GENERATOR_VERSION,
        "task_validity": "valid" if row.get("source_entities") else "invalid_task",
    }


def _public_row(row: Mapping[str, Any], layer: str) -> Dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "dataset": row.get("dataset") or "webqsp",
        "question": row["question"],
        "source_entities": list(row.get("source_entities") or []),
        "source_entity_names": dict(row.get("source_entity_names") or {}),
        "topic_entity": dict(row.get("topic_entity") or row.get("source_entity_names") or {}),
        "discovery_layer": layer,
        "usage": "discovery" if layer != "H" else "holdout_observation",
        "question_type": row.get("question_type"),
        "coverage": row.get("coverage"),
        "hop_count": row.get("hop_count"),
        "allow_multihop": row.get("allow_multihop"),
        "answer_type": row.get("answer_type"),
        "max_depth": 4,
        "task_generator_version": GENERATOR_VERSION,
        "sample_seed": DISCOVERY_SEED,
        "source_relpath": row.get("source_relpath"),
        "source_file_sha256": row.get("source_file_sha256"),
        "source_record_sha256": row.get("source_record_sha256"),
        "normalized_question_hash": row.get("normalized_question_hash"),
        "protocol_version": PROTOCOL_VERSION,
        "contains_oracle_fields": True,
        "answer_entity_ids": list(row.get("answer_entity_ids") or []),
        "normalized_answers": list(row.get("normalized_answers") or []),
        "logical_query": str(row.get("logical_query") or ""),
        "witness_paths": list(row.get("witness_paths") or []),
        "verifier_rule": row.get("verifier_rule"),
    }


def freeze_discovery(workspace: Workspace, config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    paths = discovery_paths(workspace)
    if paths["manifest"].exists():
        return verify_discovery(workspace, config)
    source_rel = "WebQSP.json"
    source_path = workspace.assert_readable_input(source_rel, root=workspace.data_root)
    source_sha = sha256_file(source_path)
    raw = json.loads(source_path.read_text(encoding="utf-8"))
    indexed = {}
    for record in raw:
        indexed[_webqsp_id(record)] = record
    banned = load_banned(workspace)
    eligible = _eligible_rows(
        indexed,
        source_relpath=f"data/{source_rel}",
        source_sha256=source_sha,
        banned=banned,
    )
    order = _shuffle_ids([row["task_id"] for row in eligible], DISCOVERY_SEED)
    by_id = {row["task_id"]: row for row in eligible}
    pool = [by_id[task_id] for task_id in order]
    used: Set[str] = set()
    d0_spec = [
        ("one_hop", 2),
        ("two_hop", 4),
        ("literal", 2),
        ("empty_result", 2),
        ("one_hop", 2),
    ]
    d0_rows, d0_gaps = _pick_stratified(pool, d0_spec, used)
    if len(d0_rows) != 12:
        raise ProtocolError(
            ViolationCode.SAMPLING_ERROR,
            f"SP3-D0 expected 12 rows, got {len(d0_rows)}",
            {"gaps": d0_gaps},
        )
    d1_rows = [row for row in pool if row["task_id"] not in used][:60]
    used.update(row["task_id"] for row in d1_rows)
    h_rows = [row for row in pool if row["task_id"] not in used][:20]
    if len(d1_rows) != 60 or len(h_rows) != 20:
        raise ProtocolError(
            ViolationCode.SAMPLING_ERROR,
            "SP3 discovery pool too small after exclusion",
            {"eligible": len(pool), "d1": len(d1_rows), "h": len(h_rows), "d0_gaps": d0_gaps},
        )
    layers = {"D0": d0_rows, "D1": d1_rows, "H": h_rows}
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    file_meta = []
    oracle: Dict[str, Any] = {}
    public_tasks: Dict[str, List[Dict[str, Any]]] = {}
    for layer, rows, key in (("D0", d0_rows, "d0"), ("D1", d1_rows, "d1"), ("H", h_rows, "h")):
        public = [_public_row(row, layer) for row in rows]
        public_tasks[layer] = public
        for row, pub in zip(rows, public):
            oracle[row["task_id"]] = _oracle_from_row(row)
        digest = _write_jsonl(paths[key], public, workspace)
        file_meta.append(
            {
                "layer": layer,
                "n": len(public),
                "path": str(paths[key].relative_to(workspace.self_play_root)),
                "file_sha256": digest,
                "task_ids": [row["task_id"] for row in public],
                "question_types": sorted({str(row.get("question_type")) for row in public}),
            }
        )
    exclusion_records = []
    for item in (json.loads((workspace.artifacts_root / "registries" / "benchmark_exclusion_registry_v1.json").read_text())["records"]):
        exclusion_records.append(item)
    exclusion = {
        "protocol_version": PROTOCOL_VERSION,
        "registry_version": "sp3-exclusion-v1",
        "generated_at": generated_at,
        "source_eval_sets": ["webqsp_smoke_20", "webqsp_model_compare_150", "cwq_model_compare_50"],
        "also_excludes": ["sp2b_b0_manual_tasks", "sp2b_b1_development_tasks", "eval_topic_entities"],
        "banned_task_id_count": len(banned["task_ids"]),
        "banned_question_count": len(banned["questions"]),
        "banned_topic_count": len(banned["topics"]),
        "records": exclusion_records,
    }
    exclusion["content_hash"] = canonical_hash(
        {"banned_task_ids": sorted(banned["task_ids"]), "banned_questions": sorted(banned["questions"])}
    )
    workspace.safe_write_text(paths["exclusion"], canonical_json(exclusion) + "\n")
    registry = {
        "protocol_version": PROTOCOL_VERSION,
        "registry_version": GENERATOR_VERSION,
        "plan_version": "SP3-PLAN 1.0",
        "sampled_from_eval_sets": False,
        "seed": DISCOVERY_SEED,
        "source_relpath": f"data/{source_rel}",
        "source_sha256": source_sha,
        "oracle": oracle,
        "tasks": {
            "D0": [{"task_id": row["task_id"], "question_type": row.get("question_type")} for row in public_tasks["D0"]],
            "D1": [{"task_id": row["task_id"], "question_type": row.get("question_type")} for row in public_tasks["D1"]],
            "H": [{"task_id": row["task_id"], "question_type": row.get("question_type")} for row in public_tasks["H"]],
        },
        "notes": [
            "Oracle lives in this registry and in JSONL oracle fields.",
            "Actor loaders must use public_task_view and never copy oracle into prompts.",
            "Holdout is observation-only and must not generate promoted memory.",
        ],
    }
    workspace.safe_write_text(paths["registry"], canonical_json(registry) + "\n")
    exposure = {
        "protocol_version": PROTOCOL_VERSION,
        "stage": "SP3",
        "purpose": "discovery_task_exposure",
        "generated_at": generated_at,
        "layers": {
            layer: {
                "task_ids": [row["task_id"] for row in public_tasks[layer]],
                "topic_entities": sorted(
                    {mid for row in public_tasks[layer] for mid in (row.get("source_entities") or [])}
                ),
            }
            for layer in ("D0", "D1", "H")
        },
        "note": "Discovery exposure only. Do not use WebQSP 20/150 or CWQ 50. Do not inject candidates.",
    }
    workspace.safe_write_text(paths["exposure"], canonical_json(exposure) + "\n")
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": DISCOVERY_SEED,
        "generated_at": generated_at,
        "frozen": True,
        "sampled_from_eval_sets": False,
        "source_relpath": f"data/{source_rel}",
        "source_sha256": source_sha,
        "eligible_after_exclusion": len(pool),
        "coverage_gaps": d0_gaps,
        "datasets": file_meta,
        "registry_path": str(paths["registry"].relative_to(workspace.self_play_root)),
        "registry_sha256": sha256_file(paths["registry"]),
        "exclusion_path": str(paths["exclusion"].relative_to(workspace.self_play_root)),
        "exclusion_sha256": sha256_file(paths["exclusion"]),
        "exposure_path": str(paths["exposure"].relative_to(workspace.self_play_root)),
        "exposure_sha256": sha256_file(paths["exposure"]),
    }
    manifest["manifest_hash"] = canonical_hash(
        {
            "seed": manifest["seed"],
            "datasets": [{"layer": item["layer"], "n": item["n"], "task_ids": item["task_ids"], "file_sha256": item["file_sha256"]} for item in file_meta],
            "source_sha256": source_sha,
        }
    )
    workspace.safe_write_text(paths["manifest"], canonical_json(manifest) + "\n")
    return {"status": "built", "manifest": manifest}


def verify_discovery(workspace: Workspace, config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    paths = discovery_paths(workspace)
    if not paths["manifest"].exists():
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, "SP3 discovery manifest missing")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    expected = {
        "D0": (12, paths["d0"]),
        "D1": (60, paths["d1"]),
        "H": (20, paths["h"]),
    }
    banned = load_banned(workspace)
    overlaps = []
    for item in manifest.get("datasets") or []:
        layer = item["layer"]
        n, path = expected[layer]
        actual = sha256_file(path)
        if actual != item["file_sha256"]:
            raise ProtocolError(
                ViolationCode.SAMPLING_ERROR,
                f"SP3 {layer} hash mismatch",
                {"expected": item["file_sha256"], "actual": actual},
            )
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(rows) != n:
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"SP3 {layer} has {len(rows)} rows")
        ids = [row["task_id"] for row in rows]
        if ids != item["task_ids"]:
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"SP3 {layer} task id order mismatch")
        for row in rows:
            if row["task_id"] in banned["task_ids"]:
                overlaps.append({"type": "task_id", "layer": layer, "task_id": row["task_id"]})
            qh = normalized_question_hash(str(row["question"]))
            if qh in banned["questions"]:
                overlaps.append({"type": "question", "layer": layer, "task_id": row["task_id"]})
            topics = set(row.get("source_entities") or []) | set((row.get("topic_entity") or {}).keys())
            hit = sorted(topics & banned["topics"])
            if hit:
                overlaps.append({"type": "topic", "layer": layer, "task_id": row["task_id"], "mids": hit})
    if overlaps:
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, "SP3 discovery overlaps exclusion set", {"overlaps": overlaps})
    config_hash = None if config is None else (config.get("expected_discovery_manifest_hash"))
    if config_hash and config_hash != manifest.get("manifest_hash"):
        raise ProtocolError(
            ViolationCode.SAMPLING_ERROR,
            "SP3 discovery manifest_hash does not match config",
            {"expected": config_hash, "actual": manifest.get("manifest_hash")},
        )
    return {"status": "ok", "manifest": manifest, "manifest_sha256": sha256_file(paths["manifest"])}


def load_layer_tasks(workspace: Workspace, layer: str) -> List[Dict[str, Any]]:
    key = {"D0": "d0", "D1": "d1", "H": "h"}[layer]
    path = discovery_paths(workspace)[key]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
