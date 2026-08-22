from __future__ import annotations

import json
import unittest
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.question_normalization import normalize_question, normalized_question_hash, QUESTION_NORMALIZATION_VERSION


class QuestionNormalizationTests(unittest.TestCase):
    def test_vectors(self) -> None:
        path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sp1" / "normalization_vectors.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["algorithm_version"], QUESTION_NORMALIZATION_VERSION)
        for item in payload["vectors"]:
            self.assertEqual(normalize_question(item["raw"]), item["expected_normalized"])
            self.assertEqual(normalized_question_hash(item["raw"]), item["expected_sha256"])

    def test_punctuation_kept(self) -> None:
        self.assertEqual(normalize_question("Hello, world!"), "hello, world!")


if __name__ == "__main__":
    unittest.main()
