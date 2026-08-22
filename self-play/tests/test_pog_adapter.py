from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.errors import ProtocolError, ViolationCode
from sp_memory.pog_adapter import PoGAdapter, make_sp1_snapshot, original_select_relations
from sp_memory.schemas import ActionType, DecisionStage
from sp_memory.sp1_checks import enabled_adapter, make_action


class PogAdapterTests(unittest.TestCase):
    def test_projection_sorts_and_drops_finish(self) -> None:
        adapter = enabled_adapter()
        snap = make_sp1_snapshot(
            frontier=["m.bob", "m.alice", "[FINISH_ID]"],
            observed_triples=[
                {"subject": "m.bob", "relation": "r", "object": "m.cara"},
                {"subject": "m.alice", "relation": "r", "object": "m.bob"},
            ],
        )
        state = adapter.project_visible_state(snap)
        self.assertEqual(state.visible_entities, ["m.alice", "m.bob", "m.cara"])
        self.assertNotIn("[FINISH_ID]", state.visible_entities)
        self.assertEqual(state.frontier, ["m.alice", "m.bob"])

    def test_missing_snapshot_field_rejected(self) -> None:
        adapter = enabled_adapter()
        with self.assertRaises(ProtocolError) as ctx:
            adapter.project_visible_state({"question": "q"})
        self.assertEqual(ctx.exception.code, ViolationCode.SCHEMA_ERROR)

    def test_disabled_passthrough(self) -> None:
        adapter = PoGAdapter(adapter_enabled=False)
        text = '["people.person.friend"]'
        original = original_select_relations(text, "m.x", ["people.person.friend"], [])
        wrapped = adapter.passthrough(original_select_relations, text, "m.x", ["people.person.friend"], [])
        self.assertEqual(original, wrapped)

    def test_enabled_passthrough_refused(self) -> None:
        adapter = enabled_adapter()
        with self.assertRaises(ProtocolError):
            adapter.passthrough(original_select_relations, "[]", "m.x", [], [])

    def test_shuffle_same_state_id(self) -> None:
        adapter = enabled_adapter()
        a = make_sp1_snapshot(frontier=["m.b", "m.a"], source_entities=["m.a", "m.b"])
        b = make_sp1_snapshot(frontier=["m.a", "m.b"], source_entities=["m.b", "m.a"])
        self.assertEqual(adapter.project_visible_state(a).state_id, adapter.project_visible_state(b).state_id)


if __name__ == "__main__":
    unittest.main()
