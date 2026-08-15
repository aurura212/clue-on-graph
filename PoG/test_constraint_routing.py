"""Unit tests for per-hop constraint routing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from constraint_compiler import (
    format_constraints_for_prompt,
    get_constraints_for_subobjective,
    lookup_constraint_trace,
    normalize_hop_routing,
    parse_subobjective_routing,
    resolve_constraint_keys,
    resolve_subobjective_routing,
    select_prompt_constraints,
    select_search_constraints,
)
from decomposition_memory import build_gold_planning_prompt, should_use_decomposition_memory


def sample_compiled():
    ohio = {
        "mention": "Ohio",
        "name": "Ohio",
        "mid": "m.05kkh",
        "confidence": 0.9,
    }
    caribbean = {
        "mention": "Caribbean",
        "name": "Caribbean",
        "mid": "m.01h6cz",
        "confidence": 0.8,
    }
    current = {
        "kind": "current",
        "start": "2015-08-10",
        "end": "2015-08-10",
        "asof_date": "2015-08-10",
        "raw_text": "current",
    }
    smallest = {"kind": "min", "raw_text": "smallest", "limit": 1}
    return {
        "enabled": True,
        "entity_constraints": [ohio, caribbean],
        "time_constraints": [current],
        "order_constraints": [smallest],
        "unlinked_mentions": [],
    }


class ResolveConstraintKeysTest(unittest.TestCase):
    def test_exact_mention_match(self):
        compiled = sample_compiled()
        resolved = resolve_constraint_keys(["entity:Ohio"], compiled)
        self.assertEqual(len(resolved["entity_constraints"]), 1)
        self.assertEqual(resolved["entity_constraints"][0]["mid"], "m.05kkh")
        self.assertEqual(resolved["unresolved_keys"], [])

    def test_partial_name_match(self):
        compiled = {
            "entity_constraints": [
                {"mention": "OH", "name": "State of Ohio", "mid": "m.05kkh", "confidence": 0.7},
            ],
            "time_constraints": [],
            "order_constraints": [],
        }
        resolved = resolve_constraint_keys(["entity:Ohio"], compiled)
        self.assertEqual(len(resolved["entity_constraints"]), 1)
        self.assertEqual(resolved["entity_constraints"][0]["mid"], "m.05kkh")
        self.assertEqual(resolved["unresolved_keys"], [])

    def test_no_match_fallback(self):
        compiled = sample_compiled()
        resolved = resolve_constraint_keys(["entity:Mars"], compiled)
        self.assertEqual(resolved["entity_constraints"], [])
        self.assertEqual(resolved["unresolved_keys"], ["entity:Mars"])

    def test_time_current_and_rank_min(self):
        compiled = sample_compiled()
        resolved = resolve_constraint_keys(["time:current", "rank:min"], compiled)
        self.assertEqual(resolved["time_constraints"][0]["kind"], "current")
        self.assertEqual(resolved["order_constraints"][0]["kind"], "min")
        self.assertEqual(resolved["entity_constraints"], [])


class GetConstraintsForSubobjectiveTest(unittest.TestCase):
    def test_per_step_subset_and_clamp(self):
        compiled = sample_compiled()
        routing = [
            {"step": "Find senators", "constraints": []},
            {"step": "Filter to Ohio", "constraints": ["entity:Ohio"]},
            {"step": "Filter to current", "constraints": ["time:current"]},
        ]
        resolved = resolve_subobjective_routing(routing, compiled)
        hop0 = get_constraints_for_subobjective(resolved, 0, compiled)
        hop1 = get_constraints_for_subobjective(resolved, 1, compiled)
        hop2 = get_constraints_for_subobjective(resolved, 2, compiled)
        hop_oob = get_constraints_for_subobjective(resolved, 99, compiled)

        self.assertEqual(hop0["entity_constraints"], [])
        self.assertEqual(hop0["time_constraints"], [])
        self.assertEqual(hop1["entity_constraints"][0]["mention"], "Ohio")
        self.assertEqual(hop1["time_constraints"], [])
        self.assertEqual(hop2["time_constraints"][0]["kind"], "current")
        self.assertEqual(hop2["entity_constraints"], [])
        self.assertEqual(hop_oob["time_constraints"][0]["kind"], "current")

    def test_format_prompt_filters_by_idx(self):
        compiled = sample_compiled()
        compiled["resolved_routing"] = resolve_subobjective_routing(
            [
                {"step": "a", "constraints": []},
                {"step": "b", "constraints": ["entity:Ohio"]},
            ],
            compiled,
        )
        empty_text = format_constraints_for_prompt(compiled, subobjective_idx=0)
        ohio_text = format_constraints_for_prompt(compiled, subobjective_idx=1)
        self.assertEqual(empty_text, "")
        self.assertIn("Ohio", ohio_text)
        self.assertNotIn("current", ohio_text.lower())


class ParseRoutingTest(unittest.TestCase):
    def test_new_format(self):
        text = (
            '[{"step": "Find senators associated with United States Senate", "constraints": []},'
            ' {"step": "Filter to senators representing Ohio", "constraints": ["entity:Ohio"]}]'
        )
        routing = parse_subobjective_routing(text)
        self.assertIsNotNone(routing)
        self.assertEqual(routing[1]["constraints"], ["entity:Ohio"])

    def test_legacy_string_list_returns_none(self):
        text = "['Search the countries in the Caribbean', 'Compare calling codes']"
        self.assertIsNone(parse_subobjective_routing(text))


class SelectConstraintsPolicyTest(unittest.TestCase):
    def _args(self, mode, routing=None, run_mode="test", compiled=None):
        compiled = compiled if compiled is not None else sample_compiled()
        if routing is not None:
            compiled["resolved_routing"] = resolve_subobjective_routing(routing, compiled)
        return SimpleNamespace(
            constraint_pushdown="on",
            constraint_routing=mode,
            run_mode=run_mode,
            current_constraints=compiled,
            resolved_constraint_routing=compiled.get("resolved_routing"),
            current_subobjective_idx=0,
        )

    def test_off_uses_full_constraints(self):
        args = self._args("off")
        selected = select_search_constraints(args, args.current_constraints, 0)
        self.assertEqual(len(selected["entity_constraints"]), 2)
        self.assertEqual(len(selected["time_constraints"]), 1)

    def test_auto_without_routing_falls_back_to_full(self):
        args = self._args("auto", routing=None)
        args.resolved_constraint_routing = None
        args.current_constraints["resolved_routing"] = None
        selected = select_search_constraints(args, args.current_constraints, 0)
        self.assertEqual(len(selected["entity_constraints"]), 2)

    def test_on_without_routing_skips_search_but_prompts_full(self):
        args = self._args("on", routing=None)
        args.resolved_constraint_routing = None
        args.current_constraints["resolved_routing"] = None
        search = select_search_constraints(args, args.current_constraints, 0)
        prompt = select_prompt_constraints(args, args.current_constraints, 0)
        self.assertEqual(search["entity_constraints"], [])
        self.assertEqual(len(prompt["entity_constraints"]), 2)

    def _ohio_current(self):
        full = sample_compiled()
        return {
            "enabled": True,
            "entity_constraints": [full["entity_constraints"][0]],
            "time_constraints": list(full["time_constraints"]),
            "order_constraints": [],
            "unlinked_mentions": [],
        }

    def test_auto_with_routing_uses_pending_lookahead(self):
        routing = [
            {"step": "Find senators", "constraints": []},
            {"step": "Filter Ohio", "constraints": ["entity:Ohio"]},
            {"step": "Filter current", "constraints": ["time:current"]},
        ]
        args = self._args("auto", routing=routing, compiled=self._ohio_current())
        hop0 = select_search_constraints(args, args.current_constraints, 0)
        hop1 = select_search_constraints(args, args.current_constraints, 1)
        hop2 = select_search_constraints(args, args.current_constraints, 2)
        self.assertEqual(hop0["entity_constraints"][0]["mid"], "m.05kkh")
        self.assertEqual(hop0["time_constraints"][0]["kind"], "current")
        self.assertEqual(hop1["entity_constraints"][0]["mid"], "m.05kkh")
        self.assertEqual(hop1["time_constraints"][0]["kind"], "current")
        self.assertEqual(hop2["entity_constraints"], [])
        self.assertEqual(hop2["time_constraints"][0]["kind"], "current")

    def test_ohio_senator_style_plan_does_not_unconstrain_first_hop(self):
        routing = [
            {"step": "Retrieve jurisdiction", "constraints": []},
            {"step": "Filter same as Senate", "constraints": []},
            {"step": "Retrieve officials", "constraints": []},
            {"step": "Retrieve office holders", "constraints": []},
            {"step": "Filter Ohio", "constraints": ["entity:Ohio"]},
            {"step": "Filter current start", "constraints": ["time:current"]},
            {"step": "Filter current end", "constraints": ["time:current"]},
            {"step": "Select answer", "constraints": []},
        ]
        args = self._args("auto", routing=routing, compiled=self._ohio_current())
        hop0 = select_search_constraints(args, args.current_constraints, 0)
        hop1 = select_search_constraints(args, args.current_constraints, 1)
        self.assertEqual(hop0["entity_constraints"][0]["mention"], "Ohio")
        self.assertEqual(hop0["time_constraints"][0]["kind"], "current")
        self.assertEqual(hop1["entity_constraints"][0]["mention"], "Ohio")

    def test_unassigned_entity_stays_active_on_every_hop(self):
        routing = [
            {"step": "Retrieve officials", "constraints": []},
            {"step": "Filter current", "constraints": ["time:current"]},
        ]
        args = self._args("auto", routing=routing, compiled=self._ohio_current())
        hop0 = select_search_constraints(args, args.current_constraints, 0)
        hop1 = select_search_constraints(args, args.current_constraints, 1)
        self.assertEqual([item["mention"] for item in hop0["entity_constraints"]], ["Ohio"])
        self.assertEqual([item["mention"] for item in hop1["entity_constraints"]], ["Ohio"])
        self.assertEqual(hop0["time_constraints"][0]["kind"], "current")
        self.assertEqual(hop1["time_constraints"][0]["kind"], "current")

    def test_train_mode_ignores_routing(self):
        routing = [{"step": "Find senators", "constraints": []}]
        args = self._args("auto", routing=routing, run_mode="train")
        selected = select_search_constraints(args, args.current_constraints, 0)
        self.assertEqual(len(selected["entity_constraints"]), 2)


class NormalizeHopRoutingTest(unittest.TestCase):
    def test_filter_keys_merge_onto_previous_hop(self):
        routing = [
            {"step": "From United States Senate, expand to senator position nodes", "constraints": []},
            {"step": "Filter to senators representing Ohio", "constraints": ["entity:Ohio"]},
            {"step": "Filter current holders", "constraints": ["time:current"]},
        ]
        hops = normalize_hop_routing(routing)
        self.assertEqual(len(hops), 1)
        self.assertEqual(hops[0]["step"], routing[0]["step"])
        self.assertEqual(hops[0]["constraints"], ["entity:Ohio", "time:current"])

    def test_select_step_is_dropped(self):
        routing = [
            {"step": "From those position nodes, expand to the office holder", "constraints": []},
            {"step": "Select the distinct entity as the answer", "constraints": []},
        ]
        hops = normalize_hop_routing(routing)
        self.assertEqual(len(hops), 1)
        self.assertEqual(hops[0]["step"], routing[0]["step"])
        self.assertEqual(hops[0]["constraints"], [])

    def test_already_hop_plan_is_unchanged(self):
        routing = [
            {
                "step": "From United States Senate, expand to senator position nodes",
                "constraints": ["entity:Ohio", "time:current"],
            },
            {"step": "From those position nodes, expand to the office holder", "constraints": []},
        ]
        self.assertEqual(normalize_hop_routing(routing), routing)

    def test_rank_step_is_kept(self):
        routing = [
            {"step": "From Caribbean, expand to countries", "constraints": ["entity:Caribbean"]},
            {"step": "Compare calling codes and keep the smallest", "constraints": ["rank:min"]},
        ]
        self.assertEqual(normalize_hop_routing(routing), routing)


class LookupConstraintTraceTest(unittest.TestCase):
    def test_prefers_three_tuple_without_routed_sig(self):
        cache = {
            ("m.07t58", "government.governmental_body.members", True): {"pushdown_applied": True, "after_count": 2},
            ("m.07t58", "government.governmental_body.members", True, (0, ("m.05kkh",), ("current",), ())): {
                "pushdown_applied": False,
            },
        }
        trace = lookup_constraint_trace(cache, "m.07t58", "government.governmental_body.members", True)
        self.assertTrue(trace["pushdown_applied"])
        self.assertEqual(trace["after_count"], 2)

    def test_falls_back_to_four_tuple_prefix(self):
        cache = {
            ("m.07t58", "government.governmental_body.members", True, (0, ("m.05kkh",), ("current",), ())): {
                "pushdown_applied": True,
                "after_count": 2,
            },
        }
        trace = lookup_constraint_trace(cache, "m.07t58", "government.governmental_body.members", True)
        self.assertTrue(trace["pushdown_applied"])
        self.assertEqual(trace["after_count"], 2)


class DecompositionHopContractTest(unittest.TestCase):
    def test_routing_skips_old_decomposition_memory(self):
        args = SimpleNamespace(
            decomposition_memory_mode="prompt",
            constraint_pushdown="on",
            constraint_routing="auto",
            run_mode="test",
        )
        self.assertFalse(should_use_decomposition_memory(args))
        args.constraint_routing = "off"
        self.assertTrue(should_use_decomposition_memory(args))

    def test_gold_planning_prompt_uses_hop_path(self):
        prompt = build_gold_planning_prompt(
            {
                "RawQuestion": "who is the current ohio state senator?",
                "topic_entity": {"m.07t2k": "United States Senate"},
                "gold_relation_path": [
                    "government.governmental_body.members",
                    "government.government_position_held.office_holder",
                ],
                "sparql": "SELECT ?x WHERE { ?x ns:type.object.name ?name }",
                "gold_answers": [{"answer": "Sherrod Brown"}],
                "constraints": [],
            }
        )
        self.assertIn("Gold relation path:", prompt)
        self.assertIn("government.governmental_body.members -> government.government_position_held.office_holder", prompt)
        self.assertIn('"step"', prompt)
        self.assertIn("Do not write standalone Filter or Select steps", prompt)


if __name__ == "__main__":
    unittest.main()
