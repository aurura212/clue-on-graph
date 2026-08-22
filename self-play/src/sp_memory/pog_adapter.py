"""PoG snapshot projection, canonicalization, action application, and recovery mapping."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .action_validator import validate_action
from .environment_binding import (
    CANONICALIZATION_VERSION,
    EnvironmentBinding,
    EnvironmentResult,
    EnvironmentStatus,
    FINISH_MARKERS,
    is_finish_marker,
    pog_head_to_direction,
    sort_triples,
)
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
    FailureClass,
    StepOutcome,
    VisibleRelation,
    VisibleState,
)

REQUIRED_SNAPSHOT_FIELDS = (
    "task_id",
    "question",
    "source_entities",
    "topic_entity",
    "ent_rel_ent_dict",
    "depth_ent_rel_ent_dict",
    "cluster_chain_of_entities",
    "frontier",
    "failed_or_exhausted_branches",
    "action_history_summary",
    "budget",
    "decision_stage",
)

BACKTRACK_STATE_PREFIX = "state:"


def _unicode_sort(values: Iterable[str]) -> List[str]:
    unique = {item for item in values if isinstance(item, str) and item and not is_finish_marker(item)}
    return sorted(unique)


def _require_mapping(name: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"{name} must be an object", {"field": name})
    return value


def _require_list(name: str, value: Any) -> List[Any]:
    if not isinstance(value, list):
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"{name} must be a list", {"field": name})
    return value


@dataclass
class PoGSnapshot:
    """Explicit PoG search snapshot. Never completed from globals or hidden files."""

    task_id: str
    question: str
    source_entities: List[str]
    topic_entity: Dict[str, str]
    ent_rel_ent_dict: Dict[str, Any]
    depth_ent_rel_ent_dict: Dict[str, Any]
    cluster_chain_of_entities: List[Any]
    frontier: List[str]
    failed_or_exhausted_branches: List[str]
    action_history_summary: List[str]
    budget: Budget
    decision_stage: DecisionStage
    enumerated_relations: List[VisibleRelation] = field(default_factory=list)
    observed_triples: List[Dict[str, str]] = field(default_factory=list)
    entid_name: Dict[str, str] = field(default_factory=dict)
    name_entid: Dict[str, str] = field(default_factory=dict)
    selected_entity: Optional[str] = None

    def clone(self) -> "PoGSnapshot":
        return copy.deepcopy(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PoGSnapshot":
        if not isinstance(payload, Mapping):
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "PoGSnapshot payload must be an object")
        missing = [name for name in REQUIRED_SNAPSHOT_FIELDS if name not in payload]
        if missing:
            raise ProtocolError(
                ViolationCode.SCHEMA_ERROR,
                f"PoGSnapshot missing required fields: {missing}",
                {"missing_fields": missing},
            )
        leak = {"answer_entity_ids", "normalized_answers", "witness_paths", "logical_query", "gold_path"} & set(payload)
        if leak:
            raise ProtocolError(
                ViolationCode.ORACLE_LEAKAGE,
                f"PoGSnapshot contains Oracle fields {sorted(leak)}",
            )
        source_entities = _require_list("source_entities", payload["source_entities"])
        if any(not isinstance(item, str) for item in source_entities):
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "source_entities must be list[str]")
        topic = dict(_require_mapping("topic_entity", payload["topic_entity"]))
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in topic.items()):
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "topic_entity must be str->str")
        budget_raw = payload["budget"]
        if isinstance(budget_raw, Budget):
            budget = copy.deepcopy(budget_raw)
        elif isinstance(budget_raw, Mapping):
            budget = Budget.from_dict(budget_raw)
        else:
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "budget must be an object")
        stage = payload["decision_stage"]
        if isinstance(stage, DecisionStage):
            decision_stage = stage
        else:
            from .schemas import parse_enum

            decision_stage = parse_enum(DecisionStage, stage, "decision_stage")  # type: ignore[arg-type]
        relations = []
        for item in payload.get("enumerated_relations") or []:
            if isinstance(item, VisibleRelation):
                relations.append(item)
            else:
                relations.append(VisibleRelation.from_dict(item))
        triples = []
        for item in payload.get("observed_triples") or []:
            if not isinstance(item, Mapping) or not {"subject", "relation", "object"} <= set(item):
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "observed_triples must use subject/relation/object")
            triples.append(
                {"subject": str(item["subject"]), "relation": str(item["relation"]), "object": str(item["object"])}
            )
        entid_name = dict(payload.get("entid_name") or topic)
        name_entid = dict(payload.get("name_entid") or {v: k for k, v in entid_name.items()})
        return cls(
            task_id=str(payload["task_id"]),
            question=str(payload["question"]),
            source_entities=list(source_entities),
            topic_entity=topic,
            ent_rel_ent_dict=copy.deepcopy(dict(_require_mapping("ent_rel_ent_dict", payload["ent_rel_ent_dict"]))),
            depth_ent_rel_ent_dict=copy.deepcopy(
                dict(_require_mapping("depth_ent_rel_ent_dict", payload["depth_ent_rel_ent_dict"]))
            ),
            cluster_chain_of_entities=copy.deepcopy(
                _require_list("cluster_chain_of_entities", payload["cluster_chain_of_entities"])
            ),
            frontier=list(_require_list("frontier", payload["frontier"])),
            failed_or_exhausted_branches=list(
                _require_list("failed_or_exhausted_branches", payload["failed_or_exhausted_branches"])
            ),
            action_history_summary=list(_require_list("action_history_summary", payload["action_history_summary"])),
            budget=budget,
            decision_stage=decision_stage,  # type: ignore[arg-type]
            enumerated_relations=relations,
            observed_triples=triples,
            entid_name=entid_name,
            name_entid=name_entid,
            selected_entity=payload.get("selected_entity"),
        )


def triples_from_ent_rel_ent_dict(ent_rel_ent_dict: Mapping[str, Any]) -> List[Dict[str, str]]:
    triples: List[Dict[str, str]] = []
    for entity, ht_dict in ent_rel_ent_dict.items():
        if is_finish_marker(str(entity)) or not isinstance(ht_dict, Mapping):
            continue
        for ht, rel_dict in ht_dict.items():
            if not isinstance(rel_dict, Mapping):
                continue
            for relation, targets in rel_dict.items():
                if not isinstance(targets, list):
                    continue
                for target in targets:
                    if not isinstance(target, str) or is_finish_marker(target):
                        continue
                    if ht == "head":
                        triples.append({"subject": str(entity), "relation": str(relation), "object": target})
                    elif ht == "tail":
                        triples.append({"subject": target, "relation": str(relation), "object": str(entity)})
    return sort_triples(triples)


def remaining_budget_from(budget: Budget, frontier: Sequence[str]) -> Dict[str, int]:
    remaining = budget.remaining()
    remaining["frontier_size"] = max(0, budget.max_frontier_size - len(_unicode_sort(frontier)))
    remaining["llm_calls"] = budget.max_llm_calls - budget.used_llm_calls
    remaining["critic_rounds"] = budget.max_critic_rounds - budget.used_critic_rounds
    return remaining


def canonical_state_payload(snapshot: PoGSnapshot, visible: Mapping[str, Any]) -> Dict[str, Any]:
    payload = {
        "canonicalization_version": CANONICALIZATION_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "task_id": visible["task_id"],
        "question": visible["question"],
        "visible_entities": list(visible["visible_entities"]),
        "visible_relations": list(visible["visible_relations"]),
        "observed_triples_or_summaries": list(visible["observed_triples_or_summaries"]),
        "frontier": list(visible["frontier"]),
        "failed_or_exhausted_branches": list(visible["failed_or_exhausted_branches"]),
        "action_history_summary": list(visible["action_history_summary"]),
        "remaining_budget": dict(visible["remaining_budget"]),
        "decision_stage": visible["decision_stage"],
    }
    return payload


def compute_state_id(payload: Mapping[str, Any]) -> str:
    if "state_id" in payload:
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, "state_id must not be hashed into itself")
    return canonical_hash(payload)


class PoGAdapter:
    def __init__(
        self,
        *,
        adapter_enabled: bool = False,
        allow_llm: bool = False,
        allow_live_kg: bool = False,
        environment: Optional[EnvironmentBinding] = None,
        backtrack_state_policy: str = "unsupported",
        stage: str = "sp1",
    ) -> None:
        if stage not in {"sp1", "sp2a", "sp2b"}:
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"unsupported adapter stage {stage}")
        if stage in {"sp1", "sp2a"} and allow_llm:
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"{stage.upper()} adapter forbids allow_llm=true")
        env_is_live = bool(getattr(environment, "allow_live_kg", False)) if environment is not None else False
        if stage == "sp1":
            if allow_live_kg or env_is_live:
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "SP1 adapter forbids allow_live_kg=true")
            self.allow_live_kg = False
            self.environment = environment or EnvironmentBinding(allow_live_kg=False)
            self.allow_llm = False
        elif stage == "sp2a":
            if not allow_live_kg:
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "SP2-A adapter requires allow_live_kg=true")
            if environment is None:
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "SP2-A adapter requires an explicit live environment")
            self.allow_live_kg = True
            self.environment = environment
            self.allow_llm = False
        else:
            if not allow_live_kg:
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "SP2-B adapter requires allow_live_kg=true")
            if environment is None:
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "SP2-B adapter requires an explicit live environment")
            self.allow_live_kg = True
            self.environment = environment
            self.allow_llm = bool(allow_llm)
        self.stage = stage
        self.adapter_enabled = adapter_enabled
        self.backtrack_state_policy = backtrack_state_policy
        self.llm_calls_observed = 0

    def project_visible_state(self, snapshot: PoGSnapshot | Mapping[str, Any]) -> VisibleState:
        snap = snapshot if isinstance(snapshot, PoGSnapshot) else PoGSnapshot.from_dict(snapshot)
        if not snap.task_id or not snap.question:
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "snapshot task_id and question are required")
        observed = sort_triples(list(snap.observed_triples) or triples_from_ent_rel_ent_dict(snap.ent_rel_ent_dict))
        visible_entities = _unicode_sort(
            list(snap.source_entities)
            + list(snap.topic_entity.keys())
            + list(snap.frontier)
            + [item["subject"] for item in observed]
            + [item["object"] for item in observed]
        )
        frontier = _unicode_sort(snap.frontier)
        failed = _unicode_sort(snap.failed_or_exhausted_branches)
        relations = []
        seen_rel = set()
        for item in snap.enumerated_relations:
            if item.key() in seen_rel:
                continue
            if is_finish_marker(item.entity):
                continue
            seen_rel.add(item.key())
            relations.append(item)
        relations.sort(key=lambda item: (item.entity, item.relation, item.direction.value))
        snap.budget.used_frontier_size = len(frontier)
        if self.stage != "sp2b":
            snap.budget.used_llm_calls = 0
        snap.budget.used_critic_rounds = 0
        remaining = remaining_budget_from(snap.budget, frontier)
        visible = {
            "task_id": snap.task_id,
            "question": snap.question,
            "visible_entities": visible_entities,
            "visible_relations": [item.to_dict() for item in relations],
            "observed_triples_or_summaries": observed,
            "frontier": frontier,
            "failed_or_exhausted_branches": failed,
            "action_history_summary": list(snap.action_history_summary),
            "remaining_budget": remaining,
            "decision_stage": snap.decision_stage.value,
        }
        payload = canonical_state_payload(snap, visible)
        state_id = compute_state_id(payload)
        return VisibleState(
            state_id=state_id,
            task_id=snap.task_id,
            question=snap.question,
            visible_entities=visible_entities,
            visible_relations=relations,
            observed_triples_or_summaries=observed,
            frontier=frontier,
            failed_or_exhausted_branches=failed,
            action_history_summary=list(snap.action_history_summary),
            remaining_budget=remaining,
            decision_stage=snap.decision_stage,
        )

    def passthrough(self, fn, *args, **kwargs):
        """adapter_enabled=false: call the original function unchanged."""
        if self.adapter_enabled:
            raise ProtocolError(
                ViolationCode.SCHEMA_ERROR,
                "passthrough is only valid when adapter_enabled=false",
            )
        return fn(*args, **kwargs)

    def apply_relation_enumeration(
        self,
        snapshot: PoGSnapshot,
        entity: str,
        *,
        head_relations: Optional[Sequence[str]] = None,
        tail_relations: Optional[Sequence[str]] = None,
        provenance_ref: Optional[str] = None,
    ) -> Tuple[PoGSnapshot, EnvironmentResult]:
        remaining = remaining_budget_from(snapshot.budget, snapshot.frontier)
        needed = 2
        if remaining.get("kg_calls", 0) < needed:
            result = EnvironmentResult(
                status=EnvironmentStatus.SCHEMA_ERROR,
                results=[],
                kg_call_delta=0,
                failure_class=FailureClass.BUDGET_INSUFFICIENT,
                error_code=ViolationCode.BUDGET_EXCEEDED.value,
                message="relation enumeration exceeds kg_calls budget",
                provenance_ref=provenance_ref or "relation_enum",
            )
            return snapshot, result
        result = self.environment.enumerate_relations(
            entity,
            head_relations=head_relations,
            tail_relations=tail_relations,
            provenance_ref=provenance_ref,
        )
        applied = snapshot.clone()
        if result.failure_class is None:
            new_items = [VisibleRelation.from_dict(item) for item in result.results]
            seen = {item.key() for item in applied.enumerated_relations}
            for item in new_items:
                if item.key() not in seen:
                    applied.enumerated_relations.append(item)
                    seen.add(item.key())
        applied.budget.used_kg_calls += result.kg_call_delta
        if self.stage != "sp2b":
            applied.budget.used_llm_calls = 0
        applied.budget.used_critic_rounds = 0
        return applied, result

    def map_recovery_to_select_frontier(self, entity: str, state: VisibleState) -> Action:
        visible = set(state.visible_entities) | set(state.frontier)
        if entity not in visible or is_finish_marker(entity):
            raise ProtocolError(
                ViolationCode.INVISIBLE_ENTITY,
                f"recovery entity is not historically visible: {entity}",
            )
        return Action(
            action_id="recovery-" + canonical_hash({"entity": entity, "state_id": state.state_id})[:12],
            action_type=ActionType.SELECT_FRONTIER,
            params={"entity": entity},
            source_role=ActorRole.EXPLORER,
            state_id=state.state_id,
        )

    def apply_action(
        self,
        snapshot: PoGSnapshot,
        action: Action,
        *,
        expand_recorded: Optional[Sequence[Any]] = None,
    ) -> Tuple[PoGSnapshot, StepOutcome, Optional[EnvironmentResult]]:
        before_state = self.project_visible_state(snapshot)
        before_budget = copy.deepcopy(snapshot.budget)

        if action.action_type is ActionType.BACKTRACK:
            return self._reject_unsupported_backtrack(snapshot, action, before_state, before_budget)

        try:
            validate_action(action, before_state)
        except ProtocolError as exc:
            outcome = self._rejected_outcome(before_state, exc)
            return snapshot, outcome, None

        env_result: Optional[EnvironmentResult] = None
        new_frontier: List[str] = []
        visible_result: Dict[str, Any] = {"action_type": action.action_type.value}
        applied = snapshot.clone()

        if action.action_type is ActionType.EXPAND:
            if before_state.remaining_budget.get("depth", 0) <= 0:
                exc = ProtocolError(
                    ViolationCode.BUDGET_EXCEEDED,
                    "EXPAND would exceed depth budget",
                    {"budget": "depth", "remaining": before_state.remaining_budget.get("depth", 0)},
                )
                return snapshot, self._rejected_outcome(before_state, exc), None
            env_result = self.environment.expand(
                action.params["entity"],
                action.params["relation"],
                action.params["direction"],
                recorded=expand_recorded,
            )
            if env_result.failure_class is not None:
                visible_result["environment"] = env_result.to_dict()
                if env_result.kg_call_delta:
                    applied.budget.used_kg_calls += env_result.kg_call_delta
                after_state = self.project_visible_state(applied)
                outcome = StepOutcome(
                    accepted=False,
                    protocol_violation=env_result.error_code,
                    visible_result=visible_result,
                    new_frontier_items=[],
                    budget_delta=self._budget_delta(before_budget, applied.budget),
                    state_id_before=before_state.state_id,
                    state_id_after=after_state.state_id,
                    deterministic_result_hash="",
                )
                outcome.deterministic_result_hash = canonical_hash(outcome.actor_visible_dict())
                return applied, outcome, env_result
            new_frontier = self._apply_expand(applied, action, env_result)
            visible_result["triples"] = env_result.results
            visible_result["environment_status"] = env_result.status.value

        elif action.action_type is ActionType.SELECT_FRONTIER:
            entity = action.params["entity"]
            if entity not in applied.frontier:
                applied.frontier.append(entity)
            applied.selected_entity = entity
            visible_result["selected"] = entity

        elif action.action_type is ActionType.CONTINUE:
            applied.decision_stage = DecisionStage.RELATION_SELECTION
            visible_result["continued"] = True

        elif action.action_type is ActionType.STOP:
            visible_result["answer_candidates"] = list(action.params.get("answer_candidates") or [])
            applied.decision_stage = DecisionStage.ANSWER_SUBMISSION

        elif action.action_type is ActionType.ABSTAIN:
            visible_result["reason_code"] = action.params.get("reason_code")
            applied.decision_stage = DecisionStage.ANSWER_SUBMISSION

        else:
            raise ProtocolError(ViolationCode.UNKNOWN_ACTION, f"unhandled action {action.action_type}")

        applied.budget.used_steps += 1
        if env_result is not None:
            applied.budget.used_kg_calls += env_result.kg_call_delta
        if self.stage != "sp2b":
            applied.budget.used_llm_calls = 0
            applied.budget.used_critic_rounds = 0
        else:
            applied.budget.used_critic_rounds = 0
        applied.budget.used_frontier_size = len(_unicode_sort(applied.frontier))
        applied.action_history_summary = list(applied.action_history_summary) + [
            self._history_item(action)
        ]
        after_state = self.project_visible_state(applied)
        outcome = StepOutcome(
            accepted=True,
            protocol_violation=None,
            visible_result=visible_result,
            new_frontier_items=sorted(set(new_frontier)),
            budget_delta=self._budget_delta(before_budget, applied.budget),
            state_id_before=before_state.state_id,
            state_id_after=after_state.state_id,
            deterministic_result_hash="",
        )
        outcome.deterministic_result_hash = canonical_hash(outcome.actor_visible_dict())
        return applied, outcome, env_result

    def _apply_expand(self, snapshot: PoGSnapshot, action: Action, env_result: EnvironmentResult) -> List[str]:
        new_frontier: List[str] = []
        if env_result.status is EnvironmentStatus.EMPTY_SUCCESS or not env_result.results:
            branch = "|".join(
                [action.params["entity"], action.params["relation"], str(action.params["direction"])]
            )
            if branch not in snapshot.failed_or_exhausted_branches:
                snapshot.failed_or_exhausted_branches.append(branch)
            return new_frontier
        existing = {(item["subject"], item["relation"], item["object"]) for item in snapshot.observed_triples}
        for triple in env_result.results:
            key = (triple["subject"], triple["relation"], triple["object"])
            if key not in existing:
                snapshot.observed_triples.append(dict(triple))
                existing.add(key)
            for node in (triple["subject"], triple["object"]):
                if is_finish_marker(node):
                    continue
                if node not in snapshot.frontier:
                    snapshot.frontier.append(node)
                    new_frontier.append(node)
        snapshot.observed_triples = sort_triples(snapshot.observed_triples)
        if self.stage != "sp2b":
            snapshot.budget.used_depth += 1
        return new_frontier

    def _reject_unsupported_backtrack(
        self,
        snapshot: PoGSnapshot,
        action: Action,
        before_state: VisibleState,
        before_budget: Budget,
    ) -> Tuple[PoGSnapshot, StepOutcome, Optional[EnvironmentResult]]:
        target = action.params.get("entity_or_state") or action.params.get("state_id") or ""
        error_code = ViolationCode.UNSUPPORTED_BACKTRACK_STATE.value
        outcome = StepOutcome(
            accepted=False,
            protocol_violation=error_code,
            visible_result={
                "failure_class": FailureClass.ACTION_SPACE_FAILURE.value,
                "error_code": error_code,
                "message": "BACKTRACK(state) is unsupported in SP1; use SELECT_FRONTIER for history reselection",
                "target": target,
            },
            new_frontier_items=[],
            budget_delta={"steps": 0, "kg_calls": 0, "llm_calls": 0, "critic_rounds": 0, "depth": 0, "frontier_size": 0},
            state_id_before=before_state.state_id,
            state_id_after=before_state.state_id,
            deterministic_result_hash="",
        )
        outcome.deterministic_result_hash = canonical_hash(outcome.actor_visible_dict())
        return snapshot, outcome, None

    def _rejected_outcome(self, before_state: VisibleState, exc: ProtocolError) -> StepOutcome:
        failure_class = FailureClass.BUDGET_INSUFFICIENT if exc.code is ViolationCode.BUDGET_EXCEEDED else None
        if exc.code in {
            ViolationCode.INVISIBLE_ENTITY,
            ViolationCode.INVISIBLE_RELATION,
            ViolationCode.INVALID_DIRECTION,
            ViolationCode.UNKNOWN_ACTION,
        }:
            failure_class = FailureClass.ACTION_SPACE_FAILURE
        if exc.code is ViolationCode.UNOBSERVED_ANSWER:
            failure_class = FailureClass.ANSWER_EXTRACTION_FAILURE
        outcome = StepOutcome(
            accepted=False,
            protocol_violation=exc.code.value,
            visible_result={
                "error": exc.message,
                "failure_class": None if failure_class is None else failure_class.value,
                "error_code": exc.code.value,
            },
            new_frontier_items=[],
            budget_delta={},
            state_id_before=before_state.state_id,
            state_id_after=before_state.state_id,
            deterministic_result_hash="",
        )
        outcome.deterministic_result_hash = canonical_hash(outcome.actor_visible_dict())
        return outcome

    @staticmethod
    def _budget_delta(before: Budget, after: Budget) -> Dict[str, int]:
        return {
            "steps": after.used_steps - before.used_steps,
            "kg_calls": after.used_kg_calls - before.used_kg_calls,
            "llm_calls": after.used_llm_calls - before.used_llm_calls,
            "critic_rounds": after.used_critic_rounds - before.used_critic_rounds,
            "depth": after.used_depth - before.used_depth,
            "frontier_size": after.used_frontier_size - before.used_frontier_size,
        }

    @staticmethod
    def _history_item(action: Action) -> str:
        if action.action_type is ActionType.EXPAND:
            return (
                f"EXPAND entity={action.params.get('entity')} "
                f"relation={action.params.get('relation')} "
                f"direction={action.params.get('direction')}"
            )
        if action.action_type is ActionType.SELECT_FRONTIER:
            return f"SELECT_FRONTIER entity={action.params.get('entity')}"
        if action.action_type is ActionType.STOP:
            return "STOP"
        if action.action_type is ActionType.CONTINUE:
            return "CONTINUE"
        if action.action_type is ActionType.ABSTAIN:
            return f"ABSTAIN {action.params.get('reason_code')}"
        return action.action_type.value


DECISION_MAP = {
    "protocol_version": PROTOCOL_VERSION,
    "plan_version": "SP1-PLAN 1.3",
    "canonicalization_version": CANONICALIZATION_VERSION,
    "backtrack_state_policy": "unsupported",
    "direction_semantics": "current_entity_role",
    "notes": [
        "SP1 binds candidate inputs, output parsers, and state application, not composite LLM+KG functions.",
        "relation_search_prune and reasoning contain run_llm and are not Environment interfaces.",
    ],
    "stages": [
        {
            "protocol_stage": "RELATION_SELECTION",
            "source_file": "freebase_func.py",
            "functions": [
                {
                    "name": "relation_search_prune",
                    "contains_llm": True,
                    "sp1_bound_as_environment": False,
                    "role": "composite_not_bound",
                },
                {
                    "name": "select_relations",
                    "contains_llm": False,
                    "sp1_bound_as_environment": False,
                    "role": "offline_parse",
                },
                {
                    "name": "entity_search",
                    "contains_llm": False,
                    "sp1_bound_as_environment": True,
                    "role": "expand_query",
                },
            ],
            "supported_actions": ["EXPAND"],
            "unsupported_actions": [],
        },
        {
            "protocol_stage": "CONTINUE_STOP",
            "source_file": "freebase_func.py",
            "functions": [
                {
                    "name": "reasoning",
                    "contains_llm": True,
                    "sp1_bound_as_environment": False,
                    "role": "composite_not_bound",
                }
            ],
            "parser": "utils.py:extract_reason_and_anwer used only as original-path replica; SP1 uses independent parser",
            "supported_actions": ["CONTINUE", "STOP", "ABSTAIN"],
            "unsupported_actions": [],
        },
        {
            "protocol_stage": "ANSWER_SUBMISSION",
            "source_file": "utils.py",
            "functions": [
                {
                    "name": "extract_reason_and_anwer",
                    "contains_llm": False,
                    "sp1_bound_as_environment": False,
                    "role": "offline_parse",
                }
            ],
            "supported_actions": ["STOP", "ABSTAIN"],
            "unsupported_actions": [],
        },
        {
            "protocol_stage": "BACKTRACK_RECOVERY",
            "source_file": "utils.py",
            "functions": [
                {
                    "name": "if_finish_list",
                    "contains_llm": True,
                    "sp1_bound_as_environment": False,
                    "role": "composite_not_bound",
                },
                {
                    "name": "add_pre_info",
                    "contains_llm": False,
                    "sp1_bound_as_environment": False,
                    "role": "history_reselection_maps_to_select_frontier",
                },
            ],
            "supported_actions": ["SELECT_FRONTIER"],
            "unsupported_actions": ["BACKTRACK(state)"],
        },
    ],
}


def original_select_relations(string, entity_id, head_relations, tail_relations):
    """Exact replica of freebase_func.select_relations for adapter-disabled equivalence."""
    last_brace_l = string.rfind("[")
    last_brace_r = string.rfind("]")
    if last_brace_l < last_brace_r:
        string = string[last_brace_l : last_brace_r + 1]
    relations = []
    rel_list = eval(string.strip())
    for relation in rel_list:
        if relation in head_relations:
            relations.append({"entity": entity_id, "relation": relation, "head": True})
        elif relation in tail_relations:
            relations.append({"entity": entity_id, "relation": relation, "head": False})
    if not relations:
        return False, "No relations found"
    return True, relations


def original_entity_search(entity, relation, head=True, *, bindings: Sequence[Mapping[str, Any]]):
    """Replica of freebase_func.entity_search with injected SPARQL bindings."""
    entity_ids = [
        item["tailEntity"]["value"].replace("http://rdf.freebase.com/ns/", "") for item in bindings
    ]
    return entity_ids


def default_sp1_budget() -> Budget:
    return Budget(
        max_depth=4,
        max_steps=12,
        max_kg_calls=16,
        max_llm_calls=8,
        max_critic_rounds=2,
        max_frontier_size=80,
    )


def make_sp1_snapshot(**overrides: Any) -> PoGSnapshot:
    payload: Dict[str, Any] = {
        "task_id": "sp1.fixture.001",
        "question": "Where was Bob born?",
        "source_entities": ["m.alice"],
        "topic_entity": {"m.alice": "Alice"},
        "ent_rel_ent_dict": {},
        "depth_ent_rel_ent_dict": {},
        "cluster_chain_of_entities": [],
        "frontier": ["m.alice"],
        "failed_or_exhausted_branches": [],
        "action_history_summary": [],
        "budget": default_sp1_budget().to_dict(),
        "decision_stage": DecisionStage.RELATION_SELECTION.value,
        "enumerated_relations": [
            {"entity": "m.alice", "relation": "people.person.friend", "direction": "head"}
        ],
        "observed_triples": [],
        "entid_name": {"m.alice": "Alice"},
        "name_entid": {"Alice": "m.alice"},
    }
    payload.update(overrides)
    return PoGSnapshot.from_dict(payload)


def original_extract_reason_and_anwer(string):
    """Replica of utils.extract_reason_and_anwer without printing."""
    import re

    first_brace_p = string.find("{")
    last_brace_p = string.rfind("}")
    string = string[first_brace_p : last_brace_p + 1]
    answer = re.search(r'"Answer":\s*"(.*?)"', string)
    try:
        if answer:
            answer = answer.group(1)
        else:
            answer = re.search(r'"Answer":\s*(\[[^\]]+\])', string).group(1)
    except Exception:
        return None, None, None
    reason_match = re.search(r'"R":\s*"(.*?)"', string)
    sufficient_match = re.search(r'"Sufficient":\s*"(.*?)"', string)
    if reason_match is None or sufficient_match is None:
        return None, None, None
    return answer, reason_match.group(1), sufficient_match.group(1)
