"""Fixed-snapshot synthetic KGQA tasks, verbalizers, and strict split builder."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash, canonical_json, sha256_file, sha256_text
from .paths import PROTOCOL_VERSION, Workspace
from .question_normalization import normalized_question_hash
from .replay import LocalGraph, ReplayEnvironment
from .schemas import Budget, TaskRecord
from .sp3_sampling import load_banned
from .sp4_io import FORBIDDEN_BENCHMARK, atomic_write_text, write_json, write_jsonl
from .sp4_schemas import SP4_TASK_VERSION, path_signature, validate_task_record
from .state_signature import MID_RE

GENERATOR_VERSION = "sp4-synthetic-v1"
SNAPSHOT_ID = "sp4-fixture-graph-v1"
VERBALIZER_VERSION = "template_v1_degraded"

SPLIT_COUNTS = {
    "discovery": 12,
    "validation_v1": 8,
    "validation_v2": 8,
    "holdout": 8,
}

RELATION_FRIEND = "people.person.friend"
RELATION_BORN = "people.person.place_of_birth"
RELATION_IN = "location.location.containedby"
RELATION_SPOUSE = "people.person.spouse_s"
RELATION_ROSTER = "sports.pro_athlete.teams"
RELATION_TEAM = "sports.sports_team_roster.team"


def snapshot_paths(workspace: Workspace) -> Dict[str, Path]:
    root = workspace.artifacts_root
    datasets = root / "datasets"
    registries = root / "registries"
    return {
        "snapshot": datasets / "sp4_kg_snapshot_v1.json",
        "discovery": datasets / "sp4_synthetic_discovery_v1.jsonl",
        "oracle_discovery": datasets / "sp4_synthetic_discovery_oracle_v1.jsonl",
        "counterfactual": datasets / "sp4_counterfactual_v1.jsonl",
        "validation_v1": datasets / "sp4_validation_v1.jsonl",
        "oracle_v1": datasets / "sp4_validation_v1_oracle.jsonl",
        "validation_v2": datasets / "sp4_validation_v2.jsonl",
        "oracle_v2": datasets / "sp4_validation_v2_oracle.jsonl",
        "holdout": datasets / "sp4_synthetic_holdout_v1.jsonl",
        "oracle_holdout": datasets / "sp4_synthetic_holdout_oracle_v1.jsonl",
        "manifest": datasets / "sp4_synthetic_manifest_v1.json",
        "validation_registry": registries / "sp4_validation_registry_v1.json",
        "exposure": registries / "sp4_exposure_registry_v1.json",
    }


def _eid(split: str, kind: str, index: int) -> str:
    return f"e.{split}.{kind}{index}"


def build_fixture_snapshot() -> Dict[str, Any]:
    """Four disjoint components, one per split. Same topology, distinct entities and path signatures."""
    splits = ("discovery", "validation_v1", "validation_v2", "holdout")
    entity_names: Dict[str, str] = {}
    triples: List[Dict[str, str]] = []
    components: Dict[str, Dict[str, List[str]]] = {}
    relation_suffix = {
        "discovery": "",
        "validation_v1": ".v1",
        "validation_v2": ".v2",
        "holdout": ".h",
    }
    for split in splits:
        suffix = relation_suffix[split]
        p1, p2, p3, p4, p5 = [_eid(split, "p", i) for i in range(1, 6)]
        c1, c2 = [_eid(split, "c", i) for i in range(1, 3)]
        n1, n2 = [_eid(split, "n", i) for i in range(1, 3)]
        v1 = _eid(split, "v", 1)
        t1 = _eid(split, "t", 1)
        names = {
            p1: f"{split.title()} Alice",
            p2: f"{split.title()} Bob",
            p3: f"{split.title()} Carol",
            p4: f"{split.title()} Dave",
            p5: f"{split.title()} Eve",
            c1: f"{split.title()} CityOne",
            c2: f"{split.title()} CityTwo",
            n1: f"{split.title()} LandOne",
            n2: f"{split.title()} LandTwo",
            v1: f"{split.title()} Roster",
            t1: f"{split.title()} Team",
        }
        entity_names.update(names)
        friend = RELATION_FRIEND + suffix
        born = RELATION_BORN + suffix
        contained = RELATION_IN + suffix
        spouse = RELATION_SPOUSE + suffix
        roster = RELATION_ROSTER + suffix
        team = RELATION_TEAM + suffix
        triples.extend(
            [
                {"head": p1, "relation": friend, "tail": p2},
                {"head": p2, "relation": friend, "tail": p4},
                {"head": p2, "relation": born, "tail": c1},
                {"head": p4, "relation": born, "tail": c2},
                {"head": c1, "relation": contained, "tail": n1},
                {"head": c2, "relation": contained, "tail": n2},
                {"head": p1, "relation": spouse, "tail": p3},
                {"head": p5, "relation": friend, "tail": p2},
                {"head": p5, "relation": spouse, "tail": p3},
                {"head": p3, "relation": roster, "tail": v1},
                {"head": v1, "relation": team, "tail": t1},
            ]
        )
        components[split] = {
            "people": [p1, p2, p3, p4, p5],
            "cities": [c1, c2],
            "countries": [n1, n2],
            "cvt": [v1],
            "teams": [t1],
            "relations": [friend, born, contained, spouse, roster, team],
            "sources": [p1, p2, p5],
        }
    snapshot = {
        "schema_version": "sp4-snapshot-v1",
        "snapshot_id": SNAPSHOT_ID,
        "protocol_version": PROTOCOL_VERSION,
        "source": "protocol_fixture",
        "verbalizer": VERBALIZER_VERSION,
        "generator_version": GENERATOR_VERSION,
        "entity_names": entity_names,
        "triples": triples,
        "components": components,
    }
    snapshot["snapshot_hash"] = canonical_hash(
        {"entity_names": entity_names, "triples": triples, "snapshot_id": SNAPSHOT_ID}
    )
    return snapshot


def local_graph_from_snapshot(snapshot: Mapping[str, Any]) -> LocalGraph:
    return LocalGraph(
        entity_names=dict(snapshot["entity_names"]),
        triples=[dict(item) for item in snapshot["triples"]],
    )


def execute_path(graph: LocalGraph, source: str, hops: Sequence[Tuple[str, str]]) -> List[str]:
    frontier = [source]
    for relation, direction in hops:
        nxt = []
        for entity in frontier:
            matches = graph.expand(entity, relation, _direction(direction))
            for triple in matches:
                nxt.append(triple["tail"] if direction == "tail" else triple["head"])
        frontier = sorted(set(nxt))
        if not frontier:
            return []
    return frontier


def _direction(value: str):
    from .schemas import Direction

    return Direction.TAIL if value == "tail" else Direction.HEAD


def _templates(split: str) -> List[Dict[str, Any]]:
    suffix = {"": "discovery", ".v1": "validation_v1", ".v2": "validation_v2", ".h": "holdout"}
    # split-specific relation names already baked into snapshot; templates keyed by hop kind.
    mapping = {
        "discovery": "",
        "validation_v1": ".v1",
        "validation_v2": ".v2",
        "holdout": ".h",
    }
    s = mapping[split]
    friend, born, contained, spouse, roster, team = (
        RELATION_FRIEND + s,
        RELATION_BORN + s,
        RELATION_IN + s,
        RELATION_SPOUSE + s,
        RELATION_ROSTER + s,
        RELATION_TEAM + s,
    )
    return [
        {
            "kind": "1hop_friend",
            "hops": [(friend, "tail")],
            "difficulty": "1hop",
            "answer_type": "entity",
            "cvt": False,
            "questions": [
                "Who has a personal relationship with {name}?",
                "Which person is socially linked to {name}?",
            ],
        },
        {
            "kind": "1hop_born",
            "hops": [(born, "tail")],
            "difficulty": "1hop",
            "answer_type": "entity",
            "cvt": False,
            "sources": "born_people",
            "questions": [
                "In which city did {name} enter the world?",
                "What birthplace is recorded for {name}?",
            ],
        },
        {
            "kind": "2hop_friend_born",
            "hops": [(friend, "tail"), (born, "tail")],
            "difficulty": "2hop",
            "answer_type": "entity",
            "cvt": False,
            "questions": [
                "Which city is associated with a personal contact of {name}?",
                "Where did a socially linked person of {name} enter the world?",
            ],
        },
        {
            "kind": "3hop_friend_born_in",
            "hops": [(friend, "tail"), (born, "tail"), (contained, "tail")],
            "difficulty": "3hop",
            "answer_type": "entity",
            "cvt": False,
            "questions": [
                "Which country contains the city linked to a contact of {name}?",
                "What larger region contains the birthplace of someone linked to {name}?",
            ],
        },
        {
            "kind": "4hop_friend_friend_born_in",
            "hops": [(friend, "tail"), (friend, "tail"), (born, "tail"), (contained, "tail")],
            "difficulty": "4hop",
            "answer_type": "entity",
            "cvt": False,
            "questions": [
                "Which country contains the city of a contact-of-a-contact of {name}?",
                "What larger region is reached after two personal links from {name} and a birthplace?",
            ],
        },
        {
            "kind": "2hop_spouse_roster",
            "hops": [(spouse, "tail"), (roster, "tail")],
            "difficulty": "2hop",
            "answer_type": "entity",
            "cvt": True,
            "questions": [
                "Which roster node is attached to the spouse of {name}?",
                "What intermediate team record is linked through the spouse of {name}?",
            ],
        },
        {
            "kind": "3hop_spouse_team",
            "hops": [(spouse, "tail"), (roster, "tail"), (team, "tail")],
            "difficulty": "3hop",
            "answer_type": "entity",
            "cvt": True,
            "questions": [
                "Which team is attached to the spouse of {name}?",
                "What sports organization is reached through the spouse of {name}?",
            ],
        },
    ]


SPLIT_KINDS = {
    "discovery": ("1hop_friend", "2hop_friend_born", "2hop_spouse_roster"),
    "validation_v1": ("1hop_born", "3hop_friend_born_in"),
    "validation_v2": ("4hop_friend_friend_born_in", "3hop_spouse_team"),
    "holdout": ("3hop_friend_born_in", "2hop_spouse_roster"),
}
# holdout 3hop would share path signature with v1 if same split suffix - suffixes differ so signatures differ.


def _leakage_hits(question: str, answers: Sequence[str], names: Mapping[str, str], hops: Sequence[Tuple[str, str]]) -> List[str]:
    hits = []
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
    if MID_RE.search(question):
        hits.append("entity_mid")
    return sorted(set(hits))


def generate_tasks_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    seed: int = 20260823,
    banned: Optional[Mapping[str, Set[str]]] = None,
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
    }
    seen_keys: Set[str] = set()
    by_split: Dict[str, List[Dict[str, Any]]] = {key: [] for key in SPLIT_COUNTS}
    oracles: Dict[str, List[Dict[str, Any]]] = {key: [] for key in SPLIT_COUNTS}
    rng = random.Random(seed)
    for split, kinds in SPLIT_KINDS.items():
        templates = {item["kind"]: item for item in _templates(split)}
        sources = list(snapshot["components"][split]["sources"])
        people = list(snapshot["components"][split]["people"])
        for kind in kinds:
            spec = templates[kind]
            hop_rels = spec["hops"]
            signature = path_signature([item[0] for item in hop_rels])
            if spec.get("sources") == "born_people":
                candidates_sources = people[1:2] + people[3:4]  # p2 and p4
            else:
                candidates_sources = sources
            for source in candidates_sources:
                answers = execute_path(graph, source, hop_rels)
                stats["generated"] += 1
                if not answers:
                    stats["unexecutable"] += 1
                    continue
                if len(answers) > 1:
                    stats["ambiguous"] += 1
                    continue
                witness = [source]
                cursor = source
                for relation, direction in hop_rels:
                    witness.extend([relation, execute_path(graph, cursor, [(relation, direction)])[0]])
                    cursor = witness[-1]
                for q_index, template in enumerate(spec["questions"]):
                    question = template.format(name=names[source])
                    leaks = _leakage_hits(question, answers, names, hop_rels)
                    if leaks:
                        stats["leakage"] += 1
                        continue
                    qh = normalized_question_hash(question)
                    if qh in banned["questions"] or source in banned["topics"] or set(answers) & banned["topics"]:
                        stats["banned"] += 1
                        continue
                    dedup_key = canonical_hash({"source": source, "signature": signature, "answers": answers, "q": qh})
                    if dedup_key in seen_keys:
                        stats["deduped"] += 1
                        continue
                    seen_keys.add(dedup_key)
                    task_id = f"sp4.{split}.{kind}.{source.split('.')[-1]}.q{q_index}"
                    if task_id in banned["task_ids"]:
                        stats["banned"] += 1
                        continue
                    actor = {
                        "schema_version": SP4_TASK_VERSION,
                        "protocol_version": PROTOCOL_VERSION,
                        "task_id": task_id,
                        "split": split if split != "validation_v1" else "validation_v1",
                        "snapshot_id": snapshot["snapshot_id"],
                        "snapshot_hash": snapshot["snapshot_hash"],
                        "source_entities": [source],
                        "source_entity_names": {source: names[source]},
                        "source_entity_hash": canonical_hash([source]),
                        "answer_entity_hash": canonical_hash(sorted(answers)),
                        "path_signature": signature,
                        "question": question,
                        "question_hash": qh,
                        "question_type": spec["difficulty"],
                        "difficulty": spec["difficulty"],
                        "answer_type": spec["answer_type"],
                        "cvt": spec["cvt"],
                        "branching": True,
                        "oracle_level": "O0",
                        "task_split": split,
                        "task_generator_version": GENERATOR_VERSION,
                        "input_snapshot_id": snapshot["snapshot_id"],
                        "verbalizer": VERBALIZER_VERSION,
                        "paraphrase_index": q_index,
                    }
                    validate_task_record(actor, actor_only=True)
                    oracle = {
                        "schema_version": SP4_TASK_VERSION,
                        "protocol_version": PROTOCOL_VERSION,
                        "task_id": task_id,
                        "split": actor["split"],
                        "oracle_level": "O4",
                        "answer_entity_ids": list(answers),
                        "normalized_answers": [names[item] for item in answers],
                        "witness_paths": [witness],
                        "logical_query": canonical_json({"source": source, "hops": hop_rels}),
                        "path_hops": [{"relation": r, "direction": d} for r, d in hop_rels],
                        "task_validity": "valid",
                        "oracle_version": GENERATOR_VERSION,
                    }
                    by_split[split if split in by_split else actor["split"]].append(actor)
                    oracles[split].append(oracle)
                    stats["kept"] += 1
    for split, need in SPLIT_COUNTS.items():
        rows = by_split[split]
        rng.shuffle(rows)
        if len(rows) < need:
            raise ProtocolError(
                ViolationCode.SAMPLING_ERROR,
                f"split {split} has {len(rows)} tasks, need {need}",
            )
        keep_ids = {item["task_id"] for item in rows[:need]}
        by_split[split] = [item for item in rows if item["task_id"] in keep_ids]
        oracles[split] = [item for item in oracles[split] if item["task_id"] in keep_ids]
        by_split[split].sort(key=lambda item: item["task_id"])
        oracles[split].sort(key=lambda item: item["task_id"])
    contamination = split_contamination(by_split, oracles)
    if contamination["cross_count"] > 0:
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, "split contamination", contamination)
    stats["executable_rate"] = stats["kept"] / max(1, stats["generated"])
    stats["dedup_rate"] = stats["deduped"] / max(1, stats["generated"])
    stats["ambiguity_rate"] = stats["ambiguous"] / max(1, stats["generated"])
    stats["leakage_rate"] = stats["leakage"] / max(1, stats["generated"])
    return {"actor": by_split, "oracle": oracles, "stats": stats, "contamination": contamination}


def split_contamination(actor: Mapping[str, Sequence[Mapping[str, Any]]], oracle: Mapping[str, Sequence[Mapping[str, Any]]]) -> Dict[str, Any]:
    buckets = {}
    for split, rows in actor.items():
        oracle_by_id = {item["task_id"]: item for item in oracle.get(split) or []}
        sources, answers, tasks, questions, signatures = set(), set(), set(), set(), set()
        for row in rows:
            sources.update(row.get("source_entities") or [])
            answers.update(oracle_by_id[row["task_id"]].get("answer_entity_ids") or [])
            tasks.add(row["task_id"])
            questions.add(row["question_hash"])
            signatures.add(row["path_signature"])
        buckets[split] = {
            "source": sources,
            "answer": answers,
            "task": tasks,
            "paraphrase": questions,
            "path_signature": signatures,
        }
    overlaps = []
    names = list(buckets)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            for kind in ("source", "answer", "task", "paraphrase", "path_signature"):
                common = sorted(buckets[left][kind] & buckets[right][kind])
                if common:
                    overlaps.append({"left": left, "right": right, "kind": kind, "values": common})
    return {"cross_count": len(overlaps), "overlaps": overlaps}


def actor_to_task_record(actor: Mapping[str, Any], oracle: Optional[Mapping[str, Any]] = None) -> TaskRecord:
    hidden = oracle or {}
    return TaskRecord(
        task_id=str(actor["task_id"]),
        question=str(actor["question"]),
        source_entities=list(actor.get("source_entities") or []),
        source_entity_names=dict(actor.get("source_entity_names") or {}),
        task_split=str(actor.get("split") or actor.get("task_split") or "discovery"),
        task_generator_version=str(actor.get("task_generator_version") or GENERATOR_VERSION),
        input_snapshot_id=str(actor.get("snapshot_id") or SNAPSHOT_ID),
        logical_query="" if not hidden else str(hidden.get("logical_query") or ""),
        answer_entity_ids=[] if not hidden else list(hidden.get("answer_entity_ids") or []),
        normalized_answers=[] if not hidden else list(hidden.get("normalized_answers") or []),
        witness_paths=[] if not hidden else list(hidden.get("witness_paths") or []),
        task_validity=str(hidden.get("task_validity") or "valid"),
        oracle_version=str(hidden.get("oracle_version") or "hidden"),
    )


def env_for_task(
    snapshot: Mapping[str, Any],
    actor: Mapping[str, Any],
    oracle: Mapping[str, Any],
    *,
    budget: Optional[Budget] = None,
) -> ReplayEnvironment:
    graph = local_graph_from_snapshot(snapshot)
    task = actor_to_task_record(actor, oracle)
    if budget is None:
        budget = Budget(
            max_depth=4,
            max_steps=16,
            max_kg_calls=24,
            max_llm_calls=8,
            max_critic_rounds=2,
            max_frontier_size=80,
        )
    source = actor["source_entities"][0]
    return ReplayEnvironment(
        task=task,
        graph=graph,
        budget=budget,
        visible_entities=[source],
        frontier=[source],
        snapshot_id=str(snapshot["snapshot_id"]),
    )


def freeze_synthetic(workspace: Workspace, config: Mapping[str, Any]) -> Dict[str, Any]:
    paths = snapshot_paths(workspace)
    for rel in FORBIDDEN_BENCHMARK:
        assert_not_benchmark_path = Path(rel)
        if (workspace.self_play_root / rel).exists():
            # existence is required for hash verify elsewhere; we just refuse to read them here
            pass
    banned = load_banned(workspace)
    snapshot = build_fixture_snapshot()
    generated = generate_tasks_from_snapshot(snapshot, seed=int(config.get("synthetic_seed") or 20260823), banned=banned)
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
    cf_rows = _counterfactual_states(snapshot, generated)
    write_jsonl(workspace, paths["counterfactual"], cf_rows)
    file_hashes["counterfactual"] = sha256_file(paths["counterfactual"])
    exposure = {
        "protocol_version": PROTOCOL_VERSION,
        "generator_version": GENERATOR_VERSION,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": snapshot["snapshot_hash"],
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
        "plan_version": str(config.get("plan_version") or "SP4-PLAN 2.1"),
        "generator_version": GENERATOR_VERSION,
        "verbalizer": VERBALIZER_VERSION,
        "verbalizer_note": "template_v1_degraded: natural-language templates, not LLM-generated questions",
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": snapshot["snapshot_hash"],
        "snapshot_source": snapshot["source"],
        "seed": int(config.get("synthetic_seed") or 20260823),
        "counts": {split: len(generated["actor"][split]) for split in SPLIT_COUNTS},
        "file_hashes": file_hashes,
        "stats": generated["stats"],
        "contamination": generated["contamination"],
        "benchmark_unused": True,
    }
    manifest["manifest_hash"] = canonical_hash(manifest)
    write_json(workspace, paths["manifest"], manifest)
    return manifest


def _counterfactual_states(snapshot: Mapping[str, Any], generated: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for actor, oracle in zip(generated["actor"]["discovery"], generated["oracle"]["discovery"]):
        env = env_for_task(snapshot, actor, oracle)
        state = env.visible_state()
        rows.append(
            {
                "schema_version": "sp4-cf-state-v1",
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
    return rows


def verify_synthetic(workspace: Workspace, config: Mapping[str, Any]) -> Dict[str, Any]:
    paths = snapshot_paths(workspace)
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        raise ProtocolError(ViolationCode.REGISTRY_ERROR, f"SP4 synthetic files missing: {missing}")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    expected = config.get("expected_synthetic_manifest_hash")
    if expected and manifest.get("manifest_hash") != expected:
        raise ProtocolError(
            ViolationCode.REGISTRY_ERROR,
            "synthetic manifest hash mismatch",
            {"got": manifest.get("manifest_hash"), "expected": expected},
        )
    for name, digest in (manifest.get("file_hashes") or {}).items():
        path = paths[name]
        got = sha256_file(path)
        if got != digest:
            raise ProtocolError(ViolationCode.REGISTRY_ERROR, f"hash mismatch for {name}", {"got": got, "expected": digest})
    actor = {
        "discovery": [json.loads(line) for line in paths["discovery"].read_text(encoding="utf-8").splitlines() if line.strip()],
        "validation_v1": [json.loads(line) for line in paths["validation_v1"].read_text(encoding="utf-8").splitlines() if line.strip()],
        "validation_v2": [json.loads(line) for line in paths["validation_v2"].read_text(encoding="utf-8").splitlines() if line.strip()],
        "holdout": [json.loads(line) for line in paths["holdout"].read_text(encoding="utf-8").splitlines() if line.strip()],
    }
    oracle = {
        "discovery": [json.loads(line) for line in paths["oracle_discovery"].read_text(encoding="utf-8").splitlines() if line.strip()],
        "validation_v1": [json.loads(line) for line in paths["oracle_v1"].read_text(encoding="utf-8").splitlines() if line.strip()],
        "validation_v2": [json.loads(line) for line in paths["oracle_v2"].read_text(encoding="utf-8").splitlines() if line.strip()],
        "holdout": [json.loads(line) for line in paths["oracle_holdout"].read_text(encoding="utf-8").splitlines() if line.strip()],
    }
    contamination = split_contamination(actor, oracle)
    if contamination["cross_count"] > 0:
        raise ProtocolError(ViolationCode.SAMPLING_ERROR, "frozen split contamination", contamination)
    for rel in FORBIDDEN_BENCHMARK:
        for rows in actor.values():
            blob = canonical_json(rows)
            if rel in blob:
                raise ProtocolError(ViolationCode.SAMPLING_ERROR, "benchmark path leaked into synthetic tasks")
    return {"ok": True, "manifest": manifest, "contamination": contamination}


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
