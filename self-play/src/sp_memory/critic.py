"""O0 online Critic and G3 random critic. Suggestions are validated before Environment."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .action_parser import abstain_action, select_frontier_action
from .action_validator import validate_action
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, sha256_file, sha256_text
from .paths import PROTOCOL_VERSION, Workspace
from .schemas import (
    Action,
    ActionType,
    ActorRole,
    AbstainReason,
    DecisionStage,
    Direction,
    FailureClass,
    VisibleState,
)
from .sp2b_guards import audit_prompt
from .state_signature import replace_secrets
from .visibility import OracleSecrets, project_critic_view


def load_prompt_text(workspace: Workspace, relpath: str) -> str:
    path = workspace.self_play_root / relpath
    return path.read_text(encoding="utf-8")


def legal_expand_actions(state: VisibleState, *, source_role: ActorRole = ActorRole.CRITIC) -> List[Action]:
    actions = []
    for item in state.visible_relations:
        payload = {
            "entity": item.entity,
            "relation": item.relation,
            "direction": item.direction.value,
        }
        actions.append(
            Action(
                action_id="critic-expand-" + canonical_hash(payload)[:12],
                action_type=ActionType.EXPAND,
                params=payload,
                source_role=source_role,
                state_id=state.state_id,
            )
        )
    return actions


def legal_recovery_actions(state: VisibleState, *, source_role: ActorRole = ActorRole.CRITIC) -> List[Action]:
    actions = legal_expand_actions(state, source_role=source_role)
    for entity in list(state.frontier) + list(state.visible_entities):
        action = select_frontier_action(state, entity)
        action.source_role = source_role
        actions.append(action)
    actions.append(
        Action(
            action_id="critic-continue-" + canonical_hash({"state": state.state_id})[:12],
            action_type=ActionType.CONTINUE,
            params={},
            source_role=source_role,
            state_id=state.state_id,
        )
    )
    remaining = state.remaining_budget or {}
    if int(remaining.get("steps") or 0) <= 2 or int(remaining.get("kg_calls") or 0) <= 4:
        reason = AbstainReason.BUDGET_EXHAUSTED
    else:
        reason = AbstainReason.INSUFFICIENT_EVIDENCE
    abs_action = abstain_action(state, reason)
    abs_action.source_role = source_role
    actions.append(abs_action)
    return actions


def _parse_json_object(text: str) -> Dict[str, Any]:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, "critic output has no JSON object")
    blob = text[first : last + 1]
    try:
        loaded = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, "critic output is not JSON") from exc
    if not isinstance(loaded, dict):
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, "critic output is not an object")
    return loaded


def bind_critic_action(payload: Mapping[str, Any], state: VisibleState, *, source_role: ActorRole) -> Action:
    try:
        action_type = ActionType(str(payload.get("action_type") or ""))
    except ValueError as exc:
        raise ProtocolError(ViolationCode.UNKNOWN_ACTION, f"critic action_type is illegal: {payload.get('action_type')!r}") from exc
    if action_type is ActionType.BACKTRACK:
        raise ProtocolError(
            ViolationCode.UNSUPPORTED_BACKTRACK_STATE,
            "Critic cannot emit BACKTRACK; PoG fallback is SELECT_FRONTIER or CONTINUE",
        )
    params: Dict[str, Any] = {}
    if action_type is ActionType.EXPAND:
        params = {
            "entity": str(payload.get("entity") or ""),
            "relation": str(payload.get("relation") or ""),
            "direction": str(payload.get("direction") or Direction.HEAD.value),
        }
    elif action_type is ActionType.SELECT_FRONTIER:
        params = {"entity": str(payload.get("entity") or "")}
    elif action_type is ActionType.ABSTAIN:
        params = {"reason_code": str(payload.get("reason_code") or AbstainReason.INSUFFICIENT_EVIDENCE.value)}
    elif action_type is ActionType.STOP:
        candidates = payload.get("answer_candidates") or []
        if not isinstance(candidates, list):
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "STOP answer_candidates must be a list")
        params = {"answer_candidates": [str(item) for item in candidates]}
    action = Action(
        action_id="critic-" + canonical_hash({"type": action_type.value, "params": params, "state": state.state_id})[:12],
        action_type=action_type,
        params=params,
        source_role=source_role,
        state_id=state.state_id,
    )
    validate_action(action, state)
    return action


class O0Critic:
    def __init__(
        self,
        workspace: Workspace,
        secrets: OracleSecrets,
        *,
        prompt_relpath: str,
        mode: str = "o0",
        seed: int = 20260822,
    ) -> None:
        self.workspace = workspace
        self.secrets = secrets
        self.mode = mode
        self.rng = random.Random(seed)
        self.prompt_relpath = prompt_relpath
        self.prompt_template = load_prompt_text(workspace, prompt_relpath)
        self.prompt_sha256 = sha256_file(workspace.self_play_root / prompt_relpath)

    def build_prompt(self, task: Mapping[str, Any], state: VisibleState, event: str) -> Dict[str, str]:
        view = project_critic_view(
            _public_task_record(task),
            state,
            secrets=self.secrets,
        )
        legal = [item.to_dict() for item in legal_recovery_actions(state)]
        dynamic = json.dumps(
            {
                "event": event,
                "oracle_level": "O0",
                "task": view["task"],
                "state": view["state"],
                "legal_actions": legal,
            },
            ensure_ascii=False,
        )
        audit_prompt(dynamic, self.secrets, allowed_values=_allowed(task, state))
        prompt = self.prompt_template.replace("{{CRITIC_INPUT}}", dynamic)
        return {"prompt": prompt, "prompt_hash": sha256_text(prompt), "prompt_version": Path(self.prompt_relpath).stem}

    def decide(
        self,
        *,
        text: str,
        task: Mapping[str, Any],
        state: VisibleState,
        event: str,
    ) -> Dict[str, Any]:
        if self.mode == "random":
            return self._random_decision(state, event)
        payload = _parse_json_object(text)
        failure_class = str(payload.get("failure_class") or FailureClass.EXPLORER_FAILURE.value)
        try:
            FailureClass(failure_class)
        except ValueError:
            failure_class = FailureClass.EXPLORER_FAILURE.value
        reason = replace_secrets(str(payload.get("reason") or ""), self.secrets.sensitive_values())
        try:
            action = bind_critic_action(payload, state, source_role=ActorRole.CRITIC)
            accepted = True
            error = None
        except ProtocolError as exc:
            action = None
            accepted = False
            error = exc.to_dict()
        return {
            "role": ActorRole.CRITIC.value,
            "oracle_level": "O0",
            "mode": self.mode,
            "event": event,
            "failure_class": failure_class,
            "decision_stage": str(payload.get("decision_stage") or state.decision_stage.value),
            "reason": reason,
            "negative_constraints": list(payload.get("negative_constraints") or []),
            "accepted": accepted,
            "action": None if action is None else action.to_dict(),
            "error": error,
            "protocol_version": PROTOCOL_VERSION,
        }

    def _random_decision(self, state: VisibleState, event: str) -> Dict[str, Any]:
        legal = legal_recovery_actions(state)
        if not legal:
            return {
                "role": ActorRole.CRITIC.value,
                "oracle_level": "O0",
                "mode": "random",
                "event": event,
                "failure_class": FailureClass.ACTION_SPACE_FAILURE.value,
                "decision_stage": state.decision_stage.value,
                "reason": "no_legal_action",
                "negative_constraints": [],
                "accepted": False,
                "action": None,
                "error": {"message": "no legal critic action"},
                "protocol_version": PROTOCOL_VERSION,
            }
        action = self.rng.choice(legal)
        return {
            "role": ActorRole.CRITIC.value,
            "oracle_level": "O0",
            "mode": "random",
            "event": event,
            "failure_class": FailureClass.EXPLORER_FAILURE.value,
            "decision_stage": state.decision_stage.value,
            "reason": "random_legal_action_control",
            "negative_constraints": ["irrelevant_to_failure"],
            "accepted": True,
            "action": action.to_dict(),
            "error": None,
            "protocol_version": PROTOCOL_VERSION,
        }


def _public_task_record(task: Mapping[str, Any]):
    from .schemas import TaskRecord

    return TaskRecord(
        task_id=str(task["task_id"]),
        question=str(task["question"]),
        source_entities=list(task.get("source_entities") or []),
        source_entity_names=dict(task.get("source_entity_names") or task.get("topic_entity") or {}),
        task_split=str(task.get("discovery_layer") or task.get("task_split") or "discovery"),
        task_generator_version=str(task.get("task_generator_version") or "sp3-discovery-v1"),
        input_snapshot_id=str(task.get("input_snapshot_id") or "sp3"),
        logical_query="",
        answer_entity_ids=[],
        normalized_answers=[],
        witness_paths=[],
        task_validity="valid",
        oracle_version="none",
    )


def _allowed(task: Mapping[str, Any], state: VisibleState) -> List[str]:
    values = [str(task.get("question") or "")]
    values.extend(task.get("source_entities") or [])
    values.extend((task.get("source_entity_names") or {}).values())
    values.extend((task.get("topic_entity") or {}).values())
    values.extend(state.visible_entities)
    values.extend(state.frontier)
    values.extend(item.relation for item in state.visible_relations)
    for triple in state.observed_triples_or_summaries:
        values.extend(str(item) for item in triple.values())
    return [item for item in values if item]
