from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.budget_ledger import CounterLedger
from sp_memory.environment_binding import EnvironmentBinding, EnvironmentStatus
from sp_memory.errors import ProtocolError
from sp_memory.kg_sparql import (
    SPARQL_HEAD_ENTITIES,
    SPARQL_TAIL_ENTITIES,
    LiveSparqlClient,
    PhysicalStatus,
    RawHttpResponse,
    ScriptedTransport,
    TransportTimeout,
    build_entity_search_request,
    logical_action_id,
    normalize_bindings,
    parse_sparql_json,
    retry_with_backoff,
    templates_match_original,
)
from sp_memory.live_environment import LiveKgBinding
from sp_memory.paths import Workspace
from sp_memory.pog_adapter import PoGAdapter
from sp_memory.schemas import Direction
from sp_memory.sp2a_guards import MemoryAccessGuard, scan_config_for_secrets


class Sp2aOfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.endpoint = "http://localhost:8890/sparql"

    def test_head_tail_sparql_not_reversed(self) -> None:
        head = build_entity_search_request("m.a", "r", Direction.HEAD, endpoint=self.endpoint)
        tail = build_entity_search_request("m.a", "r", Direction.TAIL, endpoint=self.endpoint)
        self.assertTrue(head.head)
        self.assertFalse(tail.head)
        self.assertEqual(head.sparql, SPARQL_TAIL_ENTITIES % ("m.a", "r"))
        self.assertEqual(tail.sparql, SPARQL_HEAD_ENTITIES % ("r", "m.a"))
        self.assertNotEqual(head.sparql, tail.sparql)
        self.assertNotEqual(head.request_hash, tail.request_hash)

    def test_templates_match_original_source(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "freebase_func.py").read_text(encoding="utf-8")
        info = templates_match_original(source)
        self.assertTrue(info["tail_entities_match"])
        self.assertTrue(info["head_entities_match"])

    def test_normalize_duplicate_and_literal(self) -> None:
        bindings = [
            {"tailEntity": {"type": "uri", "value": "http://rdf.freebase.com/ns/m.x"}},
            {"tailEntity": {"type": "uri", "value": "http://rdf.freebase.com/ns/m.x"}},
            {"tailEntity": {"type": "literal", "value": "Honolulu"}},
        ]
        targets = normalize_bindings(bindings)
        self.assertEqual(targets[0].value, "m.x")
        self.assertEqual(targets[0].source_location, "results.bindings[0].tailEntity.value")
        self.assertEqual(targets[2].value, "Honolulu")

    def test_malformed_missing_field(self) -> None:
        with self.assertRaises(Exception):
            normalize_bindings([{"other": {"value": "x"}}])

    def test_sp1_environment_still_forbids_live(self) -> None:
        with self.assertRaises(Exception):
            EnvironmentBinding(allow_live_kg=True)
        with self.assertRaises(ProtocolError):
            PoGAdapter(allow_live_kg=True)
        with self.assertRaises(ProtocolError):
            PoGAdapter(stage="sp2a", allow_live_kg=True)

    def test_retry_ledger_one_logical_two_physical(self) -> None:
        fixture = {
            "head": {"vars": ["tailEntity"]},
            "results": {"bindings": [{"tailEntity": {"type": "uri", "value": "http://rdf.freebase.com/ns/m.ok"}}]},
        }
        transport = ScriptedTransport([TransportTimeout("t"), RawHttpResponse(200, json.dumps(fixture))])
        client = LiveSparqlClient(
            endpoint=self.endpoint,
            allowed_endpoints=[self.endpoint],
            timeout_sec=1,
            max_retries=2,
            retry_backoff_sec=[0, 0],
            transport=transport,
        )
        request = build_entity_search_request("m.a", "r", "head", endpoint=self.endpoint)
        exchanges = retry_with_backoff(client, request, logical_action_id="log-test")
        ledger = CounterLedger()
        ledger.record_logical_with_exchanges(
            task_id="t",
            logical_action_id="log-test",
            statuses=[item.status for item in exchanges],
        )
        self.assertEqual(ledger.logical_actions, 1)
        self.assertEqual(ledger.physical_requests, 2)
        self.assertEqual(ledger.retries, 1)
        self.assertEqual(exchanges[-1].status, PhysicalStatus.SUCCESS)

    def test_memory_and_config_guards(self) -> None:
        guard = MemoryAccessGuard()
        with self.assertRaises(ProtocolError):
            guard.read("x")
        self.assertEqual(guard.reads, 1)
        banned = scan_config_for_secrets({"endpoint": "http://localhost:8890/sparql", "allow_llm": False})
        self.assertEqual(banned, [])
        banned = scan_config_for_secrets({"openai_api_key": "sk-secret"})
        self.assertTrue(banned)

    def test_live_binding_replay_without_network(self) -> None:
        fixture = {
            "head": {"vars": ["tailEntity"]},
            "results": {"bindings": [{"tailEntity": {"type": "uri", "value": "http://rdf.freebase.com/ns/m.ok"}}]},
        }
        transport = ScriptedTransport([RawHttpResponse(200, json.dumps(fixture))])
        client = LiveSparqlClient(
            endpoint=self.endpoint,
            allowed_endpoints=[self.endpoint],
            timeout_sec=1,
            max_retries=0,
            retry_backoff_sec=[],
            transport=transport,
        )
        ledger = CounterLedger()
        env = LiveKgBinding(client, ledger, task_id="sp2a.dev.test")
        first = env.expand("m.a", "r", Direction.HEAD)
        self.assertEqual(first.status, EnvironmentStatus.SUCCESS)
        records = {item["request_hash"]: item for item in env.audit_records}
        replay_client = LiveSparqlClient(
            endpoint=self.endpoint,
            allowed_endpoints=[self.endpoint],
            timeout_sec=1,
            max_retries=0,
            retry_backoff_sec=[],
            network_enabled=False,
        )
        replay_ledger = CounterLedger()
        replay = LiveKgBinding(
            replay_client,
            replay_ledger,
            records=records,
            task_id="sp2a.dev.test",
            network_enabled=False,
        )
        second = replay.expand("m.a", "r", Direction.HEAD)
        self.assertEqual(second.results, first.results)
        self.assertEqual(replay_ledger.logical_actions, 1)
        self.assertEqual(replay_client.physical_calls, 0)

    def test_credentials_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            LiveSparqlClient(
                endpoint="http://user:pass@localhost:8890/sparql",
                allowed_endpoints=["http://user:pass@localhost:8890/sparql"],
                timeout_sec=1,
                max_retries=0,
                retry_backoff_sec=[],
            )


if __name__ == "__main__":
    unittest.main()
