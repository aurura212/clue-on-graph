"""Counterfactuals at the original decision checkpoint. Separate inapplicable from invalid."""

from __future__ import annotations

import copy
import random
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .action_protocol import ProtocolSession, parse_action
from .candidate_retrieval import bind_candidate_action
from .critic import legal_expand_actions
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash
from .paths import PROTOCOL_VERSION
from .replay import ReplayEnvironment
from .schemas import Action, ActorRole, VisibleState

INAPPLICABLE = "inapplicable"
INVALID = "invalid"
WIN = "win"
HARM = "harm"
TIE = "tie"


def clone_env(env: ReplayEnvironment) -> ReplayEnvironment:
    return copy.deepcopy(env)


def _flatten_action(payload: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    params = out.get("params")
    if isinstance(params, dict):
        out = {**out, **params}
    return out


def replay_prefix(env: ReplayEnvironment, prefix: Sequence[Mapping[str, Any]]) -> ReplayEnvironment:
    cloned = clone_env(env)
    session = ProtocolSession(cloned)
    for payload in prefix:
        state = session.visible_state()
        if payload.get("action_id") and payload.get("params") is not None:
            action = Action.from_dict(payload)
        else:
            action = parse_action(_flatten_action(payload), state, source_role=ActorRole.EXPLORER)
        result = session.execute(action)
        if not result.get("accepted"):
            raise ProtocolError(ViolationCode.REPLAY_ERROR, "replay prefix failed", {"result": result})
    return cloned


def original_action(env: ReplayEnvironment) -> Action:
    state = env.visible_state()
    expands = legal_expand_actions(state, source_role=ActorRole.EXPLORER)
    if expands:
        return expands[0]
    return parse_action({"action_type": "CONTINUE"}, state, source_role=ActorRole.EXPLORER)


def random_legal_action(env: ReplayEnvironment, rng: random.Random) -> Action:
    state = env.visible_state()
    expands = legal_expand_actions(state, source_role=ActorRole.EXPLORER)
    if expands:
        return rng.choice(expands)
    return parse_action({"action_type": "CONTINUE"}, state, source_role=ActorRole.EXPLORER)


def candidate_is_visible(candidate: Mapping[str, Any], state: VisibleState) -> bool:
    rec = candidate.get("recommendation") or {}
    relation = rec.get("relation_pattern") or rec.get("relation")
    direction = rec.get("direction")
    action_type = rec.get("action_type")
    if action_type != "EXPAND":
        return True
    for item in state.visible_relations:
        if item.relation == relation and (not direction or item.direction.value == direction):
            return True
    return False


def score_result(before: ReplayEnvironment, after: ReplayEnvironment, result: Mapping[str, Any]) -> Dict[str, Any]:
    gold = set(before.task.answer_entity_ids) | set(before.task.normalized_answers)
    if not result.get("accepted"):
        return {
            "success": False,
            "local_progress": False,
            "invalid": True,
            "new_relevant_triples": 0,
            "cost": result.get("cost") or {},
        }
    vis = result.get("visible_result") or {}
    triples = vis.get("triples") or []
    relevant = 0
    for triple in triples:
        values = {str(v) for v in triple.values()}
        if values & gold:
            relevant += 1
    submitted = after.terminal_submission or []
    success = bool(submitted and set(submitted) & gold)
    observed = set(after.visible_entities)
    local = (not success) and bool((observed & gold) or relevant)
    return {
        "success": success,
        "local_progress": local,
        "invalid": False,
        "new_relevant_triples": relevant,
        "cost": result.get("cost") or {},
    }


def _rank(metrics: Mapping[str, Any]) -> Tuple[int, int, int]:
    if metrics.get("invalid"):
        return (0, 0, 0)
    success = 3 if metrics.get("success") else 0
    progress = 1 if metrics.get("local_progress") else 0
    relev = int(metrics.get("new_relevant_triples") or 0)
    return (success + progress, relev, 1)


def compare_metrics(cf0: Mapping[str, Any], cf1: Mapping[str, Any]) -> str:
    if cf1.get("invalid") and not cf0.get("invalid"):
        return INVALID
    r0, r1 = _rank(cf0), _rank(cf1)
    if r1 > r0:
        return WIN
    if r1 < r0:
        return HARM
    return TIE


def run_same_state_pair(
    env: ReplayEnvironment,
    *,
    candidate: Mapping[str, Any],
    seed: int = 1,
    control_type: str = "none",
) -> Dict[str, Any]:
    prefix = list(candidate.get("replay_prefix") or [])
    try:
        checkpoint = replay_prefix(env, prefix) if prefix else clone_env(env)
    except ProtocolError as exc:
        return {
            "schema_version": "sp4s-cf-v1",
            "protocol_version": PROTOCOL_VERSION,
            "outcome": INVALID,
            "applicable": False,
            "invalid_reason": "prefix_replay_failed",
            "error": exc.to_dict(),
            "candidate_id": candidate.get("experience_id"),
            "task_id": env.task.task_id,
            "state_hash": None,
        }
    state = checkpoint.visible_state()
    expected_hash = candidate.get("decision_state_hash")
    if expected_hash and expected_hash != state.state_id:
        return {
            "schema_version": "sp4s-cf-v1",
            "protocol_version": PROTOCOL_VERSION,
            "outcome": INVALID,
            "applicable": False,
            "invalid_reason": "state_hash_mismatch",
            "candidate_id": candidate.get("experience_id"),
            "task_id": env.task.task_id,
            "state_hash": state.state_id,
            "expected_state_hash": expected_hash,
        }
    rng = random.Random(seed)
    cf0_action = original_action(checkpoint)
    applicable = candidate_is_visible(candidate, state)
    cf1_error = None
    cf1_action: Optional[Action] = None
    outcome_override = None
    if control_type == "sham":
        cf1_action = original_action(checkpoint)
        applicable = True
    elif control_type == "irrelevant":
        cf1_action = random_legal_action(checkpoint, rng)
        applicable = True
    elif not applicable:
        outcome_override = INAPPLICABLE
    else:
        try:
            cf1_action = bind_candidate_action(candidate, state)
        except ProtocolError as exc:
            if exc.code is ViolationCode.INVISIBLE_RELATION:
                outcome_override = INAPPLICABLE
                cf1_error = exc.to_dict()
            else:
                outcome_override = INVALID
                cf1_error = exc.to_dict()
    cf2_action = random_legal_action(checkpoint, rng)

    def _run(action: Optional[Action]) -> Tuple[Dict[str, Any], ReplayEnvironment]:
        cloned = clone_env(checkpoint)
        session = ProtocolSession(cloned)
        if action is None:
            return (
                {
                    "accepted": False,
                    "violation": cf1_error,
                    "cost": {"steps": 0, "kg_calls": 0},
                    "visible_result": {"error": "unbound_candidate"},
                },
                cloned,
            )
        return session.execute(action), cloned

    r0, e0 = _run(cf0_action)
    r1, e1 = _run(cf1_action)
    r2, e2 = _run(cf2_action)
    m0 = score_result(checkpoint, e0, r0)
    m1 = score_result(checkpoint, e1, r1)
    m2 = score_result(checkpoint, e2, r2)
    outcome = outcome_override or compare_metrics(m0, m1)
    return {
        "schema_version": "sp4s-cf-v1",
        "protocol_version": PROTOCOL_VERSION,
        "pair_id": "cf-" + canonical_hash({"state": state.state_id, "cand": candidate.get("experience_id")})[:16],
        "state_hash": state.state_id,
        "budget": dict(state.remaining_budget),
        "visible_relations": [item.to_dict() for item in state.visible_relations],
        "cf0": {"action": cf0_action.to_dict(), "result": r0, "metrics": m0},
        "cf1": {
            "action": None if cf1_action is None else cf1_action.to_dict(),
            "result": r1,
            "metrics": m1,
            "error": cf1_error,
        },
        "cf2": {"action": cf2_action.to_dict(), "result": r2, "metrics": m2},
        "outcome": outcome,
        "applicable": outcome != INAPPLICABLE,
        "control_type": control_type,
        "candidate_id": candidate.get("experience_id"),
        "task_id": env.task.task_id,
        "prefix_len": len(prefix),
    }


def summarize_cf(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    applicable = [item for item in rows if item.get("outcome") != INAPPLICABLE]
    denom = max(1, len(applicable))
    return {
        "n": n,
        "n_applicable": len(applicable),
        "win_rate": sum(1 for item in applicable if item.get("outcome") == WIN) / denom,
        "harm_rate": sum(1 for item in applicable if item.get("outcome") == HARM) / denom,
        "tie_rate": sum(1 for item in applicable if item.get("outcome") == TIE) / denom,
        "invalid_rate": sum(1 for item in applicable if item.get("outcome") == INVALID) / denom,
        "inapplicable_rate": sum(1 for item in rows if item.get("outcome") == INAPPLICABLE) / max(1, n),
    }
