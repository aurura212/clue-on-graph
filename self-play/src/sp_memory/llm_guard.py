"""Fail-fast guard that proves SP1 adapter paths never call a real LLM."""

from __future__ import annotations

from typing import Any, List

from .errors import ProtocolError, ViolationCode


class LLMCallGuard:
    """Replace run_llm. Any invocation is a protocol/system failure."""

    def __init__(self) -> None:
        self.calls = 0
        self.call_args: List[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        self.call_args.append({"args": args, "kwargs": {k: "<omitted>" for k in kwargs}})
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            "SP1 forbids real LLM calls; run_llm was invoked",
            {"call_count": self.calls},
        )


def install_run_llm_guard(module: Any, guard: LLMCallGuard | None = None) -> LLMCallGuard:
    guard = guard or LLMCallGuard()
    if hasattr(module, "run_llm"):
        setattr(module, "run_llm", guard)
    return guard
