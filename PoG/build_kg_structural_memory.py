#!/usr/bin/env python3
"""Build validated schema_profile memory from a global Freebase schema survey.

Offline only: does not inject into PoG and does not read benchmark gold.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from jsonl_io import append_jsonl_record, iter_jsonl_records
from kg_probe import EXCLUDED_TYPE_EXACT, EXCLUDED_TYPE_PREFIXES, KGProbe, is_excluded_type, query_hash
from kg_structural_memory import (
    BUILDER_VERSION,
    append_memory_records,
    build_config_payload,
    config_hash,
    drop_records_for_types,
    iter_memory_records,
    make_schema_profile_record,
    utc_now,
    write_index,
    write_manifest,
)
from output_paths import append_progress, load_progress
from constraint_compiler import is_mid
from freebase_func import SPARQLPATH

KG_MEMORY_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kg_memory")
PROGRESS_TYPE_KEY = "parse_id"


class RelStats:
    def __init__(self) -> None:
        self.entities: set[str] = set()
        self.branchings: list[float] = []
        self.endpoint_types: Counter[str] = Counter()
        self.cvt_neighbors = 0
        self.typed_neighbors = 0
        self.positive_ids: list[str] = []
        self.witness: list[str] | None = None
        self.query_template_id = ""
        self.query_hash_value = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build KG schema_profile memory (Phase 1).")
    parser.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--build_dir", type=str, default="", help="Resume an existing build directory.")
    parser.add_argument("--n_types", type=int, default=-1)
    parser.add_argument("--discovery_n", type=int, default=-1)
    parser.add_argument("--validation_n", type=int, default=-1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min_support", type=int, default=3)
    parser.add_argument("--min_coverage", type=float, default=0.2)
    parser.add_argument("--neighbor_sample_limit", type=int, default=-1)
    parser.add_argument("--entity_overfetch", type=int, default=400)
    parser.add_argument("--type_sample_limit", type=int, default=-1)
    parser.add_argument("--max_rel_detail", type=int, default=-1)
    return parser.parse_args()


def resolve_mode_defaults(args: argparse.Namespace) -> None:
    if args.mode == "smoke":
        if args.n_types < 0:
            args.n_types = 5
        if args.discovery_n < 0:
            args.discovery_n = 10
        if args.validation_n < 0:
            args.validation_n = 5
        if args.neighbor_sample_limit < 0:
            args.neighbor_sample_limit = 8
        if args.type_sample_limit < 0:
            args.type_sample_limit = 1500
        if args.max_rel_detail < 0:
            args.max_rel_detail = 20
    else:
        if args.n_types < 0:
            args.n_types = 150
        if args.discovery_n < 0:
            args.discovery_n = 50
        if args.validation_n < 0:
            args.validation_n = 30
        if args.neighbor_sample_limit < 0:
            args.neighbor_sample_limit = 12
        if args.type_sample_limit < 0:
            args.type_sample_limit = 8000
        if args.max_rel_detail < 0:
            args.max_rel_detail = 40


def make_build_id(cfg_hash: str, mode: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"schema_{mode}_{cfg_hash[:10]}_{stamp}"


def memory_paths(build_dir: str) -> dict[str, str]:
    return {
        "dir": build_dir,
        "jsonl": os.path.join(build_dir, "kg_structural_memory.jsonl"),
        "index": os.path.join(build_dir, "kg_structural_memory.index.json"),
        "manifest": os.path.join(build_dir, "build_manifest.json"),
        "discovery": os.path.join(build_dir, "discovery_entities.jsonl"),
        "validation": os.path.join(build_dir, "validation_entities.jsonl"),
        "progress": os.path.join(build_dir, "progress.jsonl"),
        "cache": os.path.join(build_dir, "probe_cache"),
    }


def load_saved_entity_splits(paths: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for split, path in (("discovery", paths["discovery"]), ("validation", paths["validation"])):
        if not os.path.isfile(path):
            continue
        for record in iter_jsonl_records(path):
            source_type = record.get("source_type")
            entity_id = record.get("entity_id")
            if not source_type or not entity_id:
                continue
            out.setdefault(source_type, {"discovery": [], "validation": []})
            out[source_type][split].append(entity_id)
    return out


def save_entity_split(path: str, source_type: str, split: str, entity_ids: list[str]) -> None:
    for entity_id in entity_ids:
        append_jsonl_record(
            path,
            {"source_type": source_type, "split": split, "entity_id": entity_id},
            indent=0,
        )


def split_entities(entity_ids: list[str], discovery_n: int, validation_n: int, seed: int) -> tuple[list[str], list[str]]:
    unique = []
    seen = set()
    for entity_id in entity_ids:
        if entity_id in seen:
            continue
        seen.add(entity_id)
        unique.append(entity_id)
    rng = random.Random(seed)
    rng.shuffle(unique)
    need = discovery_n + validation_n
    picked = unique[:need]
    disc = picked[:discovery_n]
    val = [eid for eid in picked[discovery_n:] if eid not in disc][:validation_n]
    overlap = set(disc) & set(val)
    if overlap:
        val = [eid for eid in val if eid not in overlap]
    return disc, val


def collect_relations(probe: KGProbe, entity_ids: list[str]) -> dict[str, dict[str, set[str]]]:
    per_entity: dict[str, dict[str, set[str]]] = {}
    for entity_id in entity_ids:
        try:
            outgoing = set(probe.head_relations(entity_id))
            incoming = set(probe.tail_relations(entity_id))
        except Exception:
            traceback.print_exc()
            outgoing, incoming = set(), set()
        per_entity[entity_id] = {"outgoing": outgoing, "incoming": incoming}
    return per_entity


def fill_neighbor_stats(
    probe: KGProbe,
    stats: RelStats,
    entity_id: str,
    relation: str,
    direction: str,
    neighbor_limit: int,
) -> None:
    outgoing = direction == "outgoing"
    template_id = "head_neighbors" if outgoing else "tail_neighbors"
    try:
        neighbors = probe.sample_neighbors(entity_id, relation, outgoing=outgoing, limit=neighbor_limit)
    except Exception:
        traceback.print_exc()
        neighbors = []
    stats.branchings.append(float(len(neighbors)))
    if neighbors and stats.witness is None:
        stats.witness = [entity_id, relation, neighbors[0]]
        stats.query_template_id = template_id
        stats.query_hash_value = query_hash(
            f"{template_id}|{entity_id}|{relation}|{neighbor_limit}"
        )
    mid_neighbors = [nid for nid in neighbors if is_mid(nid)][:5]
    if not mid_neighbors:
        return
    try:
        type_map = probe.entity_types(mid_neighbors)
    except Exception:
        traceback.print_exc()
        return
    for nid in mid_neighbors:
        type_ids = [tid for tid in type_map.get(nid, []) if not is_excluded_type(tid)]
        mediator_hit = False
        for type_id in type_ids:
            stats.endpoint_types[type_id] += 1
            stats.typed_neighbors += 1
            try:
                if probe.is_mediator_type(type_id):
                    mediator_hit = True
            except Exception:
                traceback.print_exc()
        if mediator_hit:
            stats.cvt_neighbors += 1


def build_type_records(
    *,
    probe: KGProbe,
    source_type: str,
    discovery_ids: list[str],
    validation_ids: list[str],
    neighbor_limit: int,
    max_rel_detail: int,
    min_support: int,
    min_coverage: float,
    endpoint: str,
    build_hash: str,
    built_at: str,
) -> list[dict[str, Any]]:
    disc_rels = collect_relations(probe, discovery_ids)
    val_rels = collect_relations(probe, validation_ids)

    disc_stats: dict[tuple[str, str], RelStats] = defaultdict(RelStats)
    for entity_id, rels in disc_rels.items():
        for direction in ("outgoing", "incoming"):
            for relation in rels[direction]:
                key = (direction, relation)
                bucket = disc_stats[key]
                bucket.entities.add(entity_id)
                if entity_id not in bucket.positive_ids:
                    bucket.positive_ids.append(entity_id)

    val_support: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entity_id, rels in val_rels.items():
        for direction in ("outgoing", "incoming"):
            for relation in rels[direction]:
                val_support[(direction, relation)].add(entity_id)

    ranked_keys = sorted(
        disc_stats.keys(),
        key=lambda key: (-len(disc_stats[key].entities), key[0], key[1]),
    )[: max(1, int(max_rel_detail))]

    for direction, relation in ranked_keys:
        bucket = disc_stats[(direction, relation)]
        for entity_id in list(bucket.entities)[:8]:
            fill_neighbor_stats(probe, bucket, entity_id, relation, direction, neighbor_limit)

    records: list[dict[str, Any]] = []
    for direction, relation in ranked_keys:
        bucket = disc_stats.get((direction, relation)) or RelStats()
        v_ids = sorted(val_support.get((direction, relation), set()))
        record = make_schema_profile_record(
            source_type=source_type,
            direction=direction,
            relation=relation,
            discovery_n=len(discovery_ids),
            validation_n=len(validation_ids),
            discovery_support=len(bucket.entities),
            validation_support=len(v_ids),
            branchings=bucket.branchings,
            endpoint_type_counts=dict(bucket.endpoint_types),
            cvt_neighbors=bucket.cvt_neighbors,
            typed_neighbors=bucket.typed_neighbors,
            positive_entity_ids=bucket.positive_ids[:8],
            witness_path=bucket.witness,
            query_template_id=bucket.query_template_id or "entity_relations",
            query_hash_value=bucket.query_hash_value or query_hash(f"{source_type}|{direction}|{relation}"),
            endpoint_id=endpoint,
            build_config_hash=build_hash,
            min_support=min_support,
            min_coverage=min_coverage,
            built_at=built_at,
        )
        records.append(record)
    return records


def rebuild_index_and_manifest(
    paths: dict[str, str],
    manifest: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    records = list(iter_memory_records(paths["jsonl"]))
    index = write_index(paths["index"], records)
    if extra:
        manifest.update(extra)
    manifest["index_summary"] = {
        "n_records": index.get("n_records", 0),
        "n_validated": index.get("n_validated", 0),
    }
    write_manifest(paths["manifest"], manifest)


def main() -> int:
    args = parse_args()
    resolve_mode_defaults(args)
    excluded = sorted(EXCLUDED_TYPE_EXACT | {prefix + "*" for prefix in EXCLUDED_TYPE_PREFIXES})
    config = build_config_payload(
        n_types=args.n_types,
        discovery_n=args.discovery_n,
        validation_n=args.validation_n,
        seed=args.seed,
        min_support=args.min_support,
        min_coverage=args.min_coverage,
        neighbor_sample_limit=args.neighbor_sample_limit,
        endpoint=SPARQLPATH,
        type_source="type.object.type",
        excluded_types=excluded,
    )
    cfg_hash = config_hash(config)

    if args.build_dir:
        build_dir = os.path.abspath(args.build_dir)
        os.makedirs(build_dir, exist_ok=True)
    else:
        os.makedirs(KG_MEMORY_ROOT, exist_ok=True)
        build_dir = os.path.join(KG_MEMORY_ROOT, make_build_id(cfg_hash, args.mode))
        os.makedirs(build_dir, exist_ok=True)

    paths = memory_paths(build_dir)
    os.makedirs(paths["cache"], exist_ok=True)
    probe = KGProbe(cache_dir=paths["cache"], endpoint=SPARQLPATH)

    done_types = load_progress(build_dir)
    saved_splits = load_saved_entity_splits(paths)
    dangling = set()
    if os.path.isfile(paths["jsonl"]):
        for record in iter_memory_records(paths["jsonl"]):
            source_type = (record.get("key") or {}).get("source_type")
            if source_type and source_type not in done_types:
                dangling.add(source_type)
        if dangling:
            print(f"[build] dropping incomplete types from jsonl: {sorted(dangling)[:8]}")
            drop_records_for_types(paths["jsonl"], dangling)

    built_at = utc_now()
    manifest = {
        "memory_build_id": os.path.basename(build_dir),
        "mode": args.mode,
        "config": config,
        "build_config_hash": cfg_hash,
        "builder_version": BUILDER_VERSION,
        "created_at": built_at,
        "endpoint": SPARQLPATH,
        "status": "running",
        "source_types": [],
        "type_selection": {},
        "probe_stats": {},
    }
    if os.path.isfile(paths["manifest"]):
        try:
            from kg_structural_memory import load_json

            previous = load_json(paths["manifest"])
            if previous.get("build_config_hash") and previous["build_config_hash"] != cfg_hash:
                print("[build] warning: existing manifest hash differs from current config hash")
            manifest["source_types"] = list(previous.get("source_types") or [])
            manifest["type_selection"] = previous.get("type_selection") or {}
        except Exception:
            traceback.print_exc()

    print(f"[build] dir={build_dir}")
    print(f"[build] mode={args.mode} n_types={args.n_types} disc={args.discovery_n} val={args.validation_n} hash={cfg_hash[:12]}")

    try:
        type_counts = probe.frequent_types(args.n_types, sample_limit=args.type_sample_limit)
    except Exception:
        traceback.print_exc()
        return 1
    source_types = [tid for tid, _cnt in type_counts[: args.n_types]]
    manifest["type_selection"] = {
        "method": "type.object.type frequency with common.topic-sample fallback",
        "ranked": [{"type": tid, "count": cnt} for tid, cnt in type_counts[: args.n_types]],
    }
    manifest["source_types"] = source_types
    write_manifest(paths["manifest"], manifest)
    if not source_types:
        print("[build] no source types selected")
        return 1

    started = time.time()
    overfetch = max(args.entity_overfetch, args.discovery_n + args.validation_n)
    for idx, source_type in enumerate(source_types, start=1):
        if source_type in done_types:
            print(f"[build] skip done type {idx}/{len(source_types)} {source_type}")
            continue
        print(f"[build] type {idx}/{len(source_types)} {source_type}")
        try:
            if source_type in saved_splits and saved_splits[source_type]["discovery"] and saved_splits[source_type]["validation"]:
                disc = saved_splits[source_type]["discovery"][: args.discovery_n]
                val = saved_splits[source_type]["validation"][: args.validation_n]
            else:
                sampled = probe.sample_entities_of_type(source_type, overfetch)
                disc, val = split_entities(sampled, args.discovery_n, args.validation_n, args.seed + idx)
                save_entity_split(paths["discovery"], source_type, "discovery", disc)
                save_entity_split(paths["validation"], source_type, "validation", val)
            if len(disc) < 2 or len(val) < 1:
                print(f"[build] skip {source_type}: not enough entities disc={len(disc)} val={len(val)}")
                append_progress(build_dir, source_type)
                done_types.add(source_type)
                continue
            if set(disc) & set(val):
                raise RuntimeError(f"discovery/validation overlap for {source_type}")
            records = build_type_records(
                probe=probe,
                source_type=source_type,
                discovery_ids=disc,
                validation_ids=val,
                neighbor_limit=args.neighbor_sample_limit,
                max_rel_detail=args.max_rel_detail,
                min_support=args.min_support,
                min_coverage=args.min_coverage,
                endpoint=SPARQLPATH,
                build_hash=cfg_hash,
                built_at=built_at,
            )
            append_memory_records(paths["jsonl"], records)
            append_progress(build_dir, source_type)
            done_types.add(source_type)
            n_val = sum(1 for rec in records if rec.get("status") == "validated")
            print(
                f"[build] wrote {len(records)} records ({n_val} validated) "
                f"queries={probe.stats['n_queries']} cache_hits={probe.stats['n_cache_hits']}"
            )
        except Exception:
            traceback.print_exc()
            print(f"[build] type failed, will retry on resume: {source_type}")
            drop_records_for_types(paths["jsonl"], {source_type})
            return 1

    rebuild_index_and_manifest(
        paths,
        manifest,
        extra={
            "status": "complete",
            "completed_at": utc_now(),
            "elapsed_sec": round(time.time() - started, 2),
            "probe_stats": probe.stats,
            "n_types_done": len(done_types),
        },
    )
    print(f"[build] complete dir={build_dir}")
    print(f"[build] probe_stats={probe.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
