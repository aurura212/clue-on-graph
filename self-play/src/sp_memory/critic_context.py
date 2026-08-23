"""Compress O0 Critic context and classify protocol/schema/timeout failures."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .errors import ProtocolError, ViolationCode
from .hashing import sha256_text
from .paths import PROTOCOL_VERSION
from .schemas import VisibleState
from .state_signature import abstract_relation, replace_secrets
from .visibility import OracleSecrets

CONTEXT_CHAR_BUDGET = 6000
LEGAL_ACTION_CAP = 24
TRIPLE_CAP = 12
HISTORY_CAP = 8
RELATION_CAP = 16


def classify_critic_error(exc: BaseException, *, prompt_chars: int = 0) -> Dict[str, str]:
    text = str(exc)
    lower = text.lower()
    if "context" in lower and ("length" in lower or "maximum" in lower or "too long" in lower):
        kind = "context_too_long"
    elif "timeout" in lower:
        kind = "timeout"
    elif "retry" in lower:
        kind = "retry_exhausted"
    elif isinstance(exc, ProtocolError) and exc.code is ViolationCode.SCHEMA_ERROR:
        kind = "schema_error"
    elif "schema" in lower or "json" in lower:
        kind = "schema_error"
    else:
        kind = "protocol_failure"
    return {
        "failure_class": "system_failure",
        "protocol_error": kind,
        "message": text[:400],
        "prompt_chars": str(prompt_chars),
        "protocol_version": PROTOCOL_VERSION,
    }


def compress_state(state: VisibleState, secrets: Optional[OracleSecrets] = None) -> Dict[str, Any]:
    extra = list(secrets.sensitive_values()) if secrets else []
    triples = []
    for triple in list(state.observed_triples_or_summaries)[-TRIPLE_CAP:]:
        item = {str(k): replace_secrets(str(v), extra) for k, v in triple.items()}
        triples.append(item)
    relations = []
    for rel in list(state.visible_relations)[:RELATION_CAP]:
        relations.append(
            {
                "entity": replace_secrets(rel.entity, extra),
                "relation": abstract_relation(rel.relation),
                "direction": rel.direction.value,
            }
        )
    history = [replace_secrets(str(item), extra) for item in list(state.action_history_summary)[-HISTORY_CAP:]]
    return {
        "state_id": state.state_id,
        "task_id": state.task_id,
        "question": replace_secrets(state.question, extra),
        "decision_stage": state.decision_stage.value if hasattr(state.decision_stage, "value") else str(state.decision_stage),
        "visible_entity_count": len(state.visible_entities),
        "frontier_count": len(state.frontier),
        "remaining_budget": dict(state.remaining_budget),
        "failed_or_exhausted_branches": [
            replace_secrets(str(item), extra) for item in list(state.failed_or_exhausted_branches)[-HISTORY_CAP:]
        ],
        "action_history_summary": history,
        "observed_triples_or_summaries": triples,
        "visible_relations": relations,
        "protocol_version": PROTOCOL_VERSION,
    }


def compress_legal_actions(actions: Sequence[Mapping[str, Any]], secrets: Optional[OracleSecrets] = None) -> List[Dict[str, Any]]:
    extra = list(secrets.sensitive_values()) if secrets else []
    out = []
    for item in list(actions)[:LEGAL_ACTION_CAP]:
        row = dict(item)
        params = dict(row.get("params") or {})
        for key in ("entity", "relation", "entity_or_state"):
            if key in params:
                params[key] = replace_secrets(str(params[key]), extra)
        row["params"] = params
        out.append(row)
    return out


def build_compressed_critic_input(
    *,
    event: str,
    task_public: Mapping[str, Any],
    state: VisibleState,
    legal_actions: Sequence[Mapping[str, Any]],
    secrets: Optional[OracleSecrets] = None,
    char_budget: int = CONTEXT_CHAR_BUDGET,
) -> Dict[str, Any]:
    payload = {
        "event": event,
        "oracle_level": "O0",
        "task": {
            "task_id": task_public.get("task_id"),
            "question": task_public.get("question"),
            "question_type": task_public.get("question_type"),
            "source_entity_count": len(task_public.get("source_entities") or []),
        },
        "state": compress_state(state, secrets),
        "legal_actions": compress_legal_actions(legal_actions, secrets),
        "legal_action_count": len(list(legal_actions)),
    }
    blob = json.dumps(payload, ensure_ascii=False)
    dropped = 0
    while len(blob) > char_budget and payload["legal_actions"]:
        payload["legal_actions"] = payload["legal_actions"][:-4]
        dropped += 4
        blob = json.dumps(payload, ensure_ascii=False)
    while len(blob) > char_budget and payload["state"]["observed_triples_or_summaries"]:
        payload["state"]["observed_triples_or_summaries"] = payload["state"]["observed_triples_or_summaries"][1:]
        blob = json.dumps(payload, ensure_ascii=False)
    payload["compressed"] = True
    payload["dropped_legal_actions"] = max(0, dropped)
    payload["prompt_chars"] = len(blob)
    payload["prompt_hash"] = sha256_text(blob)
    return payload


def schema_fallback_decision(event: str) -> Dict[str, Any]:
    return {
        "failure_class": "explorer_failure",
        "decision_stage": "continue_stop",
        "action_type": "ABSTAIN",
        "reason_code": "INSUFFICIENT_EVIDENCE",
        "reason": "Critic schema fallback: abstain after malformed or oversized critic output.",
        "negative_constraints": ["do_not_trust_malformed_critic"],
        "fallback": "schema",
        "event": event,
        "accepted": True,
        "protocol_version": PROTOCOL_VERSION,
    }
