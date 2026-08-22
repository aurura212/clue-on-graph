"""Logical vs physical KG counters for SP2-A. Independent of original PoG call_num."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .errors import ProtocolError, ViolationCode
from .kg_sparql import PhysicalStatus


@dataclass
class CounterLedger:
    logical_actions: int = 0
    physical_requests: int = 0
    retries: int = 0
    successful_requests: int = 0
    empty_results: int = 0
    failed_requests: int = 0
    kg_calls: int = 0
    skipped_for_budget: int = 0
    skipped_invalid_action: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> Dict[str, int]:
        return {
            "logical_actions": self.logical_actions,
            "physical_requests": self.physical_requests,
            "retries": self.retries,
            "successful_requests": self.successful_requests,
            "empty_results": self.empty_results,
            "failed_requests": self.failed_requests,
            "kg_calls": self.kg_calls,
            "skipped_for_budget": self.skipped_for_budget,
            "skipped_invalid_action": self.skipped_invalid_action,
        }

    def record_invalid_action(self, *, task_id: str, action_type: str, message: str) -> None:
        self.skipped_invalid_action += 1
        self.events.append(
            {
                "kind": "invalid_action",
                "task_id": task_id,
                "action_type": action_type,
                "message": message,
                "physical_delta": 0,
                "logical_delta": 0,
            }
        )

    def record_budget_skip(self, *, task_id: str, remaining_kg: int) -> None:
        self.skipped_for_budget += 1
        self.events.append(
            {
                "kind": "budget_skip",
                "task_id": task_id,
                "remaining_kg": remaining_kg,
                "physical_delta": 0,
                "logical_delta": 0,
            }
        )

    def record_logical_with_exchanges(
        self,
        *,
        task_id: str,
        logical_action_id: str,
        statuses: List[PhysicalStatus],
    ) -> None:
        if not statuses:
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "logical KG action produced no physical requests")
        self.logical_actions += 1
        self.kg_calls += 1
        self.physical_requests += len(statuses)
        if len(statuses) > 1:
            self.retries += len(statuses) - 1
        final = statuses[-1]
        if final is PhysicalStatus.SUCCESS:
            self.successful_requests += 1
        elif final is PhysicalStatus.EMPTY:
            self.empty_results += 1
            self.successful_requests += 1
        else:
            self.failed_requests += 1
        self.events.append(
            {
                "kind": "logical_action",
                "task_id": task_id,
                "logical_action_id": logical_action_id,
                "physical_delta": len(statuses),
                "retry_delta": max(0, len(statuses) - 1),
                "logical_delta": 1,
                "final_status": final.value,
                "attempt_statuses": [item.value for item in statuses],
            }
        )

    def assert_invariants(self) -> None:
        if self.physical_requests < self.logical_actions:
            raise ProtocolError(
                ViolationCode.SCHEMA_ERROR,
                "physical requests cannot be fewer than logical KG actions that executed",
                self.snapshot(),
            )
        if self.retries != max(0, self.physical_requests - self.logical_actions - self._unfinished()):
            # retries are extra physical calls on the same logical action
            expected_retries = 0
            for event in self.events:
                if event.get("kind") == "logical_action":
                    expected_retries += int(event.get("retry_delta") or 0)
            if expected_retries != self.retries:
                raise ProtocolError(
                    ViolationCode.SCHEMA_ERROR,
                    "retry counter does not match physical-logical relationship",
                    {"expected_retries": expected_retries, "retries": self.retries},
                )

    def _unfinished(self) -> int:
        return 0

    def to_dict(self) -> Dict[str, Any]:
        self.assert_invariants()
        payload = self.snapshot()
        payload["events"] = list(self.events)
        return payload
