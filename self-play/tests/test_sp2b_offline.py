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

from sp_memory.action_parser import parse_reasoning_object, relations_to_expand_actions
from sp_memory.config import load_config
from sp_memory.kg_sparql import SPARQL_HEAD_RELATIONS, SPARQL_TAIL_RELATIONS, SPARQL_ID
from sp_memory.paths import Workspace
from sp_memory.pog_adapter import PoGAdapter, make_sp1_snapshot
from sp_memory.rollout import abandon_rels
from sp_memory.schemas import ActionType, Direction, FailureClass
from sp_memory.sp2a_guards import scan_config_for_secrets
from sp_memory.sp2b_checks import exclusion_overlap, load_registry, preflight
from sp_memory.working_memory import PogWorkingMemory


class Sp2bOfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Workspace.from_this_package()

    def test_nested_reasoning_parse(self) -> None:
        text = '{"A": {"Sufficient": "Yes", "Answer": "Honolulu"}, "R": "ok"}'
        parsed = parse_reasoning_object(text)
        self.assertEqual(parsed["answer"], "Honolulu")
        self.assertEqual(str(parsed["sufficient"]).lower(), "yes")

    def test_illegal_relation_does_not_become_expand(self) -> None:
        snap = make_sp1_snapshot(
            enumerated_relations=[{"entity": "m.02mjmr", "relation": "people.person.place_of_birth", "direction": "head"}]
        )
        adapter = PoGAdapter(adapter_enabled=True)
        state = adapter.project_visible_state(snap)
        parsed = relations_to_expand_actions(
            "['people.person.date_of_death']",
            "m.02mjmr",
            ["people.person.place_of_birth"],
            [],
            state,
        )
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["failure_class"], FailureClass.ACTION_SPACE_FAILURE.value)
        self.assertEqual(parsed["actions"], [])

    def test_legal_relation_maps_to_expand(self) -> None:
        snap = make_sp1_snapshot(
            enumerated_relations=[{"entity": "m.alice", "relation": "people.person.friend", "direction": "head"}]
        )
        adapter = PoGAdapter(adapter_enabled=True)
        state = adapter.project_visible_state(snap)
        parsed = relations_to_expand_actions(
            "['people.person.friend']",
            "m.alice",
            ["people.person.friend"],
            [],
            state,
        )
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["actions"][0].action_type, ActionType.EXPAND)
        self.assertEqual(parsed["actions"][0].params["direction"], Direction.HEAD.value)

    def test_working_memory_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            # Workspace.assert_writable requires path under self-play.
            run_dir = self.workspace.runs_root / "_sp2b_test_scratch"
            a = PogWorkingMemory(self.workspace, run_dir, "task-a")
            b = PogWorkingMemory(self.workspace, run_dir, "task-b")
            a.create_empty()
            b.create_empty()
            a.write("alpha")
            b.write("beta")
            self.assertEqual(a.read(), "alpha")
            self.assertEqual(b.read(), "beta")
            self.assertNotEqual(a.mem_path, b.mem_path)
            self.assertIn("scratch/task-a", str(a.mem_path))
            a.close()
            b.close()

    def test_adapter_sp2b_allows_llm_flag(self) -> None:
        from sp_memory.budget_ledger import CounterLedger
        from sp_memory.kg_sparql import LiveSparqlClient
        from sp_memory.live_environment import LiveKgBinding

        client = LiveSparqlClient(
            endpoint="http://localhost:8890/sparql",
            allowed_endpoints=["http://localhost:8890/sparql"],
            timeout_sec=1,
            max_retries=0,
            retry_backoff_sec=[],
            network_enabled=False,
        )
        env = LiveKgBinding(client, CounterLedger(), network_enabled=False)
        adapter = PoGAdapter(
            adapter_enabled=True,
            allow_llm=True,
            allow_live_kg=True,
            stage="sp2b",
            environment=env,
        )
        self.assertTrue(adapter.allow_llm)
        self.assertTrue(adapter.allow_live_kg)
        snap = make_sp1_snapshot()
        snap.budget.used_llm_calls = 3
        adapter.project_visible_state(snap)
        self.assertEqual(snap.budget.used_llm_calls, 3)
        with self.assertRaises(Exception):
            PoGAdapter(stage="sp2a", allow_llm=True, allow_live_kg=True, environment=env)

    def test_abandon_and_relation_templates(self) -> None:
        self.assertTrue(abandon_rels("type.object.name"))
        self.assertTrue(abandon_rels("common.topic.notable_types"))
        self.assertFalse(abandon_rels("people.person.place_of_birth"))
        self.assertIn("SELECT DISTINCT ?relation", SPARQL_HEAD_RELATIONS)
        self.assertIn("SELECT DISTINCT ?relation", SPARQL_TAIL_RELATIONS)
        self.assertIn("FILTER(?entity = ns:%s)", SPARQL_ID)

    def test_config_and_b0_coverage(self) -> None:
        config, _, path = load_config(self.workspace.configs_root / "sp2b_llm_kg_baseline_v1.json", self.workspace)
        self.assertEqual(config["stage"], "SP2-B")
        self.assertTrue(config["allow_llm"])
        self.assertFalse(config["allow_self_play_experience_memory"])
        self.assertEqual(scan_config_for_secrets(config), [])
        b0 = load_registry(self.workspace, config["b0_task_registry"])
        cover = {task["coverage"] for task in b0["tasks"]}
        self.assertIn("one_hop_entity_relation", cover)
        self.assertIn("two_hop_or_consecutive_state_update", cover)
        self.assertIn("literal_or_answer_submission", cover)
        self.assertIn("empty_result_or_early_stop", cover)
        self.assertTrue(all("oracle" not in task for task in b0["tasks"]))
        overlap = exclusion_overlap(self.workspace, b0["tasks"])
        self.assertTrue(overlap["ok"])
        b1 = load_registry(self.workspace, config["b1_task_registry"])
        self.assertGreaterEqual(len(b1["tasks"]), 20)
        overlap1 = exclusion_overlap(self.workspace, b1["tasks"])
        self.assertTrue(overlap1["ok"])
        self.assertEqual(path.name, "sp2b_llm_kg_baseline_v1.json")

    def test_experience_memory_path_rejected(self) -> None:
        mem = PogWorkingMemory(self.workspace, self.workspace.runs_root / "_sp2b_test_scratch", "task-x")
        mem.mem_path = self.workspace.artifacts_root / "memory" / "candidate_experience.json"
        with self.assertRaises(Exception):
            mem.create_empty()


if __name__ == "__main__":
    unittest.main()
