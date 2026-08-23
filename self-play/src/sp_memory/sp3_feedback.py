"""Offline O1-O3 feedback generation. O4 never enters these payloads."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .errors import ProtocolError, ViolationCode
from .paths import PROTOCOL_VERSION
from .schemas import FORBIDDEN_VISIBLE_FIELDS, OfflineFeedback, OracleLevel
from .state_signature import abstract_relation
from .visibility import attach_offline_feedback


FEEDBACK_VERSION = "sp3-offline-feedback-v1"


def _reject_o4(payload: Mapping[str, Any]) -> Dict[str, Any]:
    leak = set(payload) & FORBIDDEN_VISIBLE_FIELDS
    if leak:
        raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, f"offline feedback has O4 fields {sorted(leak)}")
    return dict(payload)


def o1_from_result(result: Mapping[str, Any]) -> OfflineFeedback:
    success = result.get("failure_class") in (None, "none") and result.get("termination_reason") in {
        "STOP_SUBMITTED",
        "SUCCESS",
    }
    payload = _reject_o4(
        {
            "final_success": bool(success),
            "termination_reason": str(result.get("termination_reason") or ""),
            "failure_class": str(result.get("failure_class") or "none"),
            "pipeline_ok": bool(result.get("pipeline_ok")),
        }
    )
    return OfflineFeedback.from_dict(
        {
            "task_id": str(result["task_id"]),
            "level": OracleLevel.O1.value,
            "feedback_version": FEEDBACK_VERSION,
            "payload": payload,
            "protocol_version": PROTOCOL_VERSION,
        }
    )


def o2_from_result(result: Mapping[str, Any]) -> OfflineFeedback:
    critic_events = [item for item in result.get("trace") or [] if item.get("kind") == "critic"]
    payload = _reject_o4(
        {
            "failure_class": str(result.get("failure_class") or "none"),
            "stagnation_events": [
                str(item.get("event") or "") for item in result.get("trace") or [] if item.get("kind") == "critic"
            ],
            "repeat_expand": sum(
                1
                for item in result.get("trace") or []
                if item.get("kind") == "action" and (item.get("action") or {}).get("action_type") == "EXPAND"
            ),
            "critic_rounds": len(critic_events),
            "answer_extraction_failed": result.get("failure_class") == "answer_extraction_failure",
        }
    )
    return OfflineFeedback.from_dict(
        {
            "task_id": str(result["task_id"]),
            "level": OracleLevel.O2.value,
            "feedback_version": FEEDBACK_VERSION,
            "payload": payload,
            "protocol_version": PROTOCOL_VERSION,
        }
    )


def o3_from_result(result: Mapping[str, Any]) -> OfflineFeedback:
    compared = []
    for item in result.get("trace") or []:
        if item.get("kind") != "action":
            continue
        action = item.get("action") or {}
        params = action.get("params") or {}
        compared.append(
            {
                "action_type": action.get("action_type"),
                "direction": params.get("direction"),
                "relation_pattern": abstract_relation(str(params.get("relation") or "")),
                "accepted": bool(item.get("accepted")),
                "empty_or_rejected": (item.get("environment_status") in {None, "EMPTY", "empty"}) or not item.get("accepted"),
            }
        )
    payload = _reject_o4({"compared_actions": compared})
    return OfflineFeedback.from_dict(
        {
            "task_id": str(result["task_id"]),
            "level": OracleLevel.O3.value,
            "feedback_version": FEEDBACK_VERSION,
            "payload": payload,
            "protocol_version": PROTOCOL_VERSION,
        }
    )


def feedback_bundle(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = [o1_from_result(result), o2_from_result(result), o3_from_result(result)]
    return [attach_offline_feedback(item) for item in rows]


def teacher_input(result: Mapping[str, Any], public_task: Mapping[str, Any]) -> Dict[str, Any]:
    """O1-O3 only. Public task fields plus offline feedback; no O4."""
    bundle = feedback_bundle(result)
    payload = {
        "role": "oracle_guided_offline_teacher",
        "oracle_level": "O1-O3",
        "task": dict(public_task),
        "feedback": bundle,
        "failure_class": result.get("failure_class"),
        "termination_reason": result.get("termination_reason"),
        "protocol_version": PROTOCOL_VERSION,
    }
    leak = set(payload.get("task") or {}) & FORBIDDEN_VISIBLE_FIELDS
    if leak:
        raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, f"teacher task view has O4 fields {sorted(leak)}")
    return payload
