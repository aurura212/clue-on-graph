"""Dynamic hop-2 action materialization and TAIL-positive assertions for SP2-A supplement.

Reuses existing SP1 adapter and SP2-A live Environment binding. Does not call an LLM
and does not read or write memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .environment_binding import direction_to_pog_head, is_finish_marker, triples_from_expand
from .errors import ProtocolError, ViolationCode
from .kg_sparql import SPARQL_HEAD_ENTITIES, SPARQL_TAIL_ENTITIES, build_entity_search_request
from .pog_adapter import PoGSnapshot
from .schemas import Action, ActionType, ActorRole, Direction, VisibleRelation, VisibleState

ORACLE_FIELDS = (
    "answer_entity_ids",
    "normalized_answers",
    "witness_paths",
    "logical_query",
    "gold_path",
    "oracle_version",
)
SELECTION_RULE_FIRST_SORTED = "sort_canonical_entity_id_first"
ALLOWED_PURPOSES = {"TAIL_positive", "dynamic_twohop"}


def is_kg_entity_id(value: str) -> bool:
    return bool(value) and (value.startswith("m.") or value.startswith("g."))


def extract_canonical_entities(
    direction: Direction | str,
    triples: Sequence[Mapping[str, str]],
) -> List[str]:
    parsed = Direction(direction) if not isinstance(direction, Direction) else direction
    found: List[str] = []
    seen = set()
    for triple in triples:
        node = triple["object"] if parsed is Direction.HEAD else triple["subject"]
        if is_finish_marker(node) or not is_kg_entity_id(node):
            continue
        if node in seen:
            continue
        seen.add(node)
        found.append(node)
    return found


def select_hop1_entity(entities: Sequence[str], rule: str) -> Optional[str]:
    if rule != SELECTION_RULE_FIRST_SORTED:
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, f"unsupported hop1 selection rule {rule}")
    if not entities:
        return None
    return sorted(entities)[0]


def hop1_binding_index(direction: Direction | str, triples: Sequence[Mapping[str, str]], entity: str) -> int:
    parsed = Direction(direction) if not isinstance(direction, Direction) else direction
    for index, triple in enumerate(triples):
        node = triple["object"] if parsed is Direction.HEAD else triple["subject"]
        if node == entity:
            return index
    return -1


def assert_tail_request(entity: str, relation: str, *, endpoint: str) -> Dict[str, Any]:
    built = build_entity_search_request(entity, relation, Direction.TAIL, endpoint=endpoint)
    expected_sparql = SPARQL_HEAD_ENTITIES % (relation, entity)
    ok = (
        built.head is False
        and direction_to_pog_head(Direction.TAIL) is False
        and built.sparql == expected_sparql
        and f"?tailEntity ns:{relation} ns:{entity}" in built.sparql.replace("  ", " ")
    )
    if not ok:
        raise ProtocolError(
            ViolationCode.INVALID_DIRECTION,
            "TAIL request did not use the reverse SPARQL template",
            {"entity": entity, "relation": relation, "sparql": built.sparql},
        )
    return {"ok": True, "request_hash": built.request_hash, "sparql": built.sparql, "head": built.head}


def assert_head_request(entity: str, relation: str, *, endpoint: str) -> Dict[str, Any]:
    built = build_entity_search_request(entity, relation, Direction.HEAD, endpoint=endpoint)
    expected_sparql = SPARQL_TAIL_ENTITIES % (entity, relation)
    ok = built.head is True and built.sparql == expected_sparql
    if not ok:
        raise ProtocolError(
            ViolationCode.INVALID_DIRECTION,
            "HEAD request did not use the forward SPARQL template",
            {"entity": entity, "relation": relation, "sparql": built.sparql},
        )
    return {"ok": True, "request_hash": built.request_hash, "sparql": built.sparql, "head": built.head}


def assert_tail_positive_triples(
    triples: Sequence[Mapping[str, str]],
    *,
    query_entity: str,
    relation: str,
    expected_subjects: Sequence[str],
) -> Dict[str, Any]:
    reconstructed = triples_from_expand(
        query_entity,
        relation,
        Direction.TAIL,
        [item["subject"] for item in triples],
    )
    subjects = [item["subject"] for item in triples]
    direction_ok = all(item["object"] == query_entity and item["relation"] == relation for item in triples)
    reconstructed_ok = reconstructed == list(triples) or (
        {(item["subject"], item["relation"], item["object"]) for item in reconstructed}
        == {(item["subject"], item["relation"], item["object"]) for item in triples}
    )
    missing = [item for item in expected_subjects if item not in subjects]
    return {
        "direction_ok": direction_ok,
        "reconstructed_ok": reconstructed_ok,
        "missing_expected_subjects": missing,
        "subjects": subjects,
        "ok": bool(triples) and direction_ok and reconstructed_ok and not missing,
    }


def validate_supplement_registry(payload: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if payload.get("sampled_from_eval_sets") is not False:
        errors.append("supplement registry must set sampled_from_eval_sets=false")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("supplement registry must contain a non-empty tasks list")
        return errors
    seen = set()
    purposes = set()
    for task in tasks:
        if not isinstance(task, Mapping):
            errors.append("task is not an object")
            continue
        task_id = str(task.get("task_id") or "")
        if not task_id:
            errors.append("task missing task_id")
            continue
        if task_id in seen:
            errors.append(f"duplicate task_id {task_id}")
        seen.add(task_id)
        leak = [name for name in ORACLE_FIELDS if name in task]
        if leak:
            errors.append(f"{task_id} contains Oracle field {leak}")
        purpose = task.get("query_purpose")
        if purpose not in ALLOWED_PURPOSES:
            errors.append(f"{task_id} query_purpose must be TAIL_positive or dynamic_twohop")
            continue
        purposes.add(purpose)
        if task.get("sampled_from_eval_sets") is True:
            errors.append(f"{task_id} must be independent of eval sets")
        if purpose == "TAIL_positive":
            if task.get("direction") != "tail":
                errors.append(f"{task_id} TAIL_positive must use direction=tail")
            edge = task.get("known_edge") or {}
            if edge.get("object") != task.get("entity") or edge.get("relation") != task.get("relation"):
                errors.append(f"{task_id} known_edge must match query entity/relation")
            if not task.get("expected_subjects"):
                errors.append(f"{task_id} missing expected_subjects")
            steps = task.get("steps") or []
            if any(step.get("type") == "EXPAND" and step.get("direction") != "tail" for step in steps):
                errors.append(f"{task_id} steps must remain TAIL")
        elif purpose == "dynamic_twohop":
            hop1 = task.get("hop1") or {}
            hop2 = task.get("hop2") or {}
            if not hop1.get("entity") or not hop1.get("relation") or not hop1.get("direction"):
                errors.append(f"{task_id} hop1 must freeze entity/relation/direction")
            if hop2.get("entity"):
                errors.append(f"{task_id} hop2 must not freeze entity")
            if not hop2.get("relation") or not hop2.get("direction"):
                errors.append(f"{task_id} hop2 must freeze relation and direction")
            constraint = task.get("hop1_candidate_constraint") or {}
            if constraint.get("selection_rule") != SELECTION_RULE_FIRST_SORTED:
                errors.append(f"{task_id} must freeze selection_rule={SELECTION_RULE_FIRST_SORTED}")
            steps = task.get("steps") or []
            if len(steps) != 1:
                errors.append(f"{task_id} registry may freeze only hop1; found {len(steps)} steps")
            elif steps[0].get("entity") != hop1.get("entity"):
                errors.append(f"{task_id} hop1 step entity mismatch")
    if "TAIL_positive" not in purposes:
        errors.append("registry must include at least one TAIL_positive task")
    if "dynamic_twohop" not in purposes:
        errors.append("registry must include at least one dynamic_twohop task")
    return errors


@dataclass
class MaterializedHop2:
    hop2_entity: str
    hop1_state_id: str
    hop1_binding_index: int
    hop1_source: str
    hop2_relation: str
    hop2_direction: str
    request_hash: str
    snapshot: PoGSnapshot
    action_params: Dict[str, str]
    skipped_reason: Optional[str] = None

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "hop2_entity": self.hop2_entity,
            "hop1_state_id": self.hop1_state_id,
            "hop1_binding_index": self.hop1_binding_index,
            "hop1_source": self.hop1_source,
            "hop2_relation": self.hop2_relation,
            "hop2_direction": self.hop2_direction,
            "request_hash": self.request_hash,
            "skipped_reason": self.skipped_reason,
        }


def attach_enumerated_relation(snapshot: PoGSnapshot, relation: VisibleRelation) -> PoGSnapshot:
    applied = snapshot.clone()
    keys = {item.key() for item in applied.enumerated_relations}
    if relation.key() not in keys:
        applied.enumerated_relations.append(relation)
    return applied


def materialize_hop2_from_hop1(
    snapshot: PoGSnapshot,
    state: VisibleState,
    hop1_triples: Sequence[Mapping[str, str]],
    *,
    hop1_direction: Direction | str,
    hop2_relation: str,
    hop2_direction: Direction | str,
    selection_rule: str,
    endpoint: str,
    constraint: Optional[Mapping[str, Any]] = None,
) -> Optional[MaterializedHop2]:
    parsed_hop1 = Direction(hop1_direction) if not isinstance(hop1_direction, Direction) else hop1_direction
    parsed_hop2 = Direction(hop2_direction) if not isinstance(hop2_direction, Direction) else hop2_direction
    entities = extract_canonical_entities(parsed_hop1, hop1_triples)
    prefixes = list((constraint or {}).get("must_start_with") or ["m.", "g."])
    entities = [item for item in entities if any(item.startswith(prefix) for prefix in prefixes)]
    selected = select_hop1_entity(entities, selection_rule)
    if selected is None:
        return None
    visible = set(state.visible_entities) | set(state.frontier)
    if selected not in visible:
        raise ProtocolError(
            ViolationCode.INVISIBLE_ENTITY,
            "selected hop1 entity is not in VisibleState frontier/entities",
            {"entity": selected, "state_id": state.state_id},
        )
    binding_index = hop1_binding_index(parsed_hop1, hop1_triples, selected)
    relation = VisibleRelation(entity=selected, relation=hop2_relation, direction=parsed_hop2)
    next_snapshot = attach_enumerated_relation(snapshot, relation)
    built = build_entity_search_request(selected, hop2_relation, parsed_hop2, endpoint=endpoint)
    return MaterializedHop2(
        hop2_entity=selected,
        hop1_state_id=state.state_id,
        hop1_binding_index=binding_index,
        hop1_source="hop1_canonical_entity",
        hop2_relation=hop2_relation,
        hop2_direction=parsed_hop2.value,
        request_hash=built.request_hash,
        snapshot=next_snapshot,
        action_params={
            "entity": selected,
            "relation": hop2_relation,
            "direction": parsed_hop2.value,
        },
    )


def make_expand_action(params: Mapping[str, str], state: VisibleState, action_id: str) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.EXPAND,
        params=dict(params),
        source_role=ActorRole.EXPLORER,
        state_id=state.state_id,
    )


def hop2_entity_matches_hop1(materialized: MaterializedHop2, hop1_entities: Sequence[str]) -> bool:
    return materialized.hop2_entity in set(hop1_entities)
