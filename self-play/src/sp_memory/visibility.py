"""Unidirectional Oracle visibility projection and leak audits."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from .errors import ProtocolError, ViolationCode
from .paths import PROTOCOL_VERSION
from .schemas import (
    FORBIDDEN_VISIBLE_FIELDS,
    OfflineFeedback,
    OracleLevel,
    TaskRecord,
    VisibleState,
)

SENSITIVE_FIELD_REGISTRY: Set[str] = {
    "answer_entity_ids",
    "normalized_answers",
    "witness_paths",
    "gold_path",
    "gold_sparql",
    "logical_query",
    "future_neighbors",
    "hidden_reward",
    "counterfactual_outcome",
}

# Nested aliases that still count as leakage if they appear as keys.
SENSITIVE_KEY_ALIASES: Set[str] = SENSITIVE_FIELD_REGISTRY | {
    "Sparql",
    "sparql",
    "gold_path",
    "gold_sparql",
    "InferentialChain",
    "Answers",
    "answer",
    "answers",
    "witness",
    "hidden_reward",
}


@dataclass
class OracleSecrets:
    answer_entity_ids: List[str]
    normalized_answers: List[str]
    witness_tokens: List[str]
    logical_query: str
    future_neighbors: List[str]

    @classmethod
    def from_task(cls, task: TaskRecord, future_neighbors: Optional[Sequence[str]] = None) -> "OracleSecrets":
        witness_tokens = []
        for path in task.witness_paths:
            witness_tokens.extend(path)
            witness_tokens.append(" -> ".join(path))
        return cls(
            answer_entity_ids=list(task.answer_entity_ids),
            normalized_answers=list(task.normalized_answers),
            witness_tokens=witness_tokens,
            logical_query=task.logical_query,
            future_neighbors=list(future_neighbors or []),
        )

    def sensitive_values(self) -> List[str]:
        values = []
        values.extend(self.answer_entity_ids)
        values.extend(self.normalized_answers)
        values.extend(self.witness_tokens)
        if self.logical_query:
            values.append(self.logical_query)
        values.extend(self.future_neighbors)
        return [item for item in values if item]


def walk_keys(obj: Any, prefix: str = "") -> List[str]:
    found = []
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found.append(str(key))
            found.extend(walk_keys(value, path))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            found.extend(walk_keys(value, f"{prefix}[{idx}]"))
    return found


def walk_strings(obj: Any) -> List[str]:
    found = []
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, Mapping):
        for value in obj.values():
            found.extend(walk_strings(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(walk_strings(value))
    return found


def find_sensitive_keys(obj: Any, extra_forbidden: Optional[Iterable[str]] = None) -> List[str]:
    forbidden = SENSITIVE_KEY_ALIASES | set(extra_forbidden or [])
    return sorted({key for key in walk_keys(obj) if key in forbidden})


def _contains_secret(text: str, secret: str) -> bool:
    if not secret or not text:
        return False
    if secret in text:
        return True
    return False


def find_sensitive_values(
    obj: Any,
    secrets: OracleSecrets,
    allowed_values: Optional[Iterable[str]] = None,
) -> List[str]:
    allowed = {item for item in (allowed_values or []) if item}
    hits = []
    texts = walk_strings(obj)
    blob = "\n".join(texts)
    always_secret = []
    if secrets.logical_query:
        always_secret.append(secrets.logical_query)
    always_secret.extend(item for item in secrets.witness_tokens if " -> " in item)
    for secret in always_secret:
        if secret and secret in blob:
            hits.append(secret)
    for secret in secrets.answer_entity_ids + secrets.normalized_answers + secrets.future_neighbors:
        if not secret or secret in allowed:
            continue
        if secret in blob:
            hits.append(secret)
    return sorted(set(hits))


def allowed_values_from_state(visible_state: VisibleState, task: TaskRecord) -> List[str]:
    allowed = set(task.source_entities)
    allowed.update(task.source_entity_names.values())
    allowed.add(task.question)
    allowed.update(visible_state.visible_entities)
    allowed.update(visible_state.frontier)
    for relation in visible_state.visible_relations:
        allowed.add(relation.entity)
        allowed.add(relation.relation)
    for triple in visible_state.observed_triples_or_summaries:
        allowed.update(str(value) for value in triple.values())
    return [item for item in allowed if item]


def audit_object(
    obj: Any,
    *,
    secrets: Optional[OracleSecrets] = None,
    allow_oracle_fields: bool = False,
    allowed_values: Optional[Iterable[str]] = None,
    context: str = "object",
) -> None:
    if not allow_oracle_fields:
        keys = find_sensitive_keys(obj)
        if keys:
            raise ProtocolError(
                ViolationCode.ORACLE_LEAKAGE,
                f"{context} contains sensitive keys {keys}",
                {"keys": keys, "context": context},
            )
        if secrets is not None:
            values = find_sensitive_values(obj, secrets, allowed_values=allowed_values)
            if values:
                raise ProtocolError(
                    ViolationCode.ORACLE_LEAKAGE,
                    f"{context} contains sensitive values",
                    {"values": values, "context": context},
                )


def project_actor_view(
    task: TaskRecord,
    visible_state: VisibleState,
    secrets: Optional[OracleSecrets] = None,
) -> Dict[str, Any]:
    secrets = secrets or OracleSecrets.from_task(task)
    allowed = allowed_values_from_state(visible_state, task)
    view = {
        "role": "explorer",
        "oracle_level": OracleLevel.O0.value,
        "protocol_version": PROTOCOL_VERSION,
        "task": task.public_dict(),
        "state": visible_state.to_dict(),
    }
    audit_object(view, secrets=secrets, allowed_values=allowed, context="ActorView")
    return view


def project_critic_view(
    task: TaskRecord,
    visible_state: VisibleState,
    secrets: Optional[OracleSecrets] = None,
    offline_feedback: Optional[OfflineFeedback] = None,
) -> Dict[str, Any]:
    secrets = secrets or OracleSecrets.from_task(task)
    allowed = allowed_values_from_state(visible_state, task)
    view = {
        "role": "critic",
        "oracle_level": OracleLevel.O0.value,
        "protocol_version": PROTOCOL_VERSION,
        "task": task.public_dict(),
        "state": visible_state.to_dict(),
    }
    if offline_feedback is not None:
        if offline_feedback.level not in {OracleLevel.O1, OracleLevel.O2, OracleLevel.O3}:
            raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, "online critic cannot consume O4 feedback")
        # Online critic remains O0. Offline feedback is a separate named object.
        view["offline_feedback_refused_for_online_critic"] = True
    audit_object(view, secrets=secrets, allowed_values=allowed, context="CriticView")
    return view


def project_verifier_view(
    task: TaskRecord,
    visible_state: VisibleState,
    future_neighbors: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    view = {
        "role": "verifier",
        "oracle_level": OracleLevel.O4.value,
        "protocol_version": PROTOCOL_VERSION,
        "task": task.to_dict(),
        "state": visible_state.to_dict(),
        "oracle": task.oracle_dict(),
        "future_neighbors": list(future_neighbors or []),
    }
    return view


def attach_offline_feedback(feedback: OfflineFeedback) -> Dict[str, Any]:
    """O1-O3 must use this typed object; it is not an O4 TaskRecord."""
    payload = feedback.to_dict()
    audit_object(payload["payload"], allow_oracle_fields=False, context="OfflineFeedback.payload")
    return payload


def render_actor_prompt(actor_view: Mapping[str, Any], secrets: OracleSecrets) -> str:
    allowed = []
    if "state" in actor_view and "task" in actor_view:
        from .schemas import TaskRecord, VisibleState

        state = VisibleState.from_dict(actor_view["state"])
        # Public task fields only; do not round-trip Oracle.
        dummy = TaskRecord(
            task_id=actor_view["task"]["task_id"],
            question=actor_view["task"]["question"],
            source_entities=list(actor_view["task"]["source_entities"]),
            source_entity_names=dict(actor_view["task"]["source_entity_names"]),
            task_split=actor_view["task"]["task_split"],
            task_generator_version=actor_view["task"]["task_generator_version"],
            input_snapshot_id=actor_view["task"]["input_snapshot_id"],
            logical_query="",
            answer_entity_ids=[],
            normalized_answers=[],
            witness_paths=[],
            task_validity="valid",
            oracle_version="none",
        )
        allowed = allowed_values_from_state(state, dummy)
    audit_object(actor_view, secrets=secrets, allowed_values=allowed, context="ActorView.before_prompt")
    prompt = (
        "Question: {question}\n"
        "Visible entities: {entities}\n"
        "Visible relations: {relations}\n"
        "Observed triples: {triples}\n"
        "Frontier: {frontier}\n"
        "Failed branches: {failed}\n"
        "Remaining budget: {budget}\n"
        "History: {history}\n"
        "Choose a legal action from the visible candidates."
    ).format(
        question=actor_view["task"]["question"],
        entities=json.dumps(actor_view["state"]["visible_entities"], ensure_ascii=False),
        relations=json.dumps(actor_view["state"]["visible_relations"], ensure_ascii=False),
        triples=json.dumps(actor_view["state"]["observed_triples_or_summaries"], ensure_ascii=False),
        frontier=json.dumps(actor_view["state"]["frontier"], ensure_ascii=False),
        failed=json.dumps(actor_view["state"]["failed_or_exhausted_branches"], ensure_ascii=False),
        budget=json.dumps(actor_view["state"]["remaining_budget"], ensure_ascii=False),
        history=json.dumps(actor_view["state"]["action_history_summary"], ensure_ascii=False),
    )
    audit_prompt_text(prompt, secrets, allowed_values=allowed)
    return prompt


def audit_prompt_text(text: str, secrets: OracleSecrets, allowed_values: Optional[Iterable[str]] = None) -> None:
    hits = []
    dummy = {"text": text}
    hits.extend(find_sensitive_values(dummy, secrets, allowed_values=allowed_values))
    for key in SENSITIVE_FIELD_REGISTRY:
        if re.search(rf"\b{re.escape(key)}\b\s*[:=]", text):
            hits.append(key)
    if hits:
        raise ProtocolError(
            ViolationCode.ORACLE_LEAKAGE,
            "prompt contains Oracle secrets or sensitive field labels",
            {"hits": sorted(set(hits))},
        )


def count_sensitive_fields(obj: Any) -> int:
    return len(find_sensitive_keys(obj))
