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

from sp_memory.candidate_experience import (
    CandidateReadGuard,
    CandidateStore,
    audit_candidate,
    build_candidate,
    extract_from_trace,
)
from sp_memory.config import load_config
from sp_memory.critic import O0Critic, bind_critic_action, legal_recovery_actions
from sp_memory.errors import ProtocolError, ViolationCode
from sp_memory.paths import PROTOCOL_VERSION, Workspace
from sp_memory.replay import make_env
from sp_memory.schemas import (
    ActionType,
    ActorRole,
    CandidateExperience,
    DiscoveryMethod,
    OfflineFeedback,
    OracleLevel,
)
from sp_memory.sp2b_guards import public_task_view
from sp_memory.sp3_feedback import feedback_bundle, o1_from_result, teacher_input
from sp_memory.sp3_sampling import load_banned
from sp_memory.state_signature import experience_text_is_abstract, state_signature
from sp_memory.visibility import OracleSecrets


class Sp3OfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Workspace.from_this_package()
        self.env = make_env()
        self.state = self.env.visible_state()
        self.secrets = OracleSecrets(
            answer_entity_ids=["e.paris"],
            normalized_answers=["Paris"],
            witness_tokens=["e.alice -> e.bob -> e.paris"],
            logical_query="SELECT ?x WHERE { e.bob people.person.place_of_birth ?x }",
            future_neighbors=["e.hidden"],
        )

    def _candidate_kwargs(self, **overrides):
        payload = dict(
            source_run_id="sp3-test",
            source_task_ids=["sp3.d0.demo"],
            discovery_method=DiscoveryMethod.O0_CRITIC.value,
            question_type="two_hop",
            decision_stage="continue_stop",
            failure_class="explorer_failure",
            state=self.state,
            action_type=ActionType.CONTINUE.value,
            direction=None,
            relation_pattern=None,
            reason="If a two-hop question stopped after one visible hop and budget remains, prefer CONTINUE.",
            negative_constraints=["do_not_submit_after_single_hop"],
            budget_condition="steps=high",
            observed_outcome="ABSTAINED",
            verified_replay=True,
            prompt_version="sp3_critic_o0_v1",
            config_hash="abc",
            plan_version="SP3-PLAN 1.0",
            oracle_level=OracleLevel.O0.value,
            secrets=self.secrets,
        )
        payload.update(overrides)
        return payload

    def test_candidate_round_trip_and_fields(self) -> None:
        item = build_candidate(**self._candidate_kwargs())
        restored = CandidateExperience.from_dict(item)
        self.assertEqual(restored.status, "candidate")
        self.assertEqual(restored.evidence["counterfactual_status"], "deferred_to_sp4")
        self.assertTrue(restored.privacy["entity_ids_removed"])
        self.assertEqual(restored.to_dict()["canonical_hash"], item["canonical_hash"])

    def test_o4_candidate_rejected(self) -> None:
        with self.assertRaises(ProtocolError) as ctx:
            build_candidate(**self._candidate_kwargs(oracle_level="O4"))
        self.assertEqual(ctx.exception.code, ViolationCode.ORACLE_LEAKAGE)

    def test_answer_and_entity_removed(self) -> None:
        item = build_candidate(**self._candidate_kwargs(reason="The answer is Paris via m.02mjmr"))
        reason = item["recommendation"]["reason"]
        self.assertNotIn("Paris", reason)
        self.assertNotIn("m.02mjmr", reason)
        self.assertIn("entity_id", experience_text_is_abstract("use m.02mjmr next"))
        with self.assertRaises(ProtocolError):
            build_candidate(**self._candidate_kwargs(reason="follow the gold_path and witness"))

    def test_gold_path_key_rejected(self) -> None:
        item = build_candidate(**self._candidate_kwargs())
        item["recommendation"]["gold_path"] = ["a", "b"]
        with self.assertRaises(ProtocolError):
            CandidateExperience.from_dict(item)

    def test_store_dedup_and_append(self) -> None:
        path = self.workspace.artifacts_root / "candidates" / "_sp3_test_store.jsonl"
        if path.exists():
            path.unlink()
        reject = path.parent / "_sp3_test_rejection_log.jsonl"
        store = CandidateStore(self.workspace, path)
        store.rejected_path = reject
        first = build_candidate(**self._candidate_kwargs())
        a = store.append(first, secrets=self.secrets)
        b = store.append(first, secrets=self.secrets)
        self.assertTrue(a["accepted"])
        self.assertTrue(b["duplicate"])
        store2 = CandidateStore(self.workspace, path)
        self.assertEqual(len(store2.rows()), 1)
        path.unlink()
        if reject.exists():
            reject.unlink()

    def test_illegal_critic_backtrack(self) -> None:
        with self.assertRaises(ProtocolError) as ctx:
            bind_critic_action({"action_type": "BACKTRACK", "entity_or_state": "e.alice"}, self.state, source_role=ActorRole.CRITIC)
        self.assertEqual(ctx.exception.code, ViolationCode.UNSUPPORTED_BACKTRACK_STATE)

    def test_illegal_expand_blocked(self) -> None:
        with self.assertRaises(ProtocolError):
            bind_critic_action(
                {
                    "action_type": "EXPAND",
                    "entity": "e.alice",
                    "relation": "not.visible",
                    "direction": "tail",
                },
                self.state,
                source_role=ActorRole.CRITIC,
            )

    def test_random_critic_picks_legal_action(self) -> None:
        critic = O0Critic(
            self.workspace,
            self.secrets,
            prompt_relpath="prompts/sp3_critic_o0_v1.txt",
            mode="random",
            seed=1,
        )
        decision = critic.decide(text="", task={"task_id": "t", "question": "Where was Bob born?", "source_entities": ["e.alice"]}, state=self.state, event="early_stop")
        self.assertTrue(decision["accepted"])
        legal = {item.action_id for item in legal_recovery_actions(self.state)}
        self.assertIn(decision["action"]["action_id"], legal)

    def test_o0_views_reject_oracle(self) -> None:
        from sp_memory.schemas import TaskRecord
        from sp_memory.visibility import project_actor_view, project_critic_view

        public = TaskRecord(
            task_id="t",
            question="Where was Bob born?",
            source_entities=["e.alice"],
            source_entity_names={"e.alice": "Alice"},
            task_split="D0",
            task_generator_version="sp3-discovery-v1",
            input_snapshot_id="sp3",
            logical_query="",
            answer_entity_ids=[],
            normalized_answers=[],
            witness_paths=[],
            task_validity="valid",
            oracle_version="none",
        )
        project_actor_view(public, self.state, secrets=self.secrets)
        project_critic_view(public, self.state, secrets=self.secrets)
        leaky = dict(public.to_dict())
        leaky["normalized_answers"] = ["Paris"]
        view = public_task_view({"task_id": "t", "question": "q", "normalized_answers": ["Paris"], "logical_query": "SELECT"})
        self.assertNotIn("normalized_answers", view)
        self.assertNotIn("logical_query", view)

    def test_offline_feedback_rejects_o4(self) -> None:
        with self.assertRaises(ProtocolError):
            OfflineFeedback.from_dict(
                {
                    "task_id": "t",
                    "level": "O4",
                    "feedback_version": "x",
                    "payload": {"ok": True},
                    "protocol_version": PROTOCOL_VERSION,
                }
            )
        result = {
            "task_id": "t",
            "failure_class": "explorer_failure",
            "termination_reason": "ABSTAINED",
            "pipeline_ok": True,
            "trace": [{"kind": "action", "action": {"action_type": "EXPAND", "params": {"relation": "people.person.friend", "direction": "tail"}}, "accepted": True}],
        }
        bundle = feedback_bundle(result)
        self.assertEqual([row["level"] for row in bundle], ["O1", "O2", "O3"])
        teacher = teacher_input(result, {"task_id": "t", "question": "q"})
        self.assertEqual(teacher["oracle_level"], "O1-O3")
        self.assertNotIn("logical_query", teacher["task"])

    def test_candidate_read_guard(self) -> None:
        guard = CandidateReadGuard()
        with self.assertRaises(ProtocolError):
            guard.retrieve("two_hop")

    def test_extract_from_critic_trace(self) -> None:
        continue_action = bind_critic_action({"action_type": "CONTINUE"}, self.state, source_role=ActorRole.CRITIC)
        result = {
            "task_id": "sp3.d0.demo",
            "failure_class": "explorer_failure",
            "termination_reason": "ABSTAINED",
            "trace": [
                {
                    "kind": "critic",
                    "accepted": True,
                    "action": continue_action.to_dict(),
                    "decision_stage": "continue_stop",
                    "failure_class": "explorer_failure",
                    "reason": "Prefer CONTINUE when budget remains after a single hop.",
                    "negative_constraints": [],
                    "visible_state": self.state.to_dict(),
                }
            ],
        }
        rows = extract_from_trace(
            result=result,
            state=self.state,
            source_run_id="sp3-test",
            discovery_method="o0_critic",
            question_type="two_hop",
            prompt_version="sp3_critic_o0_v1",
            config_hash="abc",
            plan_version="SP3-PLAN 1.0",
            oracle_level="O0",
            secrets=self.secrets,
            verified_replay=True,
        )
        self.assertEqual(len(rows), 1)
        audit_candidate(rows[0], secrets=self.secrets)

    def test_config_stage_and_isolation_flags(self) -> None:
        config, _, path = load_config(self.workspace.configs_root / "sp3_candidate_discovery_v1.json", self.workspace)
        self.assertEqual(config["stage"], "SP3")
        self.assertFalse(config["allow_candidate_injection"])
        self.assertFalse(config["allow_self_play_experience_memory_read"])
        self.assertFalse(config["allow_oracle_in_actor"])
        self.assertEqual(config["overall_version"], "SP-GENERAL 1.17")
        self.assertEqual(path.name, "sp3_candidate_discovery_v1.json")
        banned = load_banned(self.workspace)
        self.assertIn("WebQTest-1686", banned["task_ids"])
        self.assertGreater(len(banned["questions"]), 200)

    def test_state_signature_strips_mids(self) -> None:
        sig = state_signature(self.state, failure_class="explorer_failure", question_type="two_hop")
        blob = json.dumps(sig)
        self.assertNotIn("e.paris", blob)
        self.assertTrue(sig["state_signature"].startswith("sig-"))


if __name__ == "__main__":
    unittest.main()
