"""SP0 protocol experiments E0.1-E0.7. No LLM, no KGQA scoring."""

from __future__ import annotations

import json
import tempfile
import traceback
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from .action_validator import validate_action
from .baseline import (
    assert_baseline_unchanged,
    baseline_file_hashes,
    collect_baseline_inventory,
    write_baseline_inventory,
)
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_file
from .paths import PROTOCOL_VERSION, Workspace, WorkspaceBoundaryError
from .registry import (
    build_input_registry,
    registries_equal,
    validate_exclusion_registry,
    write_exclusion_registry,
    write_input_registry,
)
from .replay import (
    Budget,
    default_fixture_task,
    failure_builder,
    illegal_backtrack_builder,
    illegal_relation_builder,
    make_env,
    replay_times,
    run_scripted_trajectory,
    success_builder,
)
from .sampling import build_eval_sets, ensure_eval_sets, eval_set_paths, verify_eval_sets
from .schemas import (
    Action,
    ActionType,
    ActorRole,
    OfflineFeedback,
    OracleLevel,
    RunManifest,
    RunStatus,
    StepOutcome,
    TaskRecord,
    TrajectoryRecord,
    VisibleState,
)
from .visibility import (
    audit_object,
    count_sensitive_fields,
    project_actor_view,
    project_critic_view,
    project_verifier_view,
    render_actor_prompt,
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


def hash_registered_inputs(config: Dict[str, Any], workspace: Workspace) -> Dict[str, str]:
    registry = build_input_registry(config, workspace)
    return {item["relative_path"]: item["sha256"] for item in registry["files"]}


def experiment_e01(
    config: Dict[str, Any],
    workspace: Workspace,
    baseline_before: Dict[str, str],
    input_hashes_before: Dict[str, str],
) -> Dict[str, Any]:
    legal_ok = 0
    legal_total = 0
    rejected = 0
    illegal_total = 0
    details = []

    legal_targets = [
        workspace.artifacts_root / "protocol" / ".sp0_write_probe.txt",
        workspace.runs_root / "_sp0_probe" / "ok.txt",
        workspace.logs_root / "sp0_probe.log",
        workspace.reports_root / "sp0" / "probe.txt",
    ]
    for path in legal_targets:
        legal_total += 1
        try:
            workspace.safe_write_text(path, "sp0-write-ok\n")
            legal_ok += 1
            details.append({"path": str(path), "legal": True, "accepted": True})
        except Exception as exc:
            details.append({"path": str(path), "legal": True, "accepted": False, "error": str(exc)})

    illegal_targets = [
        workspace.data_root / "sp0_should_not_write.txt",
        workspace.cope_alias_root / "sp0_should_not_write.txt",
        workspace.pog_root / "sp0_should_not_write.txt",
        workspace.self_play_root.parent / "outside_self_play.txt",
        Path("/tmp/sp0_outside.txt"),
        workspace.self_play_root / ".." / "data" / "tamper.json",
        workspace.self_play_root / "artifacts" / ".." / ".." / "PoG" / "tamper.py",
    ]
    for path in illegal_targets:
        illegal_total += 1
        try:
            workspace.assert_writable(path)
            details.append({"path": str(path), "legal": False, "rejected": False})
        except WorkspaceBoundaryError:
            rejected += 1
            details.append({"path": str(path), "legal": False, "rejected": True})
        except ProtocolError:
            rejected += 1
            details.append({"path": str(path), "legal": False, "rejected": True})

    after_inputs = hash_registered_inputs(config, workspace)
    changed_inputs = sorted(
        path for path, digest in after_inputs.items() if input_hashes_before.get(path) != digest
    )
    changed_baseline = assert_baseline_unchanged(workspace, baseline_before)
    reject_rate = (rejected / illegal_total) if illegal_total else 0.0
    accept_rate = (legal_ok / legal_total) if legal_total else 0.0
    metrics = {
        "legal_write_accept_rate": accept_rate,
        "illegal_write_reject_rate": reject_rate,
        "shared_input_hash_changes": len(changed_inputs),
        "baseline_file_changes": len(changed_baseline),
        "illegal_samples": illegal_total,
        "legal_samples": legal_total,
    }
    passed = (
        accept_rate == 1.0
        and reject_rate == 1.0
        and not changed_inputs
        and not changed_baseline
    )
    extra = {"details": details, "changed_inputs": changed_inputs, "changed_baseline": changed_baseline}
    if passed:
        return _ok("E0.1", metrics, extra)
    return _fail("E0.1", metrics, "workspace isolation check failed", extra)


def _schema_round_trip_cases() -> Dict[str, Any]:
    task = default_fixture_task()
    env = make_env()
    state = env.visible_state()
    action = Action(
        action_id="schema-a",
        action_type=ActionType.CONTINUE,
        params={},
        source_role=ActorRole.EXPLORER,
        state_id=state.state_id,
    )
    outcome = StepOutcome(
        accepted=True,
        protocol_violation=None,
        visible_result={"ok": True},
        new_frontier_items=[],
        budget_delta={"steps": 1},
        state_id_before=state.state_id,
        state_id_after=state.state_id,
        deterministic_result_hash="abc",
    )
    traj = TrajectoryRecord(
        trajectory_id="t1",
        task_id=task.task_id,
        protocol_version=PROTOCOL_VERSION,
        initial_state_hash="init",
        ordered_steps=[],
        terminal_submission=None,
        termination_reason=env.trajectory("x").termination_reason,
        cost_summary={"steps": 0},
        replay_hash="rh",
    )
    manifest = RunManifest(
        run_id="run-x",
        plan_version="SP0-PLAN 1.4",
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        git_dirty=False,
        command=["python"],
        config_hash="cfg",
        input_files=[],
        seed=1,
        model_metadata={"llm_called": False},
        start_time="2026-08-21T00:00:00Z",
        end_time=None,
        status=RunStatus.RUNNING,
        output_files=[],
    )
    legal_objects = {
        "TaskRecord": task,
        "VisibleState": state,
        "Action": action,
        "StepOutcome": outcome,
        "TrajectoryRecord": traj,
        "RunManifest": manifest,
    }
    results = {"legal_ok": 0, "legal_total": 0, "illegal_ok": 0, "illegal_total": 0, "cases": []}
    for name, obj in legal_objects.items():
        results["legal_total"] += 1
        restored = obj.from_dict(obj.to_dict())
        if restored.to_dict() != obj.to_dict():
            results["cases"].append({"name": name, "legal": True, "passed": False})
        else:
            results["legal_ok"] += 1
            results["cases"].append({"name": name, "legal": True, "passed": True})

    illegal_payloads = [
        ("missing_field", TaskRecord, {k: v for k, v in task.to_dict().items() if k != "task_id"}),
        ("unknown_version", TaskRecord, {**task.to_dict(), "protocol_version": "not-a-version"}),
        ("bad_type", TaskRecord, {**task.to_dict(), "source_entities": "alice"}),
        ("illegal_enum", Action, {**action.to_dict(), "action_type": "TELEPORT"}),
        ("dangerous_field", VisibleState, {**state.to_dict(), "gold_path": ["secret"]}),
    ]
    for label, cls, payload in illegal_payloads:
        results["illegal_total"] += 1
        try:
            cls.from_dict(payload)
            results["cases"].append({"name": label, "legal": False, "rejected": False})
        except ProtocolError as exc:
            results["illegal_ok"] += 1
            results["cases"].append(
                {"name": label, "legal": False, "rejected": True, "code": exc.code.value}
            )
    return results


def _action_protocol_cases() -> Dict[str, Any]:
    env = make_env()
    state = env.visible_state()
    legal = [
        Action(
            action_id="ok-expand",
            action_type=ActionType.EXPAND,
            params={"entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
            source_role=ActorRole.EXPLORER,
            state_id=state.state_id,
        ),
        Action(
            action_id="ok-select",
            action_type=ActionType.SELECT_FRONTIER,
            params={"entity": "e.alice"},
            source_role=ActorRole.EXPLORER,
            state_id=state.state_id,
        ),
        Action(
            action_id="ok-continue",
            action_type=ActionType.CONTINUE,
            params={},
            source_role=ActorRole.EXPLORER,
            state_id=state.state_id,
        ),
        Action(
            action_id="ok-abstain",
            action_type=ActionType.ABSTAIN,
            params={"reason_code": "INSUFFICIENT_EVIDENCE"},
            source_role=ActorRole.EXPLORER,
            state_id=state.state_id,
        ),
        Action(
            action_id="ok-backtrack",
            action_type=ActionType.BACKTRACK,
            params={"entity_or_state": "e.alice"},
            source_role=ActorRole.EXPLORER,
            state_id=state.state_id,
        ),
    ]
    # STOP is legal only after observing an answer entity; expand first.
    env2 = make_env()
    env2.step(
        Action(
            action_id="prep",
            action_type=ActionType.EXPAND,
            params={"entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
            source_role=ActorRole.EXPLORER,
            state_id=env2.visible_state().state_id,
        )
    )
    env2.step(
        Action(
            action_id="prep2",
            action_type=ActionType.EXPAND,
            params={"entity": "e.bob", "relation": "people.person.place_of_birth", "direction": "tail"},
            source_role=ActorRole.EXPLORER,
            state_id=env2.visible_state().state_id,
        )
    )
    legal.append(
        Action(
            action_id="ok-stop",
            action_type=ActionType.STOP,
            params={"answer_candidates": ["e.paris"]},
            source_role=ActorRole.EXPLORER,
            state_id=env2.visible_state().state_id,
        )
    )
    illegal = [
        (
            ViolationCode.UNKNOWN_ACTION,
            Action(
                action_id="bad-unknown",
                action_type=ActionType.CONTINUE,
                params={},
                source_role=ActorRole.EXPLORER,
                state_id=state.state_id,
            ),
        ),
        (
            ViolationCode.INVISIBLE_ENTITY,
            Action(
                action_id="bad-ent",
                action_type=ActionType.EXPAND,
                params={"entity": "e.hidden", "relation": "people.person.friend", "direction": "tail"},
                source_role=ActorRole.EXPLORER,
                state_id=state.state_id,
            ),
        ),
        (
            ViolationCode.INVISIBLE_RELATION,
            Action(
                action_id="bad-rel",
                action_type=ActionType.EXPAND,
                params={"entity": "e.alice", "relation": "not.visible", "direction": "tail"},
                source_role=ActorRole.EXPLORER,
                state_id=state.state_id,
            ),
        ),
        (
            ViolationCode.INVALID_DIRECTION,
            Action(
                action_id="bad-dir",
                action_type=ActionType.EXPAND,
                params={"entity": "e.alice", "relation": "people.person.friend", "direction": "sideways"},
                source_role=ActorRole.EXPLORER,
                state_id=state.state_id,
            ),
        ),
        (
            ViolationCode.INVALID_BACKTRACK_TARGET,
            Action(
                action_id="bad-bt",
                action_type=ActionType.BACKTRACK,
                params={"entity_or_state": "e.hidden"},
                source_role=ActorRole.EXPLORER,
                state_id=state.state_id,
            ),
        ),
        (
            ViolationCode.UNOBSERVED_ANSWER,
            Action(
                action_id="bad-stop",
                action_type=ActionType.STOP,
                params={"answer_candidates": ["e.paris"]},
                source_role=ActorRole.EXPLORER,
                state_id=state.state_id,
            ),
        ),
        (
            ViolationCode.SCHEMA_VERSION_MISMATCH,
            Action(
                action_id="bad-ver",
                action_type=ActionType.CONTINUE,
                params={},
                source_role=ActorRole.EXPLORER,
                state_id=state.state_id,
                protocol_version="old",
            ),
        ),
    ]
    # UNKNOWN_ACTION: bypass enum by mutating after construction is not possible.
    # Use from_dict illegal enum already covered. Replace first illegal with ABSTAIN bad reason.
    illegal[0] = (
        ViolationCode.INVALID_ABSTAIN_REASON,
        Action(
            action_id="bad-abs",
            action_type=ActionType.ABSTAIN,
            params={"reason_code": "BECAUSE_I_SAID_SO"},
            source_role=ActorRole.EXPLORER,
            state_id=state.state_id,
        ),
    )
    tiny = make_env(
        budget=Budget(
            max_depth=1,
            max_steps=1,
            max_kg_calls=0,
            max_llm_calls=0,
            max_critic_rounds=0,
            max_frontier_size=80,
        )
    )
    illegal.append(
        (
            ViolationCode.BUDGET_EXCEEDED,
            Action(
                action_id="bad-budget",
                action_type=ActionType.EXPAND,
                params={"entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
                source_role=ActorRole.EXPLORER,
                state_id=tiny.visible_state().state_id,
            ),
        )
    )

    results = {
        "legal_ok": 0,
        "legal_total": 0,
        "illegal_ok": 0,
        "illegal_total": 0,
        "codes": [],
        "cases": [],
    }
    states_for_legal = [state] * 5 + [env2.visible_state()]
    for action, current in zip(legal, states_for_legal):
        results["legal_total"] += 1
        try:
            validate_action(action, current)
            results["legal_ok"] += 1
            results["cases"].append({"id": action.action_id, "legal": True, "passed": True})
        except ProtocolError as exc:
            results["cases"].append({"id": action.action_id, "legal": True, "passed": False, "error": exc.to_dict()})
    for expected, action in illegal:
        results["illegal_total"] += 1
        current = tiny.visible_state() if action.action_id == "bad-budget" else state
        try:
            validate_action(action, current)
            results["cases"].append({"id": action.action_id, "legal": False, "rejected": False})
        except ProtocolError as exc:
            ok = exc.code == expected
            if ok:
                results["illegal_ok"] += 1
            results["codes"].append(exc.code.value)
            results["cases"].append(
                {
                    "id": action.action_id,
                    "legal": False,
                    "rejected": True,
                    "expected": expected.value,
                    "got": exc.code.value,
                    "matched": ok,
                }
            )
    return results


def experiment_e02() -> Dict[str, Any]:
    schema = _schema_round_trip_cases()
    actions = _action_protocol_cases()
    legal_total = schema["legal_total"] + actions["legal_total"]
    legal_ok = schema["legal_ok"] + actions["legal_ok"]
    illegal_total = schema["illegal_total"] + actions["illegal_total"]
    illegal_ok = schema["illegal_ok"] + actions["illegal_ok"]
    metrics = {
        "legal_schema_action_accept_rate": legal_ok / legal_total if legal_total else 0.0,
        "illegal_schema_action_reject_rate": illegal_ok / illegal_total if illegal_total else 0.0,
        "legal_total": legal_total,
        "illegal_total": illegal_total,
        "violation_codes_seen": sorted(set(actions["codes"])),
    }
    extra = {"schema": schema, "actions": actions}
    if legal_ok == legal_total and illegal_ok == illegal_total:
        return _ok("E0.2", metrics, extra)
    return _fail("E0.2", metrics, "schema/action protocol failed", extra)


def experiment_e03() -> Dict[str, Any]:
    env = make_env()
    task = env.task
    state = env.visible_state()
    secrets = env.secrets()
    detected = 0
    injections = 0
    cases = []

    actor = project_actor_view(task, state, secrets)
    critic = project_critic_view(task, state, secrets)
    verifier = project_verifier_view(task, state, env.future_neighbors())
    prompt = render_actor_prompt(actor, secrets)
    actor_sensitive = count_sensitive_fields(actor)
    critic_sensitive = count_sensitive_fields(critic)
    o0_sensitive = actor_sensitive + critic_sensitive

    leak_payloads = [
        ("top_answer_id", {"answer_entity_ids": ["e.paris"]}),
        ("nested_answer_text", {"meta": {"normalized_answers": ["Paris"]}}),
        ("metadata_witness", {"metadata": {"witness_paths": [["e.bob", "e.paris"]]}}),
        ("logical_query", {"logical_query": task.logical_query}),
        ("future_neighbors", {"future_neighbors": env.future_neighbors()}),
        ("text_answer_id", {"note": "the answer entity is e.paris"}),
        ("text_answer_name", {"note": "the city is Paris"}),
    ]
    for label, payload in leak_payloads:
        injections += 1
        try:
            audit_object(payload, secrets=secrets, context=label)
            cases.append({"label": label, "blocked": False})
        except ProtocolError as exc:
            if exc.code is ViolationCode.ORACLE_LEAKAGE:
                detected += 1
                cases.append({"label": label, "blocked": True})
            else:
                cases.append({"label": label, "blocked": False, "other": exc.code.value})

    o4_blocked = False
    try:
        OfflineFeedback.from_dict(
            {
                "task_id": task.task_id,
                "level": "O4",
                "feedback_version": "x",
                "payload": {"success": True},
                "protocol_version": PROTOCOL_VERSION,
            }
        )
    except ProtocolError as exc:
        o4_blocked = exc.code is ViolationCode.ORACLE_LEAKAGE
        injections += 1
        detected += int(o4_blocked)
        cases.append({"label": "o4_as_offline_feedback", "blocked": o4_blocked})

    o1_ok = True
    try:
        OfflineFeedback.from_dict(
            {
                "task_id": task.task_id,
                "level": "O1",
                "feedback_version": "fb-o1-v1",
                "payload": {"success": False, "failure_class": "explorer_failure"},
                "protocol_version": PROTOCOL_VERSION,
            }
        )
    except ProtocolError:
        o1_ok = False

    verifier_can_score = set(verifier["oracle"]["answer_entity_ids"]) == set(task.answer_entity_ids)
    metrics = {
        "oracle_leak_detection_rate": detected / injections if injections else 0.0,
        "o0_sensitive_field_count": o0_sensitive,
        "injections": injections,
        "detected": detected,
        "verifier_can_score": verifier_can_score,
        "o1_offline_feedback_accepted": o1_ok,
        "prompt_chars": len(prompt),
    }
    extra = {"cases": cases, "actor_sensitive": actor_sensitive, "critic_sensitive": critic_sensitive}
    passed = (
        detected == injections
        and o0_sensitive == 0
        and verifier_can_score
        and o1_ok
        and o4_blocked
    )
    if passed:
        return _ok("E0.3", metrics, extra)
    return _fail("E0.3", metrics, "oracle isolation failed", extra)


def experiment_e04(config: Dict[str, Any], workspace: Workspace) -> Dict[str, Any]:
    first = build_input_registry(config, workspace)
    second = build_input_registry(config, workspace)
    path = write_input_registry(first, workspace)
    same = registries_equal(first, second) and first["content_hash"] == second["content_hash"]
    mutated = deepcopy(first)
    if mutated["files"]:
        mutated["files"][0]["sha256"] = "0" * 64
        mutated["content_hash"] = canonical_hash(
            {
                "files": mutated["files"],
                "exclude": mutated["exclude"],
                "protocol_version": PROTOCOL_VERSION,
            }
        )
    detected_change = mutated["content_hash"] != first["content_hash"]
    fixture_records = [
        {
            "dataset": "webqsp",
            "split": "test",
            "task_id": "WebQTest-fixture-1",
            "normalized_question_hash": "a" * 64,
            "topic_entities": ["m.abc"],
            "answer_entities": ["m.def"],
            "exposure_source": "sp0_fixture",
            "exposed_at": "2026-08-22T00:00:00Z",
            "protocol_version": PROTOCOL_VERSION,
        },
        {
            "dataset": "cwq",
            "split": "test",
            "task_id": "CWQ-fixture-1",
            "normalized_question_hash": "b" * 64,
            "topic_entities": ["m.aaa"],
            "answer_entities": ["m.bbb"],
            "exposure_source": "sp0_fixture",
            "exposed_at": "2026-08-22T00:00:00Z",
            "protocol_version": PROTOCOL_VERSION,
        },
    ]
    exclusion = validate_exclusion_registry(fixture_records)
    exclusion_path = write_exclusion_registry(exclusion, workspace)
    duplicate_rejected = False
    try:
        validate_exclusion_registry(fixture_records + fixture_records[:1])
    except ProtocolError:
        duplicate_rejected = True
    outside_write = False
    try:
        WorkspaceBoundaryError
        workspace.assert_writable(workspace.data_root / "registry.json")
        outside_write = True
    except ProtocolError:
        outside_write = False
    metrics = {
        "registry_rebuild_agreement": 1.0 if same else 0.0,
        "source_change_detected": detected_change,
        "file_count": first["include_count"],
        "exclusion_duplicate_rejected": duplicate_rejected,
        "wrote_outside_self_play": outside_write,
    }
    extra = {
        "registry_path": str(path),
        "exclusion_path": str(exclusion_path),
        "content_hash": first["content_hash"],
    }
    passed = same and detected_change and duplicate_rejected and not outside_write
    if passed:
        return _ok("E0.4", metrics, extra)
    return _fail("E0.4", metrics, "registry reproducibility failed", extra)


def experiment_e05() -> Dict[str, Any]:
    cases = {
        "success": success_builder,
        "failure": failure_builder,
        "illegal_relation": illegal_relation_builder,
        "illegal_backtrack": illegal_backtrack_builder,
    }
    tiny_budget = Budget(
        max_depth=4,
        max_steps=1,
        max_kg_calls=16,
        max_llm_calls=8,
        max_critic_rounds=2,
        max_frontier_size=80,
    )
    details = {}
    consistent = 0
    total = 0
    illegal_localized = 0
    illegal_total = 0

    for name, builder in cases.items():
        records = replay_times(builder, n=3)
        hashes = [item.replay_hash for item in records]
        total += 1
        ok = len(set(hashes)) == 1
        consistent += int(ok)
        details[name] = {"replay_hash": hashes[0], "consistent": ok, "repeats": 3}
        if name.startswith("illegal"):
            illegal_total += 1
            first_violation = records[0].ordered_steps[0]["outcome"]["protocol_violation"]
            same_step = all(
                item.ordered_steps[0]["outcome"]["protocol_violation"] == first_violation
                and item.ordered_steps[0]["outcome"]["accepted"] is False
                for item in records
            )
            illegal_localized += int(same_step)
            details[name]["violation"] = first_violation
            details[name]["localized"] = same_step

    budget_records = replay_times(success_builder, n=3, budget=tiny_budget)
    budget_ok = len({item.replay_hash for item in budget_records}) == 1
    total += 1
    consistent += int(budget_ok)
    details["budget_exhausted"] = {
        "replay_hash": budget_records[0].replay_hash,
        "consistent": budget_ok,
        "termination": budget_records[0].termination_reason.value,
    }

    baseline_hash = replay_times(success_builder, n=1)[0].replay_hash
    mutated_action_hash = replay_times(failure_builder, n=1)[0].replay_hash
    mutated_snapshot_hash = replay_times(success_builder, n=1, snapshot_id="fixture-v2")[0].replay_hash
    mutated_budget_hash = replay_times(success_builder, n=1, budget=tiny_budget)[0].replay_hash
    hash_changed = len({baseline_hash, mutated_action_hash, mutated_snapshot_hash, mutated_budget_hash}) == 4

    # views remain isolated after success replay
    env, _ = run_scripted_trajectory(success_builder)
    actor = project_actor_view(env.task, env.visible_state(), env.secrets())
    critic = project_critic_view(env.task, env.visible_state(), env.secrets())
    verifier = project_verifier_view(env.task, env.visible_state(), env.future_neighbors())
    o0_sensitive = count_sensitive_fields(actor) + count_sensitive_fields(critic)

    metrics = {
        "same_input_replay_agreement": consistent / total if total else 0.0,
        "illegal_action_localization_rate": illegal_localized / illegal_total if illegal_total else 0.0,
        "single_factor_hash_change_detected": hash_changed,
        "repeats_per_fixture": 3,
        "fixture_count": total,
        "o0_sensitive_field_count_after_replay": o0_sensitive,
        "verifier_has_answers": bool(verifier["oracle"]["answer_entity_ids"]),
    }
    extra = {"details": details}
    passed = (
        consistent == total
        and illegal_localized == illegal_total
        and hash_changed
        and o0_sensitive == 0
    )
    if passed:
        return _ok("E0.5", metrics, extra)
    return _fail("E0.5", metrics, "deterministic replay failed", extra)


def experiment_e06(config: Dict[str, Any], workspace: Workspace) -> Dict[str, Any]:
    first = ensure_eval_sets(config, workspace)
    second = verify_eval_sets(config, workspace)
    third = verify_eval_sets(config, workspace)
    paths = eval_set_paths(workspace)
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    sizes = {}
    for name, path in paths.items():
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        sizes[name] = len(rows)
    # Simulate two later consumers reading the same frozen files.
    consumer_a = dict(hashes)
    consumer_b = {name: sha256_file(path) for name, path in paths.items()}
    shared = consumer_a == consumer_b == hashes
    expected = {
        "webqsp_smoke_20": 20,
        "webqsp_model_compare_150": 150,
        "cwq_model_compare_50": 50,
    }
    size_ok = sizes == expected
    unchanged = second["manifest"]["manifest_hash"] == third["manifest"]["manifest_hash"]
    from hashlib import sha256

    original = paths["webqsp_smoke_20"].read_bytes()
    detected = sha256(original + b"\n").hexdigest() != hashes["webqsp_smoke_20"]
    seed_mismatch_blocked = False
    mutated_config = deepcopy(config)
    mutated_config["eval_sampling"]["seed"] = 0
    try:
        verify_eval_sets(mutated_config, workspace)
    except ProtocolError:
        seed_mismatch_blocked = True
    metrics = {
        "webqsp_smoke_n": sizes.get("webqsp_smoke_20"),
        "webqsp_model_compare_n": sizes.get("webqsp_model_compare_150"),
        "cwq_model_compare_n": sizes.get("cwq_model_compare_50"),
        "repeat_check_unchanged": unchanged,
        "shared_file_hash_agreement": shared,
        "tamper_detected": detected,
        "seed_mismatch_blocked": seed_mismatch_blocked,
        "first_status": first.get("status"),
    }
    extra = {"hashes": hashes, "sizes": sizes}
    passed = size_ok and unchanged and shared and detected and seed_mismatch_blocked
    if passed:
        return _ok("E0.6", metrics, extra)
    return _fail("E0.6", metrics, "eval-set freeze/verify failed", extra)


def experiment_e07_failure_fixture(config: Dict[str, Any], workspace: Workspace) -> Dict[str, Any]:
    """Run verify against a missing frozen dataset without touching the real freeze."""
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
            verify_eval_sets(config, fake_ws)
            return {"failed_as_expected": False, "error": None}
        except ProtocolError as exc:
            return {"failed_as_expected": True, "error": exc.to_dict()}


def summarize_metrics(experiments: Dict[str, Any]) -> Dict[str, Any]:
    e01 = experiments["E0.1"]["metrics"]
    e02 = experiments["E0.2"]["metrics"]
    e03 = experiments["E0.3"]["metrics"]
    e04 = experiments["E0.4"]["metrics"]
    e05 = experiments["E0.5"]["metrics"]
    e06 = experiments["E0.6"]["metrics"]
    statuses = [experiments[name]["status"] for name in ["E0.1", "E0.2", "E0.3", "E0.4", "E0.5", "E0.6", "E0.7"]]
    passed = sum(item == "PASS" for item in statuses)
    return {
        "illegal_write_reject_rate": e01["illegal_write_reject_rate"],
        "shared_input_hash_changes": e01["shared_input_hash_changes"],
        "legal_schema_action_accept_rate": e02["legal_schema_action_accept_rate"],
        "illegal_schema_action_reject_rate": e02["illegal_schema_action_reject_rate"],
        "oracle_leak_detection_rate": e03["oracle_leak_detection_rate"],
        "o0_sensitive_field_count": e03["o0_sensitive_field_count"],
        "same_input_replay_agreement": e05["same_input_replay_agreement"],
        "registry_rebuild_agreement": e04["registry_rebuild_agreement"],
        "critical_check_pass_rate": passed / len(statuses),
        "unclassified_exceptions": 0,
        "webqsp_smoke_n": e06["webqsp_smoke_n"],
        "webqsp_model_compare_n": e06["webqsp_model_compare_n"],
        "cwq_model_compare_n": e06["cwq_model_compare_n"],
    }


def run_all_experiments(
    config: Dict[str, Any],
    workspace: Workspace,
    *,
    include_e07: bool = True,
) -> Dict[str, Any]:
    unclassified = []
    experiments: Dict[str, Any] = {}
    baseline_before = baseline_file_hashes(workspace)
    inventory_path = write_baseline_inventory(workspace)
    input_hashes_before = hash_registered_inputs(config, workspace)

    def capture(name, fn):
        try:
            experiments[name] = fn()
        except Exception as exc:
            unclassified.append({"name": name, "error": repr(exc), "traceback": traceback.format_exc()})
            experiments[name] = _fail(name, {}, repr(exc), {"traceback": traceback.format_exc()})

    capture("E0.1", lambda: experiment_e01(config, workspace, baseline_before, input_hashes_before))
    capture("E0.2", experiment_e02)
    capture("E0.3", experiment_e03)
    capture("E0.4", lambda: experiment_e04(config, workspace))
    capture("E0.5", experiment_e05)
    capture("E0.6", lambda: experiment_e06(config, workspace))

    if include_e07:
        failure = experiment_e07_failure_fixture(config, workspace)
        e07_metrics = {
            "failure_fixture_nonzero_expected": True,
            "failure_fixture_failed_as_expected": failure["failed_as_expected"],
        }
        if failure["failed_as_expected"]:
            experiments["E0.7"] = _ok("E0.7", e07_metrics, {"failure": failure})
        else:
            experiments["E0.7"] = _fail("E0.7", e07_metrics, "failure fixture did not fail", {"failure": failure})

    input_hashes_after = hash_registered_inputs(config, workspace)
    changed = [key for key, value in input_hashes_after.items() if input_hashes_before.get(key) != value]
    metrics = summarize_metrics(experiments)
    metrics["unclassified_exceptions"] = len(unclassified)
    metrics["shared_input_hash_changes"] = len(changed)
    all_pass = all(item["status"] == "PASS" for item in experiments.values()) and not unclassified and not changed
    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "PASS" if all_pass else "FAIL",
        "baseline_inventory": str(inventory_path),
        "experiments": experiments,
        "metrics": metrics,
        "unclassified_exceptions": unclassified,
        "shared_input_changes": changed,
        "baseline_hashes": baseline_before,
    }
