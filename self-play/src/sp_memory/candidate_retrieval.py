"""Retrieve eligible candidates and apply controlled injection. Fail closed."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash
from .paths import PROTOCOL_VERSION
from .schemas import Action, VisibleState
from .state_signature import state_signature
from .visibility import OracleSecrets, audit_object

MATCH_THRESHOLD = 0.55


class CandidateRetriever:
    def __init__(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        memory_read: bool,
        min_score: float = MATCH_THRESHOLD,
    ) -> None:
        self.memory_read = bool(memory_read)
        self.min_score = min_score
        if not self.memory_read:
            self.candidates: List[Dict[str, Any]] = []
            self._loaded = False
        else:
            self.candidates = [dict(item) for item in candidates]
            self._loaded = True

    def retrieve(
        self,
        *,
        state: VisibleState,
        question_type: str,
        answer_type: str,
        decision_stage: Optional[str] = None,
        budget_bucket: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        if not self.memory_read:
            return {
                "retrieved": [],
                "fallback": "memory_read_false",
                "reason": "candidates were not read",
                "protocol_version": PROTOCOL_VERSION,
            }
        stage = decision_stage or (state.decision_stage.value if hasattr(state.decision_stage, "value") else str(state.decision_stage))
        signature = state_signature(state, question_type=question_type)
        scored = []
        for item in self.candidates:
            trigger = item.get("trigger") or {}
            rec = item.get("recommendation") or {}
            score = 0.0
            if trigger.get("decision_stage") == stage:
                score += 0.35
            if trigger.get("question_type") == question_type:
                score += 0.2
            if trigger.get("state_signature") == signature.get("state_signature"):
                score += 0.25
            action_type = rec.get("action_type")
            if action_type and any(
                rel.relation == rec.get("relation_pattern") for rel in state.visible_relations
            ):
                score += 0.2
            elif action_type in {"CONTINUE", "STOP", "ABSTAIN", "SELECT_FRONTIER"}:
                score += 0.1
            if score >= self.min_score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("experience_id"))))
        top = scored[:3]
        if not top:
            return {
                "retrieved": [],
                "fallback": "empty_match",
                "reason": "no eligible candidate",
                "protocol_version": PROTOCOL_VERSION,
            }
        if len(top) > 1 and abs(top[0][0] - top[1][0]) < 1e-9:
            return {
                "retrieved": [{"experience_id": item.get("experience_id"), "score": score} for score, item in top],
                "fallback": "conflict_match",
                "reason": "tied top candidates",
                "protocol_version": PROTOCOL_VERSION,
            }
        if top[0][0] < self.min_score:
            return {
                "retrieved": [{"experience_id": top[0][1].get("experience_id"), "score": top[0][0]}],
                "fallback": "low_confidence",
                "reason": "score below threshold",
                "protocol_version": PROTOCOL_VERSION,
            }
        winner = top[0]
        return {
            "retrieved": [
                {
                    "experience_id": winner[1].get("experience_id"),
                    "score": winner[0],
                    "action_type": (winner[1].get("recommendation") or {}).get("action_type"),
                    "relation_pattern": (winner[1].get("recommendation") or {}).get("relation_pattern"),
                    "direction": (winner[1].get("recommendation") or {}).get("direction"),
                }
            ],
            "fallback": None,
            "reason": "matched",
            "protocol_version": PROTOCOL_VERSION,
        }


def bind_candidate_action(candidate: Mapping[str, Any], state: VisibleState) -> Action:
    from .action_protocol import parse_action
    from .schemas import ActorRole

    rec = candidate.get("recommendation") or {}
    payload = {
        "action_type": rec.get("action_type"),
        "entity": None,
        "relation": rec.get("relation_pattern"),
        "direction": rec.get("direction"),
    }
    if payload["action_type"] == "EXPAND":
        rels = [
            item
            for item in state.visible_relations
            if item.relation == rec.get("relation_pattern")
            and (not rec.get("direction") or item.direction.value == rec.get("direction"))
        ]
        if not rels:
            raise ProtocolError(ViolationCode.INVISIBLE_RELATION, "candidate relation is not visible")
        payload["entity"] = rels[0].entity
        payload["relation"] = rels[0].relation
        payload["direction"] = rels[0].direction.value
    return parse_action(payload, state, source_role=ActorRole.CRITIC)


def inject_candidate(
    *,
    retrieval: Mapping[str, Any],
    candidate: Optional[Mapping[str, Any]],
    state: VisibleState,
    secrets: Optional[OracleSecrets] = None,
) -> Dict[str, Any]:
    record = {
        "retrieved_ids": [item.get("experience_id") for item in retrieval.get("retrieved") or []],
        "match_scores": [item.get("score") for item in retrieval.get("retrieved") or []],
        "fallback": retrieval.get("fallback"),
        "injected": False,
        "action": None,
        "scan": "ok",
        "protocol_version": PROTOCOL_VERSION,
    }
    if retrieval.get("fallback") or candidate is None:
        return record
    try:
        action = bind_candidate_action(candidate, state)
    except ProtocolError as exc:
        record["fallback"] = "illegal_action"
        record["scan"] = exc.code.value
        return record
    payload = {"action": action.to_dict(), "reason": (candidate.get("recommendation") or {}).get("reason")}
    try:
        audit_object(payload, secrets=secrets, allowed_values=list(state.visible_entities) + [state.question], context="injection")
    except ProtocolError as exc:
        record["fallback"] = "secret_scan"
        record["scan"] = exc.code.value
        return record
    record["injected"] = True
    record["action"] = action.to_dict()
    record["candidate_action"] = action.to_dict()
    return record
