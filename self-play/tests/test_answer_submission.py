from __future__ import annotations

import unittest
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.answer_submission import submit_from_text
from sp_memory.pog_adapter import make_sp1_snapshot
from sp_memory.schemas import ActionType, FailureClass
from sp_memory.sp1_checks import enabled_adapter


class AnswerSubmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = enabled_adapter()
        self.snap = make_sp1_snapshot(
            observed_triples=[{"subject": "m.alice", "relation": "r", "object": "m.bob"}],
            frontier=["m.alice", "m.bob"],
            topic_entity={"m.alice": "Alice", "m.bob": "Bob"},
            entid_name={"m.alice": "Alice", "m.bob": "Bob"},
            name_entid={"Alice": "m.alice", "Bob": "m.bob"},
        )
        self.state = self.adapter.project_visible_state(self.snap)

    def test_observed_id_stop(self) -> None:
        result = submit_from_text(
            '{"R": "ok", "Answer": "m.bob", "Sufficient": "Yes"}', self.state, self.snap
        )
        self.assertEqual(result.status, "stop")
        self.assertEqual(result.action.action_type, ActionType.STOP)
        self.assertEqual(result.action.params["answer_candidates"], ["m.bob"])

    def test_unobserved_rejected(self) -> None:
        result = submit_from_text(
            '{"R": "guess", "Answer": "m.paris", "Sufficient": "Yes"}', self.state, self.snap
        )
        self.assertEqual(result.status, "failure")
        self.assertEqual(result.failure_class, FailureClass.ANSWER_EXTRACTION_FAILURE)
        self.assertIsNone(result.action)

    def test_continue(self) -> None:
        result = submit_from_text(
            '{"R": "more", "Answer": "", "Sufficient": "No"}', self.state, self.snap
        )
        self.assertEqual(result.status, "continue")
        self.assertEqual(result.action.action_type, ActionType.CONTINUE)

    def test_ambiguous_name(self) -> None:
        snap = make_sp1_snapshot(
            observed_triples=[
                {"subject": "m.alice", "relation": "r", "object": "m.bob"},
                {"subject": "m.alice", "relation": "r", "object": "m.twin"},
            ],
            frontier=["m.alice", "m.bob", "m.twin"],
            topic_entity={"m.alice": "Alice", "m.bob": "Bob", "m.twin": "Bob"},
            entid_name={"m.alice": "Alice", "m.bob": "Bob", "m.twin": "Bob"},
            name_entid={"Alice": "m.alice", "Bob": "m.bob"},
            enumerated_relations=[],
        )
        state = self.adapter.project_visible_state(snap)
        result = submit_from_text('{"R": "x", "Answer": "Bob", "Sufficient": "Yes"}', state, snap)
        self.assertEqual(result.status, "failure")
        self.assertEqual(result.error_code, "AMBIGUOUS_NAME")


if __name__ == "__main__":
    unittest.main()
