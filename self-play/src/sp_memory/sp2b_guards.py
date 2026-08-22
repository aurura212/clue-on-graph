"""SP2-B guards: allow LLM and live KG, forbid Self-Play Experience Memory and Oracle in Actor."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from .errors import ProtocolError, ViolationCode
from .sp2a_guards import (
    EVAL_TASK_RE,
    MEMORY_PATH_MARKERS,
    scan_config_for_secrets,
    scan_paths_for_secrets,
    snapshot_readonly_roots,
)
from .visibility import OracleSecrets, audit_object, find_sensitive_values

FORBIDDEN_EXPERIENCE_MARKERS = MEMORY_PATH_MARKERS + (
    "candidate_experience",
    "promoted_memory",
    "formal_memory.json",
    "artifacts/memory",
)


class ExperienceMemoryGuard:
    def __init__(self) -> None:
        self.reads = 0
        self.writes = 0

    def read(self, *args: Any, **kwargs: Any) -> Any:
        self.reads += 1
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            "SP2-B forbids Self-Play Experience Memory read",
            {"call_count": self.reads},
        )

    def write(self, *args: Any, **kwargs: Any) -> Any:
        self.writes += 1
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            "SP2-B forbids Self-Play Experience Memory write",
            {"call_count": self.writes},
        )


class Sp2bGuards:
    def __init__(self) -> None:
        self.experience = ExperienceMemoryGuard()
        self.oracle_label_in_action = 0
        self.illegal_kg = 0
        self.secret_hits: List[Dict[str, str]] = []

    def counts(self) -> Dict[str, int]:
        return {
            "experience_memory_reads": self.experience.reads,
            "experience_memory_writes": self.experience.writes,
            "oracle_label_in_action": self.oracle_label_in_action,
            "illegal_kg": self.illegal_kg,
            "secret_hits": len(self.secret_hits),
        }

    def note_oracle_label(self) -> None:
        self.oracle_label_in_action += 1
        raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, "Oracle/test label entered an Actor/LLM view")

    def note_illegal_kg(self, message: str) -> None:
        self.illegal_kg += 1
        raise ProtocolError(ViolationCode.INVISIBLE_RELATION, message)


def public_task_view(task: Mapping[str, Any]) -> Dict[str, Any]:
    allowed = {
        "task_id",
        "question",
        "topic_entity",
        "source_entities",
        "source_entity_names",
        "max_depth",
        "allow_multihop",
        "answer_type",
        "coverage",
        "usage_tags",
        "layer",
        "protocol_version",
    }
    return {key: task[key] for key in allowed if key in task}


def load_oracle_bundle(task: Mapping[str, Any], oracle_section: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    payload = dict(oracle_section or {})
    nested = task.get("oracle")
    if isinstance(nested, Mapping):
        raise ProtocolError(
            ViolationCode.ORACLE_LEAKAGE,
            "task public record must not embed oracle fields",
            {"task_id": task.get("task_id")},
        )
    return payload


def audit_actor_payload(payload: Mapping[str, Any], secrets: OracleSecrets, *, context: str) -> None:
    audit_object(payload, secrets=secrets, context=context)


def audit_prompt(prompt: str, secrets: OracleSecrets, allowed_values: Optional[Sequence[str]] = None) -> None:
    # Audit Oracle *values* in the dynamic suffix only. Field-label scans would
    # false-positive on original PoG working-memory JSON that contains "answer".
    hits = find_sensitive_values({"text": prompt}, secrets, allowed_values=allowed_values)
    if hits:
        raise ProtocolError(
            ViolationCode.ORACLE_LEAKAGE,
            "prompt contains Oracle secrets",
            {"hits": hits},
        )


def source_mentions_experience_memory(text: str) -> List[str]:
    hits = []
    for marker in FORBIDDEN_EXPERIENCE_MARKERS:
        if marker in text and "forbids" not in text:
            hits.append(marker)
    return hits


def is_eval_task_id(task_id: str, banned_ids: Set[str]) -> bool:
    return task_id in banned_ids or bool(EVAL_TASK_RE.search(task_id))
