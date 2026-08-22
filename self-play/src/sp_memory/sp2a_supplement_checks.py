"""SP2-A supplement checks: TAIL-positive semantics and dynamic two-hop transfer."""

from __future__ import annotations

import json
import traceback
from copy import deepcopy
from typing import Any, Dict, List, Optional

from .baseline import assert_baseline_unchanged, baseline_file_hashes
from .config import load_config
from .environment_binding import EnvironmentStatus
from .errors import ProtocolError
from .hashing import canonical_hash, sha256_file
from .kg_sparql import (
    RawHttpResponse,
    ScriptedTransport,
    TransportTimeout,
    build_entity_search_request,
)
from .paths import PROTOCOL_VERSION, Workspace
from .recorded_io import index_records, write_recorded_io
from .sampling import eval_set_paths, verify_eval_sets
from .schemas import Direction, FailureClass, VisibleRelation
from .sp1_checks import EXPECTED_BASELINE_HASHES, EXPECTED_EVAL_HASHES, EXPECTED_MANIFEST_HASH
from .sp2a_checks import (
    apply_step,
    load_faults,
    make_adapter,
    snapshot_for_task,
)
from .sp2a_dynamic import (
    assert_tail_positive_triples,
    assert_tail_request,
    extract_canonical_entities,
    hop2_entity_matches_hop1,
    make_expand_action,
    materialize_hop2_from_hop1,
    select_hop1_entity,
    validate_supplement_registry,
)
from .sp2a_guards import (
    Sp2aGuards,
    assert_task_not_eval,
    load_exclusion_task_ids,
    scan_config_for_secrets,
    scan_paths_for_secrets,
    snapshot_readonly_roots,
    source_mentions_memory,
)

EXPECTED_OVERALL_VERSION = "SP-GENERAL 1.12"
EXPECTED_SP2A_RUN = "sp2a-20260822T082704Z-28a5bc97"
EXPECTED_PARENT_PLAN = "SP2A-PLAN 1.0"
RECORDED_IO_REL = "artifacts/recorded_io/sp2a/sp2a_supplement_recorded_io_v1.json"


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


def load_supplement_registry(workspace: Workspace, config: Dict[str, Any]) -> Dict[str, Any]:
    rel = config["supplement_task_registry"]
    path = workspace.self_play_root / rel
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_path"] = str(path)
    payload["_sha256"] = sha256_file(path)
    return payload


def snapshot_for_supplement_task(task: Dict[str, Any], budgets: Dict[str, Any]) -> Any:
    if task.get("query_purpose") == "dynamic_twohop":
        hop1 = task["hop1"]
        synthetic = {
            "task_id": task["task_id"],
            "question": task["question"],
            "entity": hop1["entity"],
            "entity_public_name": task.get("entity_public_name") or hop1["entity"],
            "source_entities": [hop1["entity"]],
            "enumerated_relations": [
                {"entity": hop1["entity"], "relation": hop1["relation"], "direction": hop1["direction"]}
            ],
            "steps": [
                {
                    "type": "EXPAND",
                    "entity": hop1["entity"],
                    "relation": hop1["relation"],
                    "direction": hop1["direction"],
                }
            ],
        }
        return snapshot_for_task(synthetic, budgets)
    return snapshot_for_task(task, budgets)


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
    if config.get("stage") != "SP2-A-SUPPLEMENT":
        errors.append("config stage must be SP2-A-SUPPLEMENT")
    if config.get("parent_plan_version") != EXPECTED_PARENT_PLAN:
        errors.append("parent_plan_version mismatch")
    if config.get("expected_sp2a_run_id") != EXPECTED_SP2A_RUN:
        errors.append("expected parent SP2-A run id mismatch")
    if config.get("direction_semantics") != "current_entity_role":
        errors.append("direction_semantics must remain current_entity_role")
    if config.get("canonicalization_version") != "sp1-canonical-v1":
        errors.append("canonicalization_version must remain sp1-canonical-v1")
    if scan_config_for_secrets(config):
        errors.append(f"secret-like keys in config: {scan_config_for_secrets(config)}")
    overall = (workspace.self_play_root / "exp_plan" / "00_experiment_overall_requirements.md").read_text(
        encoding="utf-8"
    )
    if "03A_SP2A_supplement_tail_and_dynamic_multihop.md" not in overall:
        errors.append("overall file does not register the supplement plan")
    if EXPECTED_SP2A_RUN not in overall:
        errors.append("parent SP2-A run id missing from overall")
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
    registry = load_supplement_registry(workspace, config)
    errors.extend(validate_supplement_registry(registry))
    for task in registry.get("tasks") or []:
        try:
            assert_task_not_eval(task["task_id"], banned, guards)
        except ProtocolError as exc:
            errors.append(exc.message)
    memory_hits = source_mentions_memory(workspace)
    if memory_hits:
        errors.append(f"memory write markers in SP2-A sources: {memory_hits}")
    return {
        "ok": not errors,
        "errors": errors,
        "readonly_snapshot": snapshot_readonly_roots(workspace),
        "supplement_registry_sha256": registry["_sha256"],
        "exclusion_count": len(banned),
        "baseline_hashes": current,
        "llm_calls": guards.llm.calls,
        "memory_reads": guards.memory.reads,
        "memory_writes": guards.memory.writes,
    }


def experiment_s1(config: Dict[str, Any], workspace: Workspace, registry: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    pf = preflight(config, workspace, guards)
    metrics = {
        "preflight_ok": pf["ok"],
        "llm_calls": guards.llm.calls,
        "memory_reads": guards.memory.reads,
        "memory_writes": guards.memory.writes,
        "supplement_registry_sha256": registry["_sha256"],
    }
    if not pf["ok"]:
        return _fail("S2A-S.1", metrics, "preflight failed", {"errors": pf["errors"]})
    return _ok("S2A-S.1", metrics, {"preflight": {k: pf[k] for k in ("ok", "errors", "exclusion_count")}})


def experiment_s2(config: Dict[str, Any], registry: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    adapter, env, ledger = make_adapter(config)
    tasks = [item for item in registry["tasks"] if item.get("query_purpose") == "TAIL_positive"]
    if not tasks:
        return _fail("S2A-S.2", {"tail_positive_cases": 0}, "no TAIL_positive task registered")
    rows = []
    nonempty = 0
    direction_ok_n = 0
    for task in tasks:
        env.set_task(task["task_id"])
        step = task["steps"][0]
        request_info = assert_tail_request(step["entity"], step["relation"], endpoint=config["endpoint"])
        snap = snapshot_for_supplement_task(task, config["budgets"])
        before = adapter.project_visible_state(snap)
        applied, outcome, result = apply_step(adapter, snap, step, f"s2-{task['task_id']}")
        if result is not None and result.status in {EnvironmentStatus.TIMEOUT, EnvironmentStatus.ENDPOINT_FAILURE}:
            return _invalid(
                "S2A-S.2",
                {"reachable": False, "status": result.status.value},
                "live KG endpoint unavailable",
            )
        after = adapter.project_visible_state(applied)
        triples = [] if result is None else result.results
        assertion = assert_tail_positive_triples(
            triples,
            query_entity=step["entity"],
            relation=step["relation"],
            expected_subjects=task["expected_subjects"],
        )
        entered_state = bool(triples) and all(item in after.observed_triples_or_summaries for item in triples)
        case_ok = (
            outcome.accepted
            and result is not None
            and result.failure_class is None
            and bool(triples)
            and assertion["ok"]
            and entered_state
            and before.state_id != after.state_id
            and request_info["ok"]
        )
        if bool(triples):
            nonempty += 1
        if assertion["direction_ok"] and assertion["reconstructed_ok"]:
            direction_ok_n += 1
        rows.append(
            {
                "task_id": task["task_id"],
                "ok": case_ok,
                "status": None if result is None else result.status.value,
                "request_hash": request_info["request_hash"],
                "sparql": request_info["sparql"],
                "head": request_info["head"],
                "triples": triples[:12],
                "triple_count": len(triples),
                "triples_hash": canonical_hash(triples),
                "subjects": assertion["subjects"][:20],
                "missing_expected_subjects": assertion["missing_expected_subjects"],
                "state_id_before": before.state_id,
                "state_id_after": after.state_id,
                "entered_visible_state": entered_state,
            }
        )
    rate = nonempty / len(tasks)
    direction_rate = direction_ok_n / len(tasks)
    metrics = {
        "tail_positive_cases": len(tasks),
        "tail_nonempty_rate": rate,
        "tail_direction_canonical_rate": direction_rate,
        "logical_actions": ledger.logical_actions,
        "physical_requests": ledger.physical_requests,
        "llm_calls": guards.llm.calls,
    }
    extra = {"rows": rows, "audit": env.audit_records}
    if rate != 1.0 or direction_rate != 1.0 or any(not row["ok"] for row in rows):
        return _fail("S2A-S.2", metrics, "TAIL-positive case did not return the known reverse edge", extra)
    return _ok("S2A-S.2", metrics, extra)


def _run_dynamic_twohop(config: Dict[str, Any], task: Dict[str, Any], *, transport=None, network_enabled: bool = True, records=None):
    adapter, env, ledger = make_adapter(config, transport=transport, network_enabled=network_enabled, records=records)
    env.set_task(task["task_id"])
    snap = snapshot_for_supplement_task(task, config["budgets"])
    hop1 = task["hop1"]
    hop2 = task["hop2"]
    before = adapter.project_visible_state(snap)
    applied, outcome1, result1 = apply_step(
        adapter,
        snap,
        {"type": "EXPAND", "entity": hop1["entity"], "relation": hop1["relation"], "direction": hop1["direction"]},
        "s3-hop1",
    )
    after1 = adapter.project_visible_state(applied)
    hop1_entities = extract_canonical_entities(hop1["direction"], [] if result1 is None else result1.results)
    hop2_physical_before = ledger.physical_requests
    materialized = None
    outcome2 = None
    result2 = None
    after2 = after1
    applied2 = applied
    if result1 is not None and result1.failure_class is None:
        materialized = materialize_hop2_from_hop1(
            applied,
            after1,
            result1.results,
            hop1_direction=hop1["direction"],
            hop2_relation=hop2["relation"],
            hop2_direction=hop2["direction"],
            selection_rule=task["hop1_candidate_constraint"]["selection_rule"],
            endpoint=config["endpoint"],
            constraint=task["hop1_candidate_constraint"],
        )
        if materialized is not None:
            hop2_state = adapter.project_visible_state(materialized.snapshot)
            action = make_expand_action(materialized.action_params, hop2_state, "s3-hop2")
            applied2, outcome2, result2 = adapter.apply_action(materialized.snapshot, action)
            after2 = adapter.project_visible_state(applied2)
    hop2_physical_delta = ledger.physical_requests - hop2_physical_before
    return {
        "adapter": adapter,
        "env": env,
        "ledger": ledger,
        "before": before,
        "after1": after1,
        "after2": after2,
        "applied2": applied2,
        "outcome1": outcome1,
        "outcome2": outcome2,
        "result1": result1,
        "result2": result2,
        "hop1_entities": hop1_entities,
        "materialized": materialized,
        "hop2_physical_delta": hop2_physical_delta,
    }


def experiment_s3(config: Dict[str, Any], registry: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    tasks = [item for item in registry["tasks"] if item.get("query_purpose") == "dynamic_twohop"]
    if not tasks:
        return _fail("S2A-S.3", {"dynamic_twohop_cases": 0}, "no dynamic_twohop task registered")
    rows = []
    ok = True
    hop2_from_hop1 = 0
    hop2_n = 0
    audits: List[Dict[str, Any]] = []
    for task in tasks:
        first = _run_dynamic_twohop(config, task)
        hop2_n += 1
        audits.extend(first["env"].audit_records)
        materialized = first["materialized"]
        result1 = first["result1"]
        result2 = first["result2"]
        outcome1 = first["outcome1"]
        outcome2 = first["outcome2"]
        if result1 is not None and result1.status in {EnvironmentStatus.TIMEOUT, EnvironmentStatus.ENDPOINT_FAILURE}:
            return _invalid(
                "S2A-S.3",
                {"reachable": False, "status": result1.status.value},
                "live KG endpoint unavailable",
            )
        hop1_ok = (
            outcome1.accepted
            and result1 is not None
            and result1.failure_class is None
            and bool(first["hop1_entities"])
            and first["before"].state_id != first["after1"].state_id
        )
        hop2_ok = False
        entity_from_hop1 = False
        if materialized is not None and outcome2 is not None and result2 is not None:
            entity_from_hop1 = hop2_entity_matches_hop1(materialized, first["hop1_entities"])
            hop2_req = build_entity_search_request(
                materialized.hop2_entity,
                materialized.hop2_relation,
                materialized.hop2_direction,
                endpoint=config["endpoint"],
            )
            expected_entity = select_hop1_entity(
                first["hop1_entities"],
                task["hop1_candidate_constraint"]["selection_rule"],
            )
            hop2_ok = (
                outcome2.accepted
                and result2.failure_class is None
                and entity_from_hop1
                and hop2_req.entity == materialized.hop2_entity
                and hop2_req.entity == expected_entity
                and first["after1"].state_id != first["after2"].state_id
                and first["applied2"].budget.used_kg_calls >= 2
                and first["applied2"].budget.used_steps >= 2
                and first["hop2_physical_delta"] >= 1
            )
            if entity_from_hop1:
                hop2_from_hop1 += 1
        repeat = _run_dynamic_twohop(config, task)
        replay_ok = (
            first["after1"].state_id == repeat["after1"].state_id
            and first["after2"].state_id == repeat["after2"].state_id
            and (None if first["materialized"] is None else first["materialized"].hop2_entity)
            == (None if repeat["materialized"] is None else repeat["materialized"].hop2_entity)
        )
        case_ok = hop1_ok and hop2_ok and replay_ok
        if not case_ok:
            ok = False
        rows.append(
            {
                "task_id": task["task_id"],
                "ok": case_ok,
                "hop1_status": None if result1 is None else result1.status.value,
                "hop2_status": None if result2 is None else result2.status.value,
                "hop1_entities": first["hop1_entities"][:12],
                "hop2_entity": None if materialized is None else materialized.hop2_entity,
                "hop1_state_id": first["after1"].state_id,
                "hop2_state_id": first["after2"].state_id,
                "hop1_binding_index": None if materialized is None else materialized.hop1_binding_index,
                "hop1_source": None if materialized is None else materialized.hop1_source,
                "hop2_request_hash": None if materialized is None else materialized.request_hash,
                "entity_from_hop1": entity_from_hop1,
                "depth_used": first["applied2"].budget.used_depth,
                "kg_used": first["applied2"].budget.used_kg_calls,
                "steps_used": first["applied2"].budget.used_steps,
                "logical": first["ledger"].logical_actions,
                "physical": first["ledger"].physical_requests,
                "repeat_state_agreement": replay_ok,
                "frontier_after": list(first["after2"].frontier)[:20],
                "triple_count": len(first["after2"].observed_triples_or_summaries),
            }
        )
    rate = hop2_from_hop1 / hop2_n if hop2_n else 0.0
    transition_rate = 1.0 if ok else 0.0
    metrics = {
        "dynamic_twohop_cases": hop2_n,
        "hop2_entity_from_hop1_rate": rate,
        "dynamic_twohop_transition_rate": transition_rate,
        "dynamic_twohop_replay_agreement": 1.0 if all(row["repeat_state_agreement"] for row in rows) else 0.0,
        "llm_calls": guards.llm.calls,
    }
    extra = {"rows": rows, "audit": audits}
    if not ok or rate != 1.0:
        return _fail("S2A-S.3", metrics, "dynamic two-hop did not use the live hop1 entity", extra)
    return _ok("S2A-S.3", metrics, extra)


def _scripted_uri_bindings(mids: List[str]) -> str:
    bindings = [{"tailEntity": {"type": "uri", "value": f"http://rdf.freebase.com/ns/{mid}"}} for mid in mids]
    return json.dumps({"head": {"vars": ["tailEntity"]}, "results": {"bindings": bindings}})


def experiment_s4(config: Dict[str, Any], workspace: Workspace, registry: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    faults = load_faults(workspace)
    twohop = next(item for item in registry["tasks"] if item["query_purpose"] == "dynamic_twohop")
    rows = []
    ok = True

    empty = ScriptedTransport([RawHttpResponse(200, json.dumps({"head": {"vars": ["tailEntity"]}, "results": {"bindings": []}}))])
    empty_run = _run_dynamic_twohop(config, twohop, transport=empty)
    empty_ok = (
        empty_run["outcome1"].accepted
        and empty_run["result1"] is not None
        and empty_run["result1"].status is EnvironmentStatus.EMPTY_SUCCESS
        and empty_run["materialized"] is None
        and empty_run["hop2_physical_delta"] == 0
        and empty_run["result2"] is None
    )
    if not empty_ok:
        ok = False
    rows.append(
        {
            "case": "empty_hop1_no_hop2_physical",
            "ok": empty_ok,
            "hop2_physical_delta": empty_run["hop2_physical_delta"],
            "mode": "scripted_transport",
        }
    )

    multi = ScriptedTransport(
        [
            RawHttpResponse(200, _scripted_uri_bindings(["m.zz_second", "m.aa_first"])),
            RawHttpResponse(200, json.dumps(faults["success_bindings"])),
        ]
    )
    multi_run = _run_dynamic_twohop(config, twohop, transport=multi)
    selected = None if multi_run["materialized"] is None else multi_run["materialized"].hop2_entity
    multi_ok = selected == "m.aa_first" and multi_run["hop2_physical_delta"] == 1
    if not multi_ok:
        ok = False
    rows.append({"case": "multi_hop1_sort_first", "ok": multi_ok, "selected": selected, "mode": "scripted_transport"})

    dup = ScriptedTransport(
        [
            RawHttpResponse(200, _scripted_uri_bindings(["m.dup", "m.dup", "m.other"])),
            RawHttpResponse(200, json.dumps(faults["success_bindings"])),
        ]
    )
    dup_run = _run_dynamic_twohop(config, twohop, transport=dup)
    dup_selected = None if dup_run["materialized"] is None else dup_run["materialized"].hop2_entity
    dup_ok = dup_selected == "m.dup" and dup_run["hop1_entities"] == ["m.dup", "m.other"]
    if not dup_ok:
        ok = False
    rows.append({"case": "duplicate_hop1_entities", "ok": dup_ok, "selected": dup_selected, "mode": "scripted_transport"})

    live_like = ScriptedTransport(
        [
            RawHttpResponse(200, _scripted_uri_bindings(["m.02hrh0_"])),
            RawHttpResponse(200, json.dumps(faults["success_bindings"])),
        ]
    )
    illegal_adapter, illegal_env, illegal_ledger = make_adapter(config, transport=live_like)
    illegal_env.set_task("sp2a.supp.scripted.illegal_entity")
    snap = snapshot_for_supplement_task(twohop, config["budgets"])
    applied, outcome1, result1 = apply_step(
        illegal_adapter,
        snap,
        {"type": "EXPAND", **twohop["hop1"]},
        "s4-hop1",
    )
    after1 = illegal_adapter.project_visible_state(applied)
    physical_before = illegal_ledger.physical_requests
    fake_params = {"entity": "m.not_from_hop1", "relation": twohop["hop2"]["relation"], "direction": twohop["hop2"]["direction"]}
    fake_snapshot = applied.clone()
    fake_snapshot.enumerated_relations.append(
        VisibleRelation(entity="m.not_from_hop1", relation=twohop["hop2"]["relation"], direction=Direction(twohop["hop2"]["direction"]))
    )
    fake_state = illegal_adapter.project_visible_state(fake_snapshot)
    fake_action = make_expand_action(fake_params, fake_state, "s4-illegal")
    _, fake_outcome, fake_result = illegal_adapter.apply_action(fake_snapshot, fake_action)
    illegal_ok = (
        not fake_outcome.accepted
        and fake_result is None
        and illegal_ledger.physical_requests == physical_before
        and fake_outcome.visible_result.get("failure_class") == FailureClass.ACTION_SPACE_FAILURE.value
    )
    if not illegal_ok:
        ok = False
    rows.append(
        {
            "case": "illegal_hop2_entity_no_physical",
            "ok": illegal_ok,
            "physical": illegal_ledger.physical_requests - physical_before,
            "mode": "scripted_transport",
        }
    )

    preserve = ScriptedTransport(
        [
            RawHttpResponse(200, _scripted_uri_bindings(["m.02hrh0_"])),
            RawHttpResponse(200, json.dumps(faults["malformed_missing_tail_entity"])),
        ]
    )
    preserve_run = _run_dynamic_twohop(config, twohop, transport=preserve)
    hop1_kept = any(
        item.get("object") == "m.02hrh0_" or item.get("subject") == "m.02hrh0_"
        for item in preserve_run["after2"].observed_triples_or_summaries
    )
    preserve_ok = (
        preserve_run["outcome1"].accepted
        and preserve_run["result2"] is not None
        and preserve_run["result2"].status is EnvironmentStatus.MALFORMED
        and not preserve_run["outcome2"].accepted
        and hop1_kept
    )
    if not preserve_ok:
        ok = False
    rows.append(
        {
            "case": "hop2_failure_keeps_hop1",
            "ok": preserve_ok,
            "hop2_status": None if preserve_run["result2"] is None else preserve_run["result2"].status.value,
            "mode": "scripted_transport",
        }
    )

    tiny = dict(config["budgets"])
    tiny["max_kg_calls"] = 1
    tiny_task = deepcopy(twohop)
    tiny_cfg = dict(config)
    tiny_cfg["budgets"] = tiny
    budget_transport = ScriptedTransport(
        [
            RawHttpResponse(200, _scripted_uri_bindings(["m.02hrh0_"])),
            RawHttpResponse(200, json.dumps(faults["success_bindings"])),
        ]
    )
    budget_run = _run_dynamic_twohop(tiny_cfg, tiny_task, transport=budget_transport)
    budget_ok = (
        budget_run["outcome1"].accepted
        and budget_run["materialized"] is not None
        and budget_run["outcome2"] is not None
        and not budget_run["outcome2"].accepted
        and budget_run["result2"] is None
        and budget_run["hop2_physical_delta"] == 0
        and budget_run["outcome2"].visible_result.get("failure_class") == FailureClass.BUDGET_INSUFFICIENT.value
        and budget_transport.calls == 1
    )
    if not budget_ok:
        ok = False
    rows.append(
        {
            "case": "budget_blocks_hop2_physical",
            "ok": budget_ok,
            "hop2_physical_delta": budget_run["hop2_physical_delta"],
            "transport_calls": budget_transport.calls,
            "mode": "scripted_transport",
        }
    )

    timeout = ScriptedTransport(
        [
            RawHttpResponse(200, _scripted_uri_bindings(["m.02hrh0_"])),
            TransportTimeout("injected hop2 timeout"),
            RawHttpResponse(200, json.dumps(faults["success_bindings"])),
        ]
    )
    timeout_run = _run_dynamic_twohop(config, twohop, transport=timeout)
    timeout_ok = (
        timeout_run["outcome1"].accepted
        and timeout_run["outcome2"] is not None
        and timeout_run["outcome2"].accepted
        and timeout_run["ledger"].logical_actions == 2
        and timeout_run["ledger"].physical_requests == 3
        and timeout_run["ledger"].retries == 1
        and timeout.calls == 3
    )
    if not timeout_ok:
        ok = False
    rows.append(
        {
            "case": "hop2_timeout_then_success",
            "ok": timeout_ok,
            "logical": timeout_run["ledger"].logical_actions,
            "physical": timeout_run["ledger"].physical_requests,
            "retries": timeout_run["ledger"].retries,
            "mode": "scripted_transport",
        }
    )

    metrics = {
        "empty_hop1_hop2_physical": empty_run["hop2_physical_delta"],
        "boundary_correct_rate": 1.0 if ok else 0.0,
        "llm_calls": guards.llm.calls,
    }
    extra = {"rows": rows}
    if not ok:
        return _fail("S2A-S.4", metrics, "supplement boundary regression failed", extra)
    return _ok("S2A-S.4", metrics, extra)


def experiment_replay(
    config: Dict[str, Any],
    workspace: Workspace,
    registry: Dict[str, Any],
    live_experiments: Dict[str, Any],
    guards: Sp2aGuards,
) -> Dict[str, Any]:
    records = []
    for name in ("S2A-S.2", "S2A-S.3"):
        records.extend((live_experiments.get(name) or {}).get("audit") or [])
    if not records:
        return _fail("S2A-S.replay", {}, "no live audit records to replay")
    path = write_recorded_io(records, workspace, relative=RECORDED_IO_REL)
    payload = json.loads(path.read_text(encoding="utf-8"))
    indexed = index_records(payload)
    tail = next(item for item in registry["tasks"] if item["query_purpose"] == "TAIL_positive")
    adapter, env, _ = make_adapter(config, network_enabled=False, records=indexed)
    env.set_task(tail["task_id"])
    snap = snapshot_for_supplement_task(tail, config["budgets"])
    applied, outcome, result = apply_step(adapter, snap, tail["steps"][0], "replay-tail")
    after = adapter.project_visible_state(applied)
    live_row = ((live_experiments.get("S2A-S.2") or {}).get("rows") or [{}])[0]
    agreement = (
        outcome.accepted
        and result is not None
        and after.state_id == live_row.get("state_id_after")
        and canonical_hash(result.results or []) == live_row.get("triples_hash")
        and env.client.physical_calls == 0
        and all(not item.get("network_used") for item in env.audit_records)
    )
    twohop = next(item for item in registry["tasks"] if item["query_purpose"] == "dynamic_twohop")
    replay_two = _run_dynamic_twohop(config, twohop, network_enabled=False, records=indexed)
    live_two = ((live_experiments.get("S2A-S.3") or {}).get("rows") or [{}])[0]
    two_ok = (
        replay_two["after2"].state_id == live_two.get("hop2_state_id")
        and (None if replay_two["materialized"] is None else replay_two["materialized"].hop2_entity)
        == live_two.get("hop2_entity")
        and replay_two["env"].client.physical_calls == 0
    )
    metrics = {
        "record_count": len(payload["records"]),
        "bundle_hash": payload.get("bundle_hash"),
        "replay_agreement": 1.0 if agreement and two_ok else 0.0,
        "network_during_replay": False,
        "llm_calls": guards.llm.calls,
        "recorded_io_path": RECORDED_IO_REL,
    }
    extra = {"tail_ok": agreement, "twohop_ok": two_ok}
    if not (agreement and two_ok):
        return _fail("S2A-S.replay", metrics, "recorded I/O replay mismatch", extra)
    return _ok("S2A-S.replay", metrics, extra)


def _overlap_mids(registry: Dict[str, Any], workspace: Workspace) -> List[str]:
    used = set()
    for task in registry["tasks"]:
        if task.get("entity"):
            used.add(task["entity"])
        hop1 = task.get("hop1") or {}
        if hop1.get("entity"):
            used.add(hop1["entity"])
        edge = task.get("known_edge") or {}
        for key in ("subject", "object"):
            if edge.get(key):
                used.add(edge[key])
        for item in task.get("expected_subjects") or []:
            used.add(item)
        for item in task.get("source_entities") or []:
            used.add(item)
        for step in task.get("steps") or []:
            if step.get("entity"):
                used.add(step["entity"])
    path = workspace.artifacts_root / "registries" / "benchmark_exclusion_registry_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    overlap = set()
    for record in payload.get("records") or []:
        for mid in list(record.get("topic_entities") or []) + list(record.get("answer_entities") or []):
            if mid in used:
                overlap.add(mid)
    return sorted(overlap)


def summarize_metrics(experiments: Dict[str, Any], guards: Sp2aGuards) -> Dict[str, Any]:
    s2 = (experiments.get("S2A-S.2") or {}).get("metrics") or {}
    s3 = (experiments.get("S2A-S.3") or {}).get("metrics") or {}
    s4 = (experiments.get("S2A-S.4") or {}).get("metrics") or {}
    replay = (experiments.get("S2A-S.replay") or {}).get("metrics") or {}
    return {
        "real_llm_calls": guards.llm.calls,
        "memory_reads": guards.memory.reads,
        "memory_writes": guards.memory.writes,
        "oracle_label_in_action": guards.oracle_label_in_action,
        "eval_set_trajectory_uses": guards.eval_set_trajectory_uses,
        "tail_positive_cases": s2.get("tail_positive_cases", 0),
        "tail_nonempty_rate": s2.get("tail_nonempty_rate", 0.0),
        "tail_direction_canonical_rate": s2.get("tail_direction_canonical_rate", 0.0),
        "hop2_entity_from_hop1_rate": s3.get("hop2_entity_from_hop1_rate", 0.0),
        "dynamic_twohop_transition_rate": s3.get("dynamic_twohop_transition_rate", 0.0),
        "dynamic_twohop_replay_agreement": s3.get("dynamic_twohop_replay_agreement", 0.0),
        "empty_hop1_hop2_physical": s4.get("empty_hop1_hop2_physical", -1),
        "budget_boundary_correct_rate": s4.get("boundary_correct_rate", 0.0),
        "recorded_io_replay_agreement": replay.get("replay_agreement", 0.0),
        "unclassified_exceptions": 0,
    }


def run_all_supplement_experiments(config: Dict[str, Any], workspace: Workspace) -> Dict[str, Any]:
    guards = Sp2aGuards()
    unclassified: List[Dict[str, Any]] = []
    experiments: Dict[str, Any] = {}
    registry = load_supplement_registry(workspace, config)
    readonly_before = snapshot_readonly_roots(workspace)

    def capture(name, fn):
        try:
            experiments[name] = fn()
        except Exception as exc:
            unclassified.append({"name": name, "error": repr(exc), "traceback": traceback.format_exc()})
            experiments[name] = _fail(name, {}, repr(exc), {"traceback": traceback.format_exc()})

    capture("S2A-S.1", lambda: experiment_s1(config, workspace, registry, guards))
    if experiments.get("S2A-S.1", {}).get("status") != "PASS":
        metrics = summarize_metrics(experiments, guards)
        metrics["unclassified_exceptions"] = len(unclassified)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "plan_version": config.get("plan_version"),
            "status": "FAIL",
            "reason": "preflight_failed",
            "experiments": experiments,
            "metrics": metrics,
            "unclassified_exceptions": unclassified,
            "guards": guards.counts(),
            "supplement_registry_sha256": registry["_sha256"],
        }

    capture("S2A-S.2", lambda: experiment_s2(config, registry, guards))
    if experiments.get("S2A-S.2", {}).get("status") == "INVALID":
        metrics = summarize_metrics(experiments, guards)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "plan_version": config.get("plan_version"),
            "status": "INVALID",
            "reason": "live_kg_unavailable",
            "experiments": experiments,
            "metrics": metrics,
            "guards": guards.counts(),
            "supplement_registry_sha256": registry["_sha256"],
        }
    capture("S2A-S.3", lambda: experiment_s3(config, registry, guards))
    capture("S2A-S.4", lambda: experiment_s4(config, workspace, registry, guards))
    capture("S2A-S.replay", lambda: experiment_replay(config, workspace, registry, experiments, guards))

    eval_changed = []
    paths = eval_set_paths(workspace)
    for name, digest in EXPECTED_EVAL_HASHES.items():
        if sha256_file(paths[name]) != digest:
            eval_changed.append(name)
    secret_hits = scan_paths_for_secrets(
        [
            workspace.artifacts_root / "recorded_io" / "sp2a" / "sp2a_supplement_recorded_io_v1.json",
            workspace.artifacts_root / "protocol" / "sp2a_supplement_check_result.json",
        ]
    )
    baseline_changed = assert_baseline_unchanged(workspace, EXPECTED_BASELINE_HASHES)
    metrics = summarize_metrics(experiments, guards)
    metrics["unclassified_exceptions"] = len(unclassified)
    metrics["secret_hits"] = len(secret_hits)
    metrics["eval_file_hash_changes"] = len(eval_changed)
    metrics["unregistered_baseline_hash_changes"] = len(baseline_changed)
    names = ["S2A-S.1", "S2A-S.2", "S2A-S.3", "S2A-S.4", "S2A-S.replay"]
    all_pass = (
        all(experiments.get(name, {}).get("status") == "PASS" for name in names)
        and not unclassified
        and guards.llm.calls == 0
        and guards.memory.reads == 0
        and guards.memory.writes == 0
        and not secret_hits
        and not eval_changed
        and not baseline_changed
        and metrics["tail_positive_cases"] >= 1
        and metrics["tail_nonempty_rate"] == 1.0
        and metrics["tail_direction_canonical_rate"] == 1.0
        and metrics["hop2_entity_from_hop1_rate"] == 1.0
        and metrics["dynamic_twohop_transition_rate"] == 1.0
        and metrics["dynamic_twohop_replay_agreement"] == 1.0
        and metrics["empty_hop1_hop2_physical"] == 0
        and metrics["recorded_io_replay_agreement"] == 1.0
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
        "supplement_registry_sha256": registry["_sha256"],
        "endpoint": config["endpoint"],
        "secret_hits": secret_hits,
        "baseline_changed": baseline_changed,
        "eval_overlap_mids": overlap,
        "readonly_snapshot": readonly_before,
        "eval_file_changes": eval_changed,
    }


def finalize_report_hash(workspace: Workspace) -> str:
    """Hash the frozen SP2-A report and write it into metrics.json. Must run after the report text is final."""
    report = workspace.self_play_root / "reports" / "sp2a" / "SP2A_experiment_report.md"
    metrics_path = workspace.self_play_root / "reports" / "sp2a" / "metrics.json"
    digest = sha256_file(report)
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    payload["report_sha256"] = digest
    workspace.safe_write_text(metrics_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return digest
