"""Read/write pretty JSONL records (multi-line JSON objects)."""

from __future__ import annotations

import json
from typing import Any, Iterator


DEFAULT_JSONL_INDENT = 2


def format_jsonl_record(obj: Any, indent: int = DEFAULT_JSONL_INDENT) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent) + "\n"


def append_jsonl_record(path: str, obj: Any, indent: int = DEFAULT_JSONL_INDENT) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(format_jsonl_record(obj, indent=indent))


def iter_jsonl_records(path: str) -> Iterator[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    if not text.strip():
        return

    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        obj, index = decoder.raw_decode(text, index)
        yield obj
