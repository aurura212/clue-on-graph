"""Helpers to build serializable PoG run traces."""

from __future__ import annotations

from typing import Any

from kg_memory_retrieval import attach_kg_memory_relation_events


def empty_kg_memory_scaffold() -> dict[str, dict]:
    return {
        "relation": {},
        "reflection_judge": {},
        "reflection_select": {},
    }


def new_run_trace(subquestions: Any, topic_entity: dict[str, str]) -> dict[str, Any]:
    return {
        "subquestions": subquestions,
        "topic_entity": dict(topic_entity),
        "constraints": None,
        "depths": [],
        "final_stop_reason": None,
        "final_stop_depth": None,
        "reverse_rec": {"time": 0, "ent": []},
    }


def new_depth_record(depth: int, topic_entities: dict[str, str]) -> dict[str, Any]:
    return {
        "depth": depth,
        "topic_entities": {eid: name for eid, name in topic_entities.items()},
        "relation_prune": [],
        "entity_search": [],
        "before_entity_prune": {},
        "after_entity_prune": {},
        "entity_prune_details": [],
        "pruned_triples": [],
        "entity_prune_success": False,
        "memory_update": None,
        "evaluation": None,
        "reverse_retrieval": None,
        "exploration_stats": None,
        "kg_memory": empty_kg_memory_scaffold(),
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
                ents = [str(x) for x in e_list]
                if len(ents) > 50:
                    ents = ents[:50] + [f"... +{len(e_list) - 50} more"]
                out[str(topic)][str(h_t)][str(rela)] = ents
    return out


def flatten_chain_triples(chain_of_entities: list) -> list[list[str]]:
    triples: list[list[str]] = []
    for chain in chain_of_entities or []:
        for t in chain or []:
            if isinstance(t, (list, tuple)) and len(t) == 3:
                triples.append([str(t[0]), str(t[1]), str(t[2])])
    return triples


def unique_entities_from_name_dict(name_dict: dict | None) -> set[str]:
    ents: set[str] = set()
    for topic, h_t_dict in (name_dict or {}).items():
        ents.add(str(topic))
        if not isinstance(h_t_dict, dict):
            continue
        for _h_t, r_e_dict in h_t_dict.items():
            if not isinstance(r_e_dict, dict):
                continue
            for _rela, e_list in r_e_dict.items():
                for item in e_list or []:
                    text = str(item)
                    if text.startswith("..."):
                        continue
                    ents.add(text)
    return ents


def compute_exploration_stats(depth_record: dict[str, Any]) -> dict[str, Any]:
    topic_entities = depth_record.get("topic_entities") or {}
    relation_prune = depth_record.get("relation_prune") or []
    entity_search = depth_record.get("entity_search") or []
    reverse = depth_record.get("reverse_retrieval") or {}
    if not isinstance(reverse, dict):
        reverse = {}
    decision_a = reverse.get("decision_a") or {}
    if not isinstance(decision_a, dict):
        decision_a = {}

    n_selected = 0
    for rel_trace in relation_prune:
        if isinstance(rel_trace, dict):
            n_selected += len(rel_trace.get("selected_relations") or [])

    return {
        "n_frontier_entities": len(topic_entities),
        "n_relations_selected": n_selected,
        "n_entity_search_attempts": len(entity_search),
        "n_relations_dead_end": sum(1 for ev in entity_search if isinstance(ev, dict) and ev.get("dead_end")),
        "n_relations_capped": sum(1 for ev in entity_search if isinstance(ev, dict) and ev.get("capped")),
        "n_entities_before_prune": len(unique_entities_from_name_dict(depth_record.get("before_entity_prune"))),
        "n_entities_after_prune": len(unique_entities_from_name_dict(depth_record.get("after_entity_prune"))),
        "n_triples_kept": len(depth_record.get("pruned_triples") or []),
        "reverse_attempted": bool(decision_a),
        "reverse_triggered": bool(reverse.get("triggered")),
        "n_reverse_add_entities": len(reverse.get("add_entities") or []),
        "decision_a_add": decision_a.get("add"),
    }


def finalize_depth_record(depth_record: dict[str, Any]) -> dict[str, Any]:
    depth_record["exploration_stats"] = compute_exploration_stats(depth_record)
    if "kg_memory" not in depth_record or depth_record["kg_memory"] is None:
        depth_record["kg_memory"] = empty_kg_memory_scaffold()
    attach_kg_memory_relation_events(depth_record)
    return depth_record


def attach_reverse_rec(
    pog_trace: dict[str, Any],
    reverse_rec: dict[str, Any] | None,
    entid_name: dict[str, str] | None = None,
) -> None:
    ents = []
    for item in (reverse_rec or {}).get("ent") or []:
        if entid_name and item in entid_name:
            ents.append(entid_name[item])
        else:
            ents.append(item)
    pog_trace["reverse_rec"] = {
        "time": (reverse_rec or {}).get("time", 0),
        "ent": ents,
    }
