from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.environment_binding import EnvironmentStatus
from sp_memory.kg_sparql import SPARQL_HEAD_ENTITIES, RawHttpResponse, ScriptedTransport, build_entity_search_request
from sp_memory.pog_adapter import make_sp1_snapshot
from sp_memory.schemas import Direction
from sp_memory.sp2a_dynamic import (
    SELECTION_RULE_FIRST_SORTED,
    assert_tail_positive_triples,
    assert_tail_request,
    extract_canonical_entities,
    materialize_hop2_from_hop1,
    select_hop1_entity,
    validate_supplement_registry,
)
from sp_memory.sp2a_supplement_checks import _run_dynamic_twohop, snapshot_for_supplement_task


def _config() -> dict:
    return {
        "endpoint": "http://localhost:8890/sparql",
        "allowed_endpoints": ["http://localhost:8890/sparql"],
        "timeout_sec": 1,
        "max_retries": 2,
        "retry_backoff_sec": [0, 0],
        "max_recorded_bindings": 50,
        "budgets": {
            "max_depth": 4,
            "max_steps": 12,
            "max_kg_calls": 16,
            "max_llm_calls": 0,
            "max_critic_rounds": 0,
            "max_frontier_size": 80,
        },
    }


def _twohop_task() -> dict:
    return {
        "task_id": "sp2a.supp.twohop.fixture",
        "query_purpose": "dynamic_twohop",
        "question": "fixture dynamic twohop",
        "entity_public_name": "Obama",
        "hop1": {"entity": "m.02mjmr", "relation": "people.person.place_of_birth", "direction": "head"},
        "hop2": {"relation": "type.object.name", "direction": "head"},
        "hop1_candidate_constraint": {
            "term_type": "uri",
            "must_start_with": ["m.", "g."],
            "selection_rule": SELECTION_RULE_FIRST_SORTED,
        },
        "steps": [
            {"type": "EXPAND", "entity": "m.02mjmr", "relation": "people.person.place_of_birth", "direction": "head"}
        ],
    }


class Sp2aSupplementOfflineTests(unittest.TestCase):
    def test_tail_sparql_is_reverse_template(self) -> None:
        info = assert_tail_request("m.02hrh0_", "people.person.place_of_birth", endpoint="http://localhost:8890/sparql")
        self.assertTrue(info["ok"])
        self.assertFalse(info["head"])
        self.assertEqual(
            info["sparql"],
            SPARQL_HEAD_ENTITIES % ("people.person.place_of_birth", "m.02hrh0_"),
        )
        built_head = build_entity_search_request(
            "m.02hrh0_",
            "people.person.place_of_birth",
            Direction.HEAD,
            endpoint="http://localhost:8890/sparql",
        )
        self.assertNotEqual(info["sparql"], built_head.sparql)

    def test_tail_positive_triples_restore_subject(self) -> None:
        triples = [
            {"subject": "m.02mjmr", "relation": "people.person.place_of_birth", "object": "m.02hrh0_"},
            {"subject": "m.other", "relation": "people.person.place_of_birth", "object": "m.02hrh0_"},
        ]
        assertion = assert_tail_positive_triples(
            triples,
            query_entity="m.02hrh0_",
            relation="people.person.place_of_birth",
            expected_subjects=["m.02mjmr"],
        )
        self.assertTrue(assertion["ok"])
        self.assertEqual(assertion["missing_expected_subjects"], [])

    def test_select_hop1_entity_sorts_ids(self) -> None:
        self.assertEqual(select_hop1_entity(["m.zz", "m.aa"], SELECTION_RULE_FIRST_SORTED), "m.aa")
        self.assertIsNone(select_hop1_entity([], SELECTION_RULE_FIRST_SORTED))

    def test_extract_canonical_entities_skips_literals(self) -> None:
        triples = [
            {"subject": "m.02mjmr", "relation": "type.object.name", "object": "Barack Obama"},
            {"subject": "m.02mjmr", "relation": "people.person.place_of_birth", "object": "m.02hrh0_"},
        ]
        self.assertEqual(extract_canonical_entities(Direction.HEAD, triples), ["m.02hrh0_"])

    def test_registry_rejects_frozen_hop2_entity_and_oracle(self) -> None:
        payload = {
            "sampled_from_eval_sets": False,
            "tasks": [
                {
                    "task_id": "bad.twohop",
                    "query_purpose": "dynamic_twohop",
                    "hop1": {"entity": "m.a", "relation": "r", "direction": "head"},
                    "hop2": {"entity": "m.prewritten", "relation": "r2", "direction": "head"},
                    "hop1_candidate_constraint": {"selection_rule": SELECTION_RULE_FIRST_SORTED},
                    "steps": [{"type": "EXPAND", "entity": "m.a", "relation": "r", "direction": "head"}],
                    "answer_entity_ids": ["m.leak"],
                }
            ],
        }
        errors = validate_supplement_registry(payload)
        self.assertTrue(any("hop2 must not freeze entity" in item for item in errors))
        self.assertTrue(any("Oracle field" in item for item in errors))
        self.assertTrue(any("TAIL_positive" in item for item in errors))

    def test_materialize_skips_empty_hop1(self) -> None:
        snap = make_sp1_snapshot(
            task_id="t",
            question="q",
            source_entities=["m.02mjmr"],
            topic_entity={"m.02mjmr": "Obama"},
            frontier=["m.02mjmr"],
            enumerated_relations=[{"entity": "m.02mjmr", "relation": "people.person.place_of_birth", "direction": "head"}],
        )
        from sp_memory.pog_adapter import PoGAdapter
        from sp_memory.live_environment import LiveKgBinding
        from sp_memory.budget_ledger import CounterLedger
        from sp_memory.kg_sparql import LiveSparqlClient

        client = LiveSparqlClient(
            endpoint="http://localhost:8890/sparql",
            allowed_endpoints=["http://localhost:8890/sparql"],
            timeout_sec=1,
            max_retries=0,
            retry_backoff_sec=[],
            network_enabled=False,
        )
        adapter = PoGAdapter(
            adapter_enabled=True,
            allow_llm=False,
            allow_live_kg=True,
            stage="sp2a",
            environment=LiveKgBinding(client, CounterLedger(), network_enabled=False),
        )
        state = adapter.project_visible_state(snap)
        materialized = materialize_hop2_from_hop1(
            snap,
            state,
            [],
            hop1_direction=Direction.HEAD,
            hop2_relation="type.object.name",
            hop2_direction=Direction.HEAD,
            selection_rule=SELECTION_RULE_FIRST_SORTED,
            endpoint="http://localhost:8890/sparql",
        )
        self.assertIsNone(materialized)

    def test_empty_hop1_issues_no_second_physical(self) -> None:
        empty = ScriptedTransport(
            [RawHttpResponse(200, json.dumps({"head": {"vars": ["tailEntity"]}, "results": {"bindings": []}}))]
        )
        run = _run_dynamic_twohop(_config(), _twohop_task(), transport=empty)
        self.assertEqual(run["result1"].status, EnvironmentStatus.EMPTY_SUCCESS)
        self.assertIsNone(run["materialized"])
        self.assertEqual(run["hop2_physical_delta"], 0)
        self.assertEqual(empty.calls, 1)

    def test_multi_hop1_uses_sorted_first_entity(self) -> None:
        body = {
            "head": {"vars": ["tailEntity"]},
            "results": {
                "bindings": [
                    {"tailEntity": {"type": "uri", "value": "http://rdf.freebase.com/ns/m.zz_second"}},
                    {"tailEntity": {"type": "uri", "value": "http://rdf.freebase.com/ns/m.aa_first"}},
                ]
            },
        }
        name_body = {
            "head": {"vars": ["tailEntity"]},
            "results": {"bindings": [{"tailEntity": {"type": "literal", "value": "Name"}}]},
        }
        transport = ScriptedTransport([RawHttpResponse(200, json.dumps(body)), RawHttpResponse(200, json.dumps(name_body))])
        run = _run_dynamic_twohop(_config(), _twohop_task(), transport=transport)
        self.assertEqual(run["materialized"].hop2_entity, "m.aa_first")
        self.assertEqual(run["hop2_physical_delta"], 1)
        self.assertTrue(run["outcome2"].accepted)

    def test_snapshot_for_dynamic_task_does_not_prewrite_hop2_entity(self) -> None:
        snap = snapshot_for_supplement_task(_twohop_task(), _config()["budgets"])
        entities = {item.entity for item in snap.enumerated_relations}
        self.assertEqual(entities, {"m.02mjmr"})
        self.assertNotIn("m.02hrh0_", entities)


if __name__ == "__main__":
    unittest.main()
