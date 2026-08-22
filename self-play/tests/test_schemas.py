from __future__ import annotations

import unittest

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.errors import ProtocolError, ViolationCode
from sp_memory.paths import PROTOCOL_VERSION
from sp_memory.replay import default_fixture_task, make_env
from sp_memory.schemas import Action, ActionType, ActorRole, TaskRecord, VisibleState


class SchemaTests(unittest.TestCase):
    def test_task_round_trip(self) -> None:
        task = default_fixture_task()
        restored = TaskRecord.from_dict(task.to_dict())
        self.assertEqual(restored.to_dict(), task.to_dict())

    def test_visible_state_round_trip(self) -> None:
        state = make_env().visible_state()
        restored = VisibleState.from_dict(state.to_dict())
        self.assertEqual(restored.to_dict(), state.to_dict())

    def test_missing_field(self) -> None:
        payload = default_fixture_task().to_dict()
        del payload["question"]
        with self.assertRaises(ProtocolError) as ctx:
            TaskRecord.from_dict(payload)
        self.assertEqual(ctx.exception.code, ViolationCode.SCHEMA_ERROR)

    def test_unknown_version(self) -> None:
        payload = default_fixture_task().to_dict()
        payload["protocol_version"] = "v0"
        with self.assertRaises(ProtocolError) as ctx:
            TaskRecord.from_dict(payload)
        self.assertEqual(ctx.exception.code, ViolationCode.SCHEMA_VERSION_MISMATCH)

    def test_bad_type(self) -> None:
        payload = default_fixture_task().to_dict()
        payload["source_entities"] = "e.alice"
        with self.assertRaises(ProtocolError) as ctx:
            TaskRecord.from_dict(payload)
        self.assertEqual(ctx.exception.code, ViolationCode.SCHEMA_ERROR)

    def test_illegal_enum(self) -> None:
        state = make_env().visible_state()
        payload = {
            "action_id": "x",
            "action_type": "FLY",
            "params": {},
            "source_role": ActorRole.EXPLORER.value,
            "state_id": state.state_id,
            "protocol_version": PROTOCOL_VERSION,
        }
        with self.assertRaises(ProtocolError) as ctx:
            Action.from_dict(payload)
        self.assertEqual(ctx.exception.code, ViolationCode.SCHEMA_ERROR)

    def test_forbidden_visible_field(self) -> None:
        payload = make_env().visible_state().to_dict()
        payload["gold_sparql"] = "SELECT ?x"
        with self.assertRaises(ProtocolError) as ctx:
            VisibleState.from_dict(payload)
        self.assertIn(ctx.exception.code, {ViolationCode.ORACLE_LEAKAGE, ViolationCode.SCHEMA_ERROR})


if __name__ == "__main__":
    unittest.main()
