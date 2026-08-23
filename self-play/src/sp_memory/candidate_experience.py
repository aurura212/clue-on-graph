"""Candidate experience store, leakage audit, and extraction. Write-only in SP3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_file
from .paths import PROTOCOL_VERSION, Workspace
from .schemas import (
    CandidateExperience,
    DiscoveryMethod,
    FORBIDDEN_VISIBLE_FIELDS,
    OracleLevel,
    VisibleState,
)
from .state_signature import experience_text_is_abstract, replace_secrets, state_signature
from .visibility import OracleSecrets, audit_object, find_sensitive_keys, find_sensitive_values


class CandidateReadGuard:
    def retrieve(self, *args: Any, **kwargs: Any) -> Any:
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            "SP3 forbids retrieving candidate experience into Explorer/Critic",
        )


def candidate_body_for_audit(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "trigger": dict(item.get("trigger") or {}),
        "recommendation": dict(item.get("recommendation") or {}),
        "evidence": dict(item.get("evidence") or {}),
        "privacy": dict(item.get("privacy") or {}),
    }


def audit_candidate(
    item: Mapping[str, Any],
    secrets: Optional[OracleSecrets] = None,
    allowed_values: Optional[Iterable[str]] = None,
) -> None:
    parsed = CandidateExperience.from_dict(item)
    body = candidate_body_for_audit(parsed.to_dict())
    audit_object(body, secrets=secrets, allowed_values=allowed_values, context="CandidateExperience")
    reason = str((parsed.recommendation or {}).get("reason") or "")
    extra = list(secrets.sensitive_values()) if secrets else []
    hits = experience_text_is_abstract(reason, extra_secrets=extra)
    if hits:
        raise ProtocolError(
            ViolationCode.ORACLE_LEAKAGE,
            "candidate reason is not entity/answer free",
            {"hits": hits},
        )
    blob = canonical_json(body)
    for key in FORBIDDEN_VISIBLE_FIELDS:
        if f'"{key}"' in blob:
            raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, "candidate body mentions Oracle field names")


def build_candidate(
    *,
    source_run_id: str,
    source_task_ids: Sequence[str],
    discovery_method: str,
    question_type: str,
    decision_stage: str,
    failure_class: str,
    state: VisibleState,
    action_type: str,
    direction: Optional[str],
    relation_pattern: Optional[str],
    reason: str,
    negative_constraints: Sequence[str],
    budget_condition: str,
    observed_outcome: str,
    verified_replay: bool,
    prompt_version: str,
    config_hash: str,
    plan_version: str,
    oracle_level: str,
    secrets: Optional[OracleSecrets] = None,
    support_count: int = 1,
) -> Dict[str, Any]:
    if oracle_level == OracleLevel.O4.value:
        raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, "candidate cannot use O4")
    signature = state_signature(state, failure_class=failure_class, question_type=question_type)
    clean_reason = replace_secrets(reason, secrets.sensitive_values() if secrets else [])
    trigger = {
        "question_type": question_type,
        "decision_stage": decision_stage,
        "state_signature": signature["state_signature"],
        "failure_class": failure_class,
    }
    recommendation = {
        "action_type": action_type,
        "direction": direction,
        "relation_pattern": relation_pattern,
        "reason": clean_reason,
        "negative_constraints": list(negative_constraints),
        "budget_condition": budget_condition,
    }
    digest = canonical_hash(
        {
            "trigger": trigger,
            "recommendation": recommendation,
            "discovery_method": discovery_method,
        }
    )
    payload = {
        "experience_id": "sp3-candidate-" + digest[:16],
        "source_run_id": source_run_id,
        "source_task_ids": list(source_task_ids),
        "discovery_method": discovery_method,
        "trigger": trigger,
        "recommendation": recommendation,
        "evidence": {
            "verified_replay": bool(verified_replay),
            "observed_outcome": observed_outcome,
            "support_count": int(support_count),
            "counterfactual_status": "deferred_to_sp4",
        },
        "privacy": {
            "answer_removed": True,
            "witness_removed": True,
            "entity_ids_removed": True,
            "gold_path_removed": True,
            "oracle_level": oracle_level,
        },
        "versions": {
            "protocol_version": PROTOCOL_VERSION,
            "plan_version": plan_version,
            "prompt_version": prompt_version,
            "config_hash": config_hash,
        },
        "status": "candidate",
        "canonical_hash": digest,
        "protocol_version": PROTOCOL_VERSION,
    }
    audit_candidate(payload, secrets=secrets)
    return CandidateExperience.from_dict(payload).to_dict()


class CandidateStore:
    """Append-only JSONL. Explorer never reads this store in SP3."""

    def __init__(self, workspace: Workspace, path: Path) -> None:
        self.workspace = workspace
        self.path = workspace.assert_writable(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rejected_path = self.path.with_name(self.path.stem.replace("experience", "rejection_log") + self.path.suffix)
        if "rejection" not in self.rejected_path.name:
            self.rejected_path = self.path.parent / "sp3_candidate_rejection_log_v1.jsonl"
        self._hashes: Set[str] = set()
        self._rows: List[Dict[str, Any]] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                self._rows.append(row)
                self._hashes.add(str(row.get("canonical_hash") or ""))

    def existing_hashes(self) -> Set[str]:
        return set(self._hashes)

    def append(self, item: Mapping[str, Any], *, secrets: Optional[OracleSecrets] = None) -> Dict[str, Any]:
        try:
            audit_candidate(item, secrets=secrets)
            parsed = CandidateExperience.from_dict(item).to_dict()
        except ProtocolError as exc:
            self._reject(item, exc)
            return {"accepted": False, "error": exc.to_dict()}
        digest = parsed["canonical_hash"]
        if digest in self._hashes:
            return {"accepted": False, "duplicate": True, "canonical_hash": digest, "experience_id": parsed["experience_id"]}
        line = canonical_json(parsed) + "\n"
        if not self.path.exists():
            self.workspace.safe_write_text(self.path, line)
        else:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        self._hashes.add(digest)
        self._rows.append(parsed)
        return {"accepted": True, "duplicate": False, "experience_id": parsed["experience_id"], "canonical_hash": digest}

    def _reject(self, item: Mapping[str, Any], exc: ProtocolError) -> None:
        record = {
            "protocol_version": PROTOCOL_VERSION,
            "rejected": True,
            "reason": exc.message,
            "code": exc.code.value,
            "details": exc.details,
            "experience_id": item.get("experience_id"),
            "source_task_ids": item.get("source_task_ids"),
        }
        self.rejected_path.parent.mkdir(parents=True, exist_ok=True)
        with self.rejected_path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(record) + "\n")

    def rows(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def sha256(self) -> Optional[str]:
        if not self.path.exists():
            return None
        return sha256_file(self.path)


def extract_from_trace(
    *,
    result: Mapping[str, Any],
    state: VisibleState,
    source_run_id: str,
    discovery_method: str,
    question_type: str,
    prompt_version: str,
    config_hash: str,
    plan_version: str,
    oracle_level: str,
    secrets: Optional[OracleSecrets] = None,
    verified_replay: bool = False,
) -> List[Dict[str, Any]]:
    out = []
    for item in result.get("trace") or []:
        if item.get("kind") != "critic":
            continue
        action = item.get("action") or {}
        if not item.get("accepted") or not action:
            continue
        local_state = state
        if item.get("visible_state"):
            local_state = VisibleState.from_dict(item["visible_state"])
        payload = build_candidate(
            source_run_id=source_run_id,
            source_task_ids=[str(result.get("task_id"))],
            discovery_method=discovery_method,
            question_type=question_type,
            decision_stage=str(item.get("decision_stage") or state.decision_stage.value),
            failure_class=str(item.get("failure_class") or result.get("failure_class") or "explorer_failure"),
            state=local_state,
            action_type=str(action.get("action_type") or "CONTINUE"),
            direction=(action.get("params") or {}).get("direction"),
            relation_pattern=(action.get("params") or {}).get("relation"),
            reason=str(item.get("reason") or "o0_critic_recovery"),
            negative_constraints=list(item.get("negative_constraints") or []),
            budget_condition=json.dumps(local_state.remaining_budget, sort_keys=True),
            observed_outcome=str(result.get("termination_reason") or ""),
            verified_replay=verified_replay,
            prompt_version=prompt_version,
            config_hash=config_hash,
            plan_version=plan_version,
            oracle_level=oracle_level,
            secrets=secrets,
        )
        out.append(payload)
    return out
