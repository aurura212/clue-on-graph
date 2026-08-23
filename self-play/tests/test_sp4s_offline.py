from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.candidate_retrieval import CandidateRetriever
from sp_memory.paths import PROTOCOL_VERSION, Workspace
from sp_memory.promotion import PROMOTION_GATES
from sp_memory.replay import make_env
from sp_memory.same_state_cf import INAPPLICABLE, INVALID, run_same_state_pair
from sp_memory.sp4s_critic import extract_checkpoint_candidate, run_checkpoint_trajectory
from sp_memory.synthetic_tasks import env_for_task, split_contamination
from sp_memory.synthetic_tasks_sp4s import (
    SHARED_RELATIONS,
    build_shared_relation_snapshot,
    generate_tasks_from_snapshot,
    leakage_hits,
)


class Sp4sOfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = build_shared_relation_snapshot()
        self.generated = generate_tasks_from_snapshot(
            self.snapshot,
            seed=20260823,
            banned={"task_ids": set(), "questions": set(), "topics": set()},
        )

    def test_shared_relations_across_splits(self) -> None:
        rels = {rel for split in self.snapshot["components"].values() for rel in split["relations"]}
        self.assertEqual(rels, set(SHARED_RELATIONS))
        for split in self.snapshot["components"]:
            self.assertEqual(set(self.snapshot["components"][split]["relations"]), set(SHARED_RELATIONS))

    def test_entity_isolation_and_counts(self) -> None:
        self.assertGreaterEqual(len(self.generated["actor"]["discovery"]), 40)
        self.assertGreaterEqual(len(self.generated["actor"]["validation_v1"]), 20)
        self.assertGreaterEqual(len(self.generated["actor"]["validation_v2"]), 20)
        self.assertGreaterEqual(len(self.generated["actor"]["holdout"]), 20)
        result = split_contamination(self.generated["actor"], self.generated["oracle"])
        self.assertEqual(result["cross_count"], 0, result)

    def test_question_leakage_zero(self) -> None:
        names = self.snapshot["entity_names"]
        oracle_by_id = {}
        for rows in self.generated["oracle"].values():
            for row in rows:
                oracle_by_id[row["task_id"]] = row
        for rows in self.generated["actor"].values():
            for row in rows:
                oracle = oracle_by_id[row["task_id"]]
                hops = [(item["relation"], item["direction"]) for item in oracle["path_hops"]]
                hits = leakage_hits(row["question"], oracle["answer_entity_ids"], names, hops, row["source_entities"][0])
                self.assertEqual(hits, [], (row["task_id"], hits, row["question"]))

    def test_same_state_cf_separates_inapplicable(self) -> None:
        actor = self.generated["actor"]["discovery"][0]
        oracle = {item["task_id"]: item for item in self.generated["oracle"]["discovery"]}[actor["task_id"]]
        env = env_for_task(self.snapshot, actor, oracle)
        visible = env.visible_state().visible_relations[0]
        cand = {
            "experience_id": "ok",
            "replay_prefix": [],
            "decision_state_hash": env.visible_state().state_id,
            "recommendation": {
                "action_type": "EXPAND",
                "relation_pattern": visible.relation,
                "direction": visible.direction.value,
            },
        }
        ok = run_same_state_pair(env, candidate=cand, seed=1)
        self.assertNotEqual(ok["outcome"], INAPPLICABLE)
        env2 = env_for_task(self.snapshot, actor, oracle)
        missing = dict(cand)
        missing["recommendation"] = {
            "action_type": "EXPAND",
            "relation_pattern": "not.a.visible.relation",
            "direction": "tail",
        }
        missing["experience_id"] = "missing"
        bad = run_same_state_pair(env2, candidate=missing, seed=1)
        self.assertEqual(bad["outcome"], INAPPLICABLE)
        self.assertNotEqual(bad["outcome"], INVALID)

    def test_checkpoint_candidate_binds_decision_state(self) -> None:
        actor = self.generated["actor"]["discovery"][0]
        oracle = {item["task_id"]: item for item in self.generated["oracle"]["discovery"]}[actor["task_id"]]
        env = env_for_task(self.snapshot, actor, oracle)
        traj = run_checkpoint_trajectory(env, run_id="t", seed=11, temperature=0.3, critic_mode="o0")
        self.assertTrue(traj["complete"])
        cand = extract_checkpoint_candidate(traj, actor)
        if cand:
            self.assertIn("decision_state_hash", cand)
            self.assertIn("replay_prefix", cand)

    def test_promotion_gates_unchanged(self) -> None:
        self.assertEqual(PROMOTION_GATES["min_cf_states"], 5)
        self.assertEqual(PROMOTION_GATES["min_v1_triggers"], 5)
        self.assertEqual(PROMOTION_GATES["min_margin"], 0.20)
        self.assertEqual(PROMOTION_GATES["max_harm_rate"], 0.10)
        self.assertEqual(PROMOTION_GATES["audit_pass_rate"], 1.0)
        self.assertEqual(PROMOTION_GATES["min_discovery_tasks"], 3)
        self.assertEqual(PROMOTION_GATES["min_cost_drop"], 0.10)

    def test_memory_read_false_loads_nothing(self) -> None:
        retriever = CandidateRetriever([{"experience_id": "x", "trigger": {}, "recommendation": {}}], memory_read=False)
        self.assertFalse(retriever._loaded)
        got = retriever.retrieve(state=make_env().visible_state(), question_type="one_hop", answer_type="entity")
        self.assertEqual(got["fallback"], "memory_read_false")

    def test_multi_template_questions(self) -> None:
        questions = [row["question"] for row in self.generated["actor"]["discovery"]]
        self.assertGreaterEqual(len(set(questions)), 8)


if __name__ == "__main__":
    unittest.main()
