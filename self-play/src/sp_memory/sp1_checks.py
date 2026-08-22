"""SP1 experiments E1.1-E1.12. No real LLM, no live KG, no memory, no EM/F1."""

from __future__ import annotations

import json
import tempfile
import traceback
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from .answer_submission import parse_reasoning_text, submit_from_text
from .baseline import assert_baseline_unchanged, baseline_file_hashes
from .config import load_config
from .environment_binding import (
    EnvironmentBinding,
    EnvironmentStatus,
    KgTimeout,
    MalformedKgResponse,
    direction_to_pog_head,
    expand_action_to_pog_params,
    pog_params_to_expand_action,
    triples_from_expand,
)
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_file, sha256_text
from .llm_guard import LLMCallGuard
from .paths import PROTOCOL_VERSION, Workspace
from .pog_adapter import (
    DECISION_MAP,
    PoGAdapter,
    PoGSnapshot,
    default_sp1_budget,
    make_sp1_snapshot,
    original_entity_search,
    original_extract_reason_and_anwer,
    original_select_relations,
)
from .question_normalization import (
    QUESTION_NORMALIZATION_VERSION,
    normalize_question,
    normalized_question_hash,
)
from .registry import build_formal_exclusion_registry, write_exclusion_registry
from .sampling import eval_set_paths, verify_eval_sets
from .schemas import (
    Action,
    ActionType,
    ActorRole,
    Budget,
    DecisionStage,
    Direction,
    FailureClass,
    TaskRecord,
    VisibleRelation,
    VisibleState,
)
from .visibility import (
    OracleSecrets,
    audit_object,
    count_sensitive_fields,
    project_actor_view,
    project_critic_view,
    project_verifier_view,
    render_actor_prompt,
)

EXPECTED_MANIFEST_HASH = "f6dd56a5b9a2937ad5e1964a25570a410e9be8720254551c78ca7f69e28226be"
EXPECTED_EVAL_HASHES = {
    "webqsp_smoke_20": "e8e6c393fecffcca9063b036c4802f50f0a86b0e0d1c219f50ca061e67585393",
    "webqsp_model_compare_150": "37276867bb297991e83c335a6d4bb4f5657642fae2c77fb16eeac56eb310628c",
    "cwq_model_compare_50": "fa5f957de02ac804253d722fc1cc1a22652450a0480a1b5b4bd582ab4c5cb25b",
}
EXPECTED_BASELINE_HASHES = {
    "main_freebase.py": "5e7e19083ffc774ee725a16686d28b519f67ab0fe05022062dc0c172bd3a5e16",
    "freebase_func.py": "b2e780762def28f0c68d71d9b41e61c9c015e7511810415a17a96169464ba31c",
    "utils.py": "78bdcccd2f5dc41fff61c04a947a52c0e9881eb91e67d6d45230ec0b717ddc81",
    "prompt_list.py": "2dfe1bd1ba8e3a978114d215f22da97a485251dba4219ddf6001eaa54b304841",
    "data_split.py": "f8087dacf4c70ad2461444b83d32d1f3879596ca135314d51058b303f1b9156f",
    "pog_w.sh": "952a06dd87836b760b93af71ada312b2cde8cfa0ab524032f5d4e41a2f814452",
}
FORBIDDEN_COMPOSITE_SNIPPETS = ("run_llm(", "relation_search_prune(", "def reasoning(")
ADAPTER_SOURCE_FILES = ("pog_adapter.py", "environment_binding.py", "answer_submission.py")
IO_REQUIRED_FIELDS = (
    "record_id",
    "source_type",
    "source_function",
    "constructed_at",
    "query_or_params",
    "raw_output",
    "canonical_output",
    "endpoint_or_snapshot_id",
    "contains_oracle_fields",
    "allowed_uses",
)


def _ok(name: str, metrics: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"name": name, "status": "PASS", "metrics": metrics}
    if extra:
        payload.update(extra)
    return payload


def _fail(name: str, metrics: Dict[str, Any], error: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"name": name, "status": "FAIL", "metrics": metrics, "error": error}
    if extra:
        payload.update(extra)
    return payload


def fixture_root(workspace: Workspace) -> Path:
    return workspace.tests_root / "fixtures" / "sp1"


def load_io_records(workspace: Workspace) -> List[Dict[str, Any]]:
    path = fixture_root(workspace) / "io_records.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload["records"] if isinstance(payload, dict) else payload
    for item in records:
        item["raw_sha256"] = sha256_text(canonical_json(item["raw_output"]))
    return records


def enabled_adapter() -> PoGAdapter:
    return PoGAdapter(adapter_enabled=True, allow_llm=False, allow_live_kg=False)


def disabled_adapter() -> PoGAdapter:
    return PoGAdapter(adapter_enabled=False, allow_llm=False, allow_live_kg=False)


def make_action(action_type: ActionType, params: Dict[str, Any], state: VisibleState, action_id: str = "a") -> Action:
    return Action(
        action_id=action_id,
        action_type=action_type,
        params=params,
        source_role=ActorRole.EXPLORER,
        state_id=state.state_id,
    )


def task_from_eval_row(row: Dict[str, Any]) -> TaskRecord:
    return TaskRecord(
        task_id=str(row["task_id"]),
        question=str(row["question"]),
        source_entities=list(row.get("source_entities") or []),
        source_entity_names=dict(row.get("source_entity_names") or {}),
        task_split=str(row.get("usage") or "eval"),
        task_generator_version="frozen-eval",
        input_snapshot_id=str(row.get("eval_set") or "unknown"),
        logical_query=str(row.get("logical_query") or "hidden-eval-label"),
        answer_entity_ids=list(row.get("answer_entity_ids") or []),
        normalized_answers=list(row.get("normalized_answers") or []),
        witness_paths=[],
        task_validity="valid",
        oracle_version="eval-set-frozen",
    )


def snapshot_from_eval_row(row: Dict[str, Any]) -> PoGSnapshot:
    sources = list(row.get("source_entities") or [])
    names = dict(row.get("source_entity_names") or {})
    return make_sp1_snapshot(
        task_id=str(row["task_id"]),
        question=str(row["question"]),
        source_entities=sources,
        topic_entity=names or {item: item for item in sources},
        frontier=list(sources),
        enumerated_relations=[],
        observed_triples=[],
        entid_name=names or {item: item for item in sources},
        name_entid={v: k for k, v in (names or {item: item for item in sources}).items()},
        decision_stage=DecisionStage.INIT.value,
    )


def experiment_e11(config: Dict[str, Any], workspace: Workspace) -> Dict[str, Any]:
    current = baseline_file_hashes(workspace)
    changed = [name for name, digest in EXPECTED_BASELINE_HASHES.items() if current.get(name) != digest]
    llm_text = '["people.person.friend", "people.person.born"]'
    entity_id = "m.alice"
    heads = ["people.person.friend"]
    tails = ["people.person.born"]
    original_rel = original_select_relations(llm_text, entity_id, heads, tails)
    disabled = disabled_adapter()
    wrapped_rel = disabled.passthrough(original_select_relations, llm_text, entity_id, heads, tails)
    bindings = [
        {"tailEntity": {"value": "http://rdf.freebase.com/ns/m.bob"}},
        {"tailEntity": {"value": "http://rdf.freebase.com/ns/m.cara"}},
    ]
    original_ent = original_entity_search("m.alice", "people.person.friend", True, bindings=bindings)
    wrapped_ent = disabled.passthrough(
        original_entity_search, "m.alice", "people.person.friend", True, bindings=bindings
    )
    original_reason = original_extract_reason_and_anwer(
        '{"R": "need more", "Answer": "", "Sufficient": "No"}'
    )
    wrapped_reason = disabled.passthrough(
        original_extract_reason_and_anwer, '{"R": "need more", "Answer": "", "Sufficient": "No"}'
    )
    equivalent = original_rel == wrapped_rel and original_ent == wrapped_ent and original_reason == wrapped_reason
    order_ok = original_ent == ["m.bob", "m.cara"]
    metrics = {
        "unregistered_baseline_hash_changes": len(changed),
        "adapter_disabled_equivalence_rate": 1.0 if equivalent and order_ok else 0.0,
    }
    extra = {"changed_baseline": changed, "original_rel": original_rel, "wrapped_rel": wrapped_rel}
    if not changed and equivalent and order_ok:
        return _ok("E1.1", metrics, extra)
    return _fail("E1.1", metrics, "baseline integrity or adapter-disabled equivalence failed", extra)


def experiment_e12(workspace: Workspace) -> Dict[str, Any]:
    stages = {item["protocol_stage"] for item in DECISION_MAP["stages"]}
    required = {"RELATION_SELECTION", "CONTINUE_STOP", "ANSWER_SUBMISSION", "BACKTRACK_RECOVERY"}
    covered = required <= stages
    source_hits = []
    for name in ADAPTER_SOURCE_FILES:
        text = (workspace.src_root / "sp_memory" / name).read_text(encoding="utf-8")
        for snippet in FORBIDDEN_COMPOSITE_SNIPPETS:
            if snippet in text:
                source_hits.append({"file": name, "snippet": snippet})
    composite_bound = []
    for stage in DECISION_MAP["stages"]:
        for fn in stage["functions"]:
            if fn.get("contains_llm") and fn.get("sp1_bound_as_environment"):
                composite_bound.append(fn["name"])
    guard = LLMCallGuard()
    adapter = enabled_adapter()
    snap = make_sp1_snapshot()
    state = adapter.project_visible_state(snap)
    action = make_action(
        ActionType.EXPAND,
        {"entity": "m.alice", "relation": "people.person.friend", "direction": "head"},
        state,
    )
    adapter.apply_action(snap, action, expand_recorded=["m.bob"])
    metrics = {
        "real_llm_calls": guard.calls,
        "decision_map_coverage": 1.0 if covered else 0.0,
        "composite_bound_as_environment": len(composite_bound),
        "forbidden_source_hits": len(source_hits),
    }
    extra = {"source_hits": source_hits, "composite_bound": composite_bound, "stages": sorted(stages)}
    if covered and not source_hits and not composite_bound and guard.calls == 0:
        return _ok("E1.2", metrics, extra)
    return _fail("E1.2", metrics, "decision boundary or LLM isolation failed", extra)


def experiment_e13() -> Dict[str, Any]:
    adapter = enabled_adapter()
    legal = make_sp1_snapshot(
        topic_entity={"m.alice": "Alice", "m.bob": "Bob"},
        source_entities=["m.alice"],
        frontier=["m.bob"],
        failed_or_exhausted_branches=["m.alice|missing|head"],
        action_history_summary=["EXPAND entity=m.alice relation=people.person.friend direction=head"],
        enumerated_relations=[
            {"entity": "m.bob", "relation": "people.person.place_of_birth", "direction": "head"},
            {"entity": "m.alice", "relation": "people.person.friend", "direction": "head"},
        ],
        observed_triples=[
            {"subject": "m.alice", "relation": "people.person.friend", "object": "m.bob"},
        ],
        depth_ent_rel_ent_dict={
            "1": {"m.alice": {"head": {"people.person.friend": ["m.bob"]}}},
        },
        ent_rel_ent_dict={"m.alice": {"head": {"people.person.friend": ["m.bob"]}}},
    )
    state = adapter.project_visible_state(legal)
    field_ok = (
        state.visible_entities == ["m.alice", "m.bob"]
        and state.frontier == ["m.bob"]
        and state.failed_or_exhausted_branches == ["m.alice|missing|head"]
        and len(state.visible_relations) == 2
        and state.observed_triples_or_summaries[0]["object"] == "m.bob"
        and "[FINISH_ID]" not in state.visible_entities
    )
    missing_rejected = 0
    missing_total = 0
    for drop in ("task_id", "budget", "decision_stage", "frontier"):
        missing_total += 1
        payload = {
            "task_id": "x",
            "question": "q",
            "source_entities": ["m.alice"],
            "topic_entity": {"m.alice": "Alice"},
            "ent_rel_ent_dict": {},
            "depth_ent_rel_ent_dict": {},
            "cluster_chain_of_entities": [],
            "frontier": ["m.alice"],
            "failed_or_exhausted_branches": [],
            "action_history_summary": [],
            "budget": default_sp1_budget().to_dict(),
            "decision_stage": "relation_selection",
        }
        del payload[drop]
        try:
            adapter.project_visible_state(payload)
        except ProtocolError:
            missing_rejected += 1
    schema_rejected = 0
    try:
        adapter.project_visible_state("not-an-object")
    except ProtocolError:
        schema_rejected += 1
    try:
        adapter.project_visible_state({**legal.__dict__, "budget": "bad"})
    except (ProtocolError, TypeError, AttributeError):
        schema_rejected += 1
    oracle_rejected = 0
    try:
        PoGSnapshot.from_dict(
            {
                "task_id": "x",
                "question": "q",
                "source_entities": ["m.alice"],
                "topic_entity": {"m.alice": "Alice"},
                "ent_rel_ent_dict": {},
                "depth_ent_rel_ent_dict": {},
                "cluster_chain_of_entities": [],
                "frontier": ["m.alice"],
                "failed_or_exhausted_branches": [],
                "action_history_summary": [],
                "budget": default_sp1_budget().to_dict(),
                "decision_stage": "relation_selection",
                "answer_entity_ids": ["m.gold"],
            }
        )
    except ProtocolError as exc:
        if exc.code is ViolationCode.ORACLE_LEAKAGE:
            oracle_rejected += 1
    metrics = {
        "legal_projection_success_rate": 1.0 if field_ok else 0.0,
        "missing_field_reject_rate": missing_rejected / missing_total if missing_total else 0.0,
        "schema_error_rejected": schema_rejected,
        "oracle_field_rejected": oracle_rejected,
    }
    if field_ok and missing_rejected == missing_total and schema_rejected == 2 and oracle_rejected == 1:
        return _ok("E1.3", metrics)
    return _fail("E1.3", metrics, "VisibleState projection contract failed")


def experiment_e14() -> Dict[str, Any]:
    adapter = enabled_adapter()
    base = make_sp1_snapshot(
        enumerated_relations=[
            {"entity": "m.bob", "relation": "b.rel", "direction": "tail"},
            {"entity": "m.alice", "relation": "a.rel", "direction": "head"},
        ],
        observed_triples=[
            {"subject": "m.bob", "relation": "r", "object": "m.cara"},
            {"subject": "m.alice", "relation": "r", "object": "m.bob"},
        ],
        frontier=["m.cara", "m.alice"],
        source_entities=["m.alice", "m.bob"],
    )
    hashes = [adapter.project_visible_state(base).state_id for _ in range(3)]
    shuffled = make_sp1_snapshot(
        enumerated_relations=[
            {"entity": "m.alice", "relation": "a.rel", "direction": "head"},
            {"entity": "m.bob", "relation": "b.rel", "direction": "tail"},
        ],
        observed_triples=[
            {"subject": "m.alice", "relation": "r", "object": "m.bob"},
            {"subject": "m.bob", "relation": "r", "object": "m.cara"},
        ],
        frontier=["m.alice", "m.cara"],
        source_entities=["m.bob", "m.alice"],
        topic_entity={"m.bob": "Bob", "m.alice": "Alice"},
        entid_name={"m.bob": "Bob", "m.alice": "Alice"},
        name_entid={"Bob": "m.bob", "Alice": "m.alice"},
    )
    shuffled_state = adapter.project_visible_state(shuffled)
    same = len(set(hashes)) == 1 and shuffled_state.state_id == hashes[0]
    mutated = make_sp1_snapshot(
        enumerated_relations=[
            {"entity": "m.bob", "relation": "b.rel", "direction": "tail"},
            {"entity": "m.alice", "relation": "a.rel", "direction": "head"},
        ],
        observed_triples=[
            {"subject": "m.bob", "relation": "r", "object": "m.cara"},
            {"subject": "m.alice", "relation": "r", "object": "m.bob"},
        ],
        frontier=["m.cara", "m.alice", "m.other"],
        source_entities=["m.alice", "m.bob"],
    )
    changed = adapter.project_visible_state(mutated).state_id != hashes[0]
    metrics = {
        "same_semantics_agreement": 1.0 if same else 0.0,
        "semantic_hash_change_rate": 1.0 if changed else 0.0,
    }
    if same and changed:
        return _ok("E1.4", metrics, {"state_id": hashes[0]})
    return _fail("E1.4", metrics, "canonicalization or state_id determinism failed")


def experiment_e15(workspace: Workspace) -> Dict[str, Any]:
    adapter = enabled_adapter()
    fixture_task = TaskRecord(
        task_id="sp1.leak.001",
        question="Where was Bob born?",
        source_entities=["m.alice"],
        source_entity_names={"m.alice": "Alice"},
        task_split="fixture",
        task_generator_version="sp1",
        input_snapshot_id="sp1",
        logical_query="SELECT ?x WHERE { m.bob people.person.place_of_birth ?x }",
        answer_entity_ids=["m.paris"],
        normalized_answers=["Paris"],
        witness_paths=[["m.alice", "m.bob", "m.paris"]],
        task_validity="valid",
        oracle_version="fixture",
    )
    snap = make_sp1_snapshot()
    state = adapter.project_visible_state(snap)
    secrets = OracleSecrets.from_task(fixture_task, future_neighbors=["m.hidden"])
    injections = [
        {"answer_entity_ids": ["m.paris"]},
        {"meta": {"normalized_answers": ["Paris"]}},
        {"metadata": {"logical_query": fixture_task.logical_query}},
        {"note": "visit m.paris"},
        {"future_neighbors": ["m.hidden"]},
        {"witness_paths": [["m.bob", "m.paris"]]},
        {"gold_path": "m.alice -> m.paris"},
    ]
    detected = 0
    for payload in injections:
        try:
            audit_object(payload, secrets=secrets, context="inject")
        except ProtocolError as exc:
            if exc.code is ViolationCode.ORACLE_LEAKAGE:
                detected += 1
    smoke_path = eval_set_paths(workspace)["webqsp_smoke_20"]
    rows = [json.loads(line) for line in smoke_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    actor_sensitive = 0
    critic_sensitive = 0
    verifier_ok = 0
    leak_back = 0
    for row in rows:
        task = task_from_eval_row(row)
        snap_row = snapshot_from_eval_row(row)
        vis = adapter.project_visible_state(snap_row)
        row_secrets = OracleSecrets.from_task(task)
        actor = project_actor_view(task, vis, row_secrets)
        critic = project_critic_view(task, vis, row_secrets)
        verifier = project_verifier_view(task, vis)
        actor_sensitive += count_sensitive_fields(actor)
        critic_sensitive += count_sensitive_fields(critic)
        if set(verifier["oracle"]["answer_entity_ids"]) == set(task.answer_entity_ids):
            verifier_ok += 1
        try:
            render_actor_prompt(actor, row_secrets)
        except ProtocolError:
            leak_back += 1
        if "answer_entity_ids" in actor["task"] or "normalized_answers" in actor["task"]:
            leak_back += 1
    metrics = {
        "leak_injection_detection_rate": detected / len(injections) if injections else 0.0,
        "webqsp_smoke_actor_critic_sensitive_fields": actor_sensitive + critic_sensitive,
        "verifier_label_readable": verifier_ok,
        "label_backflow_into_o0": leak_back,
        "smoke_n": len(rows),
    }
    passed = (
        detected == len(injections)
        and actor_sensitive + critic_sensitive == 0
        and verifier_ok == 20
        and leak_back == 0
        and len(rows) == 20
    )
    if passed:
        return _ok("E1.5", metrics)
    return _fail("E1.5", metrics, "O0 leakage check failed")


def experiment_e16() -> Dict[str, Any]:
    adapter = enabled_adapter()
    snap = make_sp1_snapshot(
        source_entities=["m.alice", "m.bob"],
        topic_entity={"m.alice": "Alice", "m.bob": "Bob"},
        frontier=["m.alice", "m.bob"],
        entid_name={"m.alice": "Alice", "m.bob": "Bob"},
        name_entid={"Alice": "m.alice", "Bob": "m.bob"},
        enumerated_relations=[
            {"entity": "m.alice", "relation": "people.person.friend", "direction": "head"},
            {"entity": "m.bob", "relation": "people.person.friend", "direction": "tail"},
            {"entity": "m.bob", "relation": "people.person.date_of_birth", "direction": "head"},
            {"entity": "m.alice", "relation": "people.person.missing", "direction": "head"},
            {"entity": "m.alice", "relation": "people.person.dead_end", "direction": "head"},
        ],
    )
    cases = []

    def run(direction, relation, recorded, entity="m.alice"):
        state = adapter.project_visible_state(snap)
        action = make_action(
            ActionType.EXPAND,
            {"entity": entity, "relation": relation, "direction": direction},
            state,
        )
        after, outcome, env = adapter.apply_action(snap.clone(), action, expand_recorded=recorded)
        return after, outcome, env

    head_after, head_out, head_env = run("head", "people.person.friend", ["m.bob"])
    tail_after, tail_out, tail_env = run("tail", "people.person.friend", ["m.alice"], entity="m.bob")
    empty_after, empty_out, empty_env = run("head", "people.person.missing", [])
    lit_after, lit_out, lit_env = run("head", "people.person.date_of_birth", ["1950-01-02"], entity="m.bob")
    finish_after, finish_out, finish_env = run("head", "people.person.dead_end", ["[FINISH_ID]"])
    dup_after, dup_out, dup_env = run("head", "people.person.friend", ["m.bob", "m.bob"])

    head_ok = head_env is not None and head_env.results == [
        {"subject": "m.alice", "relation": "people.person.friend", "object": "m.bob"}
    ]
    tail_ok = tail_env is not None and tail_env.results == [
        {"subject": "m.alice", "relation": "people.person.friend", "object": "m.bob"}
    ]
    empty_ok = empty_env is not None and empty_env.status is EnvironmentStatus.EMPTY_SUCCESS and empty_out.accepted
    literal_ok = lit_env is not None and lit_env.status is EnvironmentStatus.LITERAL
    finish_ok = (
        finish_env is not None
        and finish_env.status is EnvironmentStatus.EMPTY_SUCCESS
        and "[FINISH_ID]" not in finish_after.frontier
        and "[FINISH_ID]" not in adapter.project_visible_state(finish_after).visible_entities
    )
    dup_ok = dup_env is not None and dup_env.status is EnvironmentStatus.DUPLICATE
    pog_head = expand_action_to_pog_params({"entity": "m.alice", "relation": "r", "direction": "head"})
    roundtrip = pog_params_to_expand_action("m.alice", "r", pog_head["head"])
    roundtrip_ok = roundtrip["direction"] == "head" and direction_to_pog_head("head") is True
    reverse = 0
    if head_env and head_env.results and head_env.results[0]["subject"] != "m.alice":
        reverse += 1
    if tail_env and tail_env.results and tail_env.results[0]["object"] != "m.bob":
        reverse += 1

    illegal = 0
    illegal_ok = 0
    state = adapter.project_visible_state(snap)
    for params in (
        {"entity": "m.hidden", "relation": "people.person.friend", "direction": "head"},
        {"entity": "m.alice", "relation": "not.visible", "direction": "head"},
        {"entity": "m.alice", "relation": "people.person.friend", "direction": "sideways"},
    ):
        illegal += 1
        action = make_action(ActionType.EXPAND, params, state, action_id="bad")
        _, outcome, _ = adapter.apply_action(snap.clone(), action, expand_recorded=["m.x"])
        if not outcome.accepted:
            illegal_ok += 1

    metrics = {
        "legal_direction_mapping_rate": 1.0 if head_ok and tail_ok and roundtrip_ok else 0.0,
        "direction_reversals": reverse,
        "illegal_or_invisible_reject_rate": illegal_ok / illegal if illegal else 0.0,
        "empty_ok": empty_ok,
        "literal_ok": literal_ok,
        "finish_filtered": finish_ok,
        "duplicate_ok": dup_ok,
    }
    passed = (
        head_ok
        and tail_ok
        and roundtrip_ok
        and reverse == 0
        and illegal_ok == illegal
        and empty_ok
        and literal_ok
        and finish_ok
        and dup_ok
    )
    extra = {"cases": cases}
    if passed:
        return _ok("E1.6", metrics, extra)
    return _fail("E1.6", metrics, "EXPAND mapping failed", extra)


def experiment_e17() -> Dict[str, Any]:
    adapter = enabled_adapter()
    snap = make_sp1_snapshot(
        observed_triples=[{"subject": "m.alice", "relation": "people.person.friend", "object": "m.bob"}],
        frontier=["m.alice", "m.bob"],
        topic_entity={"m.alice": "Alice", "m.bob": "Bob"},
        entid_name={"m.alice": "Alice", "m.bob": "Bob", "m.twin": "Bob"},
        name_entid={"Alice": "m.alice", "Bob": "m.bob"},
        enumerated_relations=[{"entity": "m.alice", "relation": "people.person.friend", "direction": "head"}],
    )
    # literal observed
    snap_lit = make_sp1_snapshot(
        observed_triples=[{"subject": "m.bob", "relation": "people.person.date_of_birth", "object": "1950-01-02"}],
        frontier=["m.bob"],
        source_entities=["m.bob"],
        topic_entity={"m.bob": "Bob"},
        enumerated_relations=[],
        entid_name={"m.bob": "Bob"},
        name_entid={"Bob": "m.bob"},
    )
    state = adapter.project_visible_state(snap)
    lit_state = adapter.project_visible_state(snap_lit)
    texts = {
        "continue": '{"R": "need more hops", "Answer": "", "Sufficient": "No"}',
        "single": '{"R": "found friend", "Answer": "m.bob", "Sufficient": "Yes"}',
        "multi": '{"R": "two", "Answer": ["m.bob", "m.alice"], "Sufficient": "Yes"}',
        "name": '{"R": "name", "Answer": "Bob", "Sufficient": "Yes"}',
        "literal": '{"R": "dob", "Answer": "1950-01-02", "Sufficient": "Yes"}',
        "empty": '{"R": "none", "Answer": "", "Sufficient": "Yes"}',
        "unobserved": '{"R": "guess", "Answer": "m.paris", "Sufficient": "Yes"}',
        "malformed": "not a json object at all",
    }
    continue_r = submit_from_text(texts["continue"], state, snap)
    single_r = submit_from_text(texts["single"], state, snap)
    multi_r = submit_from_text(texts["multi"], state, snap)
    name_r = submit_from_text(texts["name"], state, snap)
    lit_r = submit_from_text(texts["literal"], lit_state, snap_lit)
    empty_r = submit_from_text(texts["empty"], state, snap)
    unobs_r = submit_from_text(texts["unobserved"], state, snap)
    mal_r = submit_from_text(texts["malformed"], state, snap)
    amb_snap = make_sp1_snapshot(
        observed_triples=[
            {"subject": "m.alice", "relation": "r", "object": "m.bob"},
            {"subject": "m.alice", "relation": "r", "object": "m.twin"},
        ],
        frontier=["m.alice", "m.bob", "m.twin"],
        source_entities=["m.alice"],
        topic_entity={"m.alice": "Alice", "m.bob": "Bob", "m.twin": "Bob"},
        entid_name={"m.alice": "Alice", "m.bob": "Bob", "m.twin": "Bob"},
        name_entid={"Alice": "m.alice", "Bob": "m.bob"},
        enumerated_relations=[],
    )
    amb_state = adapter.project_visible_state(amb_snap)
    amb_r = submit_from_text(texts["name"], amb_state, amb_snap)

    legal_stop = [
        single_r.status == "stop" and single_r.action is not None and single_r.action.params["answer_candidates"] == ["m.bob"],
        multi_r.status == "stop" and multi_r.action is not None and multi_r.action.params["answer_candidates"] == ["m.alice", "m.bob"],
        name_r.status == "stop" and name_r.action is not None and name_r.action.params["answer_candidates"] == ["m.bob"],
        lit_r.status == "stop" and lit_r.action is not None and lit_r.action.params["answer_candidates"] == ["1950-01-02"],
    ]
    illegal_accepted = sum(
        1
        for item in (empty_r, unobs_r, amb_r)
        if item.status == "stop"
    )
    structured = all(
        item.failure_class is FailureClass.ANSWER_EXTRACTION_FAILURE
        for item in (empty_r, unobs_r, amb_r, mal_r)
    )
    continue_ok = continue_r.status == "continue" and continue_r.action is not None
    metrics = {
        "observed_stop_construction_rate": sum(legal_stop) / len(legal_stop),
        "unobserved_or_ambiguous_accepted": illegal_accepted,
        "continue_mapped": continue_ok,
        "parse_failures_classified": structured,
    }
    if all(legal_stop) and illegal_accepted == 0 and structured and continue_ok:
        return _ok("E1.7", metrics)
    return _fail("E1.7", metrics, "answer submission contract failed")


def experiment_e18() -> Dict[str, Any]:
    adapter = enabled_adapter()
    snap = make_sp1_snapshot(
        observed_triples=[{"subject": "m.alice", "relation": "r", "object": "m.bob"}],
        frontier=["m.bob"],
        source_entities=["m.alice"],
        action_history_summary=["EXPAND entity=m.alice relation=r direction=head"],
    )
    state = adapter.project_visible_state(snap)
    mapped = adapter.map_recovery_to_select_frontier("m.alice", state)
    after, outcome, _ = adapter.apply_action(snap.clone(), mapped)
    select_ok = mapped.action_type is ActionType.SELECT_FRONTIER and outcome.accepted
    bt = make_action(
        ActionType.BACKTRACK,
        {"entity_or_state": "state:" + state.state_id},
        state,
        action_id="bt",
    )
    after_bt, out_bt, _ = adapter.apply_action(snap.clone(), bt)
    unsupported_success = 1 if out_bt.accepted else 0
    unchanged = after_bt.budget.to_dict() == snap.budget.to_dict() and after_bt.action_history_summary == snap.action_history_summary
    classified = (
        out_bt.visible_result.get("failure_class") == FailureClass.ACTION_SPACE_FAILURE.value
        and out_bt.visible_result.get("error_code") == ViolationCode.UNSUPPORTED_BACKTRACK_STATE.value
    )
    metrics = {
        "select_frontier_mapping_rate": 1.0 if select_ok else 0.0,
        "unsupported_backtrack_false_success": unsupported_success,
        "state_budget_unchanged_on_reject": unchanged,
        "classified": classified,
    }
    if select_ok and unsupported_success == 0 and unchanged and classified:
        return _ok("E1.8", metrics)
    return _fail("E1.8", metrics, "recovery / backtrack policy failed")


def experiment_e19() -> Dict[str, Any]:
    adapter = enabled_adapter()
    snap = make_sp1_snapshot()
    before = snap.budget.to_dict()
    applied_enum, enum_result = adapter.apply_relation_enumeration(
        snap.clone(),
        "m.alice",
        head_relations=["people.person.friend"],
        tail_relations=["people.person.born"],
    )
    enum_delta = applied_enum.budget.used_kg_calls - snap.budget.used_kg_calls
    enum_steps = applied_enum.budget.used_steps - snap.budget.used_steps
    state = adapter.project_visible_state(applied_enum)
    expand = make_action(
        ActionType.EXPAND,
        {"entity": "m.alice", "relation": "people.person.friend", "direction": "head"},
        state,
    )
    after_ok, out_ok, env_ok = adapter.apply_action(applied_enum.clone(), expand, expand_recorded=["m.bob"])
    empty_state_snap = applied_enum.clone()
    empty_state_snap.enumerated_relations = list(applied_enum.enumerated_relations) + [
        VisibleRelation(
            entity="m.alice", relation="people.person.missing", direction=Direction.HEAD
        )
    ]
    empty_vis = adapter.project_visible_state(empty_state_snap)
    empty_act = make_action(
        ActionType.EXPAND,
        {"entity": "m.alice", "relation": "people.person.missing", "direction": "head"},
        empty_vis,
        action_id="empty",
    )
    after_empty, out_empty, env_empty = adapter.apply_action(
        empty_state_snap.clone(), empty_act, expand_recorded=[]
    )
    rejected_vis = adapter.project_visible_state(applied_enum)
    bad = make_action(
        ActionType.EXPAND,
        {"entity": "m.hidden", "relation": "people.person.friend", "direction": "head"},
        rejected_vis,
        action_id="bad",
    )
    after_bad, out_bad, _ = adapter.apply_action(applied_enum.clone(), bad, expand_recorded=["m.x"])
    stop = submit_from_text(
        '{"R": "ok", "Answer": "m.bob", "Sufficient": "Yes"}',
        adapter.project_visible_state(after_ok),
        after_ok,
    )
    after_stop, out_stop, _ = adapter.apply_action(after_ok.clone(), stop.action)
    tiny = make_sp1_snapshot()
    tiny.budget.used_kg_calls = tiny.budget.max_kg_calls
    _, over_enum = adapter.apply_relation_enumeration(
        tiny, "m.alice", head_relations=["r"], tail_relations=["t"]
    )
    tiny_steps = make_sp1_snapshot()
    tiny_steps.budget.used_steps = tiny_steps.budget.max_steps
    tiny_state = adapter.project_visible_state(tiny_steps)
    over_act = make_action(
        ActionType.CONTINUE,
        {},
        tiny_state,
        action_id="over",
    )
    _, over_out, _ = adapter.apply_action(tiny_steps, over_act)
    sys_env = EnvironmentBinding()

    def boom(kind, **params):
        raise RuntimeError("injected")

    sys_binding = EnvironmentBinding(executor=boom)
    sys_adapter = PoGAdapter(
        adapter_enabled=True, allow_llm=False, allow_live_kg=False, environment=sys_binding
    )
    sys_snap = make_sp1_snapshot()
    sys_state = sys_adapter.project_visible_state(sys_snap)
    sys_act = make_action(
        ActionType.EXPAND,
        {"entity": "m.alice", "relation": "people.person.friend", "direction": "head"},
        sys_state,
    )
    after_sys, out_sys, env_sys = sys_adapter.apply_action(sys_snap, sys_act)

    llm_zero = all(
        item.budget.used_llm_calls == 0 and item.budget.used_critic_rounds == 0
        for item in (applied_enum, after_ok, after_empty, after_bad, after_stop, after_sys)
    )
    deltas_ok = (
        enum_delta == 2
        and enum_steps == 0
        and out_ok.budget_delta["steps"] == 1
        and out_ok.budget_delta["kg_calls"] == 1
        and out_ok.budget_delta["depth"] == 1
        and out_empty.budget_delta["depth"] == 0
        and out_empty.accepted
        and not out_bad.accepted
        and after_bad.budget.used_steps == applied_enum.budget.used_steps
        and out_stop.budget_delta["steps"] == 1
        and over_enum.failure_class is FailureClass.BUDGET_INSUFFICIENT
        and not over_out.accepted
        and env_sys is not None
        and env_sys.failure_class is FailureClass.SYSTEM_FAILURE
        and not out_sys.accepted
    )
    executed_over_limit = 1 if over_out.accepted else 0
    metrics = {
        "budget_delta_correct_rate": 1.0 if deltas_ok else 0.0,
        "used_llm_calls": 0 if llm_zero else 1,
        "used_critic_rounds": 0 if llm_zero else 1,
        "over_limit_executed": executed_over_limit,
    }
    extra = {"before": before, "enum_delta": enum_delta}
    if deltas_ok and llm_zero and executed_over_limit == 0:
        return _ok("E1.9", metrics, extra)
    return _fail("E1.9", metrics, "budget/counter contract failed", extra)


def experiment_e10() -> Dict[str, Any]:
    binding = EnvironmentBinding()
    ok = binding.expand("m.alice", "r", Direction.HEAD, recorded=["m.bob"])
    empty = binding.expand("m.alice", "r", Direction.HEAD, recorded=[])
    timeout_binding = EnvironmentBinding(
        executor=lambda kind, **params: (_ for _ in ()).throw(KgTimeout("timeout"))
    )
    timeout = timeout_binding.expand("m.alice", "r", Direction.HEAD)
    malformed_binding = EnvironmentBinding(
        executor=lambda kind, **params: (_ for _ in ()).throw(MalformedKgResponse("bad json"))
    )
    malformed = malformed_binding.expand("m.alice", "r", Direction.HEAD)
    schema = binding.expand("", "r", Direction.HEAD, recorded=["m.x"])
    unknown_binding = EnvironmentBinding(executor=lambda kind, **params: (_ for _ in ()).throw(ValueError("nope")))
    unknown = unknown_binding.expand("m.alice", "r", Direction.HEAD)
    empty_vs_system = empty.status is EnvironmentStatus.EMPTY_SUCCESS and empty.failure_class is None
    classified = [
        ok.status is EnvironmentStatus.SUCCESS and ok.failure_class is None,
        empty_vs_system,
        timeout.status is EnvironmentStatus.TIMEOUT and timeout.failure_class is FailureClass.SYSTEM_FAILURE,
        malformed.status is EnvironmentStatus.MALFORMED and malformed.failure_class is FailureClass.SYSTEM_FAILURE,
        schema.status is EnvironmentStatus.SCHEMA_ERROR,
        unknown.status is EnvironmentStatus.SYSTEM_ERROR
        and unknown.failure_class is FailureClass.SYSTEM_FAILURE
        and bool(unknown.traceback_text),
    ]
    metrics = {
        "empty_vs_system_separated": 1.0 if empty_vs_system else 0.0,
        "expected_classification_rate": sum(classified) / len(classified),
        "unclassified_exceptions": 0 if all(classified) else 1,
    }
    if all(classified):
        return _ok("E1.10", metrics)
    return _fail("E1.10", metrics, "environment failure classification failed")


def experiment_e111(workspace: Workspace) -> Dict[str, Any]:
    records = load_io_records(workspace)
    complete = 0
    oracle_in_actor = 0
    replay_ok = 0
    replay_total = 0
    adapter = enabled_adapter()
    for item in records:
        missing = [name for name in IO_REQUIRED_FIELDS if name not in item]
        if not missing and item.get("raw_sha256"):
            complete += 1
        if item.get("contains_oracle_fields") and "sp1_interface_test" in (item.get("allowed_uses") or []):
            oracle_in_actor += 1
        if item.get("contains_oracle_fields"):
            continue
        if item.get("source_function") != "entity_search":
            continue
        params = item["query_or_params"]
        direction = Direction.HEAD if params.get("head") else Direction.TAIL
        hashes = []
        replay_total += 1
        for _ in range(3):
            env = EnvironmentBinding()
            result = env.expand(params["entity"], params["relation"], direction, recorded=item["raw_output"])
            hashes.append(canonical_hash(result.results))
            snap = make_sp1_snapshot(
                enumerated_relations=[
                    {
                        "entity": params["entity"],
                        "relation": params["relation"],
                        "direction": direction.value,
                    }
                ],
                frontier=[params["entity"]],
                source_entities=[params["entity"]],
                topic_entity={params["entity"]: params["entity"]},
            )
            state = adapter.project_visible_state(snap)
            action = make_action(
                ActionType.EXPAND,
                {"entity": params["entity"], "relation": params["relation"], "direction": direction.value},
                state,
            )
            _, outcome, _ = adapter.apply_action(snap, action, expand_recorded=item["raw_output"])
            hashes.append(outcome.state_id_after)
        if len(set(hashes[0::2])) == 1 and len(set(hashes[1::2])) == 1:
            replay_ok += 1
    metrics = {
        "source_complete_rate": complete / len(records) if records else 0.0,
        "replay_agreement": replay_ok / replay_total if replay_total else 0.0,
        "oracle_fixture_in_actor": oracle_in_actor,
        "record_count": len(records),
    }
    if complete == len(records) and replay_ok == replay_total and oracle_in_actor == 0:
        return _ok("E1.11", metrics)
    return _fail("E1.11", metrics, "fixture provenance or replay failed")


def experiment_e112(config: Dict[str, Any], workspace: Workspace) -> Dict[str, Any]:
    sp0_config, _, _ = load_config(workspace.configs_root / "sp0_protocol_v1.json", workspace)
    verified = verify_eval_sets(sp0_config, workspace)
    manifest_hash = verified["manifest"]["manifest_hash"]
    paths = eval_set_paths(workspace)
    file_hashes = {name: sha256_file(path) for name, path in paths.items()}
    hash_changes = sum(1 for name, digest in EXPECTED_EVAL_HASHES.items() if file_hashes.get(name) != digest)
    vectors = json.loads((fixture_root(workspace) / "normalization_vectors.json").read_text(encoding="utf-8"))
    vector_ok = 0
    for item in vectors["vectors"]:
        got = normalize_question(item["raw"])
        digest = normalized_question_hash(item["raw"])
        if got == item["expected_normalized"] and digest == item["expected_sha256"]:
            vector_ok += 1
    first = build_formal_exclusion_registry(workspace)
    second = build_formal_exclusion_registry(workspace)
    write_exclusion_registry(first, workspace)
    fixture_path = workspace.tests_root / "fixtures" / "exclusion_records_sp0.json"
    fixture_records = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture_ids = {item["task_id"] for item in fixture_records}
    formal_ids = {item["task_id"] for item in first["records"]}
    mixed = bool(fixture_ids & formal_ids)
    failure = _e112_failure_fixture(sp0_config)
    metrics = {
        "fixed_eval_hash_changes": hash_changes,
        "manifest_hash_ok": manifest_hash == EXPECTED_MANIFEST_HASH,
        "normalization_vector_pass_rate": vector_ok / len(vectors["vectors"]) if vectors["vectors"] else 0.0,
        "exclusion_record_count": first["count"],
        "exclusion_rebuild_agreement": 1.0 if first["content_hash"] == second["content_hash"] else 0.0,
        "fixture_mixed_into_formal": mixed,
        "failure_fixture_failed_as_expected": failure["failed_as_expected"],
        "question_normalization_version": first["question_normalization_version"],
    }
    extra = {
        "exclusion_content_hash": first["content_hash"],
        "file_hashes": file_hashes,
        "failure": failure,
        "normalization_version": QUESTION_NORMALIZATION_VERSION,
    }
    passed = (
        hash_changes == 0
        and manifest_hash == EXPECTED_MANIFEST_HASH
        and vector_ok == len(vectors["vectors"])
        and first["count"] == 220
        and first["content_hash"] == second["content_hash"]
        and not mixed
        and failure["failed_as_expected"]
        and first["question_normalization_version"] == QUESTION_NORMALIZATION_VERSION
    )
    if passed:
        return _ok("E1.12", metrics, extra)
    return _fail("E1.12", metrics, "eval freeze / exclusion / one-click fixture failed", extra)


def _e112_failure_fixture(sp0_config: Dict[str, Any]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        sp = tmp_root / "self-play"
        data = tmp_root / "data"
        alias = tmp_root / "cope_alias"
        pog = tmp_root / "PoG"
        for path in (sp, data, alias, pog):
            path.mkdir(parents=True)
        fake_ws = Workspace.for_tests(sp, data, alias, pog)
        try:
            verify_eval_sets(sp0_config, fake_ws)
            return {"failed_as_expected": False, "error": None}
        except ProtocolError as exc:
            return {"failed_as_expected": True, "error": exc.to_dict()}


def summarize_metrics(experiments: Dict[str, Any]) -> Dict[str, Any]:
    names = [f"E1.{i}" for i in range(1, 13)]
    passed = sum(experiments[name]["status"] == "PASS" for name in names if name in experiments)
    e11 = experiments["E1.1"]["metrics"]
    e12 = experiments["E1.2"]["metrics"]
    e15 = experiments["E1.5"]["metrics"]
    e16 = experiments["E1.6"]["metrics"]
    e17 = experiments["E1.7"]["metrics"]
    e18 = experiments["E1.8"]["metrics"]
    e19 = experiments["E1.9"]["metrics"]
    e111 = experiments["E1.11"]["metrics"]
    e112 = experiments["E1.12"]["metrics"]
    return {
        "e1_pass_rate": passed / 12,
        "real_llm_calls": e12["real_llm_calls"],
        "sp1_live_kg_calls": 0,
        "adapter_disabled_equivalence_rate": e11["adapter_disabled_equivalence_rate"],
        "o0_leak_detection_rate": e15["leak_injection_detection_rate"],
        "webqsp_smoke_actor_critic_sensitive_fields": e15["webqsp_smoke_actor_critic_sensitive_fields"],
        "head_tail_mapping_rate": e16["legal_direction_mapping_rate"],
        "direction_reversals": e16["direction_reversals"],
        "unobserved_or_ambiguous_accepted": e17["unobserved_or_ambiguous_accepted"],
        "unsupported_backtrack_false_success": e18["unsupported_backtrack_false_success"],
        "budget_delta_correct_rate": e19["budget_delta_correct_rate"],
        "replay_agreement": e111["replay_agreement"],
        "normalization_vector_pass_rate": e112["normalization_vector_pass_rate"],
        "exclusion_record_count": e112["exclusion_record_count"],
        "fixed_eval_hash_changes": e112["fixed_eval_hash_changes"],
        "unclassified_exceptions": experiments["E1.10"]["metrics"]["unclassified_exceptions"],
        "unregistered_baseline_hash_changes": e11["unregistered_baseline_hash_changes"],
    }


def preflight(config: Dict[str, Any], workspace: Workspace) -> Dict[str, Any]:
    errors = []
    if config.get("allow_llm") is not False:
        errors.append("allow_llm must be false")
    if config.get("allow_live_kg") is not False:
        errors.append("allow_live_kg must be false")
    if config.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("protocol_version mismatch")
    if config.get("eval_manifest_hash") != EXPECTED_MANIFEST_HASH:
        errors.append("eval_manifest_hash mismatch")
    current = baseline_file_hashes(workspace)
    for name, digest in EXPECTED_BASELINE_HASHES.items():
        if current.get(name) != digest:
            errors.append(f"baseline hash changed: {name}")
    sp0_path = workspace.configs_root / "sp0_protocol_v1.json"
    sp0_config, _, _ = load_config(sp0_path, workspace)
    try:
        verified = verify_eval_sets(sp0_config, workspace)
        if verified["manifest"]["manifest_hash"] != EXPECTED_MANIFEST_HASH:
            errors.append("frozen manifest hash mismatch")
    except ProtocolError as exc:
        errors.append(exc.message)
    return {"ok": not errors, "errors": errors}


def run_all_sp1_experiments(config: Dict[str, Any], workspace: Workspace) -> Dict[str, Any]:
    unclassified: List[Dict[str, Any]] = []
    experiments: Dict[str, Any] = {}

    def capture(name, fn):
        try:
            experiments[name] = fn()
        except Exception as exc:
            unclassified.append({"name": name, "error": repr(exc), "traceback": traceback.format_exc()})
            experiments[name] = _fail(name, {}, repr(exc), {"traceback": traceback.format_exc()})

    capture("E1.1", lambda: experiment_e11(config, workspace))
    capture("E1.2", lambda: experiment_e12(workspace))
    capture("E1.3", experiment_e13)
    capture("E1.4", experiment_e14)
    capture("E1.5", lambda: experiment_e15(workspace))
    capture("E1.6", experiment_e16)
    capture("E1.7", experiment_e17)
    capture("E1.8", experiment_e18)
    capture("E1.9", experiment_e19)
    capture("E1.10", experiment_e10)
    capture("E1.11", lambda: experiment_e111(workspace))
    capture("E1.12", lambda: experiment_e112(config, workspace))

    try:
        metrics = summarize_metrics(experiments)
        metrics["unclassified_exceptions"] = int(metrics.get("unclassified_exceptions") or 0) + len(unclassified)
    except Exception:
        metrics = {
            "e1_pass_rate": sum(experiments.get(f"E1.{i}", {}).get("status") == "PASS" for i in range(1, 13)) / 12,
            "unclassified_exceptions": len(unclassified),
        }
    all_pass = (
        len(experiments) == 12
        and all(item["status"] == "PASS" for item in experiments.values())
        and not unclassified
    )
    decision_path = workspace.artifacts_root / "protocol" / "pog_decision_map_v1.json"
    workspace.safe_write_text(decision_path, canonical_json(DECISION_MAP) + "\n")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "plan_version": config.get("plan_version"),
        "status": "PASS" if all_pass else "FAIL",
        "experiments": experiments,
        "metrics": metrics,
        "unclassified_exceptions": unclassified,
        "decision_map": str(decision_path),
        "live_kg_calls": 0,
        "llm_calls": metrics.get("real_llm_calls", 0),
    }
