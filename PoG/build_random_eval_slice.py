#!/usr/bin/env python3
"""Build the frozen random150 capability-eval slice from WebQSP test.

Does not read KG-memory runs or original-PoG / rel+decomp error sets.
Does not call LLMs.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import datetime, timezone

from eval_slices import FROZEN_HARD150_V1, FROZEN_RANDOM150_V1, SLICE_ROOT, load_questions_file
from utils import prepare_dataset

SLICE_ID = "random150_v1"
TARGET_N = 150
SLICE_SEED = 42


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    datas, question_string = prepare_dataset("webqsp")
    n_pool = len(datas)
    if n_pool < TARGET_N:
        raise SystemExit(f"WebQSP pool too small: {n_pool}")

    rng = random.Random(SLICE_SEED)
    sampled_indices = sorted(rng.sample(range(n_pool), TARGET_N))
    selected_questions = [str(datas[i][question_string]) for i in sampled_indices]
    records = [{"index": i, "RawQuestion": q} for i, q in zip(sampled_indices, selected_questions)]

    hard_id, hard_qs = load_questions_file(FROZEN_HARD150_V1)
    hard_set = set(hard_qs)
    prefix150 = {str(row[question_string]) for row in datas[:150]}
    selected_set = set(selected_questions)

    payload = {
        "slice_id": SLICE_ID,
        "role": "capability_eval",
        "v2_role": "final_unseen",
        "n": len(records),
        "dataset": "webqsp",
        "split": "test",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "selection": {
            "rule": "uniform_random_webqsp_test",
            "target_n": TARGET_N,
            "seed": SLICE_SEED,
            "pool_n": n_pool,
            "description": (
                "Uniform random sample of 150 questions from the full WebQSP test pool "
                f"(n={n_pool}), seed={SLICE_SEED}. Not difficulty-selected. "
                "Not WebQSP test[:150]. Overlap with hard150_v1 / prefix150 is reported, not removed."
            ),
            "n_overlap_hard150": len(selected_set & hard_set),
            "n_overlap_prefix150": len(selected_set & prefix150),
            "hard150_slice_id": hard_id,
            "sampled_indices": sampled_indices,
        },
        "questions": records,
    }
    payload["questions_sha256"] = sha256_text("\n".join(selected_questions) + "\n")
    os.makedirs(SLICE_ROOT, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(FROZEN_RANDOM150_V1, "w", encoding="utf-8") as f:
        f.write(body)
    txt_path = os.path.join(SLICE_ROOT, "random150_v1.questions.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(selected_questions) + "\n")
    print(f"wrote {FROZEN_RANDOM150_V1}")
    print(f"wrote {txt_path}")
    print(
        f"n={len(records)} pool={n_pool} seed={SLICE_SEED} "
        f"overlap_hard150={payload['selection']['n_overlap_hard150']} "
        f"overlap_prefix150={payload['selection']['n_overlap_prefix150']} "
        f"index_min={records[0]['index']} index_max={records[-1]['index']} "
        f"sha256={payload['questions_sha256'][:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
