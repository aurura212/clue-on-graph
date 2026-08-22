"""Validate legal actions against the current visible state and remaining budget."""

from __future__ import annotations

from typing import Optional, Set, Tuple

from .errors import ProtocolError, ViolationCode
from .paths import PROTOCOL_VERSION
from .schemas import (
    Action,
    ActionType,
    AbstainReason,
    Direction,
    VisibleRelation,
    VisibleState,
)

BACKTRACK_PREFIX = "state:"


def _visible_entity_set(state: VisibleState) -> Set[str]:
    return set(state.visible_entities) | set(state.frontier)


def _relation_keys(state: VisibleState) -> Set[Tuple[str, str, str]]:
    return {item.key() for item in state.visible_relations}


def _observed_entities(state: VisibleState) -> Set[str]:
    observed = set(state.visible_entities)
    for triple in state.observed_triples_or_summaries:
        for key in ("head", "tail", "entity"):
            if key in triple:
                observed.add(triple[key])
    return observed


def check_budget(state: VisibleState, extra_kg: int = 0, extra_llm: int = 0, extra_critic: int = 0) -> None:
    remaining = state.remaining_budget
    required = {
        "steps": 1,
        "kg_calls": extra_kg,
        "llm_calls": extra_llm,
        "critic_rounds": extra_critic,
    }
    for name, cost in required.items():
        left = remaining.get(name, 0)
        if left < cost:
            raise ProtocolError(
                ViolationCode.BUDGET_EXCEEDED,
                f"budget {name} remaining={left} cost={cost}",
                {"budget": name, "remaining": left, "cost": cost},
            )
    if remaining.get("depth", 1) < 0:
        raise ProtocolError(ViolationCode.BUDGET_EXCEEDED, "depth budget exhausted")
    if remaining.get("frontier_size", 0) < 0:
        raise ProtocolError(ViolationCode.BUDGET_EXCEEDED, "frontier size budget exhausted")


def validate_action(action: Action, state: VisibleState) -> None:
    if action.protocol_version != PROTOCOL_VERSION or state.protocol_version != PROTOCOL_VERSION:
        raise ProtocolError(
            ViolationCode.SCHEMA_VERSION_MISMATCH,
            "action/state protocol version mismatch",
            {
                "action_version": action.protocol_version,
                "state_version": state.protocol_version,
                "expected": PROTOCOL_VERSION,
            },
        )
    if action.state_id != state.state_id:
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            "action.state_id does not match visible state",
            {"action_state_id": action.state_id, "state_id": state.state_id},
        )
    if action.action_type not in ActionType:
        raise ProtocolError(ViolationCode.UNKNOWN_ACTION, f"unknown action {action.action_type}")

    extra_kg = 1 if action.action_type is ActionType.EXPAND else 0
    check_budget(state, extra_kg=extra_kg)

    visible_entities = _visible_entity_set(state)
    relation_keys = _relation_keys(state)
    observed = _observed_entities(state)

    if action.action_type is ActionType.EXPAND:
        entity = action.params.get("entity")
        relation = action.params.get("relation")
        direction = action.params.get("direction")
        if entity not in visible_entities:
            raise ProtocolError(ViolationCode.INVISIBLE_ENTITY, f"EXPAND entity not visible: {entity}")
        try:
            parsed_direction = Direction(direction)
        except ValueError as exc:
            raise ProtocolError(
                ViolationCode.INVALID_DIRECTION,
                f"EXPAND direction is invalid: {direction!r}",
            ) from exc
        if (entity, relation, parsed_direction.value) not in relation_keys:
            raise ProtocolError(
                ViolationCode.INVISIBLE_RELATION,
                "EXPAND relation/direction is not in the visible candidate set",
                {"entity": entity, "relation": relation, "direction": parsed_direction.value},
            )
        if remaining_frontier_would_overflow(state, new_items=1):
            raise ProtocolError(ViolationCode.BUDGET_EXCEEDED, "EXPAND would exceed frontier size")

    elif action.action_type is ActionType.SELECT_FRONTIER:
        entity = action.params.get("entity")
        if entity not in visible_entities:
            raise ProtocolError(ViolationCode.INVISIBLE_ENTITY, f"SELECT_FRONTIER entity not visible: {entity}")
        if entity not in state.frontier and entity not in state.visible_entities:
            raise ProtocolError(ViolationCode.INVISIBLE_ENTITY, f"SELECT_FRONTIER target not on frontier: {entity}")

    elif action.action_type is ActionType.BACKTRACK:
        target = action.params.get("entity_or_state")
        legal_states = {item for item in state.action_history_summary if item.startswith(BACKTRACK_PREFIX)}
        legal_states.add(BACKTRACK_PREFIX + state.state_id)
        if target in visible_entities:
            return
        if target in legal_states:
            return
        raise ProtocolError(
            ViolationCode.INVALID_BACKTRACK_TARGET,
            f"BACKTRACK target is not a visible entity or recorded state: {target}",
        )

    elif action.action_type is ActionType.CONTINUE:
        return

    elif action.action_type is ActionType.STOP:
        candidates = action.params.get("answer_candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProtocolError(ViolationCode.UNOBSERVED_ANSWER, "STOP requires observed answer candidates")
        for candidate in candidates:
            if candidate not in observed:
                raise ProtocolError(
                    ViolationCode.UNOBSERVED_ANSWER,
                    f"STOP candidate has not been observed: {candidate}",
                )

    elif action.action_type is ActionType.ABSTAIN:
        reason = action.params.get("reason_code")
        try:
            AbstainReason(reason)
        except ValueError as exc:
            raise ProtocolError(
                ViolationCode.INVALID_ABSTAIN_REASON,
                f"ABSTAIN reason_code is not predefined: {reason!r}",
            ) from exc

    else:
        raise ProtocolError(ViolationCode.UNKNOWN_ACTION, f"unhandled action {action.action_type}")


def remaining_frontier_would_overflow(state: VisibleState, new_items: int) -> bool:
    current = len(state.frontier)
    cap = state.remaining_budget.get("frontier_size")
    if cap is None:
        return False
    # remaining frontier_size is capacity left relative to max, already accounting used size.
    return cap < 0 or (current + new_items) > (current + cap)


def relation_from_action(action: Action) -> Optional[VisibleRelation]:
    if action.action_type is not ActionType.EXPAND:
        return None
    return VisibleRelation(
        entity=action.params["entity"],
        relation=action.params["relation"],
        direction=Direction(action.params["direction"]),
    )
