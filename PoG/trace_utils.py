"""Helpers to build serializable PoG run traces."""

from __future__ import annotations

from typing import Any


def new_run_trace(subquestions: Any, topic_entity: dict[str, str]) -> dict[str, Any]:
    return {
        "subquestions": subquestions,
        "topic_entity": dict(topic_entity),
        "constraints": None,
        "depths": [],
        "final_stop_reason": None,
        "final_stop_depth": None,
    }


def new_depth_record(depth: int, topic_entities: dict[str, str]) -> dict[str, Any]:
    return {
        "depth": depth,
        "topic_entities": {eid: name for eid, name in topic_entities.items()},
        "relation_prune": [],
        "before_entity_prune": {},
        "after_entity_prune": {},
        "entity_prune_details": [],
        "pruned_triples": [],
        "entity_prune_success": False,
        "memory_update": None,
        "evaluation": None,
        "reverse_retrieval": None,
        "stop_reason": None,
    }


def serialize_name_dict(name_dict: dict) -> dict:
    """Convert convert_dict_name output to JSON-safe plain dict."""
    out: dict[str, Any] = {}
    for topic, h_t_dict in sorted(name_dict.items()):
        out[str(topic)] = {}
        for h_t, r_e_dict in sorted(h_t_dict.items()):
            out[str(topic)][str(h_t)] = {}
            for rela, e_list in sorted(r_e_dict.items()):
                out[str(topic)][str(h_t)][str(rela)] = [str(x) for x in e_list]
    return out


def flatten_chain_triples(chain_of_entities: list) -> list[list[str]]:
    triples: list[list[str]] = []
    for chain in chain_of_entities or []:
        for t in chain or []:
            if isinstance(t, (list, tuple)) and len(t) == 3:
                triples.append([str(t[0]), str(t[1]), str(t[2])])
    return triples
