"""SP2-A live KG Environment binding. Reuses SP1 normalization and state mapping."""

from __future__ import annotations

import traceback
from typing import Any, Dict, List, Optional, Sequence

from .budget_ledger import CounterLedger
from .environment_binding import (
    EnvironmentBinding,
    EnvironmentResult,
    EnvironmentStatus,
    KgTimeout,
    MalformedKgResponse,
    classify_expand_targets,
)
from .errors import ProtocolError, ViolationCode
from .kg_sparql import (
    LiveSparqlClient,
    PhysicalExchange,
    PhysicalStatus,
    build_entity_search_request,
    build_name_lookup_request,
    build_relation_search_request,
    logical_action_id,
    retry_with_backoff,
)
from .recorded_io import exchange_to_record, record_to_exchange
from .schemas import Direction, FailureClass, VisibleRelation

STATUS_MAP = {
    PhysicalStatus.SUCCESS: None,
    PhysicalStatus.EMPTY: None,
    PhysicalStatus.TIMEOUT: EnvironmentStatus.TIMEOUT,
    PhysicalStatus.MALFORMED_RESPONSE: EnvironmentStatus.MALFORMED,
    PhysicalStatus.ENDPOINT_FAILURE: EnvironmentStatus.ENDPOINT_FAILURE,
    PhysicalStatus.INVALID_REQUEST: EnvironmentStatus.SCHEMA_ERROR,
}


class LiveKgBinding(EnvironmentBinding):
    """Live or recorded-I/O expand. Parent constructor still forbids allow_live_kg=True."""

    def __init__(
        self,
        client: LiveSparqlClient,
        ledger: CounterLedger,
        *,
        records: Optional[Dict[str, Dict[str, Any]]] = None,
        task_id: str = "sp2a.unspecified",
        network_enabled: bool = True,
    ) -> None:
        EnvironmentBinding.__init__(self, allow_live_kg=False)
        self.allow_live_kg = True
        self.client = client
        self.ledger = ledger
        self.records = dict(records or {})
        self.task_id = task_id
        self.network_enabled = bool(network_enabled)
        self.exchanges: List[PhysicalExchange] = []
        self.audit_records: List[Dict[str, Any]] = []
        self.step_counter = 0

    def set_task(self, task_id: str) -> None:
        self.task_id = task_id

    def expand(
        self,
        entity: str,
        relation: str,
        direction: Direction | str,
        *,
        recorded: Optional[Sequence[Any]] = None,
        provenance_ref: Optional[str] = None,
    ) -> EnvironmentResult:
        self.step_counter += 1
        step_id = f"s{self.step_counter:04d}"
        ref = provenance_ref or f"live_kg:expand:{entity}:{relation}:{direction}"
        try:
            parsed_direction = Direction(direction) if not isinstance(direction, Direction) else direction
        except ValueError:
            return self._schema(f"invalid direction {direction!r}", ref)
        if not entity or not relation:
            return self._schema("EXPAND requires entity and relation", ref)

        if recorded is not None:
            self.kg_calls += 1
            result = self._result_from_raw(entity, relation, parsed_direction, recorded, 1, ref)
            status = PhysicalStatus.EMPTY if result.status is EnvironmentStatus.EMPTY_SUCCESS else PhysicalStatus.SUCCESS
            action_id = logical_action_id(
                self.task_id,
                step_id,
                "EXPAND",
                {"entity": entity, "relation": relation, "direction": parsed_direction.value, "recorded": True},
            )
            self.ledger.record_logical_with_exchanges(
                task_id=self.task_id,
                logical_action_id=action_id,
                statuses=[status],
            )
            return result

        request = build_entity_search_request(entity, relation, parsed_direction, endpoint=self.client.endpoint)
        action_id = logical_action_id(
            self.task_id,
            step_id,
            "EXPAND",
            {"entity": entity, "relation": relation, "direction": parsed_direction.value},
        )

        try:
            exchanges = self._run_physical(request, action_id)
        except ProtocolError as exc:
            if exc.code is ViolationCode.REPLAY_ERROR:
                return EnvironmentResult(
                    status=EnvironmentStatus.SYSTEM_ERROR,
                    results=[],
                    kg_call_delta=0,
                    failure_class=FailureClass.SYSTEM_FAILURE,
                    error_code="REPLAY_REQUIRED",
                    message=exc.message,
                    provenance_ref=ref,
                )
            raise
        except KgTimeout as exc:
            return EnvironmentResult(
                status=EnvironmentStatus.TIMEOUT,
                results=[],
                kg_call_delta=1,
                failure_class=FailureClass.SYSTEM_FAILURE,
                error_code="KG_TIMEOUT",
                message=str(exc),
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

        final = exchanges[-1]
        self.ledger.record_logical_with_exchanges(
            task_id=self.task_id,
            logical_action_id=action_id,
            statuses=[item.status for item in exchanges],
        )
        for item in exchanges:
            self.exchanges.append(item)
            self.audit_records.append(exchange_to_record(item, task_id=self.task_id, step_id=step_id))
        self.kg_calls += 1
        env_status = STATUS_MAP.get(final.status)
        if env_status is not None:
            failure = FailureClass.SYSTEM_FAILURE
            error_code = final.status.value.upper()
            result = EnvironmentResult(
                status=env_status,
                results=[],
                kg_call_delta=1,
                failure_class=failure,
                error_code=error_code,
                message=final.error_message or final.status.value,
                provenance_ref=ref,
                raw_targets=[],
            )
            return result
        targets = [item.value for item in final.targets]
        # Preserve SPARQL order for classification, then canonical triples sort inside _result_from_raw.
        try:
            classify_expand_targets(targets)
        except MalformedKgResponse:
            raise
        result = self._result_from_raw(entity, relation, parsed_direction, targets, 1, ref)
        result.provenance_ref = f"{ref}|{final.physical_request_id}|{final.response_hash[:16]}"
        return result

    def enumerate_relations(
        self,
        entity: str,
        *,
        head_relations: Optional[Sequence[str]] = None,
        tail_relations: Optional[Sequence[str]] = None,
        provenance_ref: Optional[str] = None,
    ) -> EnvironmentResult:
        ref = provenance_ref or f"live_kg:relations:{entity}"
        if not entity:
            return self._schema("relation enumeration requires entity", ref)
        if head_relations is not None and tail_relations is not None:
            return EnvironmentBinding.enumerate_relations(
                self,
                entity,
                head_relations=head_relations,
                tail_relations=tail_relations,
                provenance_ref=ref,
            )
        heads = list(head_relations) if head_relations is not None else None
        tails = list(tail_relations) if tail_relations is not None else None
        delta = 0
        if heads is None:
            head_result = self._execute_kind(
                "ENUMERATE_RELATIONS",
                build_relation_search_request(entity, Direction.HEAD, endpoint=self.client.endpoint),
                {"entity": entity, "direction": Direction.HEAD.value},
                ref + ":head",
            )
            if head_result.failure_class is not None:
                return head_result
            heads = list(head_result.raw_targets)
            delta += head_result.kg_call_delta
        if tails is None:
            tail_result = self._execute_kind(
                "ENUMERATE_RELATIONS",
                build_relation_search_request(entity, Direction.TAIL, endpoint=self.client.endpoint),
                {"entity": entity, "direction": Direction.TAIL.value},
                ref + ":tail",
            )
            if tail_result.failure_class is not None:
                return tail_result
            tails = list(tail_result.raw_targets)
            delta += tail_result.kg_call_delta
        visible = []
        seen = set()
        for rel in heads:
            if not isinstance(rel, str) or not rel:
                continue
            item = VisibleRelation(entity=entity, relation=rel, direction=Direction.HEAD)
            if item.key() in seen:
                continue
            seen.add(item.key())
            visible.append(item)
        for rel in tails:
            if not isinstance(rel, str) or not rel:
                continue
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
            kg_call_delta=delta,
            failure_class=None,
            error_code=None,
            message="ok",
            provenance_ref=ref,
            raw_targets=heads + tails,
        )

    def lookup_name(self, entity: str, *, provenance_ref: Optional[str] = None) -> EnvironmentResult:
        ref = provenance_ref or f"live_kg:name:{entity}"
        if not entity:
            return self._schema("name lookup requires entity", ref)
        if not (entity.startswith("m.") or entity.startswith("g.")):
            return EnvironmentResult(
                status=EnvironmentStatus.LITERAL,
                results=[],
                kg_call_delta=0,
                failure_class=None,
                error_code=None,
                message="literal identity",
                provenance_ref=ref,
                raw_targets=[entity],
            )
        return self._execute_kind(
            "NAME_LOOKUP",
            build_name_lookup_request(entity, endpoint=self.client.endpoint),
            {"entity": entity},
            ref,
        )

    def _execute_kind(
        self,
        action_type: str,
        request,
        params: Dict[str, Any],
        ref: str,
    ) -> EnvironmentResult:
        self.step_counter += 1
        step_id = f"s{self.step_counter:04d}"
        action_id = logical_action_id(self.task_id, step_id, action_type, params)
        try:
            exchanges = self._run_physical(request, action_id)
        except ProtocolError as exc:
            if exc.code is ViolationCode.REPLAY_ERROR:
                return EnvironmentResult(
                    status=EnvironmentStatus.SYSTEM_ERROR,
                    results=[],
                    kg_call_delta=0,
                    failure_class=FailureClass.SYSTEM_FAILURE,
                    error_code="REPLAY_REQUIRED",
                    message=exc.message,
                    provenance_ref=ref,
                )
            raise
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
        final = exchanges[-1]
        self.ledger.record_logical_with_exchanges(
            task_id=self.task_id,
            logical_action_id=action_id,
            statuses=[item.status for item in exchanges],
        )
        for item in exchanges:
            self.exchanges.append(item)
            self.audit_records.append(exchange_to_record(item, task_id=self.task_id, step_id=step_id))
        self.kg_calls += 1
        env_status = STATUS_MAP.get(final.status)
        if env_status is not None:
            return EnvironmentResult(
                status=env_status,
                results=[],
                kg_call_delta=1,
                failure_class=FailureClass.SYSTEM_FAILURE,
                error_code=final.status.value.upper(),
                message=final.error_message or final.status.value,
                provenance_ref=ref,
                raw_targets=[],
            )
        targets = [item.value for item in final.targets]
        status = EnvironmentStatus.EMPTY_SUCCESS if not targets else EnvironmentStatus.SUCCESS
        return EnvironmentResult(
            status=status,
            results=[{"value": item} for item in targets],
            kg_call_delta=1,
            failure_class=None,
            error_code=None,
            message="ok",
            provenance_ref=f"{ref}|{final.physical_request_id}|{final.response_hash[:16]}",
            raw_targets=targets,
        )

    def _run_physical(self, request, action_id: str) -> List[PhysicalExchange]:
        replay_key = request.request_hash
        if replay_key in self.records and not self.network_enabled:
            exchange = record_to_exchange(self.records[replay_key], endpoint=self.client.endpoint)
            exchange.logical_action_id = action_id
            exchange.network_used = False
            return [exchange]
        if not self.network_enabled:
            raise ProtocolError(ViolationCode.REPLAY_ERROR, f"no recorded I/O for request {replay_key}")
        return retry_with_backoff(self.client, request, logical_action_id=action_id)

    @staticmethod
    def _schema(message: str, ref: str) -> EnvironmentResult:
        return EnvironmentResult(
            status=EnvironmentStatus.SCHEMA_ERROR,
            results=[],
            kg_call_delta=0,
            failure_class=FailureClass.SYSTEM_FAILURE,
            error_code="ADAPTER_SCHEMA_ERROR",
            message=message,
            provenance_ref=ref,
        )
