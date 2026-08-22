"""SP2-B LLM client wrapper. Secrets stay in the process environment."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Mapping, Optional

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, sha256_text
from .sp2a_guards import SECRET_VALUE_RE

PROMPT_VERSION = "sp2b_actor_v1"


class LlmClientError(ProtocolError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(ViolationCode.SCHEMA_ERROR, message, details)


def _redact(text: str) -> str:
    return SECRET_VALUE_RE.sub("<redacted>", text or "")


class LlmClient:
    """OpenAI-compatible chat wrapper with timeout, retry, cache, and summaries."""

    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        timeout_sec: float,
        max_retries: int,
        retry_backoff_sec: List[float],
        max_tokens: int,
        cache: Optional[Dict[str, Dict[str, Any]]] = None,
        replay: bool = False,
        transport: Any = None,
    ) -> None:
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.timeout_sec = float(timeout_sec)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_sec = [float(item) for item in retry_backoff_sec]
        self.max_tokens = int(max_tokens)
        self.cache = dict(cache or {})
        self.replay = bool(replay)
        self.transport = transport
        self.real_calls = 0
        self.replay_calls = 0
        self.records: List[Dict[str, Any]] = []

    @classmethod
    def from_config(cls, config: Mapping[str, Any], *, cache=None, replay: bool = False, transport=None) -> "LlmClient":
        llm = config["llm"]
        api_base = os.environ.get(str(llm.get("api_base_env") or "OPENAI_API_BASE")) or str(
            llm.get("default_api_base") or ""
        )
        if not api_base:
            raise LlmClientError("OPENAI_API_BASE is not set and no default_api_base is configured")
        return cls(
            model=str(llm["model"]),
            api_base=api_base,
            timeout_sec=float(llm.get("timeout_sec") or 60),
            max_retries=int(llm.get("max_retries") or 2),
            retry_backoff_sec=list(llm.get("retry_backoff_sec") or [1.0, 2.0]),
            max_tokens=int(llm.get("max_tokens") or config.get("max_length") or 4096),
            cache=cache,
            replay=replay,
            transport=transport,
        )

    def cache_key(self, prompt: str, *, temperature: float, max_tokens: Optional[int] = None) -> str:
        return canonical_hash(
            {
                "model": self.model,
                "temperature": temperature,
                "max_tokens": int(max_tokens or self.max_tokens),
                "prompt_hash": sha256_text(prompt),
                "prompt_version": PROMPT_VERSION,
            }
        )

    def complete(
        self,
        prompt: str,
        *,
        temperature: float,
        purpose: str,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        tokens_limit = int(max_tokens or self.max_tokens)
        key = self.cache_key(prompt, temperature=temperature, max_tokens=tokens_limit)
        prompt_hash = sha256_text(prompt)
        if key in self.cache:
            cached = dict(self.cache[key])
            cached["replay"] = True
            cached["real_call"] = False
            self.replay_calls += 1
            record = self._record(prompt, prompt_hash, temperature, tokens_limit, purpose, cached, replay=True)
            self.records.append(record)
            return cached
        if self.replay and self.transport is None:
            raise LlmClientError(
                "LLM replay required a cache hit",
                {"prompt_hash": prompt_hash, "purpose": purpose},
            )
        result = self._call_with_retry(prompt, temperature=temperature, max_tokens=tokens_limit)
        result["replay"] = False
        result["real_call"] = True
        result["prompt_hash"] = prompt_hash
        result["response_hash"] = sha256_text(str(result.get("text") or ""))
        result["model"] = self.model
        result["temperature"] = temperature
        result["purpose"] = purpose
        result["cache_key"] = key
        self.cache[key] = {
            "text": result["text"],
            "token_num": dict(result["token_num"]),
            "prompt_hash": prompt_hash,
            "response_hash": result["response_hash"],
            "model": self.model,
            "temperature": temperature,
            "purpose": purpose,
        }
        self.real_calls += 1
        record = self._record(prompt, prompt_hash, temperature, tokens_limit, purpose, result, replay=False)
        self.records.append(record)
        return result

    def _call_with_retry(self, prompt: str, *, temperature: float, max_tokens: int) -> Dict[str, Any]:
        attempts = 1 + self.max_retries
        last_error = ""
        for retry_index in range(attempts):
            if retry_index > 0:
                delay = self.retry_backoff_sec[retry_index - 1] if retry_index - 1 < len(self.retry_backoff_sec) else 0.0
                if delay > 0:
                    time.sleep(delay)
            try:
                if self.transport is not None:
                    return self.transport(prompt, temperature=temperature, max_tokens=max_tokens, model=self.model)
                return self._openai_call(prompt, temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:
                last_error = str(exc)
                continue
        raise LlmClientError(
            "LLM call failed after retries",
            {"error": _redact(last_error), "attempts": attempts},
        )

    def _openai_call(self, prompt: str, *, temperature: float, max_tokens: int) -> Dict[str, Any]:
        try:
            import openai
        except ImportError as exc:
            raise LlmClientError("openai package is not installed") from exc
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPEANI_API_KEYS") or ""
        if not api_key:
            raise LlmClientError("OPENAI_API_KEY is not set")
        client = openai.OpenAI(api_key=api_key, base_url=self.api_base, timeout=self.timeout_sec)
        completion = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are an AI assistant that helps people find information."},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            frequency_penalty=0,
            presence_penalty=0,
        )
        text = completion.choices[0].message.content or ""
        usage = completion.usage
        token_num = {
            "total": int(getattr(usage, "total_tokens", 0) or 0),
            "input": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output": int(getattr(usage, "completion_tokens", 0) or 0),
        }
        return {"text": text, "token_num": token_num}

    def _record(
        self,
        prompt: str,
        prompt_hash: str,
        temperature: float,
        max_tokens: int,
        purpose: str,
        result: Mapping[str, Any],
        *,
        replay: bool,
    ) -> Dict[str, Any]:
        text = str(result.get("text") or "")
        return {
            "prompt_hash": prompt_hash,
            "response_hash": str(result.get("response_hash") or sha256_text(text)),
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "purpose": purpose,
            "prompt_chars": len(prompt),
            "response_chars": len(text),
            "token_num": dict(result.get("token_num") or {}),
            "replay": replay,
            "real_call": not replay,
            "text_preview": _redact(text[:400]),
            "contains_secret": bool(SECRET_VALUE_RE.search(prompt) or SECRET_VALUE_RE.search(text)),
        }

    def export_cache(self) -> Dict[str, Any]:
        return {"prompt_version": PROMPT_VERSION, "model": self.model, "entries": dict(self.cache)}
