"""SP2-A experiments E2A.1-E2A.7. Live KG, no LLM, no memory, no eval-set trajectories."""

from __future__ import annotations

import json
import traceback
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .baseline import assert_baseline_unchanged, baseline_file_hashes
from .budget_ledger import CounterLedger
from .config import load_config
from .environment_binding import EnvironmentStatus, direction_to_pog_head, triples_from_expand
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_file, sha256_text
from .kg_sparql import (
    SPARQL_HEAD_ENTITIES,
    SPARQL_TAIL_ENTITIES,
    LiveSparqlClient,
    PhysicalStatus,
    RawHttpResponse,
    ScriptedTransport,
    TransportEndpointFailure,
    TransportTimeout,
    UrllibTransport,
    build_connectivity_request,
    build_entity_search_request,
    logical_action_id,
    normalize_bindings,
    parse_sparql_json,
    retry_with_backoff,
    templates_match_original,
)
from .live_environment import LiveKgBinding
from .paths import PROTOCOL_VERSION, Workspace
from .pog_adapter import PoGAdapter, make_sp1_snapshot, original_entity_search
from .recorded_io import diff_replay, index_records, write_recorded_io
from .sampling import eval_set_paths, verify_eval_sets
from .schemas import Action, ActionType, ActorRole, Budget, DecisionStage, Direction, FailureClass
from .sp1_checks import EXPECTED_BASELINE_HASHES, EXPECTED_EVAL_HASHES, EXPECTED_MANIFEST_HASH
from .sp2a_guards import (
    Sp2aGuards,
    assert_task_not_eval,
    load_exclusion_task_ids,
    scan_config_for_secrets,
    scan_paths_for_secrets,
    snapshot_readonly_roots,
    source_mentions_memory,
)

EXPECTED_OVERALL_VERSION = "SP-GENERAL 1.10"
EXPECTED_SP1_RUN = "sp1-20260822T030044Z-8cb155e0"


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


def _invalid(name: str, metrics: Dict[str, Any], error: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"name": name, "status": "INVALID", "metrics": metrics, "error": error}
    if extra:
        payload.update(extra)
    return payload


def load_dev_registry(workspace: Workspace, config: Dict[str, Any]) -> Dict[str, Any]:
    rel = config["development_task_registry"]
    path = workspace.self_play_root / rel
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_path"] = str(path)
    payload["_sha256"] = sha256_file(path)
    return payload


def task_by_id(registry: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    for item in registry["tasks"]:
        if item["task_id"] == task_id:
            return item
    raise KeyError(task_id)


def make_client(config: Dict[str, Any], *, transport=None, network_enabled: bool = True) -> LiveSparqlClient:
    return LiveSparqlClient(
        endpoint=config["endpoint"],
        allowed_endpoints=config["allowed_endpoints"],
        timeout_sec=config["timeout_sec"],
        max_retries=config["max_retries"],
        retry_backoff_sec=config["retry_backoff_sec"],
        transport=transport,
        network_enabled=network_enabled,
        max_recorded_bindings=int(config.get("max_recorded_bindings") or 200),
    )


def make_adapter(
    config: Dict[str, Any],
    *,
    transport=None,
    network_enabled: bool = True,
    records: Optional[Dict[str, Dict[str, Any]]] = None,
    ledger: Optional[CounterLedger] = None,
) -> Tuple[PoGAdapter, LiveKgBinding, CounterLedger]:
    ledger = ledger or CounterLedger()
    client = make_client(config, transport=transport, network_enabled=network_enabled)
    env = LiveKgBinding(client, ledger, records=records, network_enabled=network_enabled)
    adapter = PoGAdapter(
        adapter_enabled=True,
        allow_llm=False,
        allow_live_kg=True,
        stage="sp2a",
        environment=env,
    )
    return adapter, env, ledger


def snapshot_for_task(task: Dict[str, Any], budgets: Dict[str, Any]) -> Any:
    source = list(task.get("source_entities") or [task["entity"]])
    names = {task["entity"]: str(task.get("entity_public_name") or task["entity"])}
    if "m.02hrh" in source:
        names["m.02hrh"] = "Honolulu"
    if task.get("enumerated_relations"):
        relations = list(task["enumerated_relations"])
    else:
        relations = []
        seen = set()
        for step in task["steps"]:
            if step["type"] != "EXPAND":
                continue
            key = (step["entity"], step["relation"], step["direction"])
            if key in seen:
                continue
            seen.add(key)
            relations.append({"entity": step["entity"], "relation": step["relation"], "direction": step["direction"]})
    budget = Budget.from_config(budgets).to_dict()
    return make_sp1_snapshot(
        task_id=task["task_id"],
        question=task["question"],
        source_entities=source,
        topic_entity=names,
        frontier=list(source),
        enumerated_relations=relations,
        budget=budget,
        entid_name=names,
        name_entid={v: k for k, v in names.items()},
        decision_stage=DecisionStage.RELATION_SELECTION.value,
    )


def make_action(action_type: ActionType, params: Dict[str, Any], state, action_id: str = "a") -> Action:
    return Action(
        action_id=action_id,
        action_type=action_type,
        params=params,
        source_role=ActorRole.EXPLORER,
        state_id=state.state_id,
    )


def apply_step(adapter: PoGAdapter, snapshot, step: Dict[str, Any], action_id: str):
    state = adapter.project_visible_state(snapshot)
    action = make_action(
        ActionType(step["type"]),
        {k: v for k, v in step.items() if k != "type"},
        state,
        action_id=action_id,
    )
    return adapter.apply_action(snapshot, action)


def load_faults(workspace: Workspace) -> Dict[str, Any]:
    path = workspace.tests_root / "fixtures" / "sp2a" / "fault_responses.json"
    return json.loads(path.read_text(encoding="utf-8"))


def preflight(config: Dict[str, Any], workspace: Workspace, guards: Sp2aGuards) -> Dict[str, Any]:
    errors: List[str] = []
    if config.get("allow_llm") is not False:
        errors.append("allow_llm must be false")
    if config.get("allow_memory") is not False:
        errors.append("allow_memory must be false")
    if config.get("allow_live_kg") is not True:
        errors.append("allow_live_kg must be true")
    if config.get("readonly_kg") is not True:
        errors.append("readonly_kg must be true")
    if config.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("protocol_version mismatch")
    if config.get("overall_version") != EXPECTED_OVERALL_VERSION:
        errors.append("overall_version mismatch")
    if config.get("stage") != "SP2-A":
        errors.append("config stage must be SP2-A")
    secret_keys = scan_config_for_secrets(config)
    if secret_keys:
        errors.append(f"secret-like keys in config: {secret_keys}")
    overall = (workspace.self_play_root / "exp_plan" / "00_experiment_overall_requirements.md").read_text(encoding="utf-8")
    if "SP2-A：真实 KG 环境验证" not in overall and "SP2-A: 真实 KG" not in overall:
        if "当前总体阶段为：**SP2-A" not in overall:
            errors.append("overall file is not at SP2-A")
    if EXPECTED_SP1_RUN not in overall:
        errors.append("SP1 effective run id missing from overall")
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
    banned = load_exclusion_task_ids(workspace)
    if len(banned) != 220:
        errors.append(f"exclusion registry size {len(banned)} != 220")
    registry = load_dev_registry(workspace, config)
    if registry.get("sampled_from_eval_sets") is not False:
        errors.append("development registry must not be sampled from eval sets")
    for task in registry["tasks"]:
        try:
            assert_task_not_eval(task["task_id"], banned, guards)
        except ProtocolError as exc:
            errors.append(exc.message)
        blob = canonical_json({k: v for k, v in task.items() if k != "question"})
        for field in ("answer_entity_ids", "normalized_answers", "witness_paths", "logical_query"):
            if field in task:
                errors.append(f"task {task['task_id']} contains Oracle field {field}")
        for banned_id in banned:
            if banned_id and banned_id in task["task_id"]:
                errors.append(f"task id overlaps eval id {banned_id}")
    memory_hits = source_mentions_memory(workspace)
    if memory_hits:
        errors.append(f"memory write markers in SP2-A sources: {memory_hits}")
    readonly_snapshot = snapshot_readonly_roots(workspace)
    return {
        "ok": not errors,
        "errors": errors,
        "readonly_snapshot": readonly_snapshot,
        "dev_registry_sha256": registry["_sha256"],
        "exclusion_count": len(banned),
        "baseline_hashes": current,
        "llm_calls": guards.llm.calls,
        "memory_reads": guards.memory.reads,
        "memory_writes": guards.memory.writes,
    }


def experiment_e2a1(config: Dict[str, Any], workspace: Workspace, registry: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    adapter, env, ledger = make_adapter(config)
    client = env.client
    request = build_connectivity_request(endpoint=config["endpoint"])
    try:
        exchanges = retry_with_backoff(
            client,
            request,
            logical_action_id=logical_action_id("sp2a.dev.connect", "c0001", "CONNECT", {"kind": "connectivity"}),
        )
    except ProtocolError as exc:
        return _invalid("E2A.1", {"reachable": False}, exc.message)
    final = exchanges[-1]
    ledger.record_logical_with_exchanges(
        task_id="sp2a.dev.connect",
        logical_action_id=final.logical_action_id,
        statuses=[item.status for item in exchanges],
    )
    if final.status in {PhysicalStatus.TIMEOUT, PhysicalStatus.ENDPOINT_FAILURE}:
        return _invalid(
            "E2A.1",
            {"reachable": False, "status": final.status.value, "error": final.error_message},
            "live KG endpoint unavailable",
            {"exchanges": [item.to_audit_dict() for item in exchanges]},
        )
    if final.status is PhysicalStatus.MALFORMED_RESPONSE:
        return _fail("E2A.1", {"reachable": True, "status": final.status.value}, final.error_message)
    task = task_by_id(registry, "sp2a.dev.connect.obama_name")
    snap = snapshot_for_task(task, config["budgets"])
    env.set_task(task["task_id"])
    applied, outcome, result = apply_step(adapter, snap, task["steps"][0], "e2a1-name")
    write_attempt = client.write_attempts
    metrics = {
        "reachable": True,
        "endpoint": client.endpoint,
        "http_method": "POST",
        "connectivity_status": final.status.value,
        "connectivity_http_status": final.http_status,
        "response_hash": final.response_hash,
        "name_expand_status": None if result is None else result.status.value,
        "name_expand_accepted": outcome.accepted,
        "write_attempts": write_attempt,
        "logical_actions": ledger.logical_actions,
        "physical_requests": ledger.physical_requests,
        "network_used": final.network_used,
        "llm_calls": guards.llm.calls,
    }
    if write_attempt != 0:
        return _fail("E2A.1", metrics, "write SPARQL attempted")
    if not outcome.accepted or result is None:
        return _fail("E2A.1", metrics, "connectivity EXPAND was not accepted")
    if result.status not in {EnvironmentStatus.LITERAL, EnvironmentStatus.SUCCESS, EnvironmentStatus.DUPLICATE}:
        return _fail("E2A.1", metrics, f"unexpected name expand status {result.status.value}")
    extra = {
        "audit": [item.to_audit_dict() for item in exchanges] + env.audit_records,
        "endpoint_snapshot_id": client.endpoint,
    }
    return _ok("E2A.1", metrics, extra)


def experiment_e2a2(config: Dict[str, Any], registry: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    adapter, env, ledger = make_adapter(config)
    cases = []
    mapping_ok = 0
    mapping_n = 0
    reversals = 0
    for task_id, expected_head, subject_is_query_entity in (
        ("sp2a.dev.head.obama_birthplace", True, True),
        ("sp2a.dev.tail.honolulu_birthplace", False, False),
    ):
        task = task_by_id(registry, task_id)
        env.set_task(task_id)
        step = task["steps"][0]
        built = build_entity_search_request(step["entity"], step["relation"], step["direction"], endpoint=config["endpoint"])
        mapping_n += 1
        head_flag = direction_to_pog_head(step["direction"])
        sparql_ok = built.head is expected_head and head_flag is expected_head
        if expected_head:
            sparql_ok = sparql_ok and built.sparql == SPARQL_TAIL_ENTITIES % (step["entity"], step["relation"])
        else:
            sparql_ok = sparql_ok and built.sparql == SPARQL_HEAD_ENTITIES % (step["relation"], step["entity"])
        snap = snapshot_for_task(task, config["budgets"])
        applied, outcome, result = apply_step(adapter, snap, step, f"e2a2-{task_id}")
        triple_ok = True
        if result is not None:
            for triple in result.results:
                if subject_is_query_entity and triple["subject"] != step["entity"]:
                    triple_ok = False
                    reversals += 1
                if (not subject_is_query_entity) and triple["object"] != step["entity"]:
                    triple_ok = False
                    reversals += 1
                reconstructed = triples_from_expand(
                    step["entity"],
                    step["relation"],
                    Direction(step["direction"]),
                    [triple["object"] if subject_is_query_entity else triple["subject"]],
                )
                if reconstructed and reconstructed[0]["subject"] != triple["subject"]:
                    triple_ok = False
                    reversals += 1
        if sparql_ok and triple_ok and outcome.accepted:
            mapping_ok += 1
        cases.append(
            {
                "task_id": task_id,
                "direction": step["direction"],
                "head": built.head,
                "sparql_ok": sparql_ok,
                "triple_ok": triple_ok,
                "accepted": outcome.accepted,
                "status": None if result is None else result.status.value,
                "request_hash": built.request_hash,
                "result_count": 0 if result is None else len(result.results),
                "triples": [] if result is None else result.results[:12],
                "provenance": None if result is None else result.provenance_ref,
            }
        )
    rate = mapping_ok / mapping_n if mapping_n else 0.0
    metrics = {
        "head_tail_mapping_rate": rate,
        "direction_reversals": reversals,
        "logical_actions": ledger.logical_actions,
        "physical_requests": ledger.physical_requests,
        "llm_calls": guards.llm.calls,
    }
    extra = {"cases": cases, "audit": env.audit_records}
    if rate != 1.0 or reversals:
        return _fail("E2A.2", metrics, "HEAD/TAIL mapping failed", extra)
    return _ok("E2A.2", metrics, extra)


def experiment_e2a3(config: Dict[str, Any], registry: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    adapter, env, ledger = make_adapter(config)
    single = task_by_id(registry, "sp2a.dev.head.obama_birthplace")
    env.set_task(single["task_id"])
    snap = snapshot_for_task(single, config["budgets"])
    before = adapter.project_visible_state(snap)
    applied, outcome, result = apply_step(adapter, snap, single["steps"][0], "e2a3-single")
    after = adapter.project_visible_state(applied)
    single_ok = (
        outcome.accepted
        and result is not None
        and before.state_id != after.state_id
        and outcome.state_id_before == before.state_id
        and outcome.state_id_after == after.state_id
    )
    two = task_by_id(registry, "sp2a.dev.twohop.obama_honolulu_containedby")
    env.set_task(two["task_id"])
    snap2 = snapshot_for_task(two, config["budgets"])
    ids = []
    current = snap2
    two_ok = True
    step_traces = []
    for index, step in enumerate(two["steps"]):
        state_before = adapter.project_visible_state(current)
        current, outcome2, result2 = apply_step(adapter, current, step, f"e2a3-h{index}")
        state_after = adapter.project_visible_state(current)
        ids.append((state_before.state_id, state_after.state_id))
        if not outcome2.accepted or result2 is None or state_before.state_id == state_after.state_id:
            two_ok = False
        frontier_sorted = list(state_after.frontier) == sorted(state_after.frontier)
        triples_sorted = list(state_after.observed_triples_or_summaries) == sorted(
            state_after.observed_triples_or_summaries,
            key=lambda item: (item["subject"], item["relation"], item["object"]),
        )
        if not frontier_sorted or not triples_sorted:
            two_ok = False
        step_traces.append(
            {
                "step": index,
                "accepted": outcome2.accepted,
                "status": None if result2 is None else result2.status.value,
                "state_id_before": state_before.state_id,
                "state_id_after": state_after.state_id,
                "depth_used": current.budget.used_depth,
                "kg_used": current.budget.used_kg_calls,
                "new_frontier": outcome2.new_frontier_items,
                "triple_count": len(state_after.observed_triples_or_summaries),
            }
        )
    replay_adapter, replay_env, _ = make_adapter(config)
    replay_env.set_task(two["task_id"])
    replay = snapshot_for_task(two, config["budgets"])
    replay_ids = []
    for index, step in enumerate(two["steps"]):
        state_before = replay_adapter.project_visible_state(replay)
        replay, _, _ = apply_step(replay_adapter, replay, step, f"e2a3-replay-{index}")
        replay_ids.append((state_before.state_id, replay_adapter.project_visible_state(replay).state_id))
    replay_ok = replay_ids == ids
    metrics = {
        "single_hop_ok": single_ok,
        "two_hop_ok": two_ok,
        "live_repeat_state_id_agreement": replay_ok,
        "legal_transition_rate": 1.0 if single_ok and two_ok and replay_ok else 0.0,
        "logical_actions": ledger.logical_actions + replay_env.ledger.logical_actions,
        "llm_calls": guards.llm.calls,
    }
    extra = {
        "single": {
            "before": before.state_id,
            "after": after.state_id,
            "status": None if result is None else result.status.value,
        },
        "two_hop": step_traces,
        "repeat_ids": replay_ids,
        "audit": env.audit_records,
    }
    if not (single_ok and two_ok and replay_ok):
        return _fail("E2A.3", metrics, "state transition mismatch", extra)
    return _ok("E2A.3", metrics, extra)


def experiment_e2a4(
    config: Dict[str, Any],
    workspace: Workspace,
    registry: Dict[str, Any],
    guards: Sp2aGuards,
) -> Dict[str, Any]:
    adapter, env, ledger = make_adapter(config)
    rows = []
    ok = True
    for task_id, expected in (
        ("sp2a.dev.head.obama_birthplace", {"non-empty"}),
        ("sp2a.dev.empty.obama_death", {"empty"}),
        ("sp2a.dev.connect.obama_name", {"literal"}),
    ):
        task = task_by_id(registry, task_id)
        env.set_task(task_id)
        snap = snapshot_for_task(task, config["budgets"])
        _, outcome, result = apply_step(adapter, snap, task["steps"][0], f"e2a4-{task_id}")
        status = None if result is None else result.status.value
        classified = "unknown"
        if result is not None and result.failure_class is None:
            if result.status is EnvironmentStatus.EMPTY_SUCCESS:
                classified = "empty"
            elif result.status is EnvironmentStatus.LITERAL:
                classified = "literal"
            elif result.status in {EnvironmentStatus.SUCCESS, EnvironmentStatus.DUPLICATE}:
                classified = "non-empty"
        if classified not in expected or not outcome.accepted:
            ok = False
        rows.append(
            {
                "task_id": task_id,
                "expected": sorted(expected),
                "classified": classified,
                "status": status,
                "accepted": outcome.accepted,
                "failure_class": None if result is None or result.failure_class is None else result.failure_class.value,
            }
        )

    faults = load_faults(workspace)
    scripted = ScriptedTransport([RawHttpResponse(200, json.dumps(faults["duplicate_bindings"]))])
    dup_adapter, dup_env, _ = make_adapter(config, transport=scripted)
    dup_task = task_by_id(registry, "sp2a.dev.head.obama_birthplace")
    dup_env.set_task("sp2a.dev.scripted.duplicate")
    snap = snapshot_for_task(dup_task, config["budgets"])
    _, dup_outcome, dup_result = apply_step(dup_adapter, snap, dup_task["steps"][0], "e2a4-dup")
    dup_ok = (
        dup_outcome.accepted
        and dup_result is not None
        and dup_result.status is EnvironmentStatus.DUPLICATE
        and dup_result.failure_class is None
        and len(dup_result.results) == 2
    )
    if not dup_ok:
        ok = False
    rows.append(
        {
            "task_id": "sp2a.dev.scripted.duplicate",
            "classified": "duplicate" if dup_ok else "unknown",
            "status": None if dup_result is None else dup_result.status.value,
            "mode": "scripted_transport",
        }
    )

    malformed = ScriptedTransport([RawHttpResponse(200, json.dumps(faults["malformed_missing_tail_entity"]))])
    mal_adapter, mal_env, _ = make_adapter(config, transport=malformed)
    mal_env.set_task("sp2a.dev.scripted.malformed")
    snap = snapshot_for_task(dup_task, config["budgets"])
    _, mal_outcome, mal_result = apply_step(mal_adapter, snap, dup_task["steps"][0], "e2a4-mal")
    mal_ok = (
        mal_result is not None
        and mal_result.status is EnvironmentStatus.MALFORMED
        and mal_result.failure_class is FailureClass.SYSTEM_FAILURE
        and not mal_outcome.accepted
    )
    if not mal_ok:
        ok = False
    rows.append(
        {
            "task_id": "sp2a.dev.scripted.malformed",
            "classified": "malformed_response" if mal_ok else "unknown",
            "status": None if mal_result is None else mal_result.status.value,
            "mode": "scripted_transport",
        }
    )
    empty_is_not_system = all(
        row.get("classified") != "empty" or row.get("failure_class") is None for row in rows if "empty" in row.get("expected", [])
    )
    metrics = {
        "cases": len(rows),
        "empty_is_not_system_failure": empty_is_not_system,
        "unclassified": sum(1 for row in rows if row.get("classified") == "unknown"),
        "llm_calls": guards.llm.calls,
        "logical_actions": ledger.logical_actions,
    }
    extra = {"rows": rows, "audit": env.audit_records + dup_env.audit_records + mal_env.audit_records}
    if not ok or not empty_is_not_system or metrics["unclassified"]:
        return _fail("E2A.4", metrics, "special-response classification failed", extra)
    return _ok("E2A.4", metrics, extra)


def experiment_e2a5(config: Dict[str, Any], workspace: Workspace, registry: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    faults = load_faults(workspace)
    rows = []
    ok = True

    timeout_then_ok = ScriptedTransport(
        [
            TransportTimeout("injected timeout"),
            RawHttpResponse(200, json.dumps(faults["success_bindings"])),
        ]
    )
    adapter, env, ledger = make_adapter(config, transport=timeout_then_ok)
    task = task_by_id(registry, "sp2a.dev.head.obama_birthplace")
    env.set_task("sp2a.dev.scripted.retry_timeout")
    snap = snapshot_for_task(task, config["budgets"])
    _, outcome, result = apply_step(adapter, snap, task["steps"][0], "e2a5-retry")
    retry_ok = (
        outcome.accepted
        and result is not None
        and result.failure_class is None
        and ledger.logical_actions == 1
        and ledger.physical_requests == 2
        and ledger.retries == 1
        and timeout_then_ok.calls == 2
    )
    if not retry_ok:
        ok = False
    rows.append(
        {
            "case": "retry_timeout_then_success",
            "ok": retry_ok,
            "logical": ledger.logical_actions,
            "physical": ledger.physical_requests,
            "retries": ledger.retries,
            "mode": "scripted_transport",
        }
    )

    always_timeout = ScriptedTransport([TransportTimeout("t1"), TransportTimeout("t2"), TransportTimeout("t3")])
    t_adapter, t_env, t_ledger = make_adapter(config, transport=always_timeout)
    t_env.set_task("sp2a.dev.scripted.timeout_exhausted")
    snap = snapshot_for_task(task, config["budgets"])
    _, t_outcome, t_result = apply_step(t_adapter, snap, task["steps"][0], "e2a5-timeout")
    timeout_ok = (
        t_result is not None
        and t_result.status is EnvironmentStatus.TIMEOUT
        and t_result.failure_class is FailureClass.SYSTEM_FAILURE
        and not t_outcome.accepted
        and t_ledger.logical_actions == 1
        and t_ledger.physical_requests == 1 + int(config["max_retries"])
        and t_ledger.failed_requests == 1
    )
    if not timeout_ok:
        ok = False
    rows.append(
        {
            "case": "timeout_exhausted",
            "ok": timeout_ok,
            "logical": t_ledger.logical_actions,
            "physical": t_ledger.physical_requests,
            "mode": "scripted_transport",
        }
    )

    ep = ScriptedTransport([TransportEndpointFailure("connection refused")])
    e_adapter, e_env, e_ledger = make_adapter(config, transport=ep)
    e_env.set_task("sp2a.dev.scripted.endpoint_failure")
    snap = snapshot_for_task(task, config["budgets"])
    _, e_outcome, e_result = apply_step(e_adapter, snap, task["steps"][0], "e2a5-ep")
    ep_ok = (
        e_result is not None
        and e_result.status is EnvironmentStatus.ENDPOINT_FAILURE
        and e_result.failure_class is FailureClass.SYSTEM_FAILURE
        and not e_outcome.accepted
        and e_ledger.logical_actions == 1
    )
    if not ep_ok:
        ok = False
    rows.append({"case": "endpoint_failure", "ok": ep_ok, "mode": "scripted_transport"})

    inv = task_by_id(registry, "sp2a.dev.invalid.unenumerated_relation")
    i_adapter, i_env, i_ledger = make_adapter(config)
    i_env.set_task(inv["task_id"])
    snap = snapshot_for_task(inv, config["budgets"])
    physical_before = i_ledger.physical_requests
    _, i_outcome, i_result = apply_step(i_adapter, snap, inv["steps"][0], "e2a5-invalid")
    i_env.ledger.record_invalid_action(
        task_id=inv["task_id"],
        action_type="EXPAND",
        message=str(i_outcome.visible_result.get("error")),
    )
    invalid_ok = (
        not i_outcome.accepted
        and i_result is None
        and i_outcome.visible_result.get("failure_class") == FailureClass.ACTION_SPACE_FAILURE.value
        and i_ledger.physical_requests == physical_before
        and i_env.client.physical_calls == 0
    )
    if not invalid_ok:
        ok = False
    rows.append({"case": "invalid_action_no_kg", "ok": invalid_ok, "physical": i_ledger.physical_requests})

    tiny = dict(config["budgets"])
    tiny["max_kg_calls"] = 1
    b_transport = ScriptedTransport(
        [
            RawHttpResponse(200, json.dumps(faults["success_bindings"])),
            RawHttpResponse(200, json.dumps(faults["success_bindings"])),
        ]
    )
    b_adapter, b_env, b_ledger = make_adapter(config, transport=b_transport)
    b_env.set_task("sp2a.dev.scripted.budget")
    snap = snapshot_for_task(task, tiny)
    current, first_outcome, first_result = apply_step(b_adapter, snap, task["steps"][0], "e2a5-b1")
    physical_after_first = b_ledger.physical_requests
    state = b_adapter.project_visible_state(current)
    second = make_action(
        ActionType.EXPAND,
        {"entity": task["entity"], "relation": task["relation"], "direction": task["direction"]},
        state,
        action_id="e2a5-b2",
    )
    _, second_outcome, second_result = b_adapter.apply_action(current, second)
    if not second_outcome.accepted:
        b_ledger.record_budget_skip(task_id="sp2a.dev.scripted.budget", remaining_kg=0)
    budget_ok = (
        first_outcome.accepted
        and first_result is not None
        and not second_outcome.accepted
        and second_result is None
        and second_outcome.visible_result.get("failure_class") == FailureClass.BUDGET_INSUFFICIENT.value
        and b_ledger.physical_requests == physical_after_first
        and b_transport.calls == 1
    )
    if not budget_ok:
        ok = False
    rows.append(
        {
            "case": "budget_no_second_physical",
            "ok": budget_ok,
            "physical": b_ledger.physical_requests,
            "transport_calls": b_transport.calls,
            "mode": "scripted_transport",
        }
    )

    counter_ok = all(
        row.get("ok")
        for row in rows
        if row["case"] in {"retry_timeout_then_success", "timeout_exhausted", "budget_no_second_physical"}
    )
    metrics = {
        "logical_physical_retry_correct": retry_ok and timeout_ok,
        "budget_boundary_correct": budget_ok,
        "invalid_action_no_physical": invalid_ok,
        "endpoint_failure_classified": ep_ok,
        "llm_calls": guards.llm.calls,
        "counter_ok": counter_ok,
    }
    extra = {
        "rows": rows,
        "audit": env.audit_records + t_env.audit_records + e_env.audit_records + b_env.audit_records,
    }
    if not ok:
        return _fail("E2A.5", metrics, "timeout/retry/budget classification failed", extra)
    return _ok("E2A.5", metrics, extra)


def experiment_e2a6(
    config: Dict[str, Any],
    workspace: Workspace,
    registry: Dict[str, Any],
    previous: Dict[str, Any],
    guards: Sp2aGuards,
) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    for name in ("E2A.1", "E2A.2", "E2A.4"):
        item = previous.get(name) or {}
        records.extend(item.get("audit") or [])
    # Keep final attempts; require success, empty, and a failure-like recorded item from E2A.4 malformed/E2A.5.
    for name in ("E2A.4", "E2A.5"):
        item = previous.get(name) or {}
        records.extend(item.get("audit") or [])
    if not records:
        return _fail("E2A.6", {}, "no recorded I/O to replay")

    rel = "artifacts/recorded_io/sp2a/sp2a_recorded_io_v1.json"
    path = write_recorded_io(records, workspace, relative=rel)
    payload = json.loads(path.read_text(encoding="utf-8"))
    indexed = index_records(payload)

    live_adapter, live_env, _ = make_adapter(config)
    replay_adapter, replay_env, _ = make_adapter(config, network_enabled=False, records=indexed)
    task = task_by_id(registry, "sp2a.dev.empty.obama_death")
    live_env.set_task(task["task_id"])
    replay_env.set_task(task["task_id"])
    live_snap = snapshot_for_task(task, config["budgets"])
    replay_snap = snapshot_for_task(task, config["budgets"])
    live_applied, live_outcome, live_result = apply_step(live_adapter, live_snap, task["steps"][0], "e2a6-live")
    # Replay must not use network: copy request hash from live audit into replay env if needed.
    replay_indexed = index_records({"records": live_env.audit_records})
    replay_env.records.update(replay_indexed)
    replay_applied, replay_outcome, replay_result = apply_step(
        replay_adapter, replay_snap, task["steps"][0], "e2a6-replay"
    )
    live_state = live_adapter.project_visible_state(live_applied)
    replay_state = replay_adapter.project_visible_state(replay_applied)
    compare = {
        "status": None if live_result is None else live_result.status.value,
        "canonical_targets": [] if live_result is None else live_result.results,
        "logical_actions": live_env.ledger.logical_actions,
        "physical_requests": live_env.ledger.physical_requests,
        "retries": live_env.ledger.retries,
        "state_id_after": live_state.state_id,
        "environment_status": None if live_result is None else live_result.status.value,
    }
    replay_compare = {
        "status": None if replay_result is None else replay_result.status.value,
        "canonical_targets": [] if replay_result is None else replay_result.results,
        "logical_actions": replay_env.ledger.logical_actions,
        "physical_requests": replay_env.ledger.physical_requests,
        "retries": replay_env.ledger.retries,
        "state_id_after": replay_state.state_id,
        "environment_status": None if replay_result is None else replay_result.status.value,
    }
    diffs = diff_replay(compare, replay_compare)
    network_during_replay = any(item.get("network_used") for item in replay_env.audit_records)
    agreement = 0.0 if diffs or network_during_replay else 1.0
    metrics = {
        "recorded_io_path": rel,
        "record_count": len(payload["records"]),
        "replay_agreement": agreement,
        "network_during_replay": network_during_replay,
        "llm_calls": guards.llm.calls,
    }
    extra = {"diffs": diffs, "live": compare, "replay": replay_compare, "bundle_hash": payload.get("bundle_hash")}
    if agreement != 1.0:
        return _fail("E2A.6", metrics, "recorded I/O replay mismatch", extra)
    return _ok("E2A.6", metrics, extra)


def experiment_e2a7(config: Dict[str, Any], workspace: Workspace, registry: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    source = (workspace.self_play_root / "freebase_func.py").read_text(encoding="utf-8")
    template_info = templates_match_original(source)
    template_ok = bool(template_info["tail_entities_match"] and template_info["head_entities_match"])
    head_logic = "if head:" in source and "sparql_tail_entities_extract% (entity, relation)" in source
    tail_logic = "sparql_head_entities_extract% (relation, entity)" in source
    adapter, env, _ = make_adapter(config)
    task = task_by_id(registry, "sp2a.dev.head.obama_birthplace")
    env.set_task(task["task_id"])
    snap = snapshot_for_task(task, config["budgets"])
    _, outcome, result = apply_step(adapter, snap, task["steps"][0], "e2a7-head")
    replica_ok = False
    replica_targets: List[str] = []
    adapter_targets: List[str] = []
    if env.exchanges:
        final = env.exchanges[-1]
        replica_targets = original_entity_search(
            task["entity"],
            task["relation"],
            True,
            bindings=final.bindings,
        )
        adapter_targets = list(result.raw_targets) if result is not None else []
        replica_ok = replica_targets == adapter_targets
    differences = []
    if not template_ok:
        differences.append("SPARQL templates differ from freebase_func.py")
    if not (head_logic and tail_logic):
        differences.append("entity_search head/tail % formatting not found in original source")
    if not replica_ok:
        differences.append("adapter targets differ from original_entity_search replica on the same bindings")
    metrics = {
        "templates_match": template_ok,
        "original_head_tail_logic_present": head_logic and tail_logic,
        "replica_target_agreement": replica_ok,
        "http_client": "urllib POST application/x-www-form-urlencoded (SPARQLWrapper not imported)",
        "llm_calls": guards.llm.calls,
    }
    extra = {
        "differences": differences,
        "replica_targets": replica_targets[:20],
        "adapter_targets": adapter_targets[:20],
        "accepted": outcome.accepted,
    }
    if differences:
        return _fail("E2A.7", metrics, "original entity_search semantic mismatch", extra)
    return _ok("E2A.7", metrics, extra)


def summarize_metrics(experiments: Dict[str, Any], guards: Sp2aGuards, ledger_total: Dict[str, int]) -> Dict[str, Any]:
    def status_of(name: str) -> str:
        return (experiments.get(name) or {}).get("status") or "MISSING"

    e2a2 = (experiments.get("E2A.2") or {}).get("metrics") or {}
    e2a3 = (experiments.get("E2A.3") or {}).get("metrics") or {}
    e2a5 = (experiments.get("E2A.5") or {}).get("metrics") or {}
    e2a6 = (experiments.get("E2A.6") or {}).get("metrics") or {}
    return {
        "real_llm_calls": guards.llm.calls,
        "memory_reads": guards.memory.reads,
        "memory_writes": guards.memory.writes,
        "oracle_label_in_action": guards.oracle_label_in_action,
        "eval_set_trajectory_uses": guards.eval_set_trajectory_uses,
        "head_tail_mapping_rate": e2a2.get("head_tail_mapping_rate", 0.0),
        "legal_transition_rate": e2a3.get("legal_transition_rate", 0.0),
        "logical_physical_retry_correct_rate": 1.0 if e2a5.get("logical_physical_retry_correct") else 0.0,
        "budget_boundary_correct_rate": 1.0 if e2a5.get("budget_boundary_correct") else 0.0,
        "replay_agreement": e2a6.get("replay_agreement", 0.0),
        "unclassified_exceptions": 0,
        "ledger": ledger_total,
        "e2a_statuses": {name: status_of(name) for name in ("E2A.1", "E2A.2", "E2A.3", "E2A.4", "E2A.5", "E2A.6", "E2A.7")},
    }


def run_all_sp2a_experiments(config: Dict[str, Any], workspace: Workspace) -> Dict[str, Any]:
    guards = Sp2aGuards()
    unclassified: List[Dict[str, Any]] = []
    experiments: Dict[str, Any] = {}
    registry = load_dev_registry(workspace, config)
    readonly_before = snapshot_readonly_roots(workspace)

    def capture(name, fn):
        try:
            experiments[name] = fn()
        except Exception as exc:
            unclassified.append({"name": name, "error": repr(exc), "traceback": traceback.format_exc()})
            experiments[name] = _fail(name, {}, repr(exc), {"traceback": traceback.format_exc()})

    capture("E2A.1", lambda: experiment_e2a1(config, workspace, registry, guards))
    if experiments.get("E2A.1", {}).get("status") == "INVALID":
        metrics = summarize_metrics(experiments, guards, {})
        metrics["unclassified_exceptions"] = len(unclassified)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "plan_version": config.get("plan_version"),
            "status": "INVALID",
            "reason": "live_kg_unavailable",
            "experiments": experiments,
            "metrics": metrics,
            "unclassified_exceptions": unclassified,
            "guards": guards.counts(),
            "dev_registry_sha256": registry["_sha256"],
        }

    capture("E2A.2", lambda: experiment_e2a2(config, registry, guards))
    capture("E2A.3", lambda: experiment_e2a3(config, registry, guards))
    capture("E2A.4", lambda: experiment_e2a4(config, workspace, registry, guards))
    capture("E2A.5", lambda: experiment_e2a5(config, workspace, registry, guards))
    capture("E2A.6", lambda: experiment_e2a6(config, workspace, registry, experiments, guards))
    capture("E2A.7", lambda: experiment_e2a7(config, workspace, registry, guards))

    eval_changed = []
    paths = eval_set_paths(workspace)
    for name, digest in EXPECTED_EVAL_HASHES.items():
        if sha256_file(paths[name]) != digest:
            eval_changed.append(name)
    secret_hits = scan_paths_for_secrets(
        [
            workspace.artifacts_root / "recorded_io" / "sp2a" / "sp2a_recorded_io_v1.json",
            workspace.artifacts_root / "protocol" / "sp2a_check_result.json",
        ]
    )
    baseline_changed = assert_baseline_unchanged(workspace, EXPECTED_BASELINE_HASHES)
    metrics = summarize_metrics(experiments, guards, {})
    metrics["unclassified_exceptions"] = len(unclassified)
    metrics["secret_hits"] = len(secret_hits)
    metrics["eval_file_hash_changes"] = len(eval_changed)
    metrics["unregistered_baseline_hash_changes"] = len(baseline_changed)
    names = [f"E2A.{i}" for i in range(1, 8)]
    all_pass = (
        all(experiments.get(name, {}).get("status") == "PASS" for name in names)
        and not unclassified
        and guards.llm.calls == 0
        and guards.memory.reads == 0
        and guards.memory.writes == 0
        and not secret_hits
        and not eval_changed
        and not baseline_changed
        and metrics["head_tail_mapping_rate"] == 1.0
        and metrics["legal_transition_rate"] == 1.0
        and metrics["replay_agreement"] == 1.0
    )
    overlap = _overlap_mids(registry, workspace)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "plan_version": config.get("plan_version"),
        "status": "PASS" if all_pass else "FAIL",
        "experiments": experiments,
        "metrics": metrics,
        "unclassified_exceptions": unclassified,
        "guards": guards.counts(),
        "dev_registry_sha256": registry["_sha256"],
        "endpoint": config["endpoint"],
        "secret_hits": secret_hits,
        "baseline_changed": baseline_changed,
        "eval_overlap_mids": overlap,
        "readonly_snapshot": readonly_before,
        "eval_file_changes": eval_changed,
    }


def _overlap_mids(registry: Dict[str, Any], workspace: Workspace) -> List[str]:
    used = set()
    for task in registry["tasks"]:
        used.add(task["entity"])
        for step in task.get("steps") or []:
            if step.get("entity"):
                used.add(step["entity"])
        for item in task.get("source_entities") or []:
            used.add(item)
    path = workspace.artifacts_root / "registries" / "benchmark_exclusion_registry_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    overlap = set()
    for record in payload.get("records") or []:
        for mid in list(record.get("topic_entities") or []) + list(record.get("answer_entities") or []):
            if mid in used:
                overlap.add(mid)
    return sorted(overlap)
