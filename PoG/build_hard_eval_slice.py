#!/usr/bin/env python3
"""Build the frozen hard150 eval slice from original PoG and relation+decomp memory errors."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from eval_run import evaluate_run_results
from eval_slices import FROZEN_HARD150_V1, SLICE_ROOT, even_subsample
from utils import prepare_dataset

ORIG_POG = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "PoG-main", "PoG", "PoG_webqsp_gpt-3.5-turbo-0125.jsonl")
)
REL_DECOMP = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "result",
        "webqsp_gpt-3.5-turbo-0125_mem-prompt_top2_hybrid_stages-relation_decompmem-prompt_top2_n1639_20260814_032036",
        "results.jsonl",
    )
)
SLICE_ID = "hard150_v1"
TARGET_N = 150


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    datas, question_string = prepare_dataset("webqsp")
    index_of = {row[question_string]: i for i, row in enumerate(datas)}
    m_orig = evaluate_run_results("webqsp", ORIG_POG)
    m_mem = evaluate_run_results("webqsp", REL_DECOMP)
    orig_err = set(m_orig["error_questions"])
    mem_err = set(m_mem["error_questions"])
    both = sorted(orig_err & mem_err, key=lambda q: index_of[q])
    selected_questions = even_subsample(both, TARGET_N)
    prefix150 = {row[question_string] for row in datas[:150]}
    records = []
    for q in selected_questions:
        records.append(
            {
                "index": index_of[q],
                "RawQuestion": q,
                "error_in": ["pog_orig", "rel_decomp"],
            }
        )
    payload = {
        "slice_id": SLICE_ID,
        "n": len(records),
        "dataset": "webqsp",
        "split": "test",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "selection": {
            "rule": "both_systems_wrong_even_subsample",
            "target_n": TARGET_N,
            "description": (
                "Questions that original PoG and the relation+decomposition memory run both got wrong "
                "(exact match), ordered by WebQSP test index, then evenly subsampled to 150. "
                "Not the first 150 test questions."
            ),
            "sources": {
                "pog_orig": {
                    "path": ORIG_POG,
                    "total": m_orig["total"],
                    "exact_match": m_orig["exact_match"],
                    "n_wrong": m_orig["wrong"],
                },
                "rel_decomp": {
                    "path": REL_DECOMP,
                    "total": m_mem["total"],
                    "exact_match": m_mem["exact_match"],
                    "n_wrong": m_mem["wrong"],
                },
            },
            "n_pog_only": len(orig_err - mem_err),
            "n_mem_only": len(mem_err - orig_err),
            "n_both_wrong": len(both),
            "n_union_wrong": len(orig_err | mem_err),
            "n_overlap_prefix150": len(set(selected_questions) & prefix150),
        },
        "questions": records,
    }
    os.makedirs(SLICE_ROOT, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    payload["questions_sha256"] = sha256_text("\n".join(selected_questions) + "\n")
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(FROZEN_HARD150_V1, "w", encoding="utf-8") as f:
        f.write(body)
    txt_path = os.path.join(SLICE_ROOT, "hard150_v1.questions.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(selected_questions) + "\n")
    print(f"wrote {FROZEN_HARD150_V1}")
    print(f"wrote {txt_path}")
    print(
        f"n={len(records)} both_wrong={len(both)} overlap_prefix150="
        f"{payload['selection']['n_overlap_prefix150']} "
        f"index_min={records[0]['index']} index_max={records[-1]['index']} "
        f"sha256={payload['questions_sha256'][:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
