from __future__ import annotations

import unittest
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.environment_binding import (
    EnvironmentBinding,
    EnvironmentStatus,
    KgTimeout,
    direction_to_pog_head,
    expand_action_to_pog_params,
    pog_head_to_direction,
    triples_from_expand,
)
from sp_memory.schemas import Direction


class EnvironmentBindingTests(unittest.TestCase):
    def test_head_and_tail_triples(self) -> None:
        head = triples_from_expand("m.a", "r", Direction.HEAD, ["m.b"])
        tail = triples_from_expand("m.b", "r", Direction.TAIL, ["m.a"])
        self.assertEqual(head, [{"subject": "m.a", "relation": "r", "object": "m.b"}])
        self.assertEqual(tail, [{"subject": "m.a", "relation": "r", "object": "m.b"}])
        self.assertTrue(direction_to_pog_head(Direction.HEAD))
        self.assertFalse(direction_to_pog_head(Direction.TAIL))
        self.assertEqual(pog_head_to_direction(True), Direction.HEAD)

    def test_empty_vs_timeout(self) -> None:
        env = EnvironmentBinding()
        empty = env.expand("m.a", "r", Direction.HEAD, recorded=[])
        self.assertEqual(empty.status, EnvironmentStatus.EMPTY_SUCCESS)
        self.assertIsNone(empty.failure_class)
        timed = EnvironmentBinding(executor=lambda kind, **p: (_ for _ in ()).throw(KgTimeout("t")))
        result = timed.expand("m.a", "r", Direction.HEAD)
        self.assertEqual(result.status, EnvironmentStatus.TIMEOUT)
        self.assertIsNotNone(result.failure_class)

    def test_finish_id_dropped(self) -> None:
        env = EnvironmentBinding()
        result = env.expand("m.a", "r", Direction.HEAD, recorded=["[FINISH_ID]"])
        self.assertEqual(result.status, EnvironmentStatus.EMPTY_SUCCESS)
        self.assertEqual(result.results, [])

    def test_roundtrip_params(self) -> None:
        params = expand_action_to_pog_params({"entity": "m.a", "relation": "r", "direction": "head"})
        self.assertTrue(params["head"])
        back = pog_head_to_direction(params["head"])
        self.assertEqual(back, Direction.HEAD)

    def test_live_kg_forbidden(self) -> None:
        with self.assertRaises(Exception):
            EnvironmentBinding(allow_live_kg=True)


if __name__ == "__main__":
    unittest.main()
