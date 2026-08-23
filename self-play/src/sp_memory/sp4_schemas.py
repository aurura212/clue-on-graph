"""SP4 data contracts. Adapters map SP3 CandidateExperience without rewriting it."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash
from .paths import PROTOCOL_VERSION

SP4_TASK_VERSION = "sp4-task-v1"
SP4_TRAJECTORY_VERSION = "sp4-trajectory-v1"
SP4_CANDIDATE_VERSION = "sp4-candidate-v1"
SP4_CF_VERSION = "sp4-counterfactual-v1"
SP4_RULE_VERSION = "sp4-memory-rule-v1"

ALLOWED_SPLITS = ("discovery", "validation_v1", "validation_v2", "holdout", "counterfactual")
ALLOWED_RULE_STATUS = ("promoted", "validated_candidate", "rejected_harmful", "deferred")
ALLOWED_CF_OUTCOMES = ("win", "tie", "harm", "invalid")
ALLOWED_CONTROL = ("none", "sham", "irrelevant", "random")


def _req(payload: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    missing = [name for name in fields if name not in payload]
    if missing:
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"{label} missing {missing}", {"missing": missing})


def _enum(value: Any, allowed: Sequence[str], field: str) -> str:
    text = str(value or "")
    if text not in allowed:
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"illegal {field}: {value!r}", {"field": field})
    return text


def validate_task_record(payload: Mapping[str, Any], *, actor_only: bool = False) -> Dict[str, Any]:
    _req(
        payload,
        [
            "schema_version",
            "task_id",
            "split",
            "snapshot_id",
            "snapshot_hash",
            "source_entity_hash",
            "answer_entity_hash",
            "path_signature",
            "question_hash",
            "difficulty",
            "oracle_level",
            "protocol_version",
        ],
        "sp4.task",
    )
    if payload.get("schema_version") != SP4_TASK_VERSION:
        raise ProtocolError(ViolationCode.SCHEMA_VERSION_MISMATCH, "task schema_version mismatch")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError(ViolationCode.SCHEMA_VERSION_MISMATCH, "task protocol_version mismatch")
    _enum(payload.get("split"), ALLOWED_SPLITS, "split")
    if actor_only and payload.get("oracle_level") != "O0":
        raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, "actor task view must be O0")
    return dict(payload)


def validate_trajectory_record(payload: Mapping[str, Any]) -> Dict[str, Any]:
    _req(
        payload,
        [
            "schema_version",
            "run_id",
            "task_id",
            "seed",
            "temperature",
            "stage",
            "initial_state_hash",
            "final_state_hash",
            "actions",
            "failure_type",
            "critic_source",
            "recovery_status",
            "budget",
            "replay_status",
            "protocol_version",
        ],
        "sp4.trajectory",
    )
    if payload.get("schema_version") != SP4_TRAJECTORY_VERSION:
        raise ProtocolError(ViolationCode.SCHEMA_VERSION_MISMATCH, "trajectory schema_version mismatch")
    return dict(payload)


def validate_counterfactual_record(payload: Mapping[str, Any]) -> Dict[str, Any]:
    _req(
        payload,
        [
            "schema_version",
            "pair_id",
            "state_hash",
            "budget",
            "cf0",
            "cf1",
            "cf2",
            "outcome",
            "cost",
            "invalid_reason",
            "new_relevant_triples",
            "control_type",
            "protocol_version",
        ],
        "sp4.counterfactual",
    )
    if payload.get("schema_version") != SP4_CF_VERSION:
        raise ProtocolError(ViolationCode.SCHEMA_VERSION_MISMATCH, "counterfactual schema_version mismatch")
    _enum(payload.get("outcome"), ALLOWED_CF_OUTCOMES, "outcome")
    _enum(payload.get("control_type"), ALLOWED_CONTROL, "control_type")
    return dict(payload)


def validate_memory_rule(payload: Mapping[str, Any]) -> Dict[str, Any]:
    _req(
        payload,
        [
            "schema_version",
            "rule_id",
            "rule_version",
            "decision_stage",
            "abstract_state",
            "action_policy",
            "applicability",
            "support",
            "statistics",
            "source_hashes",
            "status",
            "protocol_version",
        ],
        "sp4.memory_rule",
    )
    if payload.get("schema_version") != SP4_RULE_VERSION:
        raise ProtocolError(ViolationCode.SCHEMA_VERSION_MISMATCH, "memory_rule schema_version mismatch")
    _enum(payload.get("status"), ALLOWED_RULE_STATUS, "status")
    return dict(payload)


def path_signature(relations: Sequence[str]) -> str:
    return "ps-" + canonical_hash({"relations": [str(item) for item in relations]})[:20]


def make_rule_id(payload: Mapping[str, Any]) -> str:
    digest = canonical_hash(
        {
            "decision_stage": payload.get("decision_stage"),
            "abstract_state": payload.get("abstract_state"),
            "action_policy": payload.get("action_policy"),
        }
    )
    return "sp4-rule-" + digest[:16]
