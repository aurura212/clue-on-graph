"""Checkpointed explorer/critic rollouts on a frozen snapshot. Optional LLM critic."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .action_protocol import ProtocolSession, parse_action
from .critic import legal_expand_actions, legal_recovery_actions
from .critic_context import build_compressed_critic_input, classify_critic_error, schema_fallback_decision
from .hashing import canonical_hash, sha256_text
from .paths import PROTOCOL_VERSION, Workspace
from .replay import ReplayEnvironment
from .schemas import Action, ActorRole, FailureClass
from .visibility import project_actor_view

PROMPT_RELPATH = "prompts/sp4s_critic_o0_v1.txt"


def load_critic_prompt(workspace: Workspace) -> str:
    path = workspace.self_play_root / PROMPT_RELPATH
    if path.exists():
        return path.read_text(encoding="utf-8")
    alt = Path(__file__).resolve().parents[2] / PROMPT_RELPATH
    return alt.read_text(encoding="utf-8") if alt.exists() else "CRITIC_INPUT:\n{{CRITIC_INPUT}}\n"


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
        if key not in failed:
            return action
    return None


def stubborn_expand(env: ReplayEnvironment) -> Optional[Action]:
    """Prefer a non-first legal expand so the critic has a stalled frontier to repair."""
    state = env.visible_state()
    failed = set(env.failed_branches)
    actions = [
        action
        for action in legal_expand_actions(state, source_role=ActorRole.EXPLORER)
        if f"{action.params['entity']}|{action.params['relation']}|{action.params['direction']}" not in failed
    ]
    if not actions:
        return greedy_expand(env)
    if len(actions) >= 2:
        return actions[-1]
    return actions[0]


def seeded_expand(env: ReplayEnvironment, rng: random.Random) -> Optional[Action]:
    state = env.visible_state()
    actions = list(legal_expand_actions(state, source_role=ActorRole.EXPLORER))
    rng.shuffle(actions)
    failed = set(env.failed_branches)
    for action in actions:
        key = f"{action.params['entity']}|{action.params['relation']}|{action.params['direction']}"
        if key not in failed:
            return action
    return actions[0] if actions else None


def teacher_expand(env: ReplayEnvironment, hops: Sequence[Mapping[str, str]]) -> Optional[Action]:
    state = env.visible_state()
    observed = {triple["relation"] for triple in env.observed_triples}
    for hop in hops:
        for action in legal_expand_actions(state, source_role=ActorRole.EXPLORER):
            if action.params["relation"] == hop.get("relation") and action.params["direction"] == hop.get("direction") and hop.get("relation") not in observed:
                return action
    return greedy_expand(env)


def heuristic_critic(state, event: str) -> Dict[str, Any]:
    expands = legal_expand_actions(state, source_role=ActorRole.CRITIC)
    chosen = expands[0] if expands else None
    if chosen is None:
        payload = schema_fallback_decision(event)
        payload["action"] = parse_action({"action_type": "ABSTAIN", "reason_code": "INSUFFICIENT_EVIDENCE"}, state, source_role=ActorRole.CRITIC).to_dict()
        payload["mode"] = "o0_heuristic"
        return payload
    return {
        "failure_class": FailureClass.EXPLORER_FAILURE.value,
        "decision_stage": "relation_selection",
        "reason": "If the visible frontier stalled, expand a remaining legal relation.",
        "accepted": True,
        "action": chosen.to_dict(),
        "event": event,
        "mode": "o0_heuristic",
    }


def trigger_event(env: ReplayEnvironment, last: Mapping[str, Any], seen_states: List[str]) -> Optional[str]:
    remaining = env.visible_state().remaining_budget
    vis = last.get("visible_result") or {}
    if vis.get("action_type") == "STOP" and not (_gold(env) & set(env.terminal_submission or [])):
        return "stop_failure"
    if last.get("accepted") and vis.get("action_type") == "EXPAND" and not vis.get("triples"):
        return "no_new_frontier"
    if seen_states.count(env.visible_state().state_id) >= 2:
        return "repeat_state"
    if int(remaining.get("steps") or 0) <= 2:
        return "budget_critical"
    if len(env.visible_state().visible_relations) >= 2 and (not _should_stop(env)):
        return "stalled_without_answer"
    if len(env.visible_state().visible_relations) >= 8 and len(env.observed_triples) <= 1:
        return "branching_surge"
    return None


def parse_critic_json(text: str) -> Dict[str, Any]:
    blob = text.strip()
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("critic output is not JSON")
    payload = json.loads(blob[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("critic JSON is not an object")
    return payload


def llm_critic_decision(
    env: ReplayEnvironment,
    *,
    event: str,
    client: Any,
    prompt_template: str,
    actor_view: Mapping[str, Any],
) -> Dict[str, Any]:
    state = env.visible_state()
    legal = [item.to_dict() for item in legal_recovery_actions(state)]
    compressed = build_compressed_critic_input(
        event=event,
        task_public=actor_view.get("task") or {"task_id": env.task.task_id, "question": env.task.question},
        state=state,
        legal_actions=legal,
        secrets=env.secrets() if hasattr(env, "secrets") else None,
    )
    prompt = prompt_template.replace("{{CRITIC_INPUT}}", json.dumps(compressed, ensure_ascii=False))
    try:
        raw = client.complete(prompt, temperature=0.2, purpose="sp4s_o0_critic")
        payload = parse_critic_json(str(raw.get("text") or ""))
        action = parse_action(
            {
                "action_type": payload.get("action_type") or "ABSTAIN",
                "entity": payload.get("entity") or None,
                "relation": payload.get("relation") or None,
                "direction": payload.get("direction") or "tail",
                "reason_code": payload.get("reason_code") or "INSUFFICIENT_EVIDENCE",
            },
            state,
            source_role=ActorRole.CRITIC,
        )
        return {
            "mode": "o0_llm",
            "accepted": True,
            "action": action.to_dict(),
            "reason": str(payload.get("reason") or "llm_critic"),
            "failure_class": payload.get("failure_class") or FailureClass.EXPLORER_FAILURE.value,
            "compressed": compressed,
            "prompt_hash": sha256_text(prompt),
            "llm_real_call": bool(raw.get("real_call")),
        }
    except Exception as exc:
        classified = classify_critic_error(exc, prompt_chars=int(compressed.get("prompt_chars") or 0))
        fallback = schema_fallback_decision(event)
        fallback["compressed"] = compressed
        fallback["error"] = classified
        fallback["mode"] = "o0_llm_fallback"
        fallback["action"] = parse_action(
            {"action_type": "ABSTAIN", "reason_code": "INSUFFICIENT_EVIDENCE"},
            state,
            source_role=ActorRole.CRITIC,
        ).to_dict()
        return fallback


def run_checkpoint_trajectory(
    env: ReplayEnvironment,
    *,
    run_id: str,
    seed: int,
    temperature: float,
    critic_mode: str,
    teacher_hops: Optional[Sequence[Mapping[str, str]]] = None,
    max_critic_rounds: int = 2,
    llm_client: Any = None,
    prompt_template: str = "",
) -> Dict[str, Any]:
    rng = random.Random(seed)
    session = ProtocolSession(env)
    initial = env.visible_state().state_id
    actions: List[Dict[str, Any]] = []
    checkpoints: List[Dict[str, Any]] = []
    seen = [initial]
    critic_source = {
        "none": "explorer_only",
        "o0": "o0_critic",
        "o0_llm": "o0_llm_critic",
        "random": "random_critic",
        "teacher": "oracle_guided_offline_teacher",
    }[critic_mode]
    recovery = "none"
    failure = "none"
    critic_rounds = 0
    secrets = env.secrets() if hasattr(env, "secrets") else None
    try:
        actor_view = project_actor_view(env.task, env.visible_state(), secrets=secrets)
    except TypeError:
        actor_view = {"task": {"task_id": env.task.task_id, "question": env.task.question}}

    while (not env.terminated) and int(env.visible_state().remaining_budget.get("steps") or 0) > 0:
        state = env.visible_state()
        prefix = [item["action"] for item in actions if item.get("role") == "explorer" and item.get("action")]
        if _should_stop(env):
            observed = set(env.visible_entities)
            for triple in env.observed_triples:
                observed.update(triple.values())
            answers = [item for item in observed if item in _gold(env)]
            action = parse_action({"action_type": "STOP", "answer_candidates": answers[:3]}, state, source_role=ActorRole.EXPLORER)
        elif critic_mode == "teacher":
            action = teacher_expand(env, teacher_hops or []) or parse_action({"action_type": "ABSTAIN", "reason_code": "NO_LEGAL_ACTION"}, state)
        elif critic_mode == "none":
            action = greedy_expand(env) or parse_action({"action_type": "ABSTAIN", "reason_code": "NO_LEGAL_ACTION"}, state)
        else:
            action = stubborn_expand(env) or seeded_expand(env, rng) or parse_action({"action_type": "ABSTAIN", "reason_code": "NO_LEGAL_ACTION"}, state)
        result = session.execute(action)
        actions.append({"action": action.to_dict(), "result": result, "role": "explorer"})
        seen.append(env.visible_state().state_id)
        event = trigger_event(env, result, seen)
        if critic_mode in {"o0", "o0_llm", "random"} and event and critic_rounds < max_critic_rounds and not env.terminated:
            critic_rounds += 1
            post = env.visible_state()
            post_prefix = prefix + [action.to_dict()]
            if critic_mode == "random":
                legal = legal_recovery_actions(post)
                picked = rng.choice(legal) if legal else None
                decision = {
                    "mode": "random",
                    "accepted": picked is not None,
                    "action": None if picked is None else picked.to_dict(),
                    "reason": "random_legal_action_control",
                    "failure_class": FailureClass.EXPLORER_FAILURE.value,
                }
            elif critic_mode == "o0_llm" and llm_client is not None:
                decision = llm_critic_decision(
                    env,
                    event=event,
                    client=llm_client,
                    prompt_template=prompt_template,
                    actor_view=actor_view,
                )
            else:
                decision = heuristic_critic(post, event)
            if decision.get("action"):
                payload = dict(decision["action"])
                if isinstance(payload.get("params"), dict):
                    payload = {**payload, **payload["params"]}
                c_action = parse_action(payload, env.visible_state(), source_role=ActorRole.CRITIC)
                c_result = session.execute(c_action)
                actions.append({"action": c_action.to_dict(), "result": c_result, "role": "critic", "decision": decision, "event": event})
                checkpoints.append(
                    {
                        "decision_state_hash": post.state_id,
                        "replay_prefix": post_prefix,
                        "explorer_action": action.to_dict(),
                        "critic_action": c_action.to_dict(),
                        "event": event,
                        "remaining_budget": dict(post.remaining_budget),
                        "visible_relations": [item.to_dict() for item in post.visible_relations],
                    }
                )
                if c_result.get("accepted") and _should_stop(env):
                    recovery = "recovered"

    submitted = env.terminal_submission or []
    gold = _gold(env)
    success = bool(submitted and set(submitted) & gold)
    if int(env.visible_state().remaining_budget.get("steps") or 0) <= 0 and not success:
        failure = "budget_insufficient"
    elif submitted and not success:
        failure = "answer_extraction_failure"
    elif not success:
        failure = "explorer_failure"
    return {
        "schema_version": "sp4s-trajectory-v1",
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "task_id": env.task.task_id,
        "seed": seed,
        "temperature": temperature,
        "critic_source": critic_source,
        "initial_state_hash": initial,
        "final_state_hash": env.visible_state().state_id,
        "success": success,
        "complete": True,
        "failure_type": failure if not success else "none",
        "recovery_status": recovery,
        "n_critic_rounds": critic_rounds,
        "raw_actions": actions,
        "checkpoints": checkpoints,
        "critic_mode": critic_mode,
        "replay_status": "deterministic",
    }


def extract_checkpoint_candidate(traj: Mapping[str, Any], actor: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if traj.get("critic_source") not in {"o0_critic", "o0_llm_critic", "random_critic", "oracle_guided_offline_teacher"}:
        return None
    points = list(traj.get("checkpoints") or [])
    if not points:
        critic_steps = [item for item in traj.get("raw_actions") or [] if item.get("role") == "critic" and item.get("action")]
        if not critic_steps:
            return None
        action = critic_steps[0]["action"]
        point = {"decision_state_hash": traj.get("initial_state_hash"), "replay_prefix": [], "critic_action": action}
    else:
        point = points[0]
        action = point.get("critic_action") or {}
    params = action.get("params") or {}
    method = {
        "o0_critic": "o0_critic",
        "o0_llm_critic": "o0_llm_critic",
        "random_critic": "random_critic",
        "oracle_guided_offline_teacher": "oracle_guided_offline_teacher",
    }[str(traj.get("critic_source"))]
    return {
        "experience_id": "sp4s-cand-" + canonical_hash({"traj": traj.get("task_id"), "seed": traj.get("seed"), "a": action})[:16],
        "source_run_id": traj.get("run_id"),
        "source_task_ids": [traj.get("task_id")],
        "task_id": traj.get("task_id"),
        "discovery_method": method,
        "trigger": {
            "question_type": actor.get("question_type") or actor.get("difficulty"),
            "decision_stage": "relation_selection",
            "state_signature": point.get("decision_state_hash"),
            "failure_class": traj.get("failure_type") or "explorer_failure",
        },
        "recommendation": {
            "action_type": action.get("action_type"),
            "direction": params.get("direction"),
            "relation_pattern": params.get("relation"),
            "reason": "If a visible frontier stalls, expand a remaining legal relation instead of repeating an empty branch.",
            "negative_constraints": ["do_not_repeat_empty_relation"],
            "budget_condition": "steps=high",
        },
        "replay_prefix": list(point.get("replay_prefix") or []),
        "decision_state_hash": point.get("decision_state_hash"),
        "evidence": {
            "verified_replay": traj.get("replay_status") == "deterministic",
            "observed_outcome": traj.get("failure_type"),
            "support_count": 1,
        },
        "status": "candidate",
        "protocol_version": PROTOCOL_VERSION,
    }
