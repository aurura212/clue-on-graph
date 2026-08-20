"""Frozen evaluation slices for KG-memory experiments."""

from __future__ import annotations

import json
import os
from typing import Any


SLICE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_slices")
FROZEN_HARD150_V1 = os.path.join(SLICE_ROOT, "hard150_v1.json")
FROZEN_RANDOM150_V1 = os.path.join(SLICE_ROOT, "random150_v1.json")


def even_subsample(items: list[Any], k: int) -> list[Any]:
    n = len(items)
    if k <= 0:
        return []
    if k >= n:
        return list(items)
    if k == 1:
        return [items[0]]
    return [items[round(i * (n - 1) / (k - 1))] for i in range(k)]


def load_questions_file(path: str) -> tuple[str, list[str]]:
    text = (path or "").strip()
    if not text:
        return "", []
    if not os.path.isabs(text):
        here = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(here, text)
        text = candidate if os.path.isfile(candidate) else os.path.abspath(text)
    if not os.path.isfile(text):
        raise FileNotFoundError(f"questions_file not found: {text}")

    if text.endswith(".json"):
        with open(text, encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            slice_id = str(payload.get("slice_id") or os.path.splitext(os.path.basename(text))[0])
            raw = payload.get("questions") or payload.get("RawQuestions") or []
        elif isinstance(payload, list):
            slice_id = os.path.splitext(os.path.basename(text))[0]
            raw = payload
        else:
            raise ValueError(f"unsupported questions_file JSON: {text}")
        questions: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                q = str(item.get("RawQuestion") or item.get("question") or "").strip()
            else:
                q = str(item).strip()
            if q:
                questions.append(q)
        return slice_id, questions

    slice_id = os.path.splitext(os.path.basename(text))[0]
    questions = []
    with open(text, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                questions.append(line)
    return slice_id, questions


def format_eval_slice_tag(args: Any) -> str:
    path = str(getattr(args, "questions_file", "") or "").strip()
    if not path:
        return ""
    try:
        slice_id, _questions = load_questions_file(path)
    except (OSError, ValueError):
        slice_id = os.path.splitext(os.path.basename(path))[0]
    slice_id = slice_id.replace("_", "-")
    return f"slice-{slice_id}" if slice_id else ""
