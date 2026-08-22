"""Frozen question normalization: sp1-question-normalization-v1."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict

from .hashing import sha256_text

QUESTION_NORMALIZATION_VERSION = "sp1-question-normalization-v1"


def normalize_question(text: str) -> str:
    """Apply the six-step SP1 algorithm. Do not stem, tokenize, or drop punctuation."""
    if not isinstance(text, str):
        raise TypeError(f"question text must be str, got {type(text).__name__}")
    nfkc = unicodedata.normalize("NFKC", text)
    folded = nfkc.casefold()
    collapsed = re.sub(r"\s+", " ", folded, flags=re.UNICODE)
    return collapsed.strip()


def normalized_question_hash(text: str) -> str:
    return sha256_text(normalize_question(text))


def normalize_question_record(text: str) -> Dict[str, Any]:
    normalized = normalize_question(text)
    return {
        "algorithm_version": QUESTION_NORMALIZATION_VERSION,
        "normalized_question": normalized,
        "normalized_question_hash": sha256_text(normalized),
    }
