from __future__ import annotations

import unittest

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.action_validator import validate_action
from sp_memory.errors import ProtocolError, ViolationCode
from sp_memory.replay import Budget, make_env
from sp_memory.schemas import Action, ActionType, ActorRole


def _action(env, action_type, params, action_id="a"):
    return Action(
        action_id=action_id,
        action_type=action_type,
        params=params,
        source_role=ActorRole.EXPLORER,
        state_id=env.visible_state().state_id,
    )


class ActionValidatorTests(unittest.TestCase):
    def test_legal_expand(self) -> None:
        env = make_env()
        validate_action(
            _action(
                env,
                ActionType.EXPAND,
                {"entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
            ),
            env.visible_state(),
        )

    def test_invisible_entity(self) -> None:
        env = make_env()
        with self.assertRaises(ProtocolError) as ctx:
            validate_action(
                _action(
                    env,
                    ActionType.EXPAND,
                    {"entity": "e.hidden", "relation": "people.person.friend", "direction": "tail"},
                ),
                env.visible_state(),
            )
        self.assertEqual(ctx.exception.code, ViolationCode.INVISIBLE_ENTITY)

    def test_invisible_relation(self) -> None:
        env = make_env()
        with self.assertRaises(ProtocolError) as ctx:
            validate_action(
                _action(
                    env,
                    ActionType.EXPAND,
                    {"entity": "e.alice", "relation": "bogus", "direction": "tail"},
                ),
                env.visible_state(),
            )
        self.assertEqual(ctx.exception.code, ViolationCode.INVISIBLE_RELATION)

    def test_invalid_direction(self) -> None:
        env = make_env()
        with self.assertRaises(ProtocolError) as ctx:
            validate_action(
                _action(
                    env,
                    ActionType.EXPAND,
                    {"entity": "e.alice", "relation": "people.person.friend", "direction": "sideways"},
                ),
                env.visible_state(),
            )
        self.assertEqual(ctx.exception.code, ViolationCode.INVALID_DIRECTION)

    def test_unobserved_stop(self) -> None:
        env = make_env()
        with self.assertRaises(ProtocolError) as ctx:
            validate_action(
                _action(env, ActionType.STOP, {"answer_candidates": ["e.paris"]}),
                env.visible_state(),
            )
        self.assertEqual(ctx.exception.code, ViolationCode.UNOBSERVED_ANSWER)

    def test_invalid_backtrack(self) -> None:
        env = make_env()
        with self.assertRaises(ProtocolError) as ctx:
            validate_action(
                _action(env, ActionType.BACKTRACK, {"entity_or_state": "e.hidden"}),
                env.visible_state(),
            )
        self.assertEqual(ctx.exception.code, ViolationCode.INVALID_BACKTRACK_TARGET)

    def test_budget_exceeded(self) -> None:
        env = make_env(
            budget=Budget(
                max_depth=1,
                max_steps=1,
                max_kg_calls=0,
                max_llm_calls=0,
                max_critic_rounds=0,
                max_frontier_size=80,
            )
        )
        with self.assertRaises(ProtocolError) as ctx:
            validate_action(
                _action(
                    env,
                    ActionType.EXPAND,
                    {"entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
                ),
                env.visible_state(),
            )
        self.assertEqual(ctx.exception.code, ViolationCode.BUDGET_EXCEEDED)

    def test_version_mismatch(self) -> None:
        env = make_env()
        action = _action(env, ActionType.CONTINUE, {})
        action.protocol_version = "nope"
        with self.assertRaises(ProtocolError) as ctx:
            validate_action(action, env.visible_state())
        self.assertEqual(ctx.exception.code, ViolationCode.SCHEMA_VERSION_MISMATCH)


if __name__ == "__main__":
    unittest.main()
