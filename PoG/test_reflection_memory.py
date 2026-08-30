"""Offline tests for reflection-memory training and prompt injection."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


POG_DIR = Path(__file__).resolve().parent
if str(POG_DIR) not in sys.path:
    sys.path.insert(0, str(POG_DIR))


def _install_optional_dependency_stubs() -> None:
    """Keep the offline tests importable when optional model/API packages are absent."""
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        for name in list(sys.modules):
            if name == "sentence_transformers" or name.startswith("sentence_transformers."):
                sys.modules.pop(name, None)
        module = types.ModuleType("sentence_transformers")

        class DummySentenceTransformer:
            def __init__(self, *args, **kwargs):
                pass

            def encode(self, values):
                if isinstance(values, str):
                    return [0.0]
                return [[0.0] for _ in values]

        module.SentenceTransformer = DummySentenceTransformer
        module.util = SimpleNamespace(dot_score=lambda *_args, **_kwargs: [[0.0]])
        sys.modules["sentence_transformers"] = module

    try:
        import SPARQLWrapper  # noqa: F401
    except Exception:
        module = types.ModuleType("SPARQLWrapper")

        class DummySPARQLWrapper:
            def __init__(self, *args, **kwargs):
                pass

        module.SPARQLWrapper = DummySPARQLWrapper
        module.JSON = object()
        sys.modules["SPARQLWrapper"] = module

    try:
        import tqdm  # noqa: F401
    except Exception:
        module = types.ModuleType("tqdm")
        module.tqdm = lambda iterable, **_kwargs: iterable
        sys.modules["tqdm"] = module

    try:
        import requests  # noqa: F401
    except Exception:
        module = types.ModuleType("requests")
        module.get = lambda *args, **kwargs: None
        module.post = lambda *args, **kwargs: None
        sys.modules["requests"] = module

    try:
        import openai  # noqa: F401
    except Exception:
        module = types.ModuleType("openai")
        module.api_key = ""
        module.api_base = ""
        sys.modules["openai"] = module


_install_optional_dependency_stubs()

import memory_train
import output_paths
import prompt_list
import reflection_memory
import relation_memory


TOKENS = {"total": 0, "input": 0, "output": 0}


def make_hop(*, final: bool = False) -> dict:
    return {
        "dataset": "webqsp",
        "question_id": "q1",
        "parse_id": "p1",
        "question": "Who is reached by the gold relation?",
        "topic_entities": {"m.start": "Start"},
        "subobjectives": ["Find the next entity", "Answer the question"],
        "current_subobjective": "Answer the question" if final else "Find the next entity",
        "hop_index": 1 if final else 0,
        "depth": 2 if final else 1,
        "gold_relation": "r.gold",
        "gold_start_entity_ids": ["m.start"],
        "gold_start_entity_names": ["Start"],
        "gold_next_entity_ids": ["m.answer" if final else "m.next"],
        "gold_next_entity_names": ["Answer" if final else "Next"],
        "gold_answers": [{"answer_id": "m.answer", "answer": "Answer"}],
        "is_final": final,
        "initial_memory": "{}",
        "entity_names": {
            "m.start": "Start",
            "m.next": "Next",
            "m.answer": "Answer",
            "m.wrong": "Wrong",
        },
    }


def make_trace(*, final: bool = False, correct: bool = True) -> dict:
    next_id = "m.answer" if final else "m.next"
    selected_relation = "r.gold" if correct else "r.wrong"
    selected_id = next_id if correct else "m.wrong"
    selected_name = "Answer" if final and correct else ("Next" if correct else "Wrong")
    sufficient = "Yes" if final and correct else "No"
    answer = "Answer" if final and correct else "Null"
    return {
        "relation_selection": {
            "selected_relations": [{"entity": "m.start", "relation": selected_relation, "head": True}],
        },
        "entity_selection": {
            "candidate_entity_ids": [next_id, "m.wrong"],
            "candidate_entities": ["Answer" if final else "Next", "Wrong"],
            "selected_entity_ids": [selected_id],
            "selected_entities": [selected_name],
        },
        "memory_after": json.dumps({
            "1": f"Start reaches {selected_name} through the visible relation.",
            "2": "Not completed yet: answer the question." if not final else f"The visible answer is {selected_name}.",
        }),
        "knowledge_triplets": [f"Start, {selected_relation}, {selected_name}"],
        "visible_values": [selected_id, selected_name, "m.start", "Start"],
        "next_entity_names": [selected_name],
        "answer_depth": {
            "sufficient": sufficient,
            "answer": answer,
            "reason": "Grounded in the visible triplet.",
        },
        "reverse": {"invoked": not final or not correct, "add": not correct,
                    "reason": "Return to the hop start." if not correct else "No backtrack is needed.",
                    "entities_to_retrieve": [selected_name],
                    "candidate_entities": ["Start"],
                    "add_prompt_invoked": not correct,
                    "added_entity_ids": ["m.start"] if not correct else [],
                    "added_entity_names": ["Start"] if not correct else []},
    }


class PromptInjectionTest(unittest.TestCase):
    def test_empty_dynamic_examples_preserve_legacy_prompts(self):
        self.assertEqual(prompt_list.build_answer_depth_prompt(), prompt_list.answer_depth_prompt)
        self.assertEqual(prompt_list.build_judge_reverse_prompt(), prompt_list.judge_reverse)
        self.assertEqual(prompt_list.build_add_entity_prompt(), prompt_list.add_ent_prompt)

    def test_dynamic_examples_replace_fixed_examples(self):
        dynamic = "Q: Dynamic reflection example\nOutput:\n{}"
        answer_prompt = prompt_list.build_answer_depth_prompt(dynamic)
        reverse_prompt = prompt_list.build_judge_reverse_prompt(dynamic)
        add_prompt = prompt_list.build_add_entity_prompt(dynamic)
        for built in (answer_prompt, reverse_prompt, add_prompt):
            self.assertIn(dynamic, built)
        self.assertNotIn('Taste cannot be controlled by law', answer_prompt)
        self.assertNotIn('smallest country calling code', reverse_prompt)
        self.assertNotIn('Saint Marie', add_prompt)

    def test_correction_is_inserted_before_question_without_dropping_examples(self):
        correction = (
            "Training correction: output Sufficient=No and Answer=Null.\n"
            '{\n    "A": {\n        "Sufficient": "No",\n        "Answer": "Null"\n    },\n    "R": "Not enough yet."\n}'
        )
        prompt = prompt_list.insert_instruction_before_question(
            prompt_list.build_answer_depth_prompt(), correction
        )
        self.assertIn("Taste cannot be controlled by law", prompt)
        self.assertIn('"Sufficient": "No"', prompt)
        self.assertLess(prompt.find("Taste cannot be controlled by law"), prompt.find("Training correction"))
        self.assertLess(prompt.find("Training correction"), prompt.rfind("Q:"))

    def test_prompt_formatter_never_leaks_training_metadata(self):
        item = {
            "memory_type": reflection_memory.ANSWER_DEPTH,
            "question": "Q?",
            "memory": "Visible memory only.",
            "knowledge_triplets": ["Visible, relation, Fact"],
            "output": {"A": {"Sufficient": "No", "Answer": "Null"}, "R": "Need more facts."},
            "score": 0.99,
            "gold_relation": "r.secret",
            "verified": True,
            "topic_entities": {"m.hidden": "Hidden"},
            "correction_label": "secret",
        }
        formatted = reflection_memory.format_reflection_example(item)
        for forbidden in ("score", "gold_relation", "verified", "m.hidden", "correction_label", "r.secret"):
            self.assertNotIn(forbidden, formatted)


    def test_hybrid_retrieval_selects_relevant_item_and_honors_budget(self):
        relevant = {
            "memory_type": reflection_memory.ANSWER_DEPTH,
            "question": "Which Caribbean country has the smallest calling code?",
            "masked_question": "Which Caribbean country has the smallest calling code?",
            "question_key": "Which Caribbean country has the smallest calling code?",
            "state_key": "stage=answer_depth | subobjective=compare calling codes | memory=Caribbean countries",
            "memory": '{"1": "Caribbean countries are visible."}',
            "knowledge_triplets": ["Caribbean, contains, Barbados"],
            "output": {"A": {"Sufficient": "No", "Answer": "Null"}, "R": "More codes are needed."},
            "verified": True,
        }
        irrelevant = {
            **relevant,
            "question": "Who directed a science fiction movie?",
            "masked_question": "Who directed a science fiction movie?",
            "question_key": "Who directed a science fiction movie?",
            "state_key": "stage=answer_depth | subobjective=find movie director | memory=science fiction film",
        }
        args = SimpleNamespace(
            reflection_memory_mode="prompt",
            reflection_memory_stages="answer_depth,judge_reverse,add_entity",
            reflection_memory_top_k=1,
            reflection_memory_prompt_token_budget=500,
            memory_retrieval_strategy="hybrid",
            memory_state_weight=0.5,
            current_topic_entity={},
        )
        context, selected = reflection_memory.reflection_memory_context(
            [irrelevant, relevant], reflection_memory.ANSWER_DEPTH,
            "Which Caribbean country has the smallest calling code?",
            "Caribbean countries are visible.",
            ["Caribbean, contains, Barbados"],
            args,
            None,
            current_subobjective="compare calling codes",
            entities=["Barbados"],
            return_items=True,
        )
        self.assertEqual(selected, [relevant])
        self.assertIn("Caribbean country", context)
        self.assertNotIn("score", context.lower())

        args.reflection_memory_prompt_token_budget = 1
        context, selected = reflection_memory.reflection_memory_context(
            [relevant], reflection_memory.ANSWER_DEPTH,
            "Which Caribbean country has the smallest calling code?",
            "Caribbean countries are visible.",
            ["Caribbean, contains, Barbados"],
            args,
            None,
            current_subobjective="compare calling codes",
            entities=["Barbados"],
            return_items=True,
        )
        self.assertEqual(context, "")
        self.assertEqual(selected, [])


class ReflectionDiagnosisTest(unittest.TestCase):
    def test_correct_intermediate_hop_requires_insufficient_and_no_add(self):
        diagnosis = reflection_memory.diagnose_gold_hop_round(
            make_trace(final=False, correct=True), make_hop(final=False)
        )
        self.assertTrue(diagnosis["success"])
        self.assertEqual(diagnosis["expected_sufficient"], "No")
        self.assertEqual(diagnosis["expected_answer"], "Null")
        self.assertEqual(diagnosis["expected_add"], "No")

    def test_correct_final_hop_requires_visible_grounded_answer(self):
        diagnosis = reflection_memory.diagnose_gold_hop_round(
            make_trace(final=True, correct=True), make_hop(final=True)
        )
        self.assertTrue(diagnosis["success"])
        self.assertEqual(diagnosis["expected_sufficient"], "Yes")
        self.assertEqual(diagnosis["expected_answer"], "Answer")

    def test_wrong_branch_requires_backtrack_to_gold_start(self):
        diagnosis = reflection_memory.diagnose_gold_hop_round(
            make_trace(final=False, correct=False), make_hop(final=False)
        )
        self.assertFalse(diagnosis["success"])
        self.assertEqual(diagnosis["expected_add"], "Yes")
        self.assertTrue(diagnosis["reverse_ok"])
        self.assertIn("relation", diagnosis["errors"])
        self.assertIn("entity", diagnosis["errors"])

    def test_sufficient_correction_is_stage_local_and_shows_nested_json(self):
        hop = make_hop(final=False)
        trace = make_trace(final=False, correct=True)
        trace["answer_depth"] = {"sufficient": "Yes", "answer": "m.next", "reason": "stopped too early"}
        trace["reverse"] = {"invoked": False, "add": False}
        diagnosis = reflection_memory.diagnose_gold_hop_round(trace, hop)
        self.assertIn("sufficient", diagnosis["errors"])
        nxt = reflection_memory.build_next_round_state({"memory": "{}"}, trace, diagnosis, hop)
        memory = nxt["memory"]
        self.assertIn(reflection_memory.MEMORY_SLOT_NOTE_SEP, memory)
        self.assertIn("Sufficient=No", memory)
        self.assertIn("Add=No", memory)
        self.assertIn('"1"', memory)
        self.assertNotIn("relation", nxt["stage_corrections"])
        self.assertNotIn("memory", nxt["stage_corrections"])
        answer = nxt["stage_corrections"]["answer"]
        self.assertIn('"A":', answer)
        self.assertIn('"Sufficient": "No"', answer)
        self.assertNotIn('"Add"', answer)
        reverse = nxt["stage_corrections"]["reverse"]
        self.assertIn('"Add": "No"', reverse)
        self.assertNotIn('"Sufficient"', reverse)
        stripped = reflection_memory.strip_memory_slot_notes(memory)
        self.assertNotIn(reflection_memory.MEMORY_SLOT_NOTE_SEP, stripped)
        self.assertNotIn("Sufficient=No", stripped)
        self.assertIn("The triplets provide the information that", stripped)
        self.assertIn(reflection_memory.INCOMPLETE_MEMORY_SENTENCE, stripped)
        self.assertNotIn("Observed facts:", memory)
        self.assertNotIn("Not completed yet:", memory)

    def test_memory_slot_error_goes_to_update_memory_prompt_not_old_memory(self):
        hop = make_hop(final=True)
        trace = make_trace(final=True, correct=True)
        trace["memory_after"] = json.dumps({"1": "Answer is visible."})
        diagnosis = reflection_memory.diagnose_gold_hop_round(trace, hop)
        self.assertIn("memory", diagnosis["errors"])
        self.assertIn("memory_missing_subobjective_slots", diagnosis["memory_problems"])
        nxt = reflection_memory.build_next_round_state({"memory": "{}"}, trace, diagnosis, hop)
        memory_prompt = nxt["stage_corrections"]["memory"]
        self.assertIn("one short sentence per subobjective", memory_prompt)
        self.assertIn(reflection_memory.INCOMPLETE_MEMORY_SENTENCE, memory_prompt)
        self.assertIn("Find the next entity", memory_prompt)
        self.assertIn("Answer the question", memory_prompt)
        self.assertIn("omitted a subobjective", memory_prompt)
        self.assertNotIn("Visible facts for", memory_prompt)
        self.assertNotIn('"1":', memory_prompt)
        self.assertNotIn("{", memory_prompt)
        self.assertNotIn(reflection_memory.MEMORY_SLOT_NOTE_SEP, nxt["memory"])
        self.assertNotIn("one short sentence per subobjective", nxt["memory"])

    def test_three_failed_rounds_do_not_create_memory(self):
        bad_trace = make_trace(final=False, correct=False)
        with mock.patch.object(reflection_memory, "run_gold_hop_round", return_value=bad_trace):
            result = reflection_memory.train_gold_hop_reflection(
                make_hop(final=False), SimpleNamespace(max_reflection_rounds=3), None, max_rounds=3
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["rounds"], 3)
        self.assertEqual(len(result["traces"]), 3)
        self.assertEqual(result["memory_bundle"], [])

    def test_converged_hop_replaces_earlier_duplicate_stage_memory(self):
        wrong_trace = make_trace(final=False, correct=False)
        correct_trace = make_trace(final=False, correct=True)
        with mock.patch.object(
            reflection_memory, "run_gold_hop_round", side_effect=[wrong_trace, correct_trace]
        ):
            result = reflection_memory.train_gold_hop_reflection(
                make_hop(final=False), SimpleNamespace(max_reflection_rounds=3), None, max_rounds=3
            )
        self.assertTrue(result["success"])
        answer_items = [
            item for item in result["memory_bundle"]
            if item["memory_type"] == reflection_memory.ANSWER_DEPTH
        ]
        self.assertEqual(len(answer_items), 1)
        self.assertIn("Next", answer_items[0]["memory"])
        judge_items = [
            item for item in result["memory_bundle"]
            if item["memory_type"] == reflection_memory.JUDGE_REVERSE
        ]
        self.assertEqual(len(judge_items), 1)
        self.assertEqual(judge_items[0]["output"]["Add"], "No")
        add_items = [
            item for item in result["memory_bundle"]
            if item["memory_type"] == reflection_memory.ADD_ENTITY
        ]
        self.assertEqual(len(add_items), 1)
        self.assertEqual(add_items[0]["output"], ["Start"])

    def test_memory_may_mention_gold_answer_from_parametric_knowledge(self):
        hop = make_hop(final=False)
        trace = make_trace(final=False, correct=True)
        trace["memory_after"] = json.dumps({
            "1": "Start reaches Next through the visible relation.",
            "2": "Parametric knowledge says the final answer is Answer.",
        })
        diagnosis = reflection_memory.diagnose_gold_hop_round(trace, hop)
        self.assertTrue(diagnosis["memory_ok"])
        self.assertNotIn("memory", diagnosis["errors"])
        self.assertNotIn("memory_contains_hidden_gold_fact", diagnosis["memory_problems"])


class RelationMemoryTest(unittest.TestCase):
    def test_gold_relation_memory_contains_real_subobjectives(self):
        item = relation_memory.make_gold_relation_memory_item(
            episode={
                "dataset": "webqsp",
                "question_id": "q1",
                "parse_id": "p1",
                "RawQuestion": "Who is reached?",
                "topic_entity": {"m.start": "Start"},
            },
            depth=1,
            subobjectives=["Find the relation", "Find the answer"],
            current_subobjective="Find the relation",
            entity_ids=["m.start"],
            entity_names={"m.start": "Start"},
            incoming_relation="",
            previous_relations=[],
            gold_relation="r.gold",
            candidate_relations=["r.other", "r.gold"],
        )
        formatted = "\n".join(relation_memory.format_memory_example_for_prompt(item, 8))
        self.assertEqual(item["subobjectives"], ["Find the relation", "Find the answer"])
        self.assertIn("Subobjectives:", formatted)
        self.assertIn("Find the relation", formatted)
        self.assertIn("r.gold", formatted)

    def test_missing_gold_relation_is_not_fabricated_as_a_candidate(self):
        item = relation_memory.make_gold_relation_memory_item(
            episode={"RawQuestion": "Q?", "topic_entity": {"m.start": "Start"}},
            depth=1,
            subobjectives=["Find relation"],
            current_subobjective="Find relation",
            entity_ids=["m.start"],
            entity_names={"m.start": "Start"},
            incoming_relation="",
            previous_relations=[],
            gold_relation="r.gold",
            candidate_relations=["r.other"],
        )
        self.assertEqual(item["candidate_relations"], ["r.other"])
        self.assertFalse(item["gold_relation_in_candidates"])
        self.assertFalse(item["verified"])

    def test_candidate_collection_does_not_call_llm(self):
        import freebase_func

        def fake_sparql(query):
            return ["r.head", "r.skip"] if "?relation ?x" in query else ["r.tail"]

        args = SimpleNamespace(remove_unnecessary_rel=True)
        with mock.patch.object(freebase_func, "execurte_sparql", side_effect=fake_sparql), \
             mock.patch.object(freebase_func, "replace_relation_prefix", side_effect=lambda value: value), \
             mock.patch.object(freebase_func, "abandon_rels", side_effect=lambda value: value == "r.skip"), \
             mock.patch.object(freebase_func, "run_llm") as run_llm:
            result = freebase_func.collect_candidate_relations_without_llm(
                "m.start", ["r.tail"], True, args
            )
        run_llm.assert_not_called()
        self.assertEqual(result["head_relations"], ["r.head"])
        self.assertEqual(result["tail_relations"], [])
        self.assertEqual(result["candidate_relations"], ["r.head"])


class GoldHopRoundTest(unittest.TestCase):
    def test_mocked_complete_final_round_produces_full_trace(self):
        fake_freebase = types.ModuleType("freebase_func")
        fake_utils = types.ModuleType("utils")

        fake_freebase.id2entity_name_or_type = lambda entity_id: {
            "m.start": "Start", "m.answer": "Answer"
        }.get(entity_id, entity_id)
        fake_freebase.relation_search_prune = lambda *args, **kwargs: (
            [{"entity": "m.start", "relation": "r.gold", "head": True}],
            dict(TOKENS),
            {"candidate_relations": ["r.gold", "r.other"], "llm_raw_output": "['r.gold']"},
        )
        fake_freebase.entity_search_with_constraints = lambda *args, **kwargs: ["m.answer"]
        fake_freebase.entity_condition_prune = lambda *args, **kwargs: (
            True,
            [[("Start", "r.gold", "Answer")]],
            ["m.answer"],
            ["r.gold"],
            [True],
            {"m.start": {"head": {"r.gold": ["m.answer"]}}},
            1,
            dict(TOKENS),
            [{"selected_entities": ["Answer"]}],
        )
        fake_freebase.update_memory = lambda *args, **kwargs: (
            dict(TOKENS),
            {
                "memory_before": "{}",
                "memory_after": '{"1": "Start reaches Answer."}',
                "knowledge_triplets_prompt": "Start, r.gold, Answer",
            },
        )
        fake_freebase.reasoning = lambda *args, **kwargs: (
            "reasoning response",
            "Answer",
            "Yes",
            dict(TOKENS),
            {"answer": "Answer", "sufficient": "Yes", "reason": "Visible fact."},
        )
        fake_utils.if_finish_list = mock.Mock(side_effect=AssertionError("final sufficient hop must not backtrack"))

        with mock.patch.dict(sys.modules, {"freebase_func": fake_freebase, "utils": fake_utils}):
            trace = reflection_memory.run_gold_hop_round(
                make_hop(final=True), {"round": 1, "memory": "{}", "memory_dir": ""},
                SimpleNamespace(), None, "relation example",
            )

        self.assertEqual(trace["relation_selection"]["candidate_relations"], ["r.gold", "r.other"])
        self.assertEqual(trace["entity_selection"]["selected_entity_ids"], ["m.answer"])
        self.assertEqual(trace["memory_before"], "{}")
        self.assertIn("Start, r.gold, Answer", trace["knowledge_triplets"])
        self.assertEqual(trace["answer_depth"]["sufficient"], "Yes")
        self.assertFalse(trace["reverse"]["invoked"])


class EpisodeCommitTest(unittest.TestCase):
    def test_progress_is_written_after_all_memory_files(self):
        calls = []
        with mock.patch.object(memory_train, "_append_records", side_effect=lambda path, records: calls.append(path)), \
             mock.patch.object(memory_train, "append_progress", side_effect=lambda directory, parse_id: calls.append("progress")):
            memory_train.commit_episode_memory_bundle(
                memory_dir="memory",
                parse_id="p1",
                decomposition_path="decomposition",
                relation_path="relation",
                reflection_path="reflection",
                failed_path="failed",
                decomposition_items=[],
                relation_items=[],
                reflection_items=[],
                failed_items=[],
            )
        self.assertEqual(calls, ["decomposition", "relation", "reflection", "failed", "progress"])

    def test_filter_removes_only_uncommitted_parse_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "reflection_memory.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"parse_id": "p1", "value": 1}) + "\n")
                handle.write(json.dumps({"parse_id": "p2", "value": 2}) + "\n")
            output_paths.filter_jsonl_by_parse_id(path, "p1")
            self.assertEqual(output_paths.load_parse_ids_from_jsonl(path), {"p2"})

    def test_failed_hop_does_not_block_later_hops(self):
        args = SimpleNamespace(
            dataset="webqsp",
            train_memory_family="reflection",
            memory_output_dir=tempfile.mkdtemp(),
            decomposition_memory_output_path="",
            relation_memory_output_path="",
            reflection_memory_output_path="",
            max_reflection_rounds=3,
            memory_candidate_relation_limit=8,
            constraint_pushdown="off",
            constraint_asof_date="",
            constraint_hub_threshold=50,
        )
        episode = {
            "dataset": "webqsp",
            "question_id": "q1",
            "parse_id": "p1",
            "RawQuestion": "Q?",
            "topic_entity": {"m.start": "Start"},
            "gold_relation_path": ["r.one", "r.two"],
            "gold_answers": [{"answer_id": "m.answer", "answer": "Answer"}],
        }
        hops = [
            {**make_hop(final=False), "depth": 1, "reflection_eligible": True,
             "relation_memory_item": {"question": "Q?", "gold_relation": "r.one", "candidate_relations": ["r.one"]}},
            {**make_hop(final=True), "depth": 2, "reflection_eligible": True,
             "relation_memory_item": {"question": "Q?", "gold_relation": "r.two", "candidate_relations": ["r.two"]}},
        ]
        failed = {"success": False, "rounds": 3, "traces": [{"round": 1}], "memory_bundle": []}
        success_item = {"memory_type": "answer_depth", "parse_id": "p1", "verified": True}
        succeeded = {"success": True, "rounds": 1, "traces": [], "memory_bundle": [success_item]}
        committed = {}
        try:
            with mock.patch.object(memory_train, "generate_train_subobjectives", return_value=(["s1", "s2"], "[]", dict(TOKENS))), \
                 mock.patch.object(memory_train, "prepare_gold_hops_and_relation_memories", return_value=(hops, [])), \
                 mock.patch.object(memory_train, "format_memory_example_for_prompt", return_value=["example"]), \
                 mock.patch.object(memory_train, "train_gold_hop_reflection", side_effect=[failed, succeeded]) as train_hop, \
                 mock.patch.object(memory_train, "commit_episode_memory_bundle", side_effect=lambda **kwargs: committed.update(kwargs)), \
                 mock.patch.object(memory_train, "count_reflection_memory", return_value={"answer_depth": 1, "judge_reverse": 0, "add_entity": 0, "total": 1}), \
                 mock.patch.object(memory_train, "update_run_meta"):
                memory_train.run_combined_memory_train(args, {}, [episode], None)
            self.assertEqual(train_hop.call_count, 2)
            self.assertEqual(committed["reflection_items"], [success_item])
            self.assertEqual(len(committed["failed_items"]), 1)
            self.assertEqual(committed["failed_items"][0]["depth"], 1)
        finally:
            import shutil
            shutil.rmtree(args.memory_output_dir, ignore_errors=True)

    def test_count_reflection_memory_reads_pretty_printed_jsonl(self):
        from jsonl_io import append_jsonl_record
        from reflection_memory import count_reflection_memory

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "reflection_memory.jsonl")
            append_jsonl_record(
                path,
                {
                    "memory_type": "answer_depth",
                    "verified": True,
                    "parse_id": "p1",
                },
            )
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn('\n  "memory_type"', text)
            counts = count_reflection_memory(path)
            self.assertEqual(counts["answer_depth"], 1)
            self.assertEqual(counts["total"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


