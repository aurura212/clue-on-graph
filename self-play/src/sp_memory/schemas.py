"""Versioned, JSON-serializable protocol schemas for SP0."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Type, TypeVar

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json
from .paths import PROTOCOL_VERSION

T = TypeVar("T", bound="SchemaBase")


class ActionType(str, Enum):
    EXPAND = "EXPAND"
    SELECT_FRONTIER = "SELECT_FRONTIER"
    BACKTRACK = "BACKTRACK"
    CONTINUE = "CONTINUE"
    STOP = "STOP"
    ABSTAIN = "ABSTAIN"


class Direction(str, Enum):
    HEAD = "head"
    TAIL = "tail"


class ActorRole(str, Enum):
    EXPLORER = "explorer"
    CRITIC = "critic"
    VERIFIER = "verifier"
    ENVIRONMENT = "environment"
    ORACLE = "oracle"


class DecisionStage(str, Enum):
    RELATION_SELECTION = "relation_selection"
    CONTINUE_STOP = "continue_stop"
    BACKTRACK_RECOVERY = "backtrack_recovery"
    ANSWER_SUBMISSION = "answer_submission"
    INIT = "init"


class AbstainReason(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_LEGAL_ACTION = "NO_LEGAL_ACTION"
    PROTOCOL_STOP = "PROTOCOL_STOP"


class TerminationReason(str, Enum):
    STOP_SUBMITTED = "STOP_SUBMITTED"
    ABSTAINED = "ABSTAINED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class FailureClass(str, Enum):
    INVALID_TASK = "invalid_task"
    ACTION_SPACE_FAILURE = "action_space_failure"
    BUDGET_INSUFFICIENT = "budget_insufficient"
    EXPLORER_FAILURE = "explorer_failure"
    CRITIC_RECOVERY_FAILURE = "critic_recovery_failure"
    ANSWER_EXTRACTION_FAILURE = "answer_extraction_failure"
    SYSTEM_FAILURE = "system_failure"


class OracleLevel(str, Enum):
    O0 = "O0"
    O1 = "O1"
    O2 = "O2"
    O3 = "O3"
    O4 = "O4"


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    INVALID = "INVALID"


class DiscoveryMethod(str, Enum):
    O0_CRITIC = "o0_critic"
    ORACLE_GUIDED_OFFLINE_TEACHER = "oracle_guided_offline_teacher"
    RANDOM_CRITIC = "random_critic"


TASK_PUBLIC_FIELDS = (
    "task_id",
    "question",
    "source_entities",
    "source_entity_names",
    "task_split",
    "task_generator_version",
    "input_snapshot_id",
    "protocol_version",
)
TASK_ORACLE_FIELDS = (
    "logical_query",
    "answer_entity_ids",
    "normalized_answers",
    "witness_paths",
    "task_validity",
    "oracle_version",
)
VISIBLE_STATE_FIELDS = (
    "state_id",
    "task_id",
    "question",
    "visible_entities",
    "visible_relations",
    "observed_triples_or_summaries",
    "frontier",
    "failed_or_exhausted_branches",
    "action_history_summary",
    "remaining_budget",
    "decision_stage",
    "protocol_version",
)
FORBIDDEN_VISIBLE_FIELDS = {
    "answer_entity_ids",
    "normalized_answers",
    "witness_paths",
    "gold_path",
    "gold_sparql",
    "logical_query",
    "future_neighbors",
    "hidden_reward",
    "counterfactual_outcome",
}


def _expect_type(name: str, value: Any, expected: Type[Any] | Tuple[Type[Any], ...]) -> None:
    if not isinstance(value, expected):
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            f"field {name} has type {type(value).__name__}, expected {expected}",
            {"field": name, "actual_type": type(value).__name__},
        )


def _expect_str(name: str, value: Any) -> str:
    _expect_type(name, value, str)
    if not value:
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"field {name} must be non-empty", {"field": name})
    return value


def _expect_list_str(name: str, value: Any) -> List[str]:
    _expect_type(name, value, list)
    out = []
    for item in value:
        if not isinstance(item, str):
            raise ProtocolError(
                ViolationCode.SCHEMA_ERROR,
                f"field {name} must be a list of strings",
                {"field": name},
            )
        out.append(item)
    return out


def _check_protocol_version(payload: Mapping[str, Any]) -> None:
    version = payload.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            ViolationCode.SCHEMA_VERSION_MISMATCH,
            f"protocol_version {version!r} != {PROTOCOL_VERSION}",
            {"got": version, "expected": PROTOCOL_VERSION},
        )


def _reject_unknown(payload: Mapping[str, Any], allowed: Iterable[str], label: str) -> None:
    extra = sorted(set(payload) - set(allowed))
    if extra:
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            f"{label} has unknown fields: {extra}",
            {"unknown_fields": extra},
        )


def _require_fields(payload: Mapping[str, Any], required: Iterable[str], label: str) -> None:
    missing = [name for name in required if name not in payload]
    if missing:
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            f"{label} missing fields: {missing}",
            {"missing_fields": missing},
        )


def parse_enum(enum_cls: Type[Enum], value: Any, field_name: str) -> Enum:
    try:
        if isinstance(value, enum_cls):
            return value
        return enum_cls(value)
    except ValueError as exc:
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            f"illegal enum value for {field_name}: {value!r}",
            {"field": field_name, "value": value, "allowed": [item.value for item in enum_cls]},
        ) from exc


class SchemaBase:
    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    def fingerprint(self) -> str:
        return canonical_hash(self.to_dict())

    @classmethod
    def from_json(cls: Type[T], text: str) -> T:
        import json

        return cls.from_dict(json.loads(text))

    @classmethod
    def from_dict(cls: Type[T], payload: Mapping[str, Any]) -> T:
        raise NotImplementedError

    def round_trip(self: T) -> T:
        restored = self.from_dict(self.to_dict())
        if restored.to_dict() != self.to_dict():
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"{type(self).__name__} round-trip mismatch")
        return restored


@dataclass
class Budget:
    max_depth: int
    max_steps: int
    max_kg_calls: int
    max_llm_calls: int
    max_critic_rounds: int
    max_frontier_size: int
    used_depth: int = 0
    used_steps: int = 0
    used_kg_calls: int = 0
    used_llm_calls: int = 0
    used_critic_rounds: int = 0
    used_frontier_size: int = 0

    def remaining(self) -> Dict[str, int]:
        return {
            "depth": self.max_depth - self.used_depth,
            "steps": self.max_steps - self.used_steps,
            "kg_calls": self.max_kg_calls - self.used_kg_calls,
            "llm_calls": self.max_llm_calls - self.used_llm_calls,
            "critic_rounds": self.max_critic_rounds - self.used_critic_rounds,
            "frontier_size": self.max_frontier_size - self.used_frontier_size,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_depth": self.max_depth,
            "max_steps": self.max_steps,
            "max_kg_calls": self.max_kg_calls,
            "max_llm_calls": self.max_llm_calls,
            "max_critic_rounds": self.max_critic_rounds,
            "max_frontier_size": self.max_frontier_size,
            "used_depth": self.used_depth,
            "used_steps": self.used_steps,
            "used_kg_calls": self.used_kg_calls,
            "used_llm_calls": self.used_llm_calls,
            "used_critic_rounds": self.used_critic_rounds,
            "used_frontier_size": self.used_frontier_size,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Budget":
        required = [
            "max_depth",
            "max_steps",
            "max_kg_calls",
            "max_llm_calls",
            "max_critic_rounds",
            "max_frontier_size",
        ]
        _require_fields(payload, required, "Budget")
        kwargs = {}
        for key in required + [
            "used_depth",
            "used_steps",
            "used_kg_calls",
            "used_llm_calls",
            "used_critic_rounds",
            "used_frontier_size",
        ]:
            if key in payload:
                _expect_type(key, payload[key], int)
                if payload[key] < 0:
                    raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"{key} must be >= 0")
                kwargs[key] = payload[key]
        return cls(**kwargs)

    @classmethod
    def from_config(cls, budgets: Mapping[str, Any]) -> "Budget":
        return cls.from_dict(
            {
                "max_depth": int(budgets["max_depth"]),
                "max_steps": int(budgets["max_steps"]),
                "max_kg_calls": int(budgets["max_kg_calls"]),
                "max_llm_calls": int(budgets["max_llm_calls"]),
                "max_critic_rounds": int(budgets["max_critic_rounds"]),
                "max_frontier_size": int(budgets["max_frontier_size"]),
            }
        )


@dataclass
class TaskRecord(SchemaBase):
    task_id: str
    question: str
    source_entities: List[str]
    source_entity_names: Dict[str, str]
    task_split: str
    task_generator_version: str
    input_snapshot_id: str
    logical_query: str
    answer_entity_ids: List[str]
    normalized_answers: List[str]
    witness_paths: List[List[str]]
    task_validity: str
    oracle_version: str
    protocol_version: str = PROTOCOL_VERSION

    def public_dict(self) -> Dict[str, Any]:
        return {name: getattr(self, name) for name in TASK_PUBLIC_FIELDS}

    def oracle_dict(self) -> Dict[str, Any]:
        return {name: getattr(self, name) for name in TASK_ORACLE_FIELDS}

    def to_dict(self) -> Dict[str, Any]:
        payload = self.public_dict()
        payload.update(self.oracle_dict())
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaskRecord":
        _expect_type("TaskRecord", payload, dict)
        _check_protocol_version(payload)
        _require_fields(payload, list(TASK_PUBLIC_FIELDS) + list(TASK_ORACLE_FIELDS), "TaskRecord")
        _reject_unknown(payload, list(TASK_PUBLIC_FIELDS) + list(TASK_ORACLE_FIELDS), "TaskRecord")
        names = payload["source_entity_names"]
        _expect_type("source_entity_names", names, dict)
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in names.items()):
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "source_entity_names must be str->str")
        witness = payload["witness_paths"]
        _expect_type("witness_paths", witness, list)
        parsed_witness = []
        for path in witness:
            parsed_witness.append(_expect_list_str("witness_paths.item", path))
        return cls(
            task_id=_expect_str("task_id", payload["task_id"]),
            question=_expect_str("question", payload["question"]),
            source_entities=_expect_list_str("source_entities", payload["source_entities"]),
            source_entity_names=dict(names),
            task_split=_expect_str("task_split", payload["task_split"]),
            task_generator_version=_expect_str("task_generator_version", payload["task_generator_version"]),
            input_snapshot_id=_expect_str("input_snapshot_id", payload["input_snapshot_id"]),
            logical_query=payload["logical_query"] if isinstance(payload["logical_query"], str) else (_expect_str("logical_query", payload["logical_query"])),
            answer_entity_ids=_expect_list_str("answer_entity_ids", payload["answer_entity_ids"]),
            normalized_answers=_expect_list_str("normalized_answers", payload["normalized_answers"]),
            witness_paths=parsed_witness,
            task_validity=_expect_str("task_validity", payload["task_validity"]),
            oracle_version=_expect_str("oracle_version", payload["oracle_version"]),
            protocol_version=_expect_str("protocol_version", payload["protocol_version"]),
        )


@dataclass
class VisibleRelation:
    entity: str
    relation: str
    direction: Direction

    def to_dict(self) -> Dict[str, Any]:
        return {"entity": self.entity, "relation": self.relation, "direction": self.direction.value}

    def key(self) -> Tuple[str, str, str]:
        return (self.entity, self.relation, self.direction.value)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VisibleRelation":
        _require_fields(payload, ["entity", "relation", "direction"], "VisibleRelation")
        return cls(
            entity=_expect_str("entity", payload["entity"]),
            relation=_expect_str("relation", payload["relation"]),
            direction=parse_enum(Direction, payload["direction"], "direction"),  # type: ignore[arg-type]
        )


@dataclass
class VisibleState(SchemaBase):
    state_id: str
    task_id: str
    question: str
    visible_entities: List[str]
    visible_relations: List[VisibleRelation]
    observed_triples_or_summaries: List[Dict[str, str]]
    frontier: List[str]
    failed_or_exhausted_branches: List[str]
    action_history_summary: List[str]
    remaining_budget: Dict[str, int]
    decision_stage: DecisionStage
    protocol_version: str = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        for item in self.observed_triples_or_summaries:
            leak = set(item) & FORBIDDEN_VISIBLE_FIELDS
            if leak:
                raise ProtocolError(
                    ViolationCode.ORACLE_LEAKAGE,
                    f"VisibleState contains forbidden fields {sorted(leak)}",
                )
        return {
            "state_id": self.state_id,
            "task_id": self.task_id,
            "question": self.question,
            "visible_entities": list(self.visible_entities),
            "visible_relations": [item.to_dict() for item in self.visible_relations],
            "observed_triples_or_summaries": list(self.observed_triples_or_summaries),
            "frontier": list(self.frontier),
            "failed_or_exhausted_branches": list(self.failed_or_exhausted_branches),
            "action_history_summary": list(self.action_history_summary),
            "remaining_budget": dict(self.remaining_budget),
            "decision_stage": self.decision_stage.value,
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VisibleState":
        _expect_type("VisibleState", payload, dict)
        _check_protocol_version(payload)
        _require_fields(payload, VISIBLE_STATE_FIELDS, "VisibleState")
        _reject_unknown(payload, VISIBLE_STATE_FIELDS, "VisibleState")
        leak = set(payload) & FORBIDDEN_VISIBLE_FIELDS
        if leak:
            raise ProtocolError(
                ViolationCode.ORACLE_LEAKAGE,
                f"VisibleState payload contains forbidden fields {sorted(leak)}",
            )
        relations = []
        _expect_type("visible_relations", payload["visible_relations"], list)
        for item in payload["visible_relations"]:
            relations.append(VisibleRelation.from_dict(item))
        triples = payload["observed_triples_or_summaries"]
        _expect_type("observed_triples_or_summaries", triples, list)
        parsed_triples = []
        for item in triples:
            _expect_type("observed_triples_or_summaries.item", item, dict)
            if set(item) & FORBIDDEN_VISIBLE_FIELDS:
                raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, "triple summary has oracle fields")
            parsed = {}
            for key, value in item.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise ProtocolError(ViolationCode.SCHEMA_ERROR, "triple summary must be str->str")
                parsed[key] = value
            parsed_triples.append(parsed)
        budget = payload["remaining_budget"]
        _expect_type("remaining_budget", budget, dict)
        parsed_budget = {}
        for key, value in budget.items():
            if not isinstance(key, str) or not isinstance(value, int):
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "remaining_budget must be str->int")
            parsed_budget[key] = value
        return cls(
            state_id=_expect_str("state_id", payload["state_id"]),
            task_id=_expect_str("task_id", payload["task_id"]),
            question=_expect_str("question", payload["question"]),
            visible_entities=_expect_list_str("visible_entities", payload["visible_entities"]),
            visible_relations=relations,
            observed_triples_or_summaries=parsed_triples,
            frontier=_expect_list_str("frontier", payload["frontier"]),
            failed_or_exhausted_branches=_expect_list_str(
                "failed_or_exhausted_branches", payload["failed_or_exhausted_branches"]
            ),
            action_history_summary=_expect_list_str(
                "action_history_summary", payload["action_history_summary"]
            ),
            remaining_budget=parsed_budget,
            decision_stage=parse_enum(DecisionStage, payload["decision_stage"], "decision_stage"),  # type: ignore[arg-type]
            protocol_version=_expect_str("protocol_version", payload["protocol_version"]),
        )


@dataclass
class Action(SchemaBase):
    action_id: str
    action_type: ActionType
    params: Dict[str, Any]
    source_role: ActorRole
    state_id: str
    protocol_version: str = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "params": dict(self.params),
            "source_role": self.source_role.value,
            "state_id": self.state_id,
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Action":
        _expect_type("Action", payload, dict)
        _check_protocol_version(payload)
        required = ["action_id", "action_type", "params", "source_role", "state_id", "protocol_version"]
        _require_fields(payload, required, "Action")
        _reject_unknown(payload, required, "Action")
        params = payload["params"]
        _expect_type("params", params, dict)
        return cls(
            action_id=_expect_str("action_id", payload["action_id"]),
            action_type=parse_enum(ActionType, payload["action_type"], "action_type"),  # type: ignore[arg-type]
            params=dict(params),
            source_role=parse_enum(ActorRole, payload["source_role"], "source_role"),  # type: ignore[arg-type]
            state_id=_expect_str("state_id", payload["state_id"]),
            protocol_version=_expect_str("protocol_version", payload["protocol_version"]),
        )


@dataclass
class StepOutcome(SchemaBase):
    accepted: bool
    protocol_violation: Optional[str]
    visible_result: Dict[str, Any]
    new_frontier_items: List[str]
    budget_delta: Dict[str, int]
    state_id_before: str
    state_id_after: str
    deterministic_result_hash: str
    oracle_eval: Dict[str, Any] = field(default_factory=dict)
    protocol_version: str = PROTOCOL_VERSION

    def actor_visible_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "protocol_violation": self.protocol_violation,
            "visible_result": dict(self.visible_result),
            "new_frontier_items": list(self.new_frontier_items),
            "budget_delta": dict(self.budget_delta),
            "state_id_before": self.state_id_before,
            "state_id_after": self.state_id_after,
            "deterministic_result_hash": self.deterministic_result_hash,
            "protocol_version": self.protocol_version,
        }

    def to_dict(self) -> Dict[str, Any]:
        payload = self.actor_visible_dict()
        payload["oracle_eval"] = dict(self.oracle_eval)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StepOutcome":
        _expect_type("StepOutcome", payload, dict)
        _check_protocol_version(payload)
        required = [
            "accepted",
            "protocol_violation",
            "visible_result",
            "new_frontier_items",
            "budget_delta",
            "state_id_before",
            "state_id_after",
            "deterministic_result_hash",
            "protocol_version",
        ]
        _require_fields(payload, required, "StepOutcome")
        allowed = required + ["oracle_eval"]
        _reject_unknown(payload, allowed, "StepOutcome")
        _expect_type("accepted", payload["accepted"], bool)
        violation = payload["protocol_violation"]
        if violation is not None:
            _expect_str("protocol_violation", violation)
        _expect_type("visible_result", payload["visible_result"], dict)
        budget_delta = payload["budget_delta"]
        _expect_type("budget_delta", budget_delta, dict)
        parsed_delta = {}
        for key, value in budget_delta.items():
            if not isinstance(key, str) or not isinstance(value, int):
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "budget_delta must be str->int")
            parsed_delta[key] = value
        oracle_eval = payload.get("oracle_eval", {})
        _expect_type("oracle_eval", oracle_eval, dict)
        return cls(
            accepted=payload["accepted"],
            protocol_violation=violation,
            visible_result=dict(payload["visible_result"]),
            new_frontier_items=_expect_list_str("new_frontier_items", payload["new_frontier_items"]),
            budget_delta=parsed_delta,
            state_id_before=_expect_str("state_id_before", payload["state_id_before"]),
            state_id_after=_expect_str("state_id_after", payload["state_id_after"]),
            deterministic_result_hash=_expect_str(
                "deterministic_result_hash", payload["deterministic_result_hash"]
            ),
            oracle_eval=dict(oracle_eval),
            protocol_version=_expect_str("protocol_version", payload["protocol_version"]),
        )


@dataclass
class TrajectoryRecord(SchemaBase):
    trajectory_id: str
    task_id: str
    protocol_version: str
    initial_state_hash: str
    ordered_steps: List[Dict[str, Any]]
    terminal_submission: Optional[List[str]]
    termination_reason: TerminationReason
    cost_summary: Dict[str, int]
    replay_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "task_id": self.task_id,
            "protocol_version": self.protocol_version,
            "initial_state_hash": self.initial_state_hash,
            "ordered_steps": list(self.ordered_steps),
            "terminal_submission": None if self.terminal_submission is None else list(self.terminal_submission),
            "termination_reason": self.termination_reason.value,
            "cost_summary": dict(self.cost_summary),
            "replay_hash": self.replay_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrajectoryRecord":
        _expect_type("TrajectoryRecord", payload, dict)
        _check_protocol_version(payload)
        required = [
            "trajectory_id",
            "task_id",
            "protocol_version",
            "initial_state_hash",
            "ordered_steps",
            "terminal_submission",
            "termination_reason",
            "cost_summary",
            "replay_hash",
        ]
        _require_fields(payload, required, "TrajectoryRecord")
        _reject_unknown(payload, required, "TrajectoryRecord")
        steps = payload["ordered_steps"]
        _expect_type("ordered_steps", steps, list)
        submission = payload["terminal_submission"]
        if submission is not None:
            submission = _expect_list_str("terminal_submission", submission)
        cost = payload["cost_summary"]
        _expect_type("cost_summary", cost, dict)
        parsed_cost = {}
        for key, value in cost.items():
            if not isinstance(key, str) or not isinstance(value, int):
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, "cost_summary must be str->int")
            parsed_cost[key] = value
        return cls(
            trajectory_id=_expect_str("trajectory_id", payload["trajectory_id"]),
            task_id=_expect_str("task_id", payload["task_id"]),
            protocol_version=_expect_str("protocol_version", payload["protocol_version"]),
            initial_state_hash=_expect_str("initial_state_hash", payload["initial_state_hash"]),
            ordered_steps=list(steps),
            terminal_submission=submission,
            termination_reason=parse_enum(TerminationReason, payload["termination_reason"], "termination_reason"),  # type: ignore[arg-type]
            cost_summary=parsed_cost,
            replay_hash=_expect_str("replay_hash", payload["replay_hash"]),
        )


@dataclass
class RunManifest(SchemaBase):
    run_id: str
    plan_version: str
    protocol_version: str
    git_commit: Optional[str]
    git_dirty: Optional[bool]
    command: List[str]
    config_hash: str
    input_files: List[Dict[str, Any]]
    seed: Optional[int]
    model_metadata: Dict[str, Any]
    start_time: str
    end_time: Optional[str]
    status: RunStatus
    output_files: List[Dict[str, Any]]
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_version": self.plan_version,
            "protocol_version": self.protocol_version,
            "git_commit": self.git_commit,
            "git_dirty": self.git_dirty,
            "command": list(self.command),
            "config_hash": self.config_hash,
            "input_files": list(self.input_files),
            "seed": self.seed,
            "model_metadata": dict(self.model_metadata),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status.value,
            "output_files": list(self.output_files),
            "error": None if self.error is None else dict(self.error),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunManifest":
        _expect_type("RunManifest", payload, dict)
        _check_protocol_version(payload)
        required = [
            "run_id",
            "plan_version",
            "protocol_version",
            "git_commit",
            "git_dirty",
            "command",
            "config_hash",
            "input_files",
            "seed",
            "model_metadata",
            "start_time",
            "end_time",
            "status",
            "output_files",
        ]
        _require_fields(payload, required, "RunManifest")
        _reject_unknown(payload, required + ["error"], "RunManifest")
        _expect_type("command", payload["command"], list)
        if any(not isinstance(item, str) for item in payload["command"]):
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "command must be list[str]")
        if payload["git_commit"] is not None:
            _expect_type("git_commit", payload["git_commit"], str)
        if payload["git_dirty"] is not None:
            _expect_type("git_dirty", payload["git_dirty"], bool)
        if payload["seed"] is not None:
            _expect_type("seed", payload["seed"], int)
        if payload["end_time"] is not None:
            _expect_type("end_time", payload["end_time"], str)
        error = payload.get("error")
        if error is not None:
            _expect_type("error", error, dict)
        _expect_type("input_files", payload["input_files"], list)
        _expect_type("output_files", payload["output_files"], list)
        _expect_type("model_metadata", payload["model_metadata"], dict)
        return cls(
            run_id=_expect_str("run_id", payload["run_id"]),
            plan_version=_expect_str("plan_version", payload["plan_version"]),
            protocol_version=_expect_str("protocol_version", payload["protocol_version"]),
            git_commit=payload["git_commit"],
            git_dirty=payload["git_dirty"],
            command=list(payload["command"]),
            config_hash=_expect_str("config_hash", payload["config_hash"]),
            input_files=list(payload["input_files"]),
            seed=payload["seed"],
            model_metadata=dict(payload["model_metadata"]),
            start_time=_expect_str("start_time", payload["start_time"]),
            end_time=payload["end_time"],
            status=parse_enum(RunStatus, payload["status"], "status"),  # type: ignore[arg-type]
            output_files=list(payload["output_files"]),
            error=None if error is None else dict(error),
        )


@dataclass
class ExclusionRecord(SchemaBase):
    dataset: str
    split: str
    task_id: str
    normalized_question_hash: str
    topic_entities: List[str]
    answer_entities: List[str]
    exposure_source: str
    exposed_at: str
    protocol_version: str = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset,
            "split": self.split,
            "task_id": self.task_id,
            "normalized_question_hash": self.normalized_question_hash,
            "topic_entities": list(self.topic_entities),
            "answer_entities": list(self.answer_entities),
            "exposure_source": self.exposure_source,
            "exposed_at": self.exposed_at,
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExclusionRecord":
        _expect_type("ExclusionRecord", payload, dict)
        _check_protocol_version(payload)
        required = [
            "dataset",
            "split",
            "task_id",
            "normalized_question_hash",
            "topic_entities",
            "answer_entities",
            "exposure_source",
            "exposed_at",
            "protocol_version",
        ]
        _require_fields(payload, required, "ExclusionRecord")
        _reject_unknown(payload, required, "ExclusionRecord")
        return cls(
            dataset=_expect_str("dataset", payload["dataset"]),
            split=_expect_str("split", payload["split"]),
            task_id=_expect_str("task_id", payload["task_id"]),
            normalized_question_hash=_expect_str(
                "normalized_question_hash", payload["normalized_question_hash"]
            ),
            topic_entities=_expect_list_str("topic_entities", payload["topic_entities"]),
            answer_entities=_expect_list_str("answer_entities", payload["answer_entities"]),
            exposure_source=_expect_str("exposure_source", payload["exposure_source"]),
            exposed_at=_expect_str("exposed_at", payload["exposed_at"]),
            protocol_version=_expect_str("protocol_version", payload["protocol_version"]),
        )


@dataclass
class OfflineFeedback(SchemaBase):
    """O1-O3 only. O4 objects cannot be reused as offline feedback."""

    task_id: str
    level: OracleLevel
    feedback_version: str
    payload: Dict[str, Any]
    protocol_version: str = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "level": self.level.value,
            "feedback_version": self.feedback_version,
            "payload": dict(self.payload),
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OfflineFeedback":
        _expect_type("OfflineFeedback", payload, dict)
        _check_protocol_version(payload)
        required = ["task_id", "level", "feedback_version", "payload", "protocol_version"]
        _require_fields(payload, required, "OfflineFeedback")
        _reject_unknown(payload, required, "OfflineFeedback")
        level = parse_enum(OracleLevel, payload["level"], "level")
        if level not in {OracleLevel.O1, OracleLevel.O2, OracleLevel.O3}:
            raise ProtocolError(
                ViolationCode.ORACLE_LEAKAGE,
                "OfflineFeedback only accepts O1-O3",
                {"level": getattr(level, "value", level)},
            )
        body = payload["payload"]
        _expect_type("payload", body, dict)
        leak = set(body) & FORBIDDEN_VISIBLE_FIELDS
        if leak:
            raise ProtocolError(
                ViolationCode.ORACLE_LEAKAGE,
                f"OfflineFeedback payload contains O4 fields {sorted(leak)}",
            )
        return cls(
            task_id=_expect_str("task_id", payload["task_id"]),
            level=level,  # type: ignore[arg-type]
            feedback_version=_expect_str("feedback_version", payload["feedback_version"]),
            payload=dict(body),
            protocol_version=_expect_str("protocol_version", payload["protocol_version"]),
        )


CANDIDATE_EXPERIENCE_FIELDS = (
    "experience_id",
    "source_run_id",
    "source_task_ids",
    "discovery_method",
    "trigger",
    "recommendation",
    "evidence",
    "privacy",
    "versions",
    "status",
    "canonical_hash",
    "protocol_version",
)
CANDIDATE_TRIGGER_FIELDS = ("question_type", "decision_stage", "state_signature", "failure_class")
CANDIDATE_RECOMMENDATION_FIELDS = (
    "action_type",
    "direction",
    "relation_pattern",
    "reason",
    "negative_constraints",
    "budget_condition",
)
CANDIDATE_EVIDENCE_FIELDS = (
    "verified_replay",
    "observed_outcome",
    "support_count",
    "counterfactual_status",
)
CANDIDATE_PRIVACY_FIELDS = (
    "answer_removed",
    "witness_removed",
    "entity_ids_removed",
    "gold_path_removed",
    "oracle_level",
)
CANDIDATE_VERSION_FIELDS = ("protocol_version", "plan_version", "prompt_version", "config_hash")


@dataclass
class CandidateExperience(SchemaBase):
    """Write-only SP3 candidate. Never injected into Explorer/Critic in this stage."""

    experience_id: str
    source_run_id: str
    source_task_ids: List[str]
    discovery_method: DiscoveryMethod
    trigger: Dict[str, Any]
    recommendation: Dict[str, Any]
    evidence: Dict[str, Any]
    privacy: Dict[str, Any]
    versions: Dict[str, Any]
    status: str = "candidate"
    canonical_hash: str = ""
    protocol_version: str = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "experience_id": self.experience_id,
            "source_run_id": self.source_run_id,
            "source_task_ids": list(self.source_task_ids),
            "discovery_method": self.discovery_method.value,
            "trigger": dict(self.trigger),
            "recommendation": dict(self.recommendation),
            "evidence": dict(self.evidence),
            "privacy": dict(self.privacy),
            "versions": dict(self.versions),
            "status": self.status,
            "canonical_hash": self.canonical_hash,
            "protocol_version": self.protocol_version,
        }
        if not payload["canonical_hash"]:
            payload["canonical_hash"] = canonical_hash(
                {
                    "trigger": payload["trigger"],
                    "recommendation": payload["recommendation"],
                    "discovery_method": payload["discovery_method"],
                }
            )
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateExperience":
        _expect_type("CandidateExperience", payload, dict)
        _check_protocol_version(payload)
        _require_fields(payload, CANDIDATE_EXPERIENCE_FIELDS, "CandidateExperience")
        _reject_unknown(payload, CANDIDATE_EXPERIENCE_FIELDS, "CandidateExperience")
        if payload.get("status") != "candidate":
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "SP3 experience status must be candidate")
        trigger = payload["trigger"]
        recommendation = payload["recommendation"]
        evidence = payload["evidence"]
        privacy = payload["privacy"]
        versions = payload["versions"]
        _expect_type("trigger", trigger, dict)
        _expect_type("recommendation", recommendation, dict)
        _expect_type("evidence", evidence, dict)
        _expect_type("privacy", privacy, dict)
        _expect_type("versions", versions, dict)
        _require_fields(trigger, CANDIDATE_TRIGGER_FIELDS, "CandidateExperience.trigger")
        _require_fields(recommendation, CANDIDATE_RECOMMENDATION_FIELDS, "CandidateExperience.recommendation")
        _require_fields(evidence, CANDIDATE_EVIDENCE_FIELDS, "CandidateExperience.evidence")
        _require_fields(privacy, CANDIDATE_PRIVACY_FIELDS, "CandidateExperience.privacy")
        _require_fields(versions, CANDIDATE_VERSION_FIELDS, "CandidateExperience.versions")
        leak = (set(trigger) | set(recommendation) | set(evidence) | set(privacy)) & FORBIDDEN_VISIBLE_FIELDS
        if leak:
            raise ProtocolError(
                ViolationCode.ORACLE_LEAKAGE,
                f"CandidateExperience contains Oracle fields {sorted(leak)}",
            )
        method = parse_enum(DiscoveryMethod, payload["discovery_method"], "discovery_method")
        oracle_level = parse_enum(OracleLevel, privacy["oracle_level"], "privacy.oracle_level")
        if oracle_level is OracleLevel.O4:
            raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, "candidate experience cannot be O4")
        if evidence.get("counterfactual_status") != "deferred_to_sp4":
            raise ProtocolError(
                ViolationCode.SCHEMA_ERROR,
                "SP3 candidates must set counterfactual_status=deferred_to_sp4",
            )
        for flag in ("answer_removed", "witness_removed", "entity_ids_removed", "gold_path_removed"):
            if privacy.get(flag) is not True:
                raise ProtocolError(ViolationCode.ORACLE_LEAKAGE, f"privacy.{flag} must be true")
        return cls(
            experience_id=_expect_str("experience_id", payload["experience_id"]),
            source_run_id=_expect_str("source_run_id", payload["source_run_id"]),
            source_task_ids=_expect_list_str("source_task_ids", payload["source_task_ids"]),
            discovery_method=method,  # type: ignore[arg-type]
            trigger=dict(trigger),
            recommendation=dict(recommendation),
            evidence=dict(evidence),
            privacy=dict(privacy),
            versions=dict(versions),
            status=_expect_str("status", payload["status"]),
            canonical_hash=_expect_str("canonical_hash", payload["canonical_hash"]),
            protocol_version=_expect_str("protocol_version", payload["protocol_version"]),
        )
