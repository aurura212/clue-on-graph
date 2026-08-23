from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
_ROOT = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.action_protocol import ProtocolSession, parse_action
from sp_memory.candidate_retrieval import CandidateRetriever
from sp_memory.counterfactual_runner import run_pair
from sp_memory.critic_context import build_compressed_critic_input, schema_fallback_decision
from sp_memory.distiller import distill_rules
from sp_memory.errors import ProtocolError, ViolationCode
from sp_memory.hashing import sha256_file
from sp_memory.paths import PROTOCOL_VERSION, Workspace
from sp_memory.promotion import evaluate_rule, promote_rules
from sp_memory.replay import make_env
from sp_memory.schemas import ActorRole
from sp_memory.sp4_io import atomic_write_text, not_generated
from sp_memory.sp4_schemas import validate_task_record
from sp_memory.synthetic_tasks import (
    build_fixture_snapshot,
    generate_tasks_from_snapshot,
    local_graph_from_snapshot,
    split_contamination,
)
from sp_memory.visibility import project_actor_view


class Sp4OfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = build_fixture_snapshot()
        self.generated = generate_tasks_from_snapshot(self.snapshot, seed=20260823, banned={"task_ids": set(), "questions": set(), "topics": set()})

    def test_path_and_task_dedup_and_counts(self) -> None:
        for split, rows in self.generated["actor"].items():
            self.assertGreaterEqual(len(rows), 8, split)
            ids = [item["task_id"] for item in rows]
            self.assertEqual(len(ids), len(set(ids)))
            questions = [item["question_hash"] for item in rows]
            self.assertEqual(len(questions), len(set(questions)))

    def test_split_contamination_zero(self) -> None:
        result = split_contamination(self.generated["actor"], self.generated["oracle"])
        self.assertEqual(result["cross_count"], 0, result)

    def test_answer_and_path_not_in_question(self) -> None:
        names = self.snapshot["entity_names"]
        oracle_by_id = {}
        for split, rows in self.generated["oracle"].items():
            for row in rows:
                oracle_by_id[row["task_id"]] = row
        for split, rows in self.generated["actor"].items():
            for row in rows:
                oracle = oracle_by_id[row["task_id"]]
                q = row["question"]
                for answer in oracle["answer_entity_ids"]:
                    self.assertNotIn(answer, q)
                    self.assertNotIn(names[answer], q)
                for hop in oracle["path_hops"]:
                    self.assertNotIn(hop["relation"], q)
                self.assertNotIn("gold_path", q)
                validate_task_record(row, actor_only=True)

    def test_oracle_projection_actor_hides_answers(self) -> None:
        from sp_memory.synthetic_tasks import actor_to_task_record, env_for_task

        actor = self.generated["actor"]["discovery"][0]
        oracle = self.generated["oracle"]["discovery"][0]
        env = env_for_task(self.snapshot, actor, oracle)
        view = project_actor_view(env.task, env.visible_state(), secrets=env.secrets())
        blob = json.dumps(view)
        self.assertNotIn(oracle["answer_entity_ids"][0], blob)
        self.assertNotIn("witness_paths", blob)

    def test_critic_schema_fallback_and_compression(self) -> None:
        env = make_env()
        fallback = schema_fallback_decision("schema_error")
        self.assertEqual(fallback["action_type"], "ABSTAIN")
        compressed = build_compressed_critic_input(
            event="budget_critical",
            task_public={"task_id": "t", "question": "q"},
            state=env.visible_state(),
            legal_actions=[{"action_type": "EXPAND", "params": {"entity": "e.alice"}}] * 80,
            secrets=env.secrets(),
            char_budget=800,
        )
        self.assertLessEqual(compressed["prompt_chars"], 800)
        self.assertTrue(compressed["compressed"])

    def test_backtrack_rejects_invisible(self) -> None:
        session = ProtocolSession(make_env())
        state = session.visible_state()
        with self.assertRaises(ProtocolError) as ctx:
            parse_action({"action_type": "BACKTRACK", "entity_or_state": "e.hidden"}, state, source_role=ActorRole.EXPLORER)
        self.assertEqual(ctx.exception.code, ViolationCode.INVALID_BACKTRACK_TARGET)

    def test_backtrack_restores_observed_state(self) -> None:
        session = ProtocolSession(make_env())
        first = session.visible_state().state_id
        expand = parse_action(
            {"action_type": "EXPAND", "entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
            session.visible_state(),
        )
        session.execute(expand)
        self.assertIn("e.bob", session.visible_state().visible_entities)
        bt = parse_action({"action_type": "BACKTRACK", "entity_or_state": "state:" + first}, session.visible_state())
        result = session.execute(bt)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["visible_result"]["mode"], "observed_state")

    def test_memory_read_false_does_not_load(self) -> None:
        retriever = CandidateRetriever([{"experience_id": "x", "trigger": {}, "recommendation": {}}], memory_read=False)
        self.assertFalse(retriever._loaded)
        self.assertEqual(retriever.candidates, [])
        got = retriever.retrieve(state=make_env().visible_state(), question_type="one_hop", answer_type="entity")
        self.assertEqual(got["fallback"], "memory_read_false")

    def test_conflict_match_fallback(self) -> None:
        state = make_env().visible_state()
        twin = {
            "experience_id": "a",
            "trigger": {"decision_stage": state.decision_stage.value, "question_type": "one_hop", "state_signature": "nope"},
            "recommendation": {"action_type": "CONTINUE"},
        }
        other = dict(twin)
        other["experience_id"] = "b"
        retriever = CandidateRetriever([twin, other], memory_read=True, min_score=0.2)
        got = retriever.retrieve(state=state, question_type="one_hop", answer_type="entity")
        self.assertEqual(got["fallback"], "conflict_match")

    def test_same_state_replay_and_random_legal(self) -> None:
        from sp_memory.synthetic_tasks import env_for_task

        actor = self.generated["actor"]["discovery"][0]
        oracle = {item["task_id"]: item for item in self.generated["oracle"]["discovery"]}[actor["task_id"]]
        env = env_for_task(self.snapshot, actor, oracle)
        cand = {
            "experience_id": "demo",
            "recommendation": {
                "action_type": "EXPAND",
                "relation_pattern": env.visible_state().visible_relations[0].relation,
                "direction": env.visible_state().visible_relations[0].direction.value,
            },
        }
        a = run_pair(env, candidate=cand, seed=1)
        b = run_pair(env, candidate=cand, seed=1)
        self.assertEqual(a["state_hash"], b["state_hash"])
        self.assertEqual(a["outcome"], b["outcome"])
        self.assertIn(a["cf2"]["action"]["action_type"], {"EXPAND", "CONTINUE", "SELECT_FRONTIER", "ABSTAIN"})

    def test_distill_strips_entities_and_answers(self) -> None:
        candidates = [
            {
                "experience_id": "c1",
                "source_task_ids": ["t1", "t2", "t3"],
                "discovery_method": "o0_critic",
                "task_id": "t1",
                "trigger": {"decision_stage": "relation_selection", "question_type": "2hop", "failure_class": "explorer_failure", "state_signature": "sig-1"},
                "recommendation": {
                    "action_type": "EXPAND",
                    "relation_pattern": "people.person.friend",
                    "direction": "tail",
                    "reason": "If a personal link remains visible after an empty expansion, prefer another legal EXPAND.",
                    "negative_constraints": ["do_not_repeat_empty_relation"],
                    "budget_condition": "high",
                },
            }
        ]
        cf = [{"candidate_id": "c1", "pair_id": "p1", "outcome": "win"} for _ in range(5)]
        rules = distill_rules(candidates, cf)
        self.assertTrue(rules)
        blob = json.dumps(rules)
        self.assertNotIn("m.02mjmr", blob)
        self.assertNotIn("gold_path", blob)

    def test_promotion_boundaries(self) -> None:
        rule = {
            "schema_version": "sp4-memory-rule-v1",
            "protocol_version": PROTOCOL_VERSION,
            "rule_id": "r1",
            "rule_version": "v2",
            "decision_stage": "relation_selection",
            "abstract_state": {},
            "action_policy": {"recommended_action": "EXPAND"},
            "applicability": {},
            "support": {"n_tasks": 3, "n_candidates": 3, "task_ids": ["a", "b", "c"], "discovery_methods": ["o0_critic"]},
            "statistics": {"n_cf": 5, "win_rate": 0.5, "harm_rate": 0.0, "invalid_rate": 0.0, "tie_rate": 0.5},
            "source_hashes": {},
            "status": "deferred",
        }
        v1 = {"n_triggered": 5, "success_rate": 0.4, "baseline_success_rate": 0.3, "mean_cost": 1.0, "baseline_mean_cost": 1.0, "baseline_invalid_rate": 1.0}
        promoted = evaluate_rule(rule, audit_pass_rate=1.0, v1=v1, g3_or_sham_better=False, config_frozen=True)
        self.assertEqual(promoted["status"], "promoted")
        held = evaluate_rule(rule, audit_pass_rate=1.0, v1={**v1, "n_triggered": 1}, g3_or_sham_better=False, config_frozen=True)
        self.assertNotEqual(held["status"], "promoted")
        harmful = dict(rule)
        harmful["statistics"] = {"n_cf": 5, "win_rate": 0.0, "harm_rate": 0.4, "invalid_rate": 0.0, "tie_rate": 0.6}
        decided = evaluate_rule(harmful, audit_pass_rate=1.0, v1=v1, g3_or_sham_better=False, config_frozen=True)
        self.assertEqual(decided["status"], "rejected_harmful")

    def test_atomic_write_and_not_generated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.for_tests(Path(tmp) / "self-play", Path(tmp) / "data", Path(tmp) / "cope_alias", Path(tmp) / "PoG")
            target = ws.artifacts_root / "ok.json"
            atomic_write_text(ws, target, "{\"ok\":true}\n")
            self.assertTrue(target.exists())
            record = not_generated("artifacts/memory/promoted_memory_v2.jsonl", "no promoted rules")
            self.assertEqual(record["status"], "NOT_GENERATED")
            with self.assertRaises(Exception):
                ws.assert_writable(ws.data_root / "leak.txt")

    def test_fail_closed_split_hash(self) -> None:
        from sp_memory.errors import ProtocolError
        from sp_memory.synthetic_tasks import verify_synthetic

        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.for_tests(Path(tmp) / "self-play")
            with self.assertRaises(ProtocolError):
                verify_synthetic(ws, {})


if __name__ == "__main__":
    unittest.main()
