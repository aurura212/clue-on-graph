"""Same-state CF0/CF1/CF2 comparison on a cloned ProtocolSession."""

from __future__ import annotations

import copy
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .action_protocol import ProtocolSession, parse_action
from .candidate_retrieval import bind_candidate_action
from .critic import legal_expand_actions
from .errors import ProtocolError
from .hashing import canonical_hash
from .paths import PROTOCOL_VERSION
from .replay import ReplayEnvironment
from .schemas import Action, ActionType, ActorRole, VisibleState
from .sp4_schemas import SP4_CF_VERSION, validate_counterfactual_record


def clone_env(env: ReplayEnvironment) -> ReplayEnvironment:
    return copy.deepcopy(env)


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


def sham_action(env: ReplayEnvironment) -> Action:
    return original_action(env)


def _gold(env: ReplayEnvironment) -> set:
    return set(env.task.answer_entity_ids) | set(env.task.normalized_answers)


def _relevant(env: ReplayEnvironment, result: Mapping[str, Any]) -> List[Dict[str, str]]:
    gold = _gold(env)
    triples = list((result.get("visible_result") or {}).get("triples") or [])
    relevant = []
    for triple in triples:
        values = {str(v) for v in triple.values()}
        if values & gold:
            relevant.append(triple)
    return relevant


def score_result(env_before: ReplayEnvironment, env_after: ReplayEnvironment, result: Mapping[str, Any], gold_progress: bool) -> Dict[str, Any]:
    if not result.get("accepted"):
        return {
            "success": False,
            "local_progress": False,
            "invalid": True,
            "loop": False,
            "early_stop": False,
            "over_continue": False,
            "new_relevant_triples": 0,
            "invalid_expansion": True,
            "cost": result.get("cost") or {},
        }
    vis = result.get("visible_result") or {}
    action_type = vis.get("action_type")
    triples = vis.get("triples") or []
    relevant = _relevant(env_after, result)
    submitted = env_after.terminal_submission or []
    gold = _gold(env_after)
    success = bool(submitted and set(submitted) & gold)
    early_stop = action_type == "STOP" and submitted and not success
    over_continue = action_type == "CONTINUE" and bool(set(env_before.visible_entities) & gold)
    loop = bool(triples) is False and action_type == "EXPAND"
    local = bool(relevant) or bool(result.get("new_frontier_items"))
    return {
        "success": success,
        "local_progress": local and not success,
        "invalid": False,
        "loop": loop,
        "early_stop": early_stop,
        "over_continue": over_continue,
        "new_relevant_triples": len(relevant),
        "invalid_expansion": loop,
        "cost": result.get("cost") or {},
    }


def _rank(metrics: Mapping[str, Any]) -> Tuple[int, int, int, int]:
    if metrics.get("invalid"):
        return (0, 0, 0, 0)
    success = 3 if metrics.get("success") else 0
    progress = 1 if metrics.get("local_progress") else 0
    relev = int(metrics.get("new_relevant_triples") or 0)
    cost = -int((metrics.get("cost") or {}).get("kg_calls") or 0)
    penalty = 0
    if metrics.get("early_stop") or metrics.get("loop") or metrics.get("over_continue"):
        penalty = -2
    return (success + progress + penalty, relev, cost, 1)


def compare_metrics(cf0: Mapping[str, Any], cf1: Mapping[str, Any]) -> str:
    if cf1.get("invalid") and not cf0.get("invalid"):
        return "invalid"
    r0, r1 = _rank(cf0), _rank(cf1)
    if r1 > r0:
        return "win"
    if r1 < r0:
        return "harm"
    return "tie"


def run_pair(
    env: ReplayEnvironment,
    *,
    candidate: Optional[Mapping[str, Any]],
    seed: int = 1,
    control_type: str = "none",
) -> Dict[str, Any]:
    rng = random.Random(seed)
    base_state = env.visible_state()
    cf0_action = original_action(env)
    try:
        cf1_action = bind_candidate_action(candidate, base_state) if candidate else cf0_action
        cf1_error = None
    except ProtocolError as exc:
        cf1_action = None
        cf1_error = exc.to_dict()
    cf2_action = random_legal_action(env, rng)
    if control_type == "sham":
        cf1_action = sham_action(env)
        cf1_error = None
    elif control_type == "irrelevant":
        cf1_action = cf2_action
        cf1_error = None

    def _run(action: Optional[Action]) -> Tuple[Dict[str, Any], ReplayEnvironment]:
        cloned = clone_env(env)
        session = ProtocolSession(cloned)
        if action is None:
            return (
                {
                    "accepted": False,
                    "violation": cf1_error,
                    "state_hash_before": base_state.state_id,
                    "state_hash_after": base_state.state_id,
                    "cost": {"steps": 0, "kg_calls": 0},
                    "visible_result": {"error": "unbound_candidate"},
                    "new_frontier_items": [],
                },
                cloned,
            )
        return session.execute(action), cloned

    r0, e0 = _run(cf0_action)
    r1, e1 = _run(cf1_action)
    r2, e2 = _run(cf2_action)
    m0 = score_result(env, e0, r0, False)
    m1 = score_result(env, e1, r1, False)
    m2 = score_result(env, e2, r2, False)
    outcome = compare_metrics(m0, m1) if cf1_error is None else "invalid"
    record = {
        "schema_version": SP4_CF_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "pair_id": "cf-" + canonical_hash({"state": base_state.state_id, "cand": (candidate or {}).get("experience_id")})[:16],
        "state_hash": base_state.state_id,
        "budget": dict(base_state.remaining_budget),
        "cf0": {"action": cf0_action.to_dict(), "result": r0, "metrics": m0},
        "cf1": {"action": None if cf1_action is None else cf1_action.to_dict(), "result": r1, "metrics": m1, "error": cf1_error},
        "cf2": {"action": cf2_action.to_dict(), "result": r2, "metrics": m2},
        "outcome": outcome,
        "cost": {
            "cf0": m0.get("cost"),
            "cf1": m1.get("cost"),
            "cf2": m2.get("cost"),
        },
        "invalid_reason": None if outcome != "invalid" else (cf1_error or {}).get("message") or "candidate_invalid",
        "new_relevant_triples": m1.get("new_relevant_triples"),
        "control_type": control_type,
        "candidate_id": (candidate or {}).get("experience_id"),
        "task_id": env.task.task_id,
    }
    return validate_counterfactual_record(record)
