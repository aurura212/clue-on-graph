from __future__ import annotations

import unittest

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.errors import ProtocolError, ViolationCode
from sp_memory.paths import PROTOCOL_VERSION
from sp_memory.replay import make_env
from sp_memory.schemas import OfflineFeedback, OracleLevel
from sp_memory.visibility import (
    audit_object,
    count_sensitive_fields,
    project_actor_view,
    project_critic_view,
    project_verifier_view,
    render_actor_prompt,
)


class VisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = make_env()
        self.task = self.env.task
        self.state = self.env.visible_state()
        self.secrets = self.env.secrets()

    def test_actor_has_no_sensitive_fields(self) -> None:
        view = project_actor_view(self.task, self.state, self.secrets)
        self.assertEqual(count_sensitive_fields(view), 0)
        self.assertNotIn("answer_entity_ids", view["task"])
        prompt = render_actor_prompt(view, self.secrets)
        self.assertNotIn("e.paris", prompt)
        self.assertNotIn("Paris", prompt)
        self.assertNotIn(self.task.logical_query, prompt)

    def test_critic_o0(self) -> None:
        view = project_critic_view(self.task, self.state, self.secrets)
        self.assertEqual(view["oracle_level"], "O0")
        self.assertEqual(count_sensitive_fields(view), 0)

    def test_verifier_can_see_oracle(self) -> None:
        view = project_verifier_view(self.task, self.state, self.env.future_neighbors())
        self.assertEqual(view["oracle_level"], "O4")
        self.assertIn("e.paris", view["oracle"]["answer_entity_ids"])

    def test_injected_leaks_are_blocked(self) -> None:
        payloads = [
            {"answer_entity_ids": ["e.paris"]},
            {"meta": {"normalized_answers": ["Paris"]}},
            {"metadata": {"logical_query": self.task.logical_query}},
            {"note": "visit e.paris"},
            {"future_neighbors": ["e.hidden"]},
        ]
        for payload in payloads:
            with self.assertRaises(ProtocolError) as ctx:
                audit_object(payload, secrets=self.secrets, context="inject")
            self.assertEqual(ctx.exception.code, ViolationCode.ORACLE_LEAKAGE)

    def test_o4_cannot_be_offline_feedback(self) -> None:
        with self.assertRaises(ProtocolError) as ctx:
            OfflineFeedback.from_dict(
                {
                    "task_id": self.task.task_id,
                    "level": OracleLevel.O4.value,
                    "feedback_version": "x",
                    "payload": {"success": True},
                    "protocol_version": PROTOCOL_VERSION,
                }
            )
        self.assertEqual(ctx.exception.code, ViolationCode.ORACLE_LEAKAGE)

    def test_o1_feedback_ok(self) -> None:
        fb = OfflineFeedback.from_dict(
            {
                "task_id": self.task.task_id,
                "level": "O1",
                "feedback_version": "fb-o1-v1",
                "payload": {"success": True},
                "protocol_version": PROTOCOL_VERSION,
            }
        )
        self.assertEqual(fb.level, OracleLevel.O1)


if __name__ == "__main__":
    unittest.main()
