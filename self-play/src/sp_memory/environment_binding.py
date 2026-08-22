"""Minimal Environment binding for EXPAND and relation enumeration. No live KG, no LLM."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash
from .paths import PROTOCOL_VERSION
from .schemas import Direction, FailureClass, VisibleRelation

FINISH_MARKERS = {"[FINISH_ID]", "[FINISH]"}
CANONICALIZATION_VERSION = "sp1-canonical-v1"


class EnvironmentStatus(str, Enum):
    SUCCESS = "success"
    EMPTY_SUCCESS = "empty_success"
    LITERAL = "literal"
    DUPLICATE = "duplicate"
    MALFORMED = "malformed"
    TIMEOUT = "timeout"
    ENDPOINT_FAILURE = "endpoint_failure"
    SYSTEM_ERROR = "system_error"
    SCHEMA_ERROR = "schema_error"


class MalformedKgResponse(ValueError):
    pass


class KgTimeout(TimeoutError):
    pass


@dataclass
class EnvironmentResult:
    status: EnvironmentStatus
    results: List[Dict[str, str]]
    kg_call_delta: int
    failure_class: Optional[FailureClass]
    error_code: Optional[str]
    message: str
    provenance_ref: str
    raw_targets: List[str] = field(default_factory=list)
    traceback_text: Optional[str] = None
    protocol_version: str = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "results": list(self.results),
            "kg_call_delta": self.kg_call_delta,
            "failure_class": None if self.failure_class is None else self.failure_class.value,
            "error_code": self.error_code,
            "message": self.message,
            "provenance_ref": self.provenance_ref,
            "raw_targets": list(self.raw_targets),
            "traceback_text": self.traceback_text,
            "protocol_version": self.protocol_version,
        }


def direction_to_pog_head(direction: Direction | str) -> bool:
    parsed = Direction(direction) if not isinstance(direction, Direction) else direction
    if parsed is Direction.HEAD:
        return True
    if parsed is Direction.TAIL:
        return False
    raise ProtocolError(ViolationCode.INVALID_DIRECTION, f"unsupported direction {direction!r}")


def pog_head_to_direction(head: bool) -> Direction:
    if head is True:
        return Direction.HEAD
    if head is False:
        return Direction.TAIL
    raise ProtocolError(ViolationCode.INVALID_DIRECTION, f"PoG head flag is not bool: {head!r}")


def is_literal_value(value: str) -> bool:
    if not value or value in FINISH_MARKERS:
        return False
    if value.startswith("m.") or value.startswith("g."):
        return False
    return True


def is_finish_marker(value: str) -> bool:
    return value in FINISH_MARKERS


def canonical_triple(subject: str, relation: str, obj: str) -> Dict[str, str]:
    return {"subject": subject, "relation": relation, "object": obj}


def sort_triples(triples: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    unique = []
    seen = set()
    for item in triples:
        key = (item["subject"], item["relation"], item["object"])
        if key in seen:
            continue
        seen.add(key)
        unique.append({"subject": item["subject"], "relation": item["relation"], "object": item["object"]})
    return sorted(unique, key=lambda item: (item["subject"], item["relation"], item["object"]))


def triples_from_expand(entity: str, relation: str, direction: Direction, targets: Sequence[str]) -> List[Dict[str, str]]:
    triples = []
    for target in targets:
        if is_finish_marker(target):
            continue
        if direction is Direction.HEAD:
            triples.append(canonical_triple(entity, relation, target))
        else:
            triples.append(canonical_triple(target, relation, entity))
    return sort_triples(triples)


def _schema_error(message: str, provenance_ref: str, kg_call_delta: int = 0) -> EnvironmentResult:
    return EnvironmentResult(
        status=EnvironmentStatus.SCHEMA_ERROR,
        results=[],
        kg_call_delta=kg_call_delta,
        failure_class=FailureClass.SYSTEM_FAILURE,
        error_code="ADAPTER_SCHEMA_ERROR",
        message=message,
        provenance_ref=provenance_ref,
    )


def classify_expand_targets(raw_targets: Sequence[Any]) -> Tuple[List[str], bool, bool]:
    """Return (filtered targets in order, had_duplicate, had_literal). FINISH markers dropped."""
    if not isinstance(raw_targets, (list, tuple)):
        raise MalformedKgResponse("entity_search result is not a list")
    filtered: List[str] = []
    seen = set()
    had_duplicate = False
    had_literal = False
    for item in raw_targets:
        if not isinstance(item, str) or not item:
            raise MalformedKgResponse("entity_search result contains a non-string target")
        if is_finish_marker(item):
            continue
        if is_literal_value(item):
            had_literal = True
        if item in seen:
            had_duplicate = True
            continue
        seen.add(item)
        filtered.append(item)
    return filtered, had_duplicate, had_literal


class EnvironmentBinding:
    """Fixture/recorded I/O only. Live Freebase is forbidden in SP1."""

    def __init__(
        self,
        *,
        allow_live_kg: bool = False,
        executor: Optional[Callable[..., Any]] = None,
        provenance_prefix: str = "hand_fixture",
    ) -> None:
        if allow_live_kg:
            raise ProtocolError(
                ViolationCode.SCHEMA_ERROR,
                "SP1 EnvironmentBinding forbids live KG",
            )
        self.allow_live_kg = False
        self.executor = executor
        self.provenance_prefix = provenance_prefix
        self.kg_calls = 0

    def expand(
        self,
        entity: str,
        relation: str,
        direction: Direction | str,
        *,
        recorded: Optional[Sequence[Any]] = None,
        provenance_ref: Optional[str] = None,
    ) -> EnvironmentResult:
        ref = provenance_ref or f"{self.provenance_prefix}:expand:{entity}:{relation}"
        try:
            parsed_direction = Direction(direction) if not isinstance(direction, Direction) else direction
        except ValueError:
            return _schema_error(f"invalid direction {direction!r}", ref)
        if not entity or not relation:
            return _schema_error("EXPAND requires entity and relation", ref)
        try:
            raw = recorded
            delta = 1
            if raw is None:
                if self.executor is None:
                    return _schema_error("no fixture/recorded I/O provided; live KG is forbidden", ref)
                raw = self._call_executor(
                    "entity_search",
                    entity=entity,
                    relation=relation,
                    head=direction_to_pog_head(parsed_direction),
                )
                self.kg_calls += 1
            else:
                self.kg_calls += 1
            return self._result_from_raw(entity, relation, parsed_direction, raw, delta, ref)
        except KgTimeout as exc:
            return EnvironmentResult(
                status=EnvironmentStatus.TIMEOUT,
                results=[],
                kg_call_delta=1,
                failure_class=FailureClass.SYSTEM_FAILURE,
                error_code="KG_TIMEOUT",
                message=str(exc) or "KG timeout",
                provenance_ref=ref,
            )
        except MalformedKgResponse as exc:
            return EnvironmentResult(
                status=EnvironmentStatus.MALFORMED,
                results=[],
                kg_call_delta=1,
                failure_class=FailureClass.SYSTEM_FAILURE,
                error_code="MALFORMED_KG_RESPONSE",
                message=str(exc),
                provenance_ref=ref,
            )
        except Exception as exc:
            return EnvironmentResult(
                status=EnvironmentStatus.SYSTEM_ERROR,
                results=[],
                kg_call_delta=1,
                failure_class=FailureClass.SYSTEM_FAILURE,
                error_code="UNHANDLED_ENVIRONMENT_EXCEPTION",
                message=str(exc),
                provenance_ref=ref,
                traceback_text=traceback.format_exc(),
            )

    def enumerate_relations(
        self,
        entity: str,
        *,
        head_relations: Optional[Sequence[str]] = None,
        tail_relations: Optional[Sequence[str]] = None,
        provenance_ref: Optional[str] = None,
    ) -> EnvironmentResult:
        """Environment candidate boundary. Does not call LLM."""
        ref = provenance_ref or f"{self.provenance_prefix}:relations:{entity}"
        if not entity:
            return _schema_error("relation enumeration requires entity", ref)
        try:
            heads = list(head_relations) if head_relations is not None else None
            tails = list(tail_relations) if tail_relations is not None else None
            delta = 0
            if heads is None or tails is None:
                if self.executor is None:
                    return _schema_error("no fixture relations; live KG is forbidden", ref)
                if heads is None:
                    heads = list(self._call_executor("head_relations", entity=entity) or [])
                    delta += 1
                if tails is None:
                    tails = list(self._call_executor("tail_relations", entity=entity) or [])
                    delta += 1
                self.kg_calls += delta
            if any(not isinstance(item, str) or not item for item in heads + tails):
                raise MalformedKgResponse("relation list contains a non-string")
            visible = []
            seen = set()
            for rel in heads:
                item = VisibleRelation(entity=entity, relation=rel, direction=Direction.HEAD)
                if item.key() in seen:
                    continue
                seen.add(item.key())
                visible.append(item)
            for rel in tails:
                item = VisibleRelation(entity=entity, relation=rel, direction=Direction.TAIL)
                if item.key() in seen:
                    continue
                seen.add(item.key())
                visible.append(item)
            visible.sort(key=lambda item: (item.entity, item.relation, item.direction.value))
            results = [item.to_dict() for item in visible]
            status = EnvironmentStatus.EMPTY_SUCCESS if not results else EnvironmentStatus.SUCCESS
            return EnvironmentResult(
                status=status,
                results=results,
                kg_call_delta=delta if delta else 2,
                failure_class=None,
                error_code=None,
                message="ok",
                provenance_ref=ref,
                raw_targets=heads + tails,
            )
        except KgTimeout as exc:
            return EnvironmentResult(
                status=EnvironmentStatus.TIMEOUT,
                results=[],
                kg_call_delta=1,
                failure_class=FailureClass.SYSTEM_FAILURE,
                error_code="KG_TIMEOUT",
                message=str(exc) or "KG timeout",
                provenance_ref=ref,
            )
        except MalformedKgResponse as exc:
            return EnvironmentResult(
                status=EnvironmentStatus.MALFORMED,
                results=[],
                kg_call_delta=1,
                failure_class=FailureClass.SYSTEM_FAILURE,
                error_code="MALFORMED_KG_RESPONSE",
                message=str(exc),
                provenance_ref=ref,
            )
        except Exception as exc:
            return EnvironmentResult(
                status=EnvironmentStatus.SYSTEM_ERROR,
                results=[],
                kg_call_delta=1,
                failure_class=FailureClass.SYSTEM_FAILURE,
                error_code="UNHANDLED_ENVIRONMENT_EXCEPTION",
                message=str(exc),
                provenance_ref=ref,
                traceback_text=traceback.format_exc(),
            )

    def _call_executor(self, kind: str, **params: Any) -> Any:
        assert self.executor is not None
        return self.executor(kind, **params)

    def _result_from_raw(
        self,
        entity: str,
        relation: str,
        direction: Direction,
        raw: Any,
        kg_call_delta: int,
        ref: str,
    ) -> EnvironmentResult:
        filtered, had_duplicate, had_literal = classify_expand_targets(raw)
        triples = triples_from_expand(entity, relation, direction, filtered)
        if not triples:
            return EnvironmentResult(
                status=EnvironmentStatus.EMPTY_SUCCESS,
                results=[],
                kg_call_delta=kg_call_delta,
                failure_class=None,
                error_code=None,
                message="empty expansion",
                provenance_ref=ref,
                raw_targets=list(filtered),
            )
        if had_duplicate:
            status = EnvironmentStatus.DUPLICATE
        elif had_literal:
            status = EnvironmentStatus.LITERAL
        else:
            status = EnvironmentStatus.SUCCESS
        return EnvironmentResult(
            status=status,
            results=triples,
            kg_call_delta=kg_call_delta,
            failure_class=None,
            error_code=None,
            message="ok",
            provenance_ref=ref,
            raw_targets=list(filtered),
        )


def expand_action_to_pog_params(action_params: Dict[str, Any]) -> Dict[str, Any]:
    entity = action_params.get("entity")
    relation = action_params.get("relation")
    direction = action_params.get("direction")
    return {
        "entity": entity,
        "relation": relation,
        "head": direction_to_pog_head(direction),
    }


def pog_params_to_expand_action(entity: str, relation: str, head: bool) -> Dict[str, Any]:
    return {
        "entity": entity,
        "relation": relation,
        "direction": pog_head_to_direction(head).value,
    }


def environment_result_hash(result: EnvironmentResult) -> str:
    payload = result.to_dict()
    payload.pop("traceback_text", None)
    return canonical_hash(payload)
