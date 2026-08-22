"""Minimal deterministic replay environment. Fixture graphs only; no Freebase, no LLM."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .action_validator import validate_action
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash
from .paths import PROTOCOL_VERSION
from .schemas import (
    Action,
    ActionType,
    ActorRole,
    Budget,
    DecisionStage,
    Direction,
    StepOutcome,
    TaskRecord,
    TerminationReason,
    TrajectoryRecord,
    VisibleRelation,
    VisibleState,
)
from .visibility import OracleSecrets, project_actor_view, project_critic_view, project_verifier_view


def _state_id(payload: Dict[str, Any]) -> str:
    return "st-" + canonical_hash(payload)[:16]


@dataclass
class LocalGraph:
    entity_names: Dict[str, str]
    triples: List[Dict[str, str]]

    def relations_of(self, entity: str) -> List[VisibleRelation]:
        found = []
        seen = set()
        for triple in self.triples:
            if triple["head"] == entity:
                item = VisibleRelation(entity=entity, relation=triple["relation"], direction=Direction.TAIL)
            elif triple["tail"] == entity:
                item = VisibleRelation(entity=entity, relation=triple["relation"], direction=Direction.HEAD)
            else:
                continue
            if item.key() in seen:
                continue
            seen.add(item.key())
            found.append(item)
        return sorted(found, key=lambda item: item.key())

    def expand(self, entity: str, relation: str, direction: Direction) -> List[Dict[str, str]]:
        matches = []
        for triple in self.triples:
            if triple["relation"] != relation:
                continue
            if direction is Direction.TAIL and triple["head"] == entity:
                matches.append(dict(triple))
            elif direction is Direction.HEAD and triple["tail"] == entity:
                matches.append(dict(triple))
        return sorted(matches, key=lambda item: (item["head"], item["relation"], item["tail"]))

    def neighbors(self, entity: str) -> List[str]:
        out = []
        for triple in self.triples:
            if triple["head"] == entity:
                out.append(triple["tail"])
            elif triple["tail"] == entity:
                out.append(triple["head"])
        return sorted(set(out))


@dataclass
class ReplayEnvironment:
    task: TaskRecord
    graph: LocalGraph
    budget: Budget
    visible_entities: List[str]
    frontier: List[str]
    observed_triples: List[Dict[str, str]] = field(default_factory=list)
    failed_branches: List[str] = field(default_factory=list)
    history: List[str] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    terminated: bool = False
    termination_reason: Optional[TerminationReason] = None
    terminal_submission: Optional[List[str]] = None
    snapshot_id: str = "fixture-v1"

    def future_neighbors(self) -> List[str]:
        known = set(self.visible_entities)
        hidden = []
        for entity in self.graph.entity_names:
            if entity not in known:
                hidden.append(entity)
        return sorted(hidden)

    def secrets(self) -> OracleSecrets:
        return OracleSecrets.from_task(self.task, self.future_neighbors())

    def remaining_budget(self) -> Dict[str, int]:
        remaining = self.budget.remaining()
        remaining["frontier_size"] = max(0, self.budget.max_frontier_size - len(self.frontier))
        return remaining

    def visible_relations(self) -> List[VisibleRelation]:
        relations = []
        seen = set()
        for entity in self.visible_entities:
            for item in self.graph.relations_of(entity):
                if item.key() in seen:
                    continue
                seen.add(item.key())
                relations.append(item)
        return relations

    def visible_state(self) -> VisibleState:
        payload = {
            "task_id": self.task.task_id,
            "visible_entities": list(self.visible_entities),
            "visible_relations": [item.to_dict() for item in self.visible_relations()],
            "observed_triples_or_summaries": list(self.observed_triples),
            "frontier": list(self.frontier),
            "failed_or_exhausted_branches": list(self.failed_branches),
            "action_history_summary": list(self.history),
            "remaining_budget": self.remaining_budget(),
            "snapshot_id": self.snapshot_id,
        }
        state = VisibleState(
            state_id=_state_id(payload),
            task_id=self.task.task_id,
            question=self.task.question,
            visible_entities=list(self.visible_entities),
            visible_relations=self.visible_relations(),
            observed_triples_or_summaries=list(self.observed_triples),
            frontier=list(self.frontier),
            failed_or_exhausted_branches=list(self.failed_branches),
            action_history_summary=list(self.history),
            remaining_budget=self.remaining_budget(),
            decision_stage=DecisionStage.RELATION_SELECTION,
        )
        return state

    def _apply_budget(self, kg_calls: int = 0) -> Dict[str, int]:
        self.budget.used_steps += 1
        self.budget.used_kg_calls += kg_calls
        self.budget.used_frontier_size = len(self.frontier)
        self.budget.used_depth = min(
            self.budget.max_depth,
            max(self.budget.used_depth, max(0, len(self.history) // 2)),
        )
        return {
            "steps": 1,
            "kg_calls": kg_calls,
            "llm_calls": 0,
            "critic_rounds": 0,
        }

    def step(self, action: Action) -> StepOutcome:
        before = self.visible_state()
        try:
            validate_action(action, before)
        except ProtocolError as exc:
            outcome = StepOutcome(
                accepted=False,
                protocol_violation=exc.code.value,
                visible_result={"error": exc.message},
                new_frontier_items=[],
                budget_delta={},
                state_id_before=before.state_id,
                state_id_after=before.state_id,
                deterministic_result_hash="",
                oracle_eval={"rejected": True},
            )
            outcome.deterministic_result_hash = canonical_hash(outcome.actor_visible_dict())
            self.steps.append({"action": action.to_dict(), "outcome": outcome.to_dict()})
            self.terminated = True
            self.termination_reason = TerminationReason.PROTOCOL_VIOLATION
            return outcome

        new_frontier: List[str] = []
        visible_result: Dict[str, Any] = {"action_type": action.action_type.value}
        kg_calls = 0
        terminal = False
        reason = None
        submission = None

        if action.action_type is ActionType.EXPAND:
            kg_calls = 1
            entity = action.params["entity"]
            relation = action.params["relation"]
            direction = Direction(action.params["direction"])
            matches = self.graph.expand(entity, relation, direction)
            if not matches:
                self.failed_branches.append(f"{entity}|{relation}|{direction.value}")
                visible_result["triples"] = []
            else:
                for triple in matches:
                    if triple not in self.observed_triples:
                        self.observed_triples.append(triple)
                    neighbor = triple["tail"] if direction is Direction.TAIL else triple["head"]
                    if neighbor not in self.visible_entities:
                        self.visible_entities.append(neighbor)
                        self.visible_entities = sorted(set(self.visible_entities))
                    if neighbor not in self.frontier:
                        self.frontier.append(neighbor)
                        new_frontier.append(neighbor)
                self.frontier = sorted(set(self.frontier))
                visible_result["triples"] = matches
            self.budget.used_depth = min(self.budget.max_depth, self.budget.used_depth + 1)

        elif action.action_type is ActionType.SELECT_FRONTIER:
            entity = action.params["entity"]
            if entity not in self.frontier:
                self.frontier.append(entity)
            visible_result["selected"] = entity

        elif action.action_type is ActionType.BACKTRACK:
            target = action.params["entity_or_state"]
            visible_result["backtrack_to"] = target
            if target in self.visible_entities and target not in self.frontier:
                self.frontier.append(target)

        elif action.action_type is ActionType.CONTINUE:
            visible_result["continued"] = True

        elif action.action_type is ActionType.STOP:
            submission = list(action.params["answer_candidates"])
            terminal = True
            reason = TerminationReason.STOP_SUBMITTED

        elif action.action_type is ActionType.ABSTAIN:
            terminal = True
            reason = TerminationReason.ABSTAINED
            visible_result["reason_code"] = action.params["reason_code"]

        budget_delta = self._apply_budget(kg_calls=kg_calls)
        self.history.append(f"state:{before.state_id}")
        self.history.append(action.action_id)
        if terminal:
            self.terminated = True
            self.termination_reason = reason
            self.terminal_submission = submission

        after = self.visible_state()
        oracle_eval = {}
        if terminal and submission is not None:
            gold = set(self.task.answer_entity_ids) | set(self.task.normalized_answers)
            oracle_eval = {
                "submitted": submission,
                "correct": any(item in gold for item in submission),
            }
        outcome = StepOutcome(
            accepted=True,
            protocol_violation=None,
            visible_result=visible_result,
            new_frontier_items=sorted(set(new_frontier)),
            budget_delta=budget_delta,
            state_id_before=before.state_id,
            state_id_after=after.state_id,
            deterministic_result_hash="",
            oracle_eval=oracle_eval,
        )
        outcome.deterministic_result_hash = canonical_hash(outcome.actor_visible_dict())
        self.steps.append({"action": action.to_dict(), "outcome": outcome.to_dict()})
        return outcome

    def trajectory(self, trajectory_id: str) -> TrajectoryRecord:
        initial = {
            "task_id": self.task.task_id,
            "snapshot_id": self.snapshot_id,
            "start_entities": list(self.task.source_entities),
        }
        reason = self.termination_reason or TerminationReason.FAILURE
        record = TrajectoryRecord(
            trajectory_id=trajectory_id,
            task_id=self.task.task_id,
            protocol_version=PROTOCOL_VERSION,
            initial_state_hash=canonical_hash(initial),
            ordered_steps=list(self.steps),
            terminal_submission=self.terminal_submission,
            termination_reason=reason,
            cost_summary={
                "steps": self.budget.used_steps,
                "kg_calls": self.budget.used_kg_calls,
                "llm_calls": self.budget.used_llm_calls,
                "critic_rounds": self.budget.used_critic_rounds,
            },
            replay_hash="",
        )
        record.replay_hash = canonical_hash(record.to_dict())
        return record


def make_action(
    action_id: str,
    action_type: ActionType,
    params: Dict[str, Any],
    state_id: str,
    role: ActorRole = ActorRole.EXPLORER,
) -> Action:
    return Action(
        action_id=action_id,
        action_type=action_type,
        params=params,
        source_role=role,
        state_id=state_id,
    )


def default_fixture_task() -> TaskRecord:
    return TaskRecord(
        task_id="fixture.birthplace.001",
        question="Where was Bob born?",
        source_entities=["e.alice"],
        source_entity_names={"e.alice": "Alice"},
        task_split="protocol_fixture",
        task_generator_version="sp0-fixture-v1",
        input_snapshot_id="fixture-graph-v1",
        logical_query="SELECT ?x WHERE { e.bob people.person.place_of_birth ?x }",
        answer_entity_ids=["e.paris"],
        normalized_answers=["Paris"],
        witness_paths=[["e.alice", "people.person.friend", "e.bob", "people.person.place_of_birth", "e.paris"]],
        task_validity="valid",
        oracle_version="oracle-fixture-v1",
    )


def default_fixture_graph() -> LocalGraph:
    return LocalGraph(
        entity_names={
            "e.alice": "Alice",
            "e.bob": "Bob",
            "e.paris": "Paris",
            "e.hidden": "HiddenCity",
        },
        triples=[
            {"head": "e.alice", "relation": "people.person.friend", "tail": "e.bob"},
            {"head": "e.bob", "relation": "people.person.place_of_birth", "tail": "e.paris"},
            {"head": "e.hidden", "relation": "location.location.containedby", "tail": "e.paris"},
        ],
    )


def make_env(budget: Optional[Budget] = None, snapshot_id: str = "fixture-v1") -> ReplayEnvironment:
    task = default_fixture_task()
    graph = default_fixture_graph()
    if budget is None:
        budget = Budget(
            max_depth=4,
            max_steps=12,
            max_kg_calls=16,
            max_llm_calls=8,
            max_critic_rounds=2,
            max_frontier_size=80,
        )
    return ReplayEnvironment(
        task=task,
        graph=graph,
        budget=budget,
        visible_entities=["e.alice"],
        frontier=["e.alice"],
        snapshot_id=snapshot_id,
    )


def success_actions(env: ReplayEnvironment) -> List[Action]:
    state = env.visible_state()
    first = make_action(
        "a1",
        ActionType.EXPAND,
        {"entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
        state.state_id,
    )
    return [first]


def run_scripted_trajectory(
    actions_builder,
    *,
    budget: Optional[Budget] = None,
    snapshot_id: str = "fixture-v1",
) -> Tuple[ReplayEnvironment, TrajectoryRecord]:
    env = make_env(budget=copy.deepcopy(budget) if budget else None, snapshot_id=snapshot_id)
    # actions depend on evolving state IDs, so builder receives env and returns next action or None.
    step_no = 0
    while True:
        action = actions_builder(env, step_no)
        if action is None:
            break
        env.step(action)
        step_no += 1
        if env.terminated:
            break
        if env.budget.remaining()["steps"] <= 0:
            env.terminated = True
            env.termination_reason = TerminationReason.BUDGET_EXHAUSTED
            break
    return env, env.trajectory("traj-" + env.task.task_id)


def success_builder(env: ReplayEnvironment, step_no: int) -> Optional[Action]:
    state = env.visible_state()
    if step_no == 0:
        return make_action(
            "a1",
            ActionType.EXPAND,
            {"entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
            state.state_id,
        )
    if step_no == 1:
        return make_action("a2", ActionType.SELECT_FRONTIER, {"entity": "e.bob"}, state.state_id)
    if step_no == 2:
        return make_action(
            "a3",
            ActionType.EXPAND,
            {"entity": "e.bob", "relation": "people.person.place_of_birth", "direction": "tail"},
            state.state_id,
        )
    if step_no == 3:
        return make_action("a4", ActionType.STOP, {"answer_candidates": ["e.paris"]}, state.state_id)
    return None


def failure_builder(env: ReplayEnvironment, step_no: int) -> Optional[Action]:
    state = env.visible_state()
    if step_no == 0:
        return make_action(
            "f1",
            ActionType.EXPAND,
            {"entity": "e.alice", "relation": "people.person.friend", "direction": "tail"},
            state.state_id,
        )
    if step_no == 1:
        return make_action(
            "f2",
            ActionType.ABSTAIN,
            {"reason_code": "INSUFFICIENT_EVIDENCE"},
            state.state_id,
        )
    return None


def illegal_relation_builder(env: ReplayEnvironment, step_no: int) -> Optional[Action]:
    if step_no > 0:
        return None
    state = env.visible_state()
    return make_action(
        "bad-rel",
        ActionType.EXPAND,
        {"entity": "e.alice", "relation": "not.a.visible.relation", "direction": "tail"},
        state.state_id,
    )


def illegal_backtrack_builder(env: ReplayEnvironment, step_no: int) -> Optional[Action]:
    if step_no > 0:
        return None
    state = env.visible_state()
    return make_action(
        "bad-bt",
        ActionType.BACKTRACK,
        {"entity_or_state": "e.hidden"},
        state.state_id,
    )


def replay_times(builder, n: int = 3, **kwargs) -> List[TrajectoryRecord]:
    records = []
    hashes = []
    for _ in range(n):
        env, traj = run_scripted_trajectory(builder, **kwargs)
        records.append(traj)
        hashes.append(traj.replay_hash)
    if len(set(hashes)) != 1:
        raise ProtocolError(
            ViolationCode.REPLAY_ERROR,
            "replay hashes diverged for identical inputs",
            {"hashes": hashes},
        )
    return records
