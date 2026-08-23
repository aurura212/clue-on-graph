"""Parse, validate, execute, and replay relation selection / continue / stop / backtrack."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from .action_validator import BACKTRACK_PREFIX, validate_action
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash
from .replay import ReplayEnvironment
from .schemas import Action, ActionType, ActorRole, Direction, VisibleState


def parse_action(payload: Dict[str, Any], state: VisibleState, *, source_role: ActorRole = ActorRole.EXPLORER) -> Action:
    action_type = ActionType(str(payload.get("action_type") or ""))
    params: Dict[str, Any] = {}
    if action_type is ActionType.EXPAND:
        params = {
            "entity": str(payload.get("entity") or ""),
            "relation": str(payload.get("relation") or ""),
            "direction": str(payload.get("direction") or Direction.HEAD.value),
        }
    elif action_type is ActionType.SELECT_FRONTIER:
        params = {"entity": str(payload.get("entity") or "")}
    elif action_type is ActionType.BACKTRACK:
        params = {"entity_or_state": str(payload.get("entity_or_state") or payload.get("target") or "")}
    elif action_type is ActionType.STOP:
        candidates = payload.get("answer_candidates") or []
        if not isinstance(candidates, list):
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "STOP answer_candidates must be a list")
        params = {"answer_candidates": [str(item) for item in candidates]}
    elif action_type is ActionType.ABSTAIN:
        params = {"reason_code": str(payload.get("reason_code") or "INSUFFICIENT_EVIDENCE")}
    elif action_type is ActionType.CONTINUE:
        params = {}
    else:
        raise ProtocolError(ViolationCode.UNKNOWN_ACTION, f"unhandled action {action_type}")
    action = Action(
        action_id="proto-" + canonical_hash({"type": action_type.value, "params": params, "state": state.state_id})[:12],
        action_type=action_type,
        params=params,
        source_role=source_role,
        state_id=state.state_id,
    )
    validate_action(action, state)
    return action


def visible_backtrack_targets(state: VisibleState) -> List[str]:
    targets = list(state.frontier) + list(state.visible_entities)
    for item in state.action_history_summary:
        if str(item).startswith(BACKTRACK_PREFIX):
            targets.append(str(item))
    targets.append(BACKTRACK_PREFIX + state.state_id)
    out = []
    seen = set()
    for item in targets:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


class ProtocolSession:
    """Checkpointed replay session. Backtrack restores a previously observed state."""

    def __init__(self, env: ReplayEnvironment) -> None:
        self.env = env
        self.checkpoints: Dict[str, ReplayEnvironment] = {}
        self._store_checkpoint()
        self.last_violation: Optional[Dict[str, Any]] = None

    def _store_checkpoint(self) -> None:
        state = self.env.visible_state()
        self.checkpoints[state.state_id] = copy.deepcopy(self.env)
        self.checkpoints[BACKTRACK_PREFIX + state.state_id] = self.checkpoints[state.state_id]

    def visible_state(self) -> VisibleState:
        return self.env.visible_state()

    def _restore(self, snapshot: ReplayEnvironment) -> None:
        self.env.visible_entities = list(snapshot.visible_entities)
        self.env.frontier = list(snapshot.frontier)
        self.env.observed_triples = copy.deepcopy(snapshot.observed_triples)
        self.env.failed_branches = list(snapshot.failed_branches)
        self.env.history = list(snapshot.history)
        self.env.steps = copy.deepcopy(snapshot.steps)
        self.env.terminated = snapshot.terminated
        self.env.termination_reason = snapshot.termination_reason
        self.env.terminal_submission = None if snapshot.terminal_submission is None else list(snapshot.terminal_submission)
        self.env.budget = copy.deepcopy(snapshot.budget)

    def execute(self, action: Action) -> Dict[str, Any]:
        before = self.env.visible_state()
        before_hash = before.state_id
        self.last_violation = None
        try:
            validate_action(action, before)
        except ProtocolError as exc:
            self.last_violation = exc.to_dict()
            return {
                "accepted": False,
                "violation": exc.to_dict(),
                "state_hash_before": before_hash,
                "state_hash_after": before_hash,
                "cost": {"steps": 0, "kg_calls": 0},
                "visible_result": {"error": exc.message},
            }
        if action.action_type is ActionType.BACKTRACK:
            return self._execute_backtrack(action, before)
        outcome = self.env.step(action)
        after = self.env.visible_state()
        if outcome.accepted:
            self._store_checkpoint()
        return {
            "accepted": outcome.accepted,
            "violation": None if outcome.accepted else {"code": outcome.protocol_violation},
            "state_hash_before": before_hash,
            "state_hash_after": after.state_id,
            "cost": dict(outcome.budget_delta),
            "visible_result": dict(outcome.visible_result),
            "new_frontier_items": list(outcome.new_frontier_items),
            "oracle_eval": dict(outcome.oracle_eval or {}),
            "deterministic_result_hash": outcome.deterministic_result_hash,
        }

    def _execute_backtrack(self, action: Action, before: VisibleState) -> Dict[str, Any]:
        target = str(action.params.get("entity_or_state") or "")
        visible = set(before.visible_entities) | set(before.frontier)
        if target in visible:
            if target not in self.env.frontier:
                self.env.frontier.append(target)
                self.env.frontier = sorted(set(self.env.frontier))
            self.env.history.append(f"state:{before.state_id}")
            self.env.history.append(action.action_id)
            self.env.budget.used_steps += 1
            after = self.env.visible_state()
            self._store_checkpoint()
            return {
                "accepted": True,
                "violation": None,
                "state_hash_before": before.state_id,
                "state_hash_after": after.state_id,
                "cost": {"steps": 1, "kg_calls": 0},
                "visible_result": {"action_type": "BACKTRACK", "backtrack_to": target, "mode": "visible_entity"},
                "new_frontier_items": [target] if target not in before.frontier else [],
                "oracle_eval": {},
                "deterministic_result_hash": canonical_hash({"after": after.state_id, "target": target}),
            }
        key = target if target.startswith(BACKTRACK_PREFIX) else BACKTRACK_PREFIX + target
        snapshot = self.checkpoints.get(target) or self.checkpoints.get(key)
        if snapshot is None:
            exc = ProtocolError(
                ViolationCode.INVALID_BACKTRACK_TARGET,
                f"BACKTRACK target is not a visible entity or observed state: {target}",
            )
            self.last_violation = exc.to_dict()
            return {
                "accepted": False,
                "violation": exc.to_dict(),
                "state_hash_before": before.state_id,
                "state_hash_after": before.state_id,
                "cost": {"steps": 0, "kg_calls": 0},
                "visible_result": {"error": exc.message},
            }
        self._restore(snapshot)
        self.env.history.append(f"state:{before.state_id}")
        self.env.history.append(action.action_id)
        self.env.budget.used_steps += 1
        after = self.env.visible_state()
        self._store_checkpoint()
        return {
            "accepted": True,
            "violation": None,
            "state_hash_before": before.state_id,
            "state_hash_after": after.state_id,
            "cost": {"steps": 1, "kg_calls": 0},
            "visible_result": {"action_type": "BACKTRACK", "backtrack_to": target, "mode": "observed_state"},
            "new_frontier_items": [],
            "oracle_eval": {},
            "deterministic_result_hash": canonical_hash({"after": after.state_id, "target": target}),
        }


def replay_actions(env: ReplayEnvironment, actions: List[Action]) -> List[Dict[str, Any]]:
    session = ProtocolSession(copy.deepcopy(env) if not isinstance(env, ReplayEnvironment) else env)
    # operate on the provided env; caller should deepcopy if needed
    session = ProtocolSession(env)
    return [session.execute(action) for action in actions]


def backtrack_action(state: VisibleState, target: str, *, source_role: ActorRole = ActorRole.EXPLORER) -> Action:
    return parse_action({"action_type": "BACKTRACK", "entity_or_state": target}, state, source_role=source_role)
