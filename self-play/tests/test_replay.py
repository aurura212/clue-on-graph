from __future__ import annotations

import unittest

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.replay import (
    Budget,
    failure_builder,
    illegal_backtrack_builder,
    illegal_relation_builder,
    replay_times,
    run_scripted_trajectory,
    success_builder,
)
from sp_memory.visibility import count_sensitive_fields, project_actor_view, project_verifier_view


class ReplayTests(unittest.TestCase):
    def test_success_repeatable(self) -> None:
        records = replay_times(success_builder, n=3)
        hashes = {item.replay_hash for item in records}
        self.assertEqual(len(hashes), 1)
        self.assertTrue(records[0].ordered_steps[-1]["outcome"]["accepted"])

    def test_failure_repeatable(self) -> None:
        records = replay_times(failure_builder, n=3)
        self.assertEqual(len({item.replay_hash for item in records}), 1)

    def test_illegal_relation_localized(self) -> None:
        records = replay_times(illegal_relation_builder, n=3)
        for item in records:
            step = item.ordered_steps[0]["outcome"]
            self.assertFalse(step["accepted"])
            self.assertEqual(step["protocol_violation"], "INVISIBLE_RELATION")

    def test_illegal_backtrack_localized(self) -> None:
        records = replay_times(illegal_backtrack_builder, n=3)
        for item in records:
            step = item.ordered_steps[0]["outcome"]
            self.assertEqual(step["protocol_violation"], "INVALID_BACKTRACK_TARGET")

    def test_single_factor_changes_hash(self) -> None:
        base = replay_times(success_builder, n=1)[0].replay_hash
        failed = replay_times(failure_builder, n=1)[0].replay_hash
        snap = replay_times(success_builder, n=1, snapshot_id="other")[0].replay_hash
        tiny = Budget(
            max_depth=4,
            max_steps=1,
            max_kg_calls=16,
            max_llm_calls=8,
            max_critic_rounds=2,
            max_frontier_size=80,
        )
        budget = replay_times(success_builder, n=1, budget=tiny)[0].replay_hash
        self.assertEqual(len({base, failed, snap, budget}), 4)

    def test_views_after_success(self) -> None:
        env, traj = run_scripted_trajectory(success_builder)
        actor = project_actor_view(env.task, env.visible_state(), env.secrets())
        self.assertEqual(count_sensitive_fields(actor), 0)
        verifier = project_verifier_view(env.task, env.visible_state(), env.future_neighbors())
        self.assertIn("e.paris", verifier["oracle"]["answer_entity_ids"])
        self.assertTrue(traj.ordered_steps[-1]["outcome"]["oracle_eval"]["correct"])


if __name__ == "__main__":
    unittest.main()
