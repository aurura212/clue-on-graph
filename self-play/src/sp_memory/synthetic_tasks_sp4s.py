"""SP4-SUPPLEMENT snapshot: shared relations, disjoint entities, multi-template questions."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_file, sha256_text
from .paths import PROTOCOL_VERSION, Workspace
from .question_normalization import normalized_question_hash
from .replay import LocalGraph
from .sp3_sampling import load_banned
from .sp4_io import FORBIDDEN_BENCHMARK, write_json, write_jsonl
from .synthetic_tasks import actor_to_task_record, env_for_task, split_contamination

GENERATOR_VERSION = "sp4s-synthetic-v1"
SNAPSHOT_ID = "sp4s-shared-rel-graph-v1"
VERBALIZER_VERSION = "multi_template_v1"

SPLIT_COUNTS = {
    "discovery": 40,
    "validation_v1": 20,
    "validation_v2": 20,
    "holdout": 20,
}

REL_FRIEND = "people.person.friend"
REL_BORN = "people.person.place_of_birth"
REL_IN = "location.location.containedby"
REL_SPOUSE = "people.person.spouse_s"
REL_ROSTER = "sports.pro_athlete.teams"
REL_TEAM = "sports.sports_team_roster.team"
SHARED_RELATIONS = (REL_FRIEND, REL_BORN, REL_IN, REL_SPOUSE, REL_ROSTER, REL_TEAM)

QUESTION_BANK: Dict[str, List[str]] = {
    "1hop_friend": [
        "Who is a personal contact of {name}?",
        "Which person has a social connection with {name}?",
        "Name someone personally linked to {name}.",
        "Who shares a personal link with {name}?",
    ],
    "1hop_born": [
        "In which city did {name} enter the world?",
        "What birthplace is recorded for {name}?",
        "Where was {name} born?",
        "Which city is listed as the birthplace of {name}?",
    ],
    "2hop_friend_born": [
        "Which city is associated with a personal contact of {name}?",
        "Where did a socially linked person of {name} enter the world?",
        "What birthplace belongs to someone connected to {name}?",
        "Which city is reached through a personal contact of {name}?",
    ],
    "3hop_friend_born_in": [
        "Which country contains the city linked to a contact of {name}?",
        "What larger region contains the birthplace of someone linked to {name}?",
        "Which country is reached after a personal link from {name} and a birthplace?",
        "What containing region belongs to a contact's birthplace for {name}?",
    ],
    "4hop_friend_friend_born_in": [
        "Which country contains the city of a contact-of-a-contact of {name}?",
        "What larger region is reached after two personal links from {name} and a birthplace?",
        "Which country follows two social links and a birthplace from {name}?",
        "What containing region belongs to a second-degree contact of {name}?",
    ],
    "2hop_spouse_roster": [
        "Which roster node is attached to the spouse of {name}?",
        "What intermediate team record is linked through the spouse of {name}?",
        "Which roster entry belongs to the partner of {name}?",
        "What team-record node is reached via the spouse of {name}?",
    ],
    "3hop_spouse_team": [
        "Which team is attached to the spouse of {name}?",
        "What sports organization is reached through the spouse of {name}?",
        "Which club is linked via the partner of {name}?",
        "What team belongs to the spouse of {name}?",
    ],
}

SPLIT_KINDS = {
    "discovery": ("1hop_friend", "2hop_friend_born", "2hop_spouse_roster"),
    "validation_v1": ("1hop_born", "3hop_friend_born_in"),
    "validation_v2": ("4hop_friend_friend_born_in", "3hop_spouse_team"),
    "holdout": ("3hop_friend_born_in", "2hop_spouse_roster"),
}

KIND_HOPS = {
    "1hop_friend": [(REL_FRIEND, "tail")],
    "1hop_born": [(REL_BORN, "tail")],
    "2hop_friend_born": [(REL_FRIEND, "tail"), (REL_BORN, "tail")],
    "3hop_friend_born_in": [(REL_FRIEND, "tail"), (REL_BORN, "tail"), (REL_IN, "tail")],
    "4hop_friend_friend_born_in": [(REL_FRIEND, "tail"), (REL_FRIEND, "tail"), (REL_BORN, "tail"), (REL_IN, "tail")],
    "2hop_spouse_roster": [(REL_SPOUSE, "tail"), (REL_ROSTER, "tail")],
    "3hop_spouse_team": [(REL_SPOUSE, "tail"), (REL_ROSTER, "tail"), (REL_TEAM, "tail")],
}


def snapshot_paths(workspace: Workspace) -> Dict[str, Path]:
    datasets = workspace.artifacts_root / "datasets"
    registries = workspace.artifacts_root / "registries"
    return {
        "snapshot": datasets / "sp4s_kg_snapshot_v1.json",
        "discovery": datasets / "sp4s_synthetic_discovery_v1.jsonl",
        "oracle_discovery": datasets / "sp4s_synthetic_discovery_oracle_v1.jsonl",
        "counterfactual": datasets / "sp4s_counterfactual_v1.jsonl",
        "validation_v1": datasets / "sp4s_validation_v1.jsonl",
        "oracle_v1": datasets / "sp4s_validation_v1_oracle.jsonl",
        "validation_v2": datasets / "sp4s_validation_v2.jsonl",
        "oracle_v2": datasets / "sp4s_validation_v2_oracle.jsonl",
        "holdout": datasets / "sp4s_synthetic_holdout_v1.jsonl",
        "oracle_holdout": datasets / "sp4s_synthetic_holdout_oracle_v1.jsonl",
        "manifest": datasets / "sp4s_synthetic_manifest_v1.json",
        "validation_registry": registries / "sp4s_validation_registry_v1.json",
        "exposure": registries / "sp4s_exposure_registry_v1.json",
    }


def _eid(split: str, kind: str, index: int) -> str:
    return f"e.{split}.{kind}{index}"


def build_shared_relation_snapshot() -> Dict[str, Any]:
    splits = ("discovery", "validation_v1", "validation_v2", "holdout")
    entity_names: Dict[str, str] = {}
    triples: List[Dict[str, str]] = []
    components: Dict[str, Dict[str, List[str]]] = {}
    for split in splits:
        people = [_eid(split, "p", i) for i in range(1, 9)]
        cities = [_eid(split, "c", i) for i in range(1, 4)]
        lands = [_eid(split, "n", i) for i in range(1, 4)]
        cvt = [_eid(split, "v", i) for i in range(1, 3)]
        teams = [_eid(split, "t", i) for i in range(1, 3)]
        labels = {
            people[0]: f"{split} Avery",
            people[1]: f"{split} Blair",
            people[2]: f"{split} Casey",
            people[3]: f"{split} Drew",
            people[4]: f"{split} Eden",
            people[5]: f"{split} Finley",
            people[6]: f"{split} Gray",
            people[7]: f"{split} Harper",
            cities[0]: f"{split} HarborCity",
            cities[1]: f"{split} MapleCity",
            cities[2]: f"{split} RidgeCity",
            lands[0]: f"{split} Northland",
            lands[1]: f"{split} Westland",
            lands[2]: f"{split} Eastland",
            cvt[0]: f"{split} RosterA",
            cvt[1]: f"{split} RosterB",
            teams[0]: f"{split} ClubA",
            teams[1]: f"{split} ClubB",
        }
        entity_names.update(labels)
        p, c, n, v, t = people, cities, lands, cvt, teams
        triples.extend(
            [
                {"head": p[0], "relation": REL_FRIEND, "tail": p[1]},
                {"head": p[1], "relation": REL_FRIEND, "tail": p[3]},
                {"head": p[1], "relation": REL_BORN, "tail": c[0]},
                {"head": p[3], "relation": REL_BORN, "tail": c[1]},
                {"head": c[0], "relation": REL_IN, "tail": n[0]},
                {"head": c[1], "relation": REL_IN, "tail": n[1]},
                {"head": p[0], "relation": REL_SPOUSE, "tail": p[2]},
                {"head": p[4], "relation": REL_FRIEND, "tail": p[1]},
                {"head": p[4], "relation": REL_SPOUSE, "tail": p[2]},
                {"head": p[2], "relation": REL_ROSTER, "tail": v[0]},
                {"head": v[0], "relation": REL_TEAM, "tail": t[0]},
                {"head": p[5], "relation": REL_FRIEND, "tail": p[6]},
                {"head": p[6], "relation": REL_FRIEND, "tail": p[7]},
                {"head": p[6], "relation": REL_BORN, "tail": c[2]},
                {"head": p[7], "relation": REL_BORN, "tail": c[0]},
                {"head": c[2], "relation": REL_IN, "tail": n[2]},
                {"head": p[5], "relation": REL_SPOUSE, "tail": p[7]},
                {"head": p[7], "relation": REL_ROSTER, "tail": v[1]},
                {"head": v[1], "relation": REL_TEAM, "tail": t[1]},
            ]
        )
        components[split] = {
            "people": people,
            "cities": cities,
            "countries": lands,
            "cvt": cvt,
            "teams": teams,
            "relations": list(SHARED_RELATIONS),
            "sources": [p[0], p[1], p[4], p[5], p[6]],
        }
    snapshot = {
        "schema_version": "sp4s-snapshot-v1",
        "snapshot_id": SNAPSHOT_ID,
        "protocol_version": PROTOCOL_VERSION,
        "source": "protocol_fixture_shared_relations",
        "verbalizer": VERBALIZER_VERSION,
        "generator_version": GENERATOR_VERSION,
        "shared_relations": list(SHARED_RELATIONS),
        "entity_names": entity_names,
        "triples": triples,
        "components": components,
    }
    snapshot["snapshot_hash"] = canonical_hash(
        {
            "entity_names": entity_names,
            "triples": triples,
            "snapshot_id": SNAPSHOT_ID,
            "relations": list(SHARED_RELATIONS),
        }
    )
    return snapshot


def local_graph_from_snapshot(snapshot: Mapping[str, Any]) -> LocalGraph:
    return LocalGraph(
        entity_names=dict(snapshot["entity_names"]),
        triples=[dict(item) for item in snapshot["triples"]],
    )


def execute_path(graph: LocalGraph, source: str, hops: Sequence[Tuple[str, str]]) -> List[str]:
    from .schemas import Direction

    frontier = [source]
    for relation, direction in hops:
        nxt: List[str] = []
        for entity in frontier:
            matches = graph.expand(entity, relation, Direction.TAIL if direction == "tail" else Direction.HEAD)
            for triple in matches:
                nxt.append(triple["tail"] if direction == "tail" else triple["head"])
        frontier = sorted(set(nxt))
        if not frontier:
            return []
    return frontier


def leakage_hits(
    question: str,
    answers: Sequence[str],
    names: Mapping[str, str],
    hops: Sequence[Tuple[str, str]],
    source_id: str,
) -> List[str]:
    hits = []
    if source_id in question:
        hits.append("source_id")
    for answer in answers:
        if answer and answer in question:
            hits.append("answer_id")
        label = names.get(answer, "")
        if label and label in question:
            hits.append("answer_name")
    for relation, _direction in hops:
        if relation in question:
            hits.append("relation_id")
    if " -> " in question or "ns:" in question:
        hits.append("explicit_path")
    return sorted(set(hits))


def render_question(kind: str, name: str, paraphrase_index: int) -> str:
    bank = QUESTION_BANK[kind]
    return bank[paraphrase_index % len(bank)].format(name=name)


def generate_llm_paraphrases(
    snapshot: Mapping[str, Any],
    *,
    client: Any,
    prompt_template: str,
    seed: int = 20260823,
    max_items: int = 24,
) -> Dict[str, str]:
    """Optional NL paraphrases. Leakage is dropped, never written into Actor files."""
    names = dict(snapshot["entity_names"])
    out: Dict[str, str] = {}
    produced = 0
    for split, kinds in SPLIT_KINDS.items():
        sources = list(snapshot["components"][split]["sources"])
        people = list(snapshot["components"][split]["people"])
        for kind in kinds:
            hops = KIND_HOPS[kind]
            candidate_sources = people[1:2] + people[3:4] if kind == "1hop_born" else sources
            for source in candidate_sources:
                if produced >= max_items:
                    return out
                key = f"{split}:{kind}:{source}:0"
                template_q = render_question(kind, names[source], 0)
                prompt = (
                    prompt_template.replace("{{QUESTION}}", template_q)
                    .replace("{{NAME}}", names[source])
                    .replace("{{KIND}}", kind)
                )
                raw = client.complete(prompt, temperature=0.4, purpose="sp4s_verbalizer")
                text = str(raw.get("text") or "").strip().splitlines()[0].strip().strip('"')
                answers = []
                leaks = leakage_hits(text, answers, names, hops, source)
                if leaks or not text or text == template_q:
                    continue
                if any(rel in text for rel in SHARED_RELATIONS):
                    continue
                out[key] = text
                produced += 1
    return out


def generate_tasks_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    seed: int = 20260823,
    banned: Optional[Mapping[str, Set[str]]] = None,
    llm_paraphrases: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    graph = local_graph_from_snapshot(snapshot)
    names = dict(snapshot["entity_names"])
    banned = banned or {"task_ids": set(), "questions": set(), "topics": set()}
    stats = {
        "generated": 0,
        "kept": 0,
        "deduped": 0,
        "ambiguous": 0,
        "unexecutable": 0,
        "leakage": 0,
        "banned": 0,
        "llm_paraphrases_used": 0,
    }
    seen_keys: Set[str] = set()
    by_split: Dict[str, List[Dict[str, Any]]] = {key: [] for key in SPLIT_COUNTS}
    oracles: Dict[str, List[Dict[str, Any]]] = {key: [] for key in SPLIT_COUNTS}
    rng = random.Random(seed)
    for split, kinds in SPLIT_KINDS.items():
        sources = list(snapshot["components"][split]["sources"])
        people = list(snapshot["components"][split]["people"])
        for kind in kinds:
            hops = KIND_HOPS[kind]
            candidate_sources = people[1:2] + people[3:4] if kind == "1hop_born" else sources
            for source in candidate_sources:
                answers = execute_path(graph, source, hops)
                stats["generated"] += 1
                if not answers:
                    stats["unexecutable"] += 1
                    continue
                if len(answers) > 1:
                    stats["ambiguous"] += 1
                    continue
                witness = [source]
                cursor = source
                ok = True
                for relation, direction in hops:
                    nxt = execute_path(graph, cursor, [(relation, direction)])
                    if not nxt:
                        ok = False
                        break
                    witness.extend([relation, nxt[0]])
                    cursor = nxt[0]
                if not ok:
                    stats["unexecutable"] += 1
                    continue
                signature = canonical_hash({"source": source, "hops": list(hops), "answers": list(answers), "witness": list(witness)})
                for q_index in range(len(QUESTION_BANK[kind])):
                    key = f"{split}:{kind}:{source}:{q_index}"
                    question = (llm_paraphrases or {}).get(key) or render_question(kind, names[source], q_index)
                    if key in (llm_paraphrases or {}):
                        stats["llm_paraphrases_used"] += 1
                    leaks = leakage_hits(question, answers, names, hops, source)
                    if leaks:
                        stats["leakage"] += 1
                        continue
                    qh = normalized_question_hash(question)
                    if qh in banned.get("questions", set()) or source in banned.get("topics", set()) or set(answers) & banned.get("topics", set()):
                        stats["banned"] += 1
                        continue
                    dedup_key = canonical_hash({"source": source, "signature": signature, "answers": answers, "q": qh})
                    if dedup_key in seen_keys:
                        stats["deduped"] += 1
                        continue
                    seen_keys.add(dedup_key)
                    task_id = f"sp4s.{split}.{kind}.{source.split('.')[-1]}.q{q_index}"
                    if task_id in banned.get("task_ids", set()):
                        stats["banned"] += 1
                        continue
                    difficulty = kind.split("_")[0]
                    actor = {
                        "schema_version": "sp4-task-v1",
                        "protocol_version": PROTOCOL_VERSION,
                        "task_id": task_id,
                        "split": split,
                        "snapshot_id": snapshot["snapshot_id"],
                        "snapshot_hash": snapshot["snapshot_hash"],
                        "source_entities": [source],
                        "source_entity_names": {source: names[source]},
                        "source_entity_hash": canonical_hash([source]),
                        "answer_entity_hash": canonical_hash(sorted(answers)),
                        "path_signature": signature,
                        "question": question,
                        "question_hash": qh,
                        "question_type": difficulty,
                        "difficulty": difficulty,
                        "answer_type": "entity",
                        "cvt": kind.endswith("roster") or kind.endswith("team"),
                        "oracle_level": "O0",
                        "task_split": split,
                        "task_generator_version": GENERATOR_VERSION,
                        "input_snapshot_id": snapshot["snapshot_id"],
                        "verbalizer": VERBALIZER_VERSION,
                        "paraphrase_index": q_index,
                    }
                    oracle = {
                        "schema_version": "sp4-task-v1",
                        "protocol_version": PROTOCOL_VERSION,
                        "task_id": task_id,
                        "split": split,
                        "oracle_level": "O4",
                        "answer_entity_ids": list(answers),
                        "normalized_answers": [names[item] for item in answers],
                        "witness_paths": [witness],
                        "logical_query": canonical_json({"source": source, "hops": hops}),
                        "path_hops": [{"relation": rel, "direction": d} for rel, d in hops],
                        "task_validity": "valid",
                        "oracle_version": GENERATOR_VERSION,
                    }
                    by_split[split].append(actor)
                    oracles[split].append(oracle)
                    stats["kept"] += 1
    for split, need in SPLIT_COUNTS.items():
        rows = by_split[split]
        rng.shuffle(rows)
        if len(rows) < need:
            raise ProtocolError(ViolationCode.SAMPLING_ERROR, f"split {split} has {len(rows)} tasks, need {need}")
        keep_ids = {item["task_id"] for item in rows[:need]}
        by_split[split] = sorted([item for item in rows if item["task_id"] in keep_ids], key=lambda item: item["task_id"])
        oracles[split] = sorted([item for item in oracles[split] if item["task_id"] in keep_ids], key=lambda item: item["task_id"])
    contamination = split_contamination(by_split, oracles)
    if contamination["cross_count"] > 0:
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, "split contamination", contamination)
    stats["executable_rate"] = stats["kept"] / max(1, stats["generated"])
    stats["dedup_rate"] = stats["deduped"] / max(1, stats["generated"])
    stats["ambiguity_rate"] = stats["ambiguous"] / max(1, stats["generated"])
    stats["leakage_rate"] = stats["leakage"] / max(1, stats["generated"])
    return {"actor": by_split, "oracle": oracles, "stats": stats, "contamination": contamination}


def freeze_synthetic(
    workspace: Workspace,
    config: Mapping[str, Any],
    *,
    llm_paraphrases: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    paths = snapshot_paths(workspace)
    try:
        banned = load_banned(workspace)
    except Exception:
        banned = {"task_ids": set(), "questions": set(), "topics": set()}
    snapshot = build_shared_relation_snapshot()
    generated = generate_tasks_from_snapshot(
        snapshot,
        seed=int(config.get("synthetic_seed") or 20260823),
        banned=banned,
        llm_paraphrases=llm_paraphrases,
    )
    write_json(workspace, paths["snapshot"], snapshot)
    mapping = {
        "discovery": ("discovery", "oracle_discovery"),
        "validation_v1": ("validation_v1", "oracle_v1"),
        "validation_v2": ("validation_v2", "oracle_v2"),
        "holdout": ("holdout", "oracle_holdout"),
    }
    file_hashes = {"snapshot": sha256_file(paths["snapshot"])}
    for split, (actor_key, oracle_key) in mapping.items():
        write_jsonl(workspace, paths[actor_key], generated["actor"][split])
        write_jsonl(workspace, paths[oracle_key], generated["oracle"][split])
        file_hashes[actor_key] = sha256_file(paths[actor_key])
        file_hashes[oracle_key] = sha256_file(paths[oracle_key])
    cf_rows = []
    for actor, oracle in zip(generated["actor"]["discovery"], generated["oracle"]["discovery"]):
        env = env_for_task(snapshot, actor, oracle)
        state = env.visible_state()
        cf_rows.append(
            {
                "schema_version": "sp4s-cf-state-v1",
                "protocol_version": PROTOCOL_VERSION,
                "task_id": actor["task_id"],
                "split": "counterfactual",
                "snapshot_id": snapshot["snapshot_id"],
                "snapshot_hash": snapshot["snapshot_hash"],
                "state_hash": state.state_id,
                "budget": dict(state.remaining_budget),
                "source_entities": list(actor["source_entities"]),
                "path_signature": actor["path_signature"],
                "question_hash": actor["question_hash"],
                "decision_stage": "relation_selection",
            }
        )
    write_jsonl(workspace, paths["counterfactual"], cf_rows)
    file_hashes["counterfactual"] = sha256_file(paths["counterfactual"])
    exposure = {
        "protocol_version": PROTOCOL_VERSION,
        "generator_version": GENERATOR_VERSION,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": snapshot["snapshot_hash"],
        "shared_relations": list(SHARED_RELATIONS),
        "benchmark_files_forbidden": list(FORBIDDEN_BENCHMARK),
        "splits": {
            split: {
                "n": len(generated["actor"][split]),
                "task_ids": [item["task_id"] for item in generated["actor"][split]],
                "question_hashes": [item["question_hash"] for item in generated["actor"][split]],
                "path_signatures": sorted({item["path_signature"] for item in generated["actor"][split]}),
            }
            for split in SPLIT_COUNTS
        },
        "contamination": generated["contamination"],
        "stats": generated["stats"],
    }
    write_json(workspace, paths["exposure"], exposure)
    registry = {
        "protocol_version": PROTOCOL_VERSION,
        "validation_v1": [item["task_id"] for item in generated["actor"]["validation_v1"]],
        "validation_v2": [item["task_id"] for item in generated["actor"]["validation_v2"]],
        "holdout": [item["task_id"] for item in generated["actor"]["holdout"]],
        "discovery": [item["task_id"] for item in generated["actor"]["discovery"]],
        "file_hashes": file_hashes,
    }
    write_json(workspace, paths["validation_registry"], registry)
    file_hashes["exposure"] = sha256_file(paths["exposure"])
    file_hashes["validation_registry"] = sha256_file(paths["validation_registry"])
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "plan_version": str(config.get("plan_version") or "SP4-SUPPLEMENT 1.0"),
        "generator_version": GENERATOR_VERSION,
        "verbalizer": VERBALIZER_VERSION,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": snapshot["snapshot_hash"],
        "shared_relations": list(SHARED_RELATIONS),
        "seed": int(config.get("synthetic_seed") or 20260823),
        "counts": {split: len(generated["actor"][split]) for split in SPLIT_COUNTS},
        "file_hashes": file_hashes,
        "stats": generated["stats"],
        "contamination": generated["contamination"],
        "benchmark_unused": True,
    }
    manifest["manifest_hash"] = canonical_hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
    write_json(workspace, paths["manifest"], manifest)
    return manifest


def verify_synthetic(workspace: Workspace, config: Mapping[str, Any]) -> Dict[str, Any]:
    paths = snapshot_paths(workspace)
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        raise ProtocolError(ViolationCode.REGISTRY_ERROR, f"SP4S synthetic files missing: {missing}")
    snapshot = json.loads(paths["snapshot"].read_text(encoding="utf-8"))
    rels = {rel for split in snapshot["components"].values() for rel in split["relations"]}
    if rels != set(SHARED_RELATIONS):
        raise ProtocolError(ViolationCode.SCHEMA_ERROR, "frozen snapshot relations are not shared")
    actor = {
        split: [json.loads(line) for line in paths[key].read_text(encoding="utf-8").splitlines() if line.strip()]
        for split, key in (
            ("discovery", "discovery"),
            ("validation_v1", "validation_v1"),
            ("validation_v2", "validation_v2"),
            ("holdout", "holdout"),
        )
    }
    oracle = {
        split: [json.loads(line) for line in paths[key].read_text(encoding="utf-8").splitlines() if line.strip()]
        for split, key in (
            ("discovery", "oracle_discovery"),
            ("validation_v1", "oracle_v1"),
            ("validation_v2", "oracle_v2"),
            ("holdout", "oracle_holdout"),
        )
    }
    contamination = split_contamination(actor, oracle)
    if contamination["cross_count"] > 0:
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, "frozen split contamination", contamination)
    return {"ok": True, "contamination": contamination, "snapshot": snapshot, "actor": actor, "oracle": oracle}


def load_split(workspace: Workspace, split: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    paths = snapshot_paths(workspace)
    actor_key = {
        "discovery": "discovery",
        "validation_v1": "validation_v1",
        "validation_v2": "validation_v2",
        "holdout": "holdout",
    }[split]
    oracle_key = {
        "discovery": "oracle_discovery",
        "validation_v1": "oracle_v1",
        "validation_v2": "oracle_v2",
        "holdout": "oracle_holdout",
    }[split]
    actor = [json.loads(line) for line in paths[actor_key].read_text(encoding="utf-8").splitlines() if line.strip()]
    oracle = [json.loads(line) for line in paths[oracle_key].read_text(encoding="utf-8").splitlines() if line.strip()]
    return actor, oracle
