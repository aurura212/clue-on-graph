"""Independent STOP / CONTINUE / ABSTAIN mapping from prefabricated reasoning text."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash
from .paths import PROTOCOL_VERSION
from .pog_adapter import PoGSnapshot
from .schemas import (
    Action,
    ActionType,
    ActorRole,
    AbstainReason,
    FailureClass,
    VisibleState,
)

FINISH_MARKERS = {"[FINISH_ID]", "[FINISH]"}


@dataclass
class AnswerSubmissionResult:
    status: str
    action: Optional[Action]
    failure_class: Optional[FailureClass]
    error_code: Optional[str]
    message: str
    parsed: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "action": None if self.action is None else self.action.to_dict(),
            "failure_class": None if self.failure_class is None else self.failure_class.value,
            "error_code": self.error_code,
            "message": self.message,
            "parsed": dict(self.parsed),
            "protocol_version": PROTOCOL_VERSION,
        }


def parse_reasoning_text(text: str) -> Dict[str, Any]:
    """Independent parser. Prefabricated text only; does not call LLM."""
    if not isinstance(text, str) or not text.strip():
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            "reasoning text is empty",
        )
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
            try:
                answer = ast.literal_eval(answer_l.group(1))
            except (ValueError, SyntaxError) as exc:
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "malformed Answer list") from exc
        payload = {"Answer": answer, "R": reason.group(1), "Sufficient": sufficient.group(1)}
    if "Answer" not in payload or "Sufficient" not in payload:
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, "reasoning object missing Answer or Sufficient")
    return {
        "answer": payload.get("Answer"),
        "reason": payload.get("R") or payload.get("Reason"),
        "sufficient": payload.get("Sufficient"),
    }


def _as_candidate_list(answer: Any) -> List[str]:
    if answer is None:
        return []
    if isinstance(answer, str):
        text = answer.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except (ValueError, SyntaxError):
                return [text]
        return [text]
    if isinstance(answer, list):
        return [str(item).strip() for item in answer if str(item).strip()]
    return [str(answer).strip()]


def observed_values(state: VisibleState, snapshot: Optional[PoGSnapshot] = None) -> Set[str]:
    observed: Set[str] = set(state.visible_entities) | set(state.frontier)
    for triple in state.observed_triples_or_summaries:
        for key in ("subject", "object", "head", "tail", "entity"):
            value = triple.get(key)
            if isinstance(value, str) and value and value not in FINISH_MARKERS:
                observed.add(value)
    if snapshot is not None:
        for entity_id, name in snapshot.entid_name.items():
            if entity_id in observed and name:
                # names are mapping keys only; they become legal after mapping, not by themselves
                pass
    observed -= FINISH_MARKERS
    return observed


def _stable_unique(items: Sequence[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return sorted(out)


def map_candidates_to_observed(
    candidates: Sequence[str],
    state: VisibleState,
    snapshot: PoGSnapshot,
) -> Tuple[List[str], Optional[str], Optional[str]]:
    observed = observed_values(state, snapshot)
    name_to_ids: Dict[str, List[str]] = {}
    for entity_id, name in snapshot.entid_name.items():
        if entity_id in observed and name:
            name_to_ids.setdefault(name, []).append(entity_id)
    for name, entity_id in snapshot.name_entid.items():
        if entity_id in observed and name:
            name_to_ids.setdefault(name, [])
            if entity_id not in name_to_ids[name]:
                name_to_ids[name].append(entity_id)

    mapped: List[str] = []
    for candidate in candidates:
        if candidate in FINISH_MARKERS:
            return [], ViolationCode.UNOBSERVED_ANSWER.value, f"FINISH marker cannot be submitted: {candidate}"
        if candidate in observed:
            mapped.append(candidate)
            continue
        ids = name_to_ids.get(candidate, [])
        if len(ids) == 1:
            mapped.append(ids[0])
            continue
        if len(ids) > 1:
            return [], "AMBIGUOUS_NAME", f"name {candidate!r} maps to multiple observed IDs"
        return [], ViolationCode.UNOBSERVED_ANSWER.value, f"candidate has not been observed: {candidate}"
    if not mapped:
        return [], ViolationCode.UNOBSERVED_ANSWER.value, "STOP requires observed answer candidates"
    return _stable_unique(mapped), None, None


def submit_from_parsed(
    parsed: Mapping[str, Any],
    state: VisibleState,
    snapshot: PoGSnapshot,
    *,
    on_unresolvable: str = "failure",
) -> AnswerSubmissionResult:
    sufficient_raw = parsed.get("sufficient")
    sufficient = str(sufficient_raw or "").strip().lower()
    if sufficient in {"no", "false", "n"}:
        action = Action(
            action_id="continue-" + canonical_hash({"state": state.state_id})[:12],
            action_type=ActionType.CONTINUE,
            params={},
            source_role=ActorRole.EXPLORER,
            state_id=state.state_id,
        )
        return AnswerSubmissionResult(
            status="continue",
            action=action,
            failure_class=None,
            error_code=None,
            message="Sufficient=No maps to CONTINUE",
            parsed=dict(parsed),
        )
    if sufficient not in {"yes", "true", "y"}:
        return AnswerSubmissionResult(
            status="failure",
            action=None,
            failure_class=FailureClass.ANSWER_EXTRACTION_FAILURE,
            error_code="MALFORMED_SUFFICIENT",
            message=f"unrecognized Sufficient value: {sufficient_raw!r}",
            parsed=dict(parsed),
        )
    candidates = _as_candidate_list(parsed.get("answer"))
    if not candidates:
        if on_unresolvable == "abstain":
            action = Action(
                action_id="abstain-" + canonical_hash({"state": state.state_id})[:12],
                action_type=ActionType.ABSTAIN,
                params={"reason_code": AbstainReason.INSUFFICIENT_EVIDENCE.value},
                source_role=ActorRole.EXPLORER,
                state_id=state.state_id,
            )
            return AnswerSubmissionResult(
                status="abstain",
                action=action,
                failure_class=None,
                error_code=None,
                message="empty answer; caller requested ABSTAIN",
                parsed=dict(parsed),
            )
        return AnswerSubmissionResult(
            status="failure",
            action=None,
            failure_class=FailureClass.ANSWER_EXTRACTION_FAILURE,
            error_code=ViolationCode.UNOBSERVED_ANSWER.value,
            message="empty answer cannot construct STOP",
            parsed=dict(parsed),
        )
    mapped, error_code, message = map_candidates_to_observed(candidates, state, snapshot)
    if error_code:
        if on_unresolvable == "abstain" and error_code != "AMBIGUOUS_NAME":
            action = Action(
                action_id="abstain-" + canonical_hash({"state": state.state_id, "err": error_code})[:12],
                action_type=ActionType.ABSTAIN,
                params={"reason_code": AbstainReason.AMBIGUOUS.value if error_code == "AMBIGUOUS_NAME" else AbstainReason.INSUFFICIENT_EVIDENCE.value},
                source_role=ActorRole.EXPLORER,
                state_id=state.state_id,
            )
            return AnswerSubmissionResult(
                status="abstain",
                action=action,
                failure_class=None,
                error_code=error_code,
                message=message or "",
                parsed=dict(parsed),
            )
        return AnswerSubmissionResult(
            status="failure",
            action=None,
            failure_class=FailureClass.ANSWER_EXTRACTION_FAILURE,
            error_code=error_code,
            message=message or "answer mapping failed",
            parsed=dict(parsed),
        )
    action = Action(
        action_id="stop-" + canonical_hash({"answers": mapped, "state": state.state_id})[:12],
        action_type=ActionType.STOP,
        params={"answer_candidates": mapped},
        source_role=ActorRole.EXPLORER,
        state_id=state.state_id,
    )
    return AnswerSubmissionResult(
        status="stop",
        action=action,
        failure_class=None,
        error_code=None,
        message="ok",
        parsed=dict(parsed),
    )


def submit_from_text(
    text: str,
    state: VisibleState,
    snapshot: PoGSnapshot,
    *,
    on_unresolvable: str = "failure",
) -> AnswerSubmissionResult:
    try:
        parsed = parse_reasoning_text(text)
    except ProtocolError as exc:
        return AnswerSubmissionResult(
            status="failure",
            action=None,
            failure_class=FailureClass.ANSWER_EXTRACTION_FAILURE,
            error_code=exc.code.value,
            message=exc.message,
            parsed={},
        )
    return submit_from_parsed(parsed, state, snapshot, on_unresolvable=on_unresolvable)
