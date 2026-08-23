"""Audit SP3 candidates for schema, source, privacy, leakage, and replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .candidate_experience import audit_candidate, candidate_body_for_audit
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, sha256_file
from .paths import PROTOCOL_VERSION, Workspace
from .schemas import CandidateExperience, FORBIDDEN_VISIBLE_FIELDS
from .sp4_io import read_jsonl, write_jsonl
from .state_signature import MID_RE, experience_text_is_abstract
from .visibility import OracleSecrets, find_sensitive_keys

SP3_CANDIDATE_PATH = "artifacts/candidates/sp3_candidate_experience_v1.jsonl"
SP3_D1_PATH = "artifacts/datasets/sp3_discovery_d1_60.jsonl"


def load_sp3_candidates(workspace: Workspace) -> List[Dict[str, Any]]:
    path = workspace.self_play_root / SP3_CANDIDATE_PATH
    return read_jsonl(path)


def _task_secrets(workspace: Workspace) -> Dict[str, OracleSecrets]:
    path = workspace.self_play_root / SP3_D1_PATH
    out: Dict[str, OracleSecrets] = {}
    if not path.exists():
        return out
    for row in read_jsonl(path):
        names = dict(row.get("source_entity_names") or row.get("topic_entity") or {})
        answers = list(row.get("normalized_answers") or [])
        ids = list(row.get("answer_entity_ids") or [])
        witness = []
        for path_item in row.get("witness_paths") or []:
            if isinstance(path_item, list):
                witness.append(" -> ".join(str(x) for x in path_item))
                witness.extend(str(x) for x in path_item)
        out[str(row["task_id"])] = OracleSecrets(
            answer_entity_ids=ids,
            normalized_answers=answers + list(names.values()),
            witness_tokens=witness,
            logical_query=str(row.get("logical_query") or ""),
            future_neighbors=[],
        )
    return out


def audit_one(item: Mapping[str, Any], secrets: Optional[OracleSecrets] = None) -> Dict[str, Any]:
    reasons: List[str] = []
    ok_schema = True
    try:
        CandidateExperience.from_dict(item)
    except ProtocolError as exc:
        ok_schema = False
        reasons.append(f"schema:{exc.code.value}:{exc.message}")
    try:
        audit_candidate(item, secrets=secrets)
    except ProtocolError as exc:
        reasons.append(f"leakage:{exc.code.value}:{exc.message}")
    body = candidate_body_for_audit(item)
    keys = find_sensitive_keys(body)
    if keys:
        reasons.append(f"privacy_keys:{keys}")
    reason = str((item.get("recommendation") or {}).get("reason") or "")
    if MID_RE.search(reason):
        reasons.append("privacy:entity_id_in_reason")
    abstract_hits = experience_text_is_abstract(reason, extra_secrets=secrets.sensitive_values() if secrets else [])
    if abstract_hits:
        reasons.append(f"privacy:abstract:{abstract_hits}")
    blob = json.dumps(body, ensure_ascii=False)
    for key in FORBIDDEN_VISIBLE_FIELDS:
        if f'"{key}"' in blob:
            reasons.append(f"oracle_field:{key}")
    replay = bool((item.get("evidence") or {}).get("verified_replay"))
    if not replay:
        reasons.append("replay:not_verified")
    source = str(item.get("discovery_method") or "")
    if source not in {"o0_critic", "oracle_guided_offline_teacher", "random_critic"}:
        reasons.append(f"source:{source}")
    privacy_ok = not any(item.startswith("privacy") or item.startswith("leakage") or item.startswith("oracle") for item in reasons)
    return {
        "experience_id": item.get("experience_id"),
        "source_task_ids": list(item.get("source_task_ids") or []),
        "discovery_method": source,
        "decision_stage": (item.get("trigger") or {}).get("decision_stage"),
        "failure_class": (item.get("trigger") or {}).get("failure_class"),
        "schema_ok": ok_schema,
        "privacy_ok": privacy_ok and not keys,
        "leakage_ok": not any(item.startswith("leakage") or item.startswith("oracle") for item in reasons),
        "replay_ok": replay,
        "passed": ok_schema and privacy_ok and replay and not reasons,
        "reject_reasons": reasons,
        "canonical_hash": item.get("canonical_hash"),
        "protocol_version": PROTOCOL_VERSION,
    }


def audit_candidates(workspace: Workspace) -> Dict[str, Any]:
    rows = load_sp3_candidates(workspace)
    secrets_map = _task_secrets(workspace)
    audits = []
    accepted = []
    rejected = []
    for item in rows:
        task_ids = list(item.get("source_task_ids") or [])
        secrets = None
        if task_ids and task_ids[0] in secrets_map:
            secrets = secrets_map[task_ids[0]]
        result = audit_one(item, secrets=secrets)
        audits.append(result)
        if result["passed"]:
            accepted.append(item)
        else:
            rejected.append({**result, "candidate": {"experience_id": item.get("experience_id"), "discovery_method": item.get("discovery_method")}})
    by_method: Dict[str, int] = {}
    by_stage: Dict[str, int] = {}
    for item in rows:
        method = str(item.get("discovery_method") or "unknown")
        stage = str((item.get("trigger") or {}).get("decision_stage") or "unknown")
        by_method[method] = by_method.get(method, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
    summary = {
        "protocol_version": PROTOCOL_VERSION,
        "n": len(rows),
        "passed": sum(1 for item in audits if item["passed"]),
        "rejected": sum(1 for item in audits if not item["passed"]),
        "schema_pass_rate": sum(1 for item in audits if item["schema_ok"]) / max(1, len(audits)),
        "privacy_pass_rate": sum(1 for item in audits if item["privacy_ok"]) / max(1, len(audits)),
        "leakage_pass_rate": sum(1 for item in audits if item["leakage_ok"]) / max(1, len(audits)),
        "replay_pass_rate": sum(1 for item in audits if item["replay_ok"]) / max(1, len(audits)),
        "by_method": by_method,
        "by_stage": by_stage,
        "source_file_sha256": sha256_file(workspace.self_play_root / SP3_CANDIDATE_PATH),
        "audits": audits,
    }
    return {"summary": summary, "accepted": accepted, "rejected": rejected, "audits": audits}
