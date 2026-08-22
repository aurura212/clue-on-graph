"""Parse original PoG LLM text into protocol Actions. Illegal output never reaches KG."""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .answer_submission import submit_from_parsed
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash
from .pog_adapter import original_select_relations
from .schemas import (
    Action,
    ActionType,
    ActorRole,
    AbstainReason,
    Direction,
    FailureClass,
    VisibleRelation,
    VisibleState,
)


def _extract_bracket_list(text: str) -> Optional[List[str]]:
    first = text.rfind("[")
    last = text.rfind("]")
    if first < 0 or last <= first:
        return None
    blob = text[first : last + 1]
    try:
        loaded = json.loads(blob)
        if isinstance(loaded, list):
            return [str(item) for item in loaded]
    except json.JSONDecodeError:
        pass
    try:
        loaded = ast.literal_eval(blob)
        if isinstance(loaded, list):
            return [str(item) for item in loaded]
    except (ValueError, SyntaxError):
        pass
    try:
        loaded = eval(blob.strip(), {"__builtins__": {}}, {})
        if isinstance(loaded, list):
            return [str(item) for item in loaded]
    except Exception:
        parts = blob.strip().strip("[").strip("]").split(", ")
        return [item.strip().strip("'").strip('"') for item in parts if item.strip()]
    return None


def parse_relation_list(text: str) -> Tuple[Optional[List[str]], Optional[str]]:
    parsed = _extract_bracket_list(text or "")
    if parsed is None:
        return None, "RELATION_LIST_PARSE_ERROR"
    return parsed, None


def relations_to_expand_actions(
    text: str,
    entity_id: str,
    head_relations: Sequence[str],
    tail_relations: Sequence[str],
    state: VisibleState,
) -> Dict[str, Any]:
    parsed, error = parse_relation_list(text)
    if error:
        return {
            "ok": False,
            "failure_class": FailureClass.ACTION_SPACE_FAILURE.value,
            "error_code": error,
            "actions": [],
            "rejected": [],
        }
    try:
        flag, mapped = original_select_relations(text, entity_id, list(head_relations), list(tail_relations))
    except Exception:
        flag, mapped = False, "No relations found"
    if not flag:
        return {
            "ok": False,
            "failure_class": FailureClass.ACTION_SPACE_FAILURE.value,
            "error_code": "NO_LEGAL_RELATIONS",
            "actions": [],
            "rejected": parsed or [],
            "message": str(mapped),
        }
    actions = []
    rejected = []
    visible = {(item.entity, item.relation, item.direction.value) for item in state.visible_relations}
    for item in mapped:
        direction = Direction.HEAD.value if item["head"] else Direction.TAIL.value
        key = (item["entity"], item["relation"], direction)
        action = Action(
            action_id="expand-" + canonical_hash(item)[:12],
            action_type=ActionType.EXPAND,
            params={"entity": item["entity"], "relation": item["relation"], "direction": direction},
            source_role=ActorRole.EXPLORER,
            state_id=state.state_id,
        )
        if key not in visible:
            rejected.append({"action": action.to_dict(), "reason": "not_in_visible_relations"})
            continue
        actions.append(action)
    return {
        "ok": bool(actions),
        "failure_class": None if actions else FailureClass.ACTION_SPACE_FAILURE.value,
        "error_code": None if actions else "NO_VISIBLE_EXPAND",
        "actions": actions,
        "rejected": rejected,
        "parsed": parsed,
    }


def flatten_reasoning_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if "Answer" in payload and "Sufficient" in payload:
        return {
            "answer": payload.get("Answer"),
            "reason": payload.get("R") or payload.get("Reason"),
            "sufficient": payload.get("Sufficient"),
        }
    nested = payload.get("A")
    if isinstance(nested, Mapping) and "Answer" in nested and "Sufficient" in nested:
        return {
            "answer": nested.get("Answer"),
            "reason": payload.get("R") or nested.get("R") or nested.get("Reason"),
            "sufficient": nested.get("Sufficient"),
        }
    raise ProtocolError(ViolationCode.SCHEMA_ERROR, "reasoning object missing Answer or Sufficient")


def parse_reasoning_object(text: str) -> Dict[str, Any]:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, "reasoning text has no JSON object")
    blob = text[first : last + 1]
    payload: Optional[Dict[str, Any]] = None
    try:
        loaded = json.loads(blob)
        if isinstance(loaded, dict):
            payload = loaded
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        try:
            loaded = ast.literal_eval(blob)
            if isinstance(loaded, dict):
                payload = loaded
        except (ValueError, SyntaxError):
            payload = None
    if payload is None:
        answer_q = re.search(r'"Answer":\s*"(.*?)"', blob)
        answer_l = re.search(r'"Answer":\s*(\[[^\]]+\])', blob)
        reason = re.search(r'"R":\s*"(.*?)"', blob)
        sufficient = re.search(r'"Sufficient":\s*"(.*?)"', blob)
        if reason is None or sufficient is None or (answer_q is None and answer_l is None):
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "malformed reasoning object")
        answer: Any
        if answer_q:
            answer = answer_q.group(1)
        else:
            answer = ast.literal_eval(answer_l.group(1))
        payload = {"Answer": answer, "R": reason.group(1), "Sufficient": sufficient.group(1)}
    return flatten_reasoning_payload(payload)


def map_stop_or_continue(text: str, state: VisibleState, snapshot, *, on_unresolvable: str = "failure"):
    try:
        parsed = parse_reasoning_object(text)
    except ProtocolError as exc:
        from .answer_submission import AnswerSubmissionResult

        return AnswerSubmissionResult(
            status="failure",
            action=None,
            failure_class=FailureClass.ANSWER_EXTRACTION_FAILURE,
            error_code=exc.code.value,
            message=exc.message,
            parsed={},
        )
    return submit_from_parsed(parsed, state, snapshot, on_unresolvable=on_unresolvable)


def parse_add_flag(text: str) -> Tuple[Optional[bool], Optional[str], Optional[str]]:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        return None, None, "ADD_PARSE_ERROR"
    blob = text[first : last + 1]
    flag = re.search(r'"Add":\s*"(.*?)"', blob)
    reason = re.search(r'"Reason":\s*"(.*?)"', blob)
    if flag is None or reason is None:
        return None, None, "ADD_PARSE_ERROR"
    value = flag.group(1)
    if "yes" in value.lower():
        return True, reason.group(1), None
    if "no" in value.lower():
        return False, reason.group(1), None
    return None, reason.group(1), "ADD_VALUE_UNRECOGNIZED"


def parse_entity_list(text: str, legal: Sequence[str]) -> Tuple[List[str], List[str]]:
    parsed = _extract_bracket_list(text or "") or []
    legal_set = set(legal)
    accepted = [item for item in parsed if item in legal_set]
    rejected = [item for item in parsed if item not in legal_set]
    return accepted, rejected


def unsupported_backtrack_action(state: VisibleState, target: str) -> Action:
    return Action(
        action_id="backtrack-" + canonical_hash({"target": target, "state": state.state_id})[:12],
        action_type=ActionType.BACKTRACK,
        params={"entity_or_state": target},
        source_role=ActorRole.EXPLORER,
        state_id=state.state_id,
    )


def select_frontier_action(state: VisibleState, entity: str) -> Action:
    return Action(
        action_id="frontier-" + canonical_hash({"entity": entity, "state": state.state_id})[:12],
        action_type=ActionType.SELECT_FRONTIER,
        params={"entity": entity},
        source_role=ActorRole.EXPLORER,
        state_id=state.state_id,
    )


def abstain_action(state: VisibleState, reason: AbstainReason) -> Action:
    return Action(
        action_id="abstain-" + canonical_hash({"state": state.state_id, "reason": reason.value})[:12],
        action_type=ActionType.ABSTAIN,
        params={"reason_code": reason.value},
        source_role=ActorRole.EXPLORER,
        state_id=state.state_id,
    )
