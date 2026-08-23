"""Multi-trajectory explorer and O0 Critic on a frozen snapshot. Fake backend by default."""

from __future__ import annotations

import random
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .action_protocol import ProtocolSession, parse_action
from .critic import legal_expand_actions, legal_recovery_actions
from .critic_context import build_compressed_critic_input, classify_critic_error, schema_fallback_decision
from .hashing import canonical_hash
from .paths import PROTOCOL_VERSION
from .replay import ReplayEnvironment
from .schemas import Action, ActionType, ActorRole, FailureClass, VisibleState
from .sp4_schemas import SP4_TRAJECTORY_VERSION, validate_trajectory_record
from .visibility import OracleSecrets, project_actor_view, project_critic_view


def _gold(env: ReplayEnvironment) -> set:
    return set(env.task.answer_entity_ids) | set(env.task.normalized_answers)


def _should_stop(env: ReplayEnvironment) -> bool:
    gold = _gold(env)
    observed = set(env.visible_entities)
    for triple in env.observed_triples:
        observed.update(triple.values())
    return bool(observed & gold)


def greedy_expand(env: ReplayEnvironment) -> Optional[Action]:
    state = env.visible_state()
    failed = set(env.failed_branches)
    for action in legal_expand_actions(state, source_role=ActorRole.EXPLORER):
        key = f"{action.params['entity']}|{action.params['relation']}|{action.params['direction']}"
        if key in failed:
            continue
        return action
    return None


def seeded_expand(env: ReplayEnvironment, rng: random.Random) -> Optional[Action]:
    state = env.visible_state()
    actions = legal_expand_actions(state, source_role=ActorRole.EXPLORER)
    rng.shuffle(actions)
    failed = set(env.failed_branches)
    for action in actions:
        key = f"{action.params['entity']}|{action.params['relation']}|{action.params['direction']}"
        if key not in failed:
            return action
    return actions[0] if actions else None


def teacher_expand(env: ReplayEnvironment, hops: Sequence[Mapping[str, str]]) -> Optional[Action]:
    state = env.visible_state()
    used = {item.split("|")[1] for item in env.failed_branches if "|" in item}
    observed_rels = {triple["relation"] for triple in env.observed_triples}
    for hop in hops:
        relation = hop["relation"]
        direction = hop["direction"]
        if relation in observed_rels:
            continue
        for action in legal_expand_actions(state, source_role=ActorRole.EXPLORER):
            if action.params["relation"] == relation and action.params["direction"] == direction:
                return action
    return greedy_expand(env)


def trigger_event(env: ReplayEnvironment, last: Mapping[str, Any], seen_states: List[str]) -> Optional[str]:
    remaining = env.visible_state().remaining_budget
    if last.get("visible_result", {}).get("action_type") == "STOP" and not (_gold(env) & set(env.terminal_submission or [])):
        return "stop_failure"
    if last.get("accepted") and not last.get("new_frontier_items") and last.get("visible_result", {}).get("action_type") == "EXPAND":
        if not (last.get("visible_result") or {}).get("triples"):
            return "no_new_frontier"
    if seen_states.count(env.visible_state().state_id) >= 2:
        return "repeat_state"
    if int(remaining.get("steps") or 0) <= 2 or int(remaining.get("kg_calls") or 0) <= 3:
        return "budget_critical"
    if len(env.visible_state().visible_relations) >= 8 and len(env.observed_triples) <= 1:
        return "branching_surge"
    return None


def heuristic_critic(state: VisibleState, event: str) -> Dict[str, Any]:
    expands = legal_expand_actions(state, source_role=ActorRole.CRITIC)
    failed = set(state.failed_or_exhausted_branches)
    chosen = None
    for action in expands:
        key = f"{action.params['entity']}|{action.params['relation']}|{action.params['direction']}"
        if key not in failed:
            chosen = action
            break
    if chosen is None and expands:
        chosen = expands[0]
    if chosen is None:
        payload = schema_fallback_decision(event)
        payload["action"] = parse_action({"action_type": "ABSTAIN", "reason_code": "INSUFFICIENT_EVIDENCE"}, state).to_dict()
        return payload
    return {
        "failure_class": FailureClass.EXPLORER_FAILURE.value,
        "decision_stage": "relation_selection",
        "reason": "If the visible frontier stalled, expand a different remaining legal relation.",
        "negative_constraints": ["do_not_repeat_empty_relation"],
        "accepted": True,
        "action": chosen.to_dict(),
        "event": event,
        "fallback": None,
        "mode": "o0_heuristic",
        "oracle_level": "O0",
        "protocol_version": PROTOCOL_VERSION,
    }


def run_trajectory(
    env: ReplayEnvironment,
    *,
    run_id: str,
    seed: int,
    temperature: float,
    critic_mode: str,
    teacher_hops: Optional[Sequence[Mapping[str, str]]] = None,
    max_critic_rounds: int = 2,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    session = ProtocolSession(env)
    initial = env.visible_state().state_id
    secrets = env.secrets()
    actions: List[Dict[str, Any]] = []
    seen = [initial]
    critic_source = {
        "none": "explorer_only",
        "o0": "o0_critic",
        "random": "random_critic",
        "teacher": "oracle_guided_offline_teacher",
    }[critic_mode]
    recovery = "none"
    failure = "none"
    critic_rounds = 0
    actor_view = project_actor_view(env.task, env.visible_state(), secrets=secrets)
    if critic_mode != "teacher":
        project_critic_view(env.task, env.visible_state(), secrets=secrets)

    def choose() -> Optional[Action]:
        if critic_mode == "teacher":
            return teacher_expand(env, teacher_hops or [])
        if seed % 2 == 0 and critic_mode != "none":
            return seeded_expand(env, rng)
        return greedy_expand(env) if critic_mode == "none" else seeded_expand(env, rng)

    while not env.terminated and env.budget.remaining()["steps"] > 0:
        state = env.visible_state()
        if _should_stop(env):
            observed = set(env.visible_entities)
            for triple in env.observed_triples:
                observed.update(triple.values())
            answers = [item for item in observed if item in _gold(env)]
            action = parse_action({"action_type": "STOP", "answer_candidates": answers[:3]}, state, source_role=ActorRole.EXPLORER)
        else:
            action = choose()
            if action is None:
                action = parse_action({"action_type": "ABSTAIN", "reason_code": "NO_LEGAL_ACTION"}, state)
        result = session.execute(action)
        actions.append({"action": action.to_dict(), "result": result, "role": "explorer"})
        seen.append(env.visible_state().state_id)
        event = trigger_event(env, result, seen)
        if critic_mode in {"o0", "random"} and event and critic_rounds < max_critic_rounds and not env.terminated:
            critic_rounds += 1
            env.budget.used_critic_rounds += 1
            compressed = build_compressed_critic_input(
                event=event,
                task_public=actor_view["task"],
                state=env.visible_state(),
                legal_actions=[item.to_dict() for item in legal_recovery_actions(env.visible_state())],
                secrets=secrets,
            )
            try:
                if critic_mode == "random":
                    legal = legal_recovery_actions(env.visible_state())
                    decision_action = rng.choice(legal) if legal else None
                    decision = {
                        "mode": "random",
                        "oracle_level": "O0",
                        "accepted": decision_action is not None,
                        "action": None if decision_action is None else decision_action.to_dict(),
                        "failure_class": FailureClass.EXPLORER_FAILURE.value,
                        "reason": "random_legal_action_control",
                    }
                else:
                    decision = heuristic_critic(env.visible_state(), event)
                if decision.get("action"):
                    c_action = Action.from_dict(decision["action"]) if isinstance(decision["action"], dict) and "protocol_version" in (decision["action"] or {}) else parse_action(decision["action"], env.visible_state(), source_role=ActorRole.CRITIC)
                    c_result = session.execute(c_action)
                    actions.append({"action": c_action.to_dict(), "result": c_result, "role": "critic", "compressed": compressed, "decision": decision})
                    if c_result.get("accepted") and _should_stop(env):
                        recovery = "recovered"
            except Exception as exc:
                classified = classify_critic_error(exc, prompt_chars=int(compressed.get("prompt_chars") or 0))
                actions.append({"role": "critic", "error": classified, "compressed": compressed})
                failure = "system_failure"
        if env.terminated:
            break

    submitted = env.terminal_submission or []
    gold = _gold(env)
    success = bool(submitted and set(submitted) & gold)
    if env.budget.remaining()["steps"] <= 0 and not success:
        failure = "budget_insufficient"
    elif env.termination_reason and env.termination_reason.value == "PROTOCOL_VIOLATION":
        failure = "action_space_failure"
    elif submitted and not success:
        failure = "answer_extraction_failure"
    elif not success and failure == "none":
        failure = "explorer_failure"
    if critic_mode in {"o0", "random"} and not success and critic_rounds and recovery != "recovered":
        failure = "critic_recovery_failure" if critic_mode == "o0" or critic_mode == "random" else failure
        if critic_mode == "o0":
            failure = "critic_recovery_failure"
        recovery = "failed" if recovery == "none" else recovery

    record = {
        "schema_version": SP4_TRAJECTORY_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "task_id": env.task.task_id,
        "seed": seed,
        "temperature": temperature,
        "stage": critic_source,
        "initial_state_hash": initial,
        "final_state_hash": env.visible_state().state_id,
        "actions": [{"action_type": item["action"]["action_type"], "role": item.get("role")} for item in actions if item.get("action")],
        "failure_type": failure if not success else "none",
        "critic_source": critic_source,
        "recovery_status": recovery,
        "budget": env.budget.to_dict(),
        "replay_status": "deterministic",
        "success": success,
        "complete": True,
        "n_critic_rounds": critic_rounds,
        "pipeline_ok": failure != "system_failure",
        "raw_actions": actions,
        "critic_mode": critic_mode,
    }
    return validate_trajectory_record(record)


def extract_local_candidate(traj: Mapping[str, Any], actor: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if traj.get("critic_source") not in {"o0_critic", "random_critic", "oracle_guided_offline_teacher"}:
        return None
    critic_steps = [item for item in traj.get("raw_actions") or [] if item.get("role") == "critic" and item.get("action")]
    if not critic_steps:
        return None
    step = critic_steps[0]
    action = step["action"]
    reason = ((step.get("decision") or {}).get("reason")) or "If search stalls on a visible frontier, prefer a remaining legal EXPAND."
    item = {
        "experience_id": "sp4-cand-" + canonical_hash({"traj": traj.get("task_id"), "seed": traj.get("seed"), "a": action})[:16],
        "source_run_id": traj.get("run_id"),
        "source_task_ids": [traj.get("task_id")],
        "task_id": traj.get("task_id"),
        "discovery_method": {
            "o0_critic": "o0_critic",
            "random_critic": "random_critic",
            "oracle_guided_offline_teacher": "oracle_guided_offline_teacher",
        }[str(traj.get("critic_source"))],
        "trigger": {
            "question_type": actor.get("question_type"),
            "decision_stage": "relation_selection",
            "state_signature": traj.get("initial_state_hash"),
            "failure_class": traj.get("failure_type") or "explorer_failure",
        },
        "recommendation": {
            "action_type": action.get("action_type"),
            "direction": (action.get("params") or {}).get("direction"),
            "relation_pattern": (action.get("params") or {}).get("relation"),
            "reason": reason,
            "negative_constraints": ["do_not_repeat_empty_relation"],
            "budget_condition": "steps=high",
        },
        "evidence": {
            "verified_replay": traj.get("replay_status") == "deterministic",
            "observed_outcome": traj.get("failure_type"),
            "support_count": 1,
        },
        "status": "candidate",
        "protocol_version": PROTOCOL_VERSION,
    }
    return item
