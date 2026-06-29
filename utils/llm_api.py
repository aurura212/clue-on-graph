"""Shared helpers for OpenAI-compatible LLM API calls."""

from typing import Any, Dict, Optional


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


def chat_completion(
    *,
    api_key: Optional[str],
    base_url: Optional[str],
    engine: str,
    messages,
    temperature,
    max_tokens,
) -> str:
    """Call an OpenAI-compatible chat API, supporting both openai>=1 and 0.28.x."""
    import openai

    completion_kwargs: Dict[str, Any] = {
        "model": engine,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "frequency_penalty": 0,
        "presence_penalty": 0,
    }
    completion_kwargs.update(get_chat_completion_extra_kwargs(engine))

    if hasattr(openai, "OpenAI"):
        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = openai.OpenAI(**client_kwargs)
        response = client.chat.completions.create(**completion_kwargs)
        return response.choices[0].message.content.strip()

    openai.api_key = api_key
    if base_url:
        openai.api_base = base_url

    legacy_kwargs = {
        key: value
        for key, value in completion_kwargs.items()
        if key in {"model", "messages", "temperature", "max_tokens", "frequency_penalty", "presence_penalty"}
    }
    response = openai.ChatCompletion.create(**legacy_kwargs)
    return response["choices"][0]["message"]["content"].strip()
