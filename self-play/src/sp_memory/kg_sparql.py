"""Read-only SPARQL request builder and HTTP client for SP2-A.

SPARQL templates are frozen copies of self-play/freebase_func.py entity_search
queries. This module does not import that file (it has LLM and other side effects)
and does not send SPARQL Update or write requests.
"""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

from .environment_binding import direction_to_pog_head
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_text
from .schemas import Direction

FREEBASE_NS = "http://rdf.freebase.com/ns/"
QUERY_KIND_ENTITY_SEARCH = "entity_search"
QUERY_KIND_CONNECTIVITY = "connectivity"
QUERY_KIND_RELATION_SEARCH = "relation_search"
QUERY_KIND_NAME_LOOKUP = "name_lookup"
HTTP_METHOD = "POST"

# Exact copies of freebase_func.py templates used by entity_search.
SPARQL_TAIL_ENTITIES = (
    "PREFIX ns: <http://rdf.freebase.com/ns/>\n"
    "SELECT ?tailEntity\n"
    "WHERE {\n"
    "ns:%s ns:%s ?tailEntity .\n"
    "}"
)
SPARQL_HEAD_ENTITIES = (
    "PREFIX ns: <http://rdf.freebase.com/ns/>\n"
    "SELECT ?tailEntity\n"
    "WHERE {\n"
    "?tailEntity ns:%s ns:%s  .\n"
    "}"
)
SPARQL_CONNECTIVITY = (
    "PREFIX ns: <http://rdf.freebase.com/ns/>\n"
    "SELECT ?tailEntity WHERE {\n"
    "  ns:m.02mjmr ns:type.object.name ?tailEntity .\n"
    "} LIMIT 5"
)
# Exact copies of freebase_func.py relation-list and name-lookup templates.
SPARQL_HEAD_RELATIONS = (
    "\nPREFIX ns: <http://rdf.freebase.com/ns/>\n"
    "SELECT DISTINCT ?relation\n"
    "WHERE {\n"
    "  ns:%s ?relation ?x .\n"
    "}"
)
SPARQL_TAIL_RELATIONS = (
    "\nPREFIX ns: <http://rdf.freebase.com/ns/>\n"
    "SELECT DISTINCT ?relation\n"
    "WHERE {\n"
    "  ?x ?relation ns:%s .\n"
    "}"
)
SPARQL_ID = (
    "PREFIX ns: <http://rdf.freebase.com/ns/>\n"
    "SELECT DISTINCT ?tailEntity\n"
    "WHERE {\n"
    "  {\n"
    "    ?entity ns:type.object.name ?tailEntity .\n"
    "    FILTER(?entity = ns:%s)\n"
    "  }\n"
    "  UNION\n"
    "  {\n"
    "    ?entity <http://www.w3.org/2002/07/owl#sameAs> ?tailEntity .\n"
    "    FILTER(?entity = ns:%s)\n"
    "  }\n"
    "}"
)

WRITE_SPARQL_RE = re.compile(
    r"(?is)\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|COPY|MOVE|MODIFY|UPDATE)\b"
)
SELECT_OR_ASK_RE = re.compile(r"(?is)^\s*(PREFIX\b.*\b)?(SELECT|ASK)\b")


class PhysicalStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty_result"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    ENDPOINT_FAILURE = "endpoint_failure"
    INVALID_REQUEST = "invalid_request"


@dataclass
class BuiltRequest:
    query_kind: str
    entity: Optional[str]
    relation: Optional[str]
    direction: Optional[str]
    head: Optional[bool]
    sparql: str
    endpoint: str
    method: str
    request_hash: str
    params_summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_kind": self.query_kind,
            "entity": self.entity,
            "relation": self.relation,
            "direction": self.direction,
            "head": self.head,
            "sparql": self.sparql,
            "endpoint": self.endpoint,
            "method": self.method,
            "request_hash": self.request_hash,
            "params_summary": dict(self.params_summary),
        }


@dataclass
class NormalizedTarget:
    value: str
    source_location: str
    binding_index: int
    term_type: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "source_location": self.source_location,
            "binding_index": self.binding_index,
            "term_type": self.term_type,
        }


@dataclass
class PhysicalExchange:
    physical_request_id: str
    logical_action_id: str
    request: BuiltRequest
    retry_index: int
    status: PhysicalStatus
    http_status: Optional[int]
    response_hash: str
    elapsed_ms: float
    bindings: List[Dict[str, Any]] = field(default_factory=list)
    targets: List[NormalizedTarget] = field(default_factory=list)
    error_message: str = ""
    truncated: bool = False
    network_used: bool = False

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "physical_request_id": self.physical_request_id,
            "logical_action_id": self.logical_action_id,
            "retry_index": self.retry_index,
            "status": self.status.value,
            "http_status": self.http_status,
            "response_hash": self.response_hash,
            "elapsed_ms": self.elapsed_ms,
            "request": self.request.to_dict(),
            "target_count": len(self.targets),
            "targets": [item.to_dict() for item in self.targets],
            "error_message": self.error_message,
            "truncated": self.truncated,
            "network_used": self.network_used,
            "binding_count": len(self.bindings),
        }


@dataclass
class RawHttpResponse:
    http_status: int
    body: str
    content_type: str = ""


class TransportError(Exception):
    pass


class TransportTimeout(TransportError):
    pass


class TransportEndpointFailure(TransportError):
    pass


def redact_endpoint(url: str) -> str:
    parts = urlsplit(url)
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def assert_readonly_sparql(sparql: str) -> None:
    text = sparql.strip()
    if WRITE_SPARQL_RE.search(text):
        raise ProtocolError(
            ViolationCode.SCHEMA_ERROR,
            "refusing SPARQL write/update keyword",
            {"snippet": text[:120]},
        )
    if not re.search(r"(?is)\b(SELECT|ASK)\b", text):
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, "SPARQL must be SELECT or ASK")


def strip_freebase_prefix(value: str) -> str:
    return value.replace(FREEBASE_NS, "")


def _request_payload(built: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "endpoint": built["endpoint"],
        "method": built["method"],
        "query_kind": built["query_kind"],
        "entity": built.get("entity"),
        "relation": built.get("relation"),
        "direction": built.get("direction"),
        "head": built.get("head"),
        "sparql": built["sparql"],
    }


def hash_request(payload: Mapping[str, Any]) -> str:
    return canonical_hash(_request_payload(payload))


def build_entity_search_request(
    entity: str,
    relation: str,
    direction: Direction | str,
    *,
    endpoint: str,
) -> BuiltRequest:
    parsed = Direction(direction) if not isinstance(direction, Direction) else direction
    head = direction_to_pog_head(parsed)
    if head:
        sparql = SPARQL_TAIL_ENTITIES % (entity, relation)
    else:
        sparql = SPARQL_HEAD_ENTITIES % (relation, entity)
    assert_readonly_sparql(sparql)
    clean_endpoint = redact_endpoint(endpoint)
    params = {
        "entity": entity,
        "relation": relation,
        "direction": parsed.value,
        "head": head,
        "variable": "tailEntity",
    }
    payload = {
        "endpoint": clean_endpoint,
        "method": HTTP_METHOD,
        "query_kind": QUERY_KIND_ENTITY_SEARCH,
        "entity": entity,
        "relation": relation,
        "direction": parsed.value,
        "head": head,
        "sparql": sparql,
    }
    return BuiltRequest(
        query_kind=QUERY_KIND_ENTITY_SEARCH,
        entity=entity,
        relation=relation,
        direction=parsed.value,
        head=head,
        sparql=sparql,
        endpoint=clean_endpoint,
        method=HTTP_METHOD,
        request_hash=hash_request(payload),
        params_summary=params,
    )


def build_connectivity_request(*, endpoint: str) -> BuiltRequest:
    sparql = SPARQL_CONNECTIVITY
    assert_readonly_sparql(sparql)
    clean_endpoint = redact_endpoint(endpoint)
    payload = {
        "endpoint": clean_endpoint,
        "method": HTTP_METHOD,
        "query_kind": QUERY_KIND_CONNECTIVITY,
        "entity": "m.02mjmr",
        "relation": "type.object.name",
        "direction": Direction.HEAD.value,
        "head": True,
        "sparql": sparql,
    }
    return BuiltRequest(
        query_kind=QUERY_KIND_CONNECTIVITY,
        entity="m.02mjmr",
        relation="type.object.name",
        direction=Direction.HEAD.value,
        head=True,
        sparql=sparql,
        endpoint=clean_endpoint,
        method=HTTP_METHOD,
        request_hash=hash_request(payload),
        params_summary={"limit": 5, "variable": "tailEntity"},
    )


def build_relation_search_request(
    entity: str,
    direction: Direction | str,
    *,
    endpoint: str,
) -> BuiltRequest:
    parsed = Direction(direction) if not isinstance(direction, Direction) else direction
    if parsed is Direction.HEAD:
        sparql = SPARQL_HEAD_RELATIONS % (entity,)
        head = True
    elif parsed is Direction.TAIL:
        sparql = SPARQL_TAIL_RELATIONS % (entity,)
        head = False
    else:
        raise ProtocolError(ViolationCode.INVALID_DIRECTION, f"unsupported direction {direction!r}")
    assert_readonly_sparql(sparql)
    clean_endpoint = redact_endpoint(endpoint)
    params = {
        "entity": entity,
        "relation": None,
        "direction": parsed.value,
        "head": head,
        "variable": "relation",
    }
    payload = {
        "endpoint": clean_endpoint,
        "method": HTTP_METHOD,
        "query_kind": QUERY_KIND_RELATION_SEARCH,
        "entity": entity,
        "relation": None,
        "direction": parsed.value,
        "head": head,
        "sparql": sparql,
    }
    return BuiltRequest(
        query_kind=QUERY_KIND_RELATION_SEARCH,
        entity=entity,
        relation=None,
        direction=parsed.value,
        head=head,
        sparql=sparql,
        endpoint=clean_endpoint,
        method=HTTP_METHOD,
        request_hash=hash_request(payload),
        params_summary=params,
    )


def build_name_lookup_request(entity: str, *, endpoint: str) -> BuiltRequest:
    sparql = SPARQL_ID % (entity, entity)
    assert_readonly_sparql(sparql)
    clean_endpoint = redact_endpoint(endpoint)
    params = {
        "entity": entity,
        "relation": "type.object.name",
        "direction": Direction.HEAD.value,
        "head": True,
        "variable": "tailEntity",
    }
    payload = {
        "endpoint": clean_endpoint,
        "method": HTTP_METHOD,
        "query_kind": QUERY_KIND_NAME_LOOKUP,
        "entity": entity,
        "relation": "type.object.name",
        "direction": Direction.HEAD.value,
        "head": True,
        "sparql": sparql,
    }
    return BuiltRequest(
        query_kind=QUERY_KIND_NAME_LOOKUP,
        entity=entity,
        relation="type.object.name",
        direction=Direction.HEAD.value,
        head=True,
        sparql=sparql,
        endpoint=clean_endpoint,
        method=HTTP_METHOD,
        request_hash=hash_request(payload),
        params_summary=params,
    )


def original_templates_from_source(source_text: str) -> Dict[str, str]:
    import ast

    found = {}
    for name in ("sparql_tail_entities_extract", "sparql_head_entities_extract"):
        match = re.search(
            rf'{name}\s*=\s*(""".*?""")',
            source_text,
            flags=re.DOTALL,
        )
        if not match:
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"missing {name} in freebase_func.py")
        found[name] = ast.literal_eval(match.group(1))
    return found


def templates_match_original(source_text: str) -> Dict[str, Any]:
    original = original_templates_from_source(source_text)
    return {
        "tail_entities_match": original["sparql_tail_entities_extract"] == SPARQL_TAIL_ENTITIES,
        "head_entities_match": original["sparql_head_entities_extract"] == SPARQL_HEAD_ENTITIES,
        "original_tail": original["sparql_tail_entities_extract"],
        "original_head": original["sparql_head_entities_extract"],
    }


def normalize_bindings(bindings: Any, variable: str = "tailEntity") -> List[NormalizedTarget]:
    if not isinstance(bindings, list):
        raise MalformedSparql("results.bindings is not a list")
    if not variable:
        raise MalformedSparql("binding variable is empty")
    targets: List[NormalizedTarget] = []
    for index, item in enumerate(bindings):
        if not isinstance(item, dict):
            raise MalformedSparql(f"binding {index} is not an object")
        if variable not in item:
            raise MalformedSparql(f"binding {index} missing {variable}")
        node = item[variable]
        if not isinstance(node, dict) or "value" not in node:
            raise MalformedSparql(f"binding {index} {variable} missing value")
        raw = node["value"]
        if not isinstance(raw, str) or not raw:
            raise MalformedSparql(f"binding {index} {variable} value is not a non-empty string")
        term_type = str(node.get("type") or "unknown")
        targets.append(
            NormalizedTarget(
                value=strip_freebase_prefix(raw),
                source_location=f"results.bindings[{index}].{variable}.value",
                binding_index=index,
                term_type=term_type,
            )
        )
    return targets


def parse_sparql_json(body: str) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MalformedSparql(f"response is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MalformedSparql("SPARQL JSON root is not an object")
    results = payload.get("results")
    if not isinstance(results, dict):
        raise MalformedSparql("SPARQL JSON missing results object")
    bindings = results.get("bindings")
    if not isinstance(bindings, list):
        raise MalformedSparql("SPARQL JSON missing results.bindings list")
    return bindings


class MalformedSparql(ValueError):
    pass


class UrllibTransport:
    """POST application/x-www-form-urlencoded SPARQL query. No cookies, no auth headers."""

    def post(self, url: str, data: bytes, headers: Mapping[str, str], timeout: float) -> RawHttpResponse:
        request = urllib.request.Request(url, data=data, method="POST")
        for key, value in headers.items():
            if key.lower() in {"authorization", "cookie", "set-cookie", "proxy-authorization"}:
                raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"refusing secret HTTP header {key}")
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as handle:
                body = handle.read().decode("utf-8", errors="replace")
                content_type = handle.headers.get("Content-Type", "")
                return RawHttpResponse(http_status=getattr(handle, "status", 200), body=body, content_type=content_type)
        except socket.timeout as exc:
            raise TransportTimeout(str(exc) or "socket timeout") from exc
        except TimeoutError as exc:
            raise TransportTimeout(str(exc) or "timeout") from exc
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            if exc.code >= 500:
                raise TransportEndpointFailure(f"HTTP {exc.code}") from exc
            return RawHttpResponse(http_status=exc.code, body=body, content_type=exc.headers.get("Content-Type", "") if exc.headers else "")
        except urllib.error.URLError as exc:
            raise TransportEndpointFailure(str(exc.reason) if getattr(exc, "reason", None) else str(exc)) from exc


class ScriptedTransport:
    """Deterministic fault injection. Outcomes are RawHttpResponse or TransportError instances."""

    def __init__(self, outcomes: Sequence[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def post(self, url: str, data: bytes, headers: Mapping[str, str], timeout: float) -> RawHttpResponse:
        del url, data, headers, timeout
        if self.calls >= len(self.outcomes):
            raise TransportEndpointFailure("scripted transport exhausted")
        item = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        if not isinstance(item, RawHttpResponse):
            raise TransportEndpointFailure("scripted transport produced a non-response")
        return item


def encode_query(sparql: str) -> bytes:
    return urllib.parse.urlencode({"query": sparql, "format": "json"}).encode("utf-8")


def _physical_id(request_hash: str, logical_action_id: str, retry_index: int) -> str:
    return "phys-" + canonical_hash(
        {"request_hash": request_hash, "logical_action_id": logical_action_id, "retry_index": retry_index}
    )[:16]


class LiveSparqlClient:
    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: Sequence[str],
        timeout_sec: float,
        max_retries: int,
        retry_backoff_sec: Sequence[float],
        transport: Any = None,
        network_enabled: bool = True,
        max_recorded_bindings: int = 200,
    ) -> None:
        self.endpoint = redact_endpoint(endpoint)
        allowed = [redact_endpoint(item) for item in allowed_endpoints]
        if self.endpoint not in allowed:
            raise ProtocolError(
                ViolationCode.SCHEMA_ERROR,
                "endpoint is not in the allowlist",
                {"endpoint": self.endpoint, "allowed": allowed},
            )
        if urlsplit(self.endpoint).username or urlsplit(endpoint).password:
            raise ProtocolError(ViolationCode.SCHEMA_ERROR, "endpoint URL must not contain credentials")
        self.allowed_endpoints = allowed
        self.timeout_sec = float(timeout_sec)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_sec = [float(item) for item in retry_backoff_sec]
        self.transport = transport or UrllibTransport()
        self.network_enabled = bool(network_enabled)
        self.max_recorded_bindings = int(max_recorded_bindings)
        self.physical_calls = 0
        self.write_attempts = 0

    def execute(
        self,
        request: BuiltRequest,
        *,
        logical_action_id: str,
        retry_index: int = 0,
    ) -> PhysicalExchange:
        if request.endpoint != self.endpoint:
            raise ProtocolError(
                ViolationCode.SCHEMA_ERROR,
                "request endpoint does not match client endpoint",
                {"request_endpoint": request.endpoint, "client_endpoint": self.endpoint},
            )
        assert_readonly_sparql(request.sparql)
        if not self.network_enabled:
            raise ProtocolError(
                ViolationCode.REPLAY_ERROR,
                "live SPARQL client network is disabled; replay required",
            )
        headers = {
            "Accept": "application/sparql-results+json, application/json;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        started = time.monotonic()
        self.physical_calls += 1
        try:
            raw = self.transport.post(
                self.endpoint,
                encode_query(request.sparql),
                headers,
                self.timeout_sec,
            )
        except TransportTimeout as exc:
            elapsed = (time.monotonic() - started) * 1000.0
            return self._failed_exchange(
                request,
                logical_action_id,
                retry_index,
                PhysicalStatus.TIMEOUT,
                None,
                elapsed,
                str(exc),
            )
        except TransportEndpointFailure as exc:
            elapsed = (time.monotonic() - started) * 1000.0
            return self._failed_exchange(
                request,
                logical_action_id,
                retry_index,
                PhysicalStatus.ENDPOINT_FAILURE,
                None,
                elapsed,
                str(exc),
            )
        elapsed = (time.monotonic() - started) * 1000.0
        body_hash = sha256_text(raw.body)
        if raw.http_status >= 500:
            return self._failed_exchange(
                request,
                logical_action_id,
                retry_index,
                PhysicalStatus.ENDPOINT_FAILURE,
                raw.http_status,
                elapsed,
                f"HTTP {raw.http_status}",
                response_hash=body_hash,
            )
        if raw.http_status >= 400:
            return self._failed_exchange(
                request,
                logical_action_id,
                retry_index,
                PhysicalStatus.ENDPOINT_FAILURE,
                raw.http_status,
                elapsed,
                f"HTTP {raw.http_status}",
                response_hash=body_hash,
            )
        try:
            bindings = parse_sparql_json(raw.body)
            variable = str(request.params_summary.get("variable") or "tailEntity")
            targets = normalize_bindings(bindings, variable=variable)
        except MalformedSparql as exc:
            return self._failed_exchange(
                request,
                logical_action_id,
                retry_index,
                PhysicalStatus.MALFORMED_RESPONSE,
                raw.http_status,
                elapsed,
                str(exc),
                response_hash=body_hash,
            )
        truncated = len(bindings) > self.max_recorded_bindings
        stored_bindings = bindings[: self.max_recorded_bindings]
        stored_targets = targets[: self.max_recorded_bindings]
        status = PhysicalStatus.EMPTY if not targets else PhysicalStatus.SUCCESS
        return PhysicalExchange(
            physical_request_id=_physical_id(request.request_hash, logical_action_id, retry_index),
            logical_action_id=logical_action_id,
            request=request,
            retry_index=retry_index,
            status=status,
            http_status=raw.http_status,
            response_hash=body_hash,
            elapsed_ms=elapsed,
            bindings=stored_bindings,
            targets=stored_targets,
            truncated=truncated,
            network_used=True,
        )

    def _failed_exchange(
        self,
        request: BuiltRequest,
        logical_action_id: str,
        retry_index: int,
        status: PhysicalStatus,
        http_status: Optional[int],
        elapsed_ms: float,
        message: str,
        response_hash: str = "",
    ) -> PhysicalExchange:
        return PhysicalExchange(
            physical_request_id=_physical_id(request.request_hash, logical_action_id, retry_index),
            logical_action_id=logical_action_id,
            request=request,
            retry_index=retry_index,
            status=status,
            http_status=http_status,
            response_hash=response_hash or sha256_text(""),
            elapsed_ms=elapsed_ms,
            error_message=message,
            network_used=True,
        )


def logical_action_id(task_id: str, step_id: str, action_type: str, params: Mapping[str, Any]) -> str:
    return "log-" + canonical_hash(
        {
            "task_id": task_id,
            "step_id": step_id,
            "action_type": action_type,
            "params": dict(params),
        }
    )[:16]


def retry_with_backoff(
    client: LiveSparqlClient,
    request: BuiltRequest,
    *,
    logical_action_id: str,
) -> List[PhysicalExchange]:
    """Run one logical query with retries. Does not retry malformed JSON after HTTP 200."""
    exchanges: List[PhysicalExchange] = []
    attempts = 1 + client.max_retries
    for retry_index in range(attempts):
        if retry_index > 0:
            delay = 0.0
            if retry_index - 1 < len(client.retry_backoff_sec):
                delay = client.retry_backoff_sec[retry_index - 1]
            if delay > 0:
                time.sleep(delay)
        exchange = client.execute(request, logical_action_id=logical_action_id, retry_index=retry_index)
        exchanges.append(exchange)
        if exchange.status in {PhysicalStatus.SUCCESS, PhysicalStatus.EMPTY, PhysicalStatus.MALFORMED_RESPONSE, PhysicalStatus.INVALID_REQUEST}:
            break
    return exchanges
