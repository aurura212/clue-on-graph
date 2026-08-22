"""Structured protocol errors. Illegal actions are rejected, never silently repaired."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class ViolationCode(str, Enum):
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    INVISIBLE_ENTITY = "INVISIBLE_ENTITY"
    INVISIBLE_RELATION = "INVISIBLE_RELATION"
    INVALID_DIRECTION = "INVALID_DIRECTION"
    INVALID_BACKTRACK_TARGET = "INVALID_BACKTRACK_TARGET"
    UNSUPPORTED_BACKTRACK_STATE = "UNSUPPORTED_BACKTRACK_STATE"
    UNOBSERVED_ANSWER = "UNOBSERVED_ANSWER"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    SCHEMA_VERSION_MISMATCH = "SCHEMA_VERSION_MISMATCH"
    ORACLE_LEAKAGE = "ORACLE_LEAKAGE"
    INVALID_ABSTAIN_REASON = "INVALID_ABSTAIN_REASON"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    WORKSPACE_BOUNDARY = "WORKSPACE_BOUNDARY"
    REGISTRY_ERROR = "REGISTRY_ERROR"
    SAMPLING_ERROR = "SAMPLING_ERROR"
    REPLAY_ERROR = "REPLAY_ERROR"


class ProtocolError(Exception):
    def __init__(
        self,
        code: ViolationCode | str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if isinstance(code, ViolationCode):
            self.code = code
        else:
            try:
                self.code = ViolationCode(code)
            except ValueError:
                self.code = ViolationCode.SCHEMA_ERROR
                details = dict(details or {})
                details["unclassified_code"] = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{self.code.value}: {message}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }
