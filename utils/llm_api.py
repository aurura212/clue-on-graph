"""Shared helpers for OpenAI-compatible LLM API calls."""

from typing import Any, Dict


def is_openai_compatible_engine(engine: str) -> bool:
    """Return True when the model should use an OpenAI-compatible HTTP API."""
    engine_lower = engine.lower()
    return any(
        keyword in engine_lower
        for keyword in ("gpt", "deepseek", "qwen3-80b", "qwen3-next")
    )


def get_chat_completion_extra_kwargs(engine: str) -> Dict[str, Any]:
    """Return provider-specific kwargs for chat completion requests."""
    if "deepseek" in engine.lower():
        # DeepSeek V4 enables thinking by default; disable it for KGQA/PoG pipelines.
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {}
