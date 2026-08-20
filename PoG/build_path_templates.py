#!/usr/bin/env python3
"""Build validated 2-hop path_template memory from Protocol 1 schema_profile seeds.

Offline SPARQL only. Does not inject into PoG and does not read benchmark gold.
Unobserved paths are stored as unknown_or_low_support, never as negative facts.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections import defaultdict
from datetime import datetime
from typing import Any

from jsonl_io import iter_jsonl_records
from kg_probe import KGProbe, is_excluded_type, query_hash
from kg_structural_memory import (
    PATH_BUILDER_VERSION,
    append_memory_records,
    config_hash,
    iter_memory_records,
    make_path_template_record,
    utc_now,
    write_index,
    write_manifest,
)
from output_paths import append_progress, load_progress
from constraint_compiler import is_mid
from freebase_func import SPARQLPATH, abandon_rels

KG_MEMORY_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kg_memory")
FROZEN_SCHEMA_DIR = os.path.join(KG_MEMORY_ROOT, "schema_full_ee55ef9f17_20260818_114056")


class PathStats:
    def __init__(self) -> None:
        self.entities: set[str] = set()
        self.positive_ids: list[str] = []
        self.branchings: list[float] = []
        self.cvt_hits = 0
        self.midpoints = 0
        self.target_types: dict[str, int] = defaultdict(int)
        self.witness: list[str] | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build KG path_template memory (Phase 2).")
    parser.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--schema_dir", type=str, default=FROZEN_SCHEMA_DIR)
    parser.add_argument("--build_dir", type=str, default="")
    parser.add_argument("--n_types", type=int, default=-1)
    parser.add_argument("--discovery_n", type=int, default=-1)
    parser.add_argument("--validation_n", type=int, default=-1)
    parser.add_argument("--max_seed_rels", type=int, default=-1)
    parser.add_argument("--max_r2", type=int, default=-1)
    parser.add_argument("--neighbor_sample_limit", type=int, default=-1)
    parser.add_argument("--max_templates_per_type", type=int, default=-1)
    parser.add_argument("--min_support", type=int, default=-1)
    parser.add_argument("--min_coverage", type=float, default=-1)
    parser.add_argument("--directions", choices=["outgoing", "both"], default="")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def resolve_mode_defaults(args: argparse.Namespace) -> None:
    if not args.directions:
        args.directions = "outgoing" if args.mode == "smoke" else "both"
    if args.mode == "smoke":
        if args.n_types < 0:
            args.n_types = 3
        if args.discovery_n < 0:
            args.discovery_n = 8
        if args.validation_n < 0:
            args.validation_n = 6
        if args.max_seed_rels < 0:
            args.max_seed_rels = 3
        if args.max_r2 < 0:
            args.max_r2 = 4
        if args.neighbor_sample_limit < 0:
            args.neighbor_sample_limit = 4
        if args.max_templates_per_type < 0:
            args.max_templates_per_type = 8
        if args.min_support < 0:
            args.min_support = 2
        if args.min_coverage < 0:
            args.min_coverage = 0.2
    else:
        if args.n_types < 0:
            args.n_types = 150
        if args.discovery_n < 0:
            args.discovery_n = 30
        if args.validation_n < 0:
            args.validation_n = 20
        if args.max_seed_rels < 0:
            args.max_seed_rels = 8
        if args.max_r2 < 0:
            args.max_r2 = 8
        if args.neighbor_sample_limit < 0:
            args.neighbor_sample_limit = 8
        if args.max_templates_per_type < 0:
            args.max_templates_per_type = 20
        if args.min_support < 0:
            args.min_support = 3
        if args.min_coverage < 0:
            args.min_coverage = 0.2


def path_config_payload(args: argparse.Namespace, schema_hash: str) -> dict[str, Any]:
    return {
        "builder_version": PATH_BUILDER_VERSION,
        "memory_kind": "path_template",
        "source_protocol": "path_probe",
        "mode": args.mode,
        "schema_dir": os.path.abspath(args.schema_dir),
        "schema_build_config_hash": schema_hash,
        "n_types": int(args.n_types),
        "directions": str(args.directions),
        "discovery_n": int(args.discovery_n),
        "validation_n": int(args.validation_n),
        "max_seed_rels": int(args.max_seed_rels),
        "max_r2": int(args.max_r2),
        "neighbor_sample_limit": int(args.neighbor_sample_limit),
        "max_templates_per_type": int(args.max_templates_per_type),
        "min_validation_entity_support": int(args.min_support),
        "min_validation_coverage": float(args.min_coverage),
        "seed": int(args.seed),
        "endpoint": SPARQLPATH,
        "max_hops": 2,
    }


def load_splits(schema_dir: str) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for split, name in (("discovery", "discovery_entities.jsonl"), ("validation", "validation_entities.jsonl")):
        path = os.path.join(schema_dir, name)
        if not os.path.isfile(path):
            continue
        for record in iter_jsonl_records(path):
            source_type = record.get("source_type")
            entity_id = record.get("entity_id")
            if not source_type or not entity_id:
                continue
            out.setdefault(source_type, {"discovery": [], "validation": []})
            bucket = out[source_type][split]
            if entity_id not in bucket:
                bucket.append(entity_id)
    return out


def load_seed_schema_records(
    schema_dir: str, source_type: str, max_seed: int, direction: str = "outgoing"
) -> list[dict[str, Any]]:
    jsonl = os.path.join(schema_dir, "kg_structural_memory.jsonl")
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    seen = set()
    for record in iter_memory_records(jsonl):
        if record.get("status") != "validated":
            continue
        key = record.get("key") or {}
        if key.get("source_type") != source_type or key.get("direction") != direction:
            continue
        path = key.get("relation_path") or []
        if len(path) != 1:
            continue
        relation = str(path[0])
        if not relation or relation in seen or abandon_rels(relation):
            continue
        seen.add(relation)
        stats = record.get("statistics") or {}
        coverage = float(stats.get("validation_coverage") or 0.0)
        support = int(stats.get("validation_entity_support") or 0)
        ranked.append((-coverage, -support, record))
    ranked.sort(key=lambda item: (item[0], item[1], (item[2].get("key") or {}).get("relation_path", [""])[0]))
    return [item[2] for item in ranked[: max(1, max_seed)]]


def schema_record_to_one_hop_path(
    schema_record: dict[str, Any],
    *,
    build_hash: str,
    built_at: str,
    min_support: int,
    min_coverage: float,
    endpoint: str,
) -> dict[str, Any]:
    key = schema_record.get("key") or {}
    stats = schema_record.get("statistics") or {}
    evidence = schema_record.get("evidence") or {}
    witnesses = evidence.get("witness_paths") or []
    witness = witnesses[0] if witnesses and isinstance(witnesses[0], list) else None
    path = [str(rel) for rel in (key.get("relation_path") or [])]
    relation = path[0] if path else ""
    direction = str(key.get("direction") or "outgoing")
    template_id = f"one_hop_{direction}"
    qh = query_hash(f"{template_id}|{key.get('source_type')}|{relation}")
    record = make_path_template_record(
        source_type=str(key.get("source_type") or ""),
        direction=direction,
        relation_path=path,
        target_type=str(key.get("target_type") or ""),
        discovery_n=int(stats.get("discovery_n") or 0),
        validation_n=int(stats.get("validation_n") or 0),
        discovery_support=int(stats.get("discovery_entity_support") or 0),
        validation_support=int(stats.get("validation_entity_support") or 0),
        branchings=[float(stats.get("median_branching") or 0.0)],
        cvt_ratio=float(stats.get("cvt_ratio") or 0.0),
        contains_cvt=float(stats.get("cvt_ratio") or 0.0) >= 0.5,
        positive_entity_ids=list((evidence.get("positive_entity_ids") or [])[:8]),
        witness_path=witness,
        query_template_id=template_id,
        query_hash_value=qh,
        endpoint_id=endpoint,
        build_config_hash=build_hash,
        min_support=min_support,
        min_coverage=min_coverage,
        built_at=built_at,
    )
    if record["status"] == "low_support":
        record["status"] = "unknown_or_low_support"
    return record


def copy_split_subset(schema_dir: str, build_dir: str, source_types: list[str], discovery_n: int, validation_n: int) -> None:
    splits = load_splits(schema_dir)
    from jsonl_io import append_jsonl_record

    for source_type in source_types:
        disc = (splits.get(source_type) or {}).get("discovery") or []
        val = (splits.get(source_type) or {}).get("validation") or []
        for entity_id in disc[:discovery_n]:
            append_jsonl_record(
                os.path.join(build_dir, "discovery_entities.jsonl"),
                {"source_type": source_type, "split": "discovery", "entity_id": entity_id},
                indent=0,
            )
        for entity_id in val[:validation_n]:
            append_jsonl_record(
                os.path.join(build_dir, "validation_entities.jsonl"),
                {"source_type": source_type, "split": "validation", "entity_id": entity_id},
                indent=0,
            )


def discover_two_hop(
    probe: KGProbe,
    discovery_ids: list[str],
    seed_rels: list[str],
    neighbor_limit: int,
    max_r2: int,
    first_outgoing: bool = True,
) -> dict[tuple[str, str], PathStats]:
    stats: dict[tuple[str, str], PathStats] = defaultdict(PathStats)
    for entity_id in discovery_ids:
        for r1 in seed_rels:
            try:
                neighbors = probe.sample_neighbors(entity_id, r1, outgoing=first_outgoing, limit=neighbor_limit)
            except Exception:
                traceback.print_exc()
                neighbors = []
            mids = [nid for nid in neighbors if is_mid(nid)][:3]
            if not mids:
                continue
            try:
                type_map = probe.entity_types(mids)
            except Exception:
                traceback.print_exc()
                type_map = {}
            for nid in mids:
                try:
                    r2s = [rel for rel in probe.head_relations(nid) if not abandon_rels(rel)][:max_r2]
                except Exception:
                    traceback.print_exc()
                    r2s = []
                type_ids = [tid for tid in type_map.get(nid, []) if not is_excluded_type(tid)]
                mediator = False
                for tid in type_ids:
                    try:
                        if probe.is_mediator_type(tid):
                            mediator = True
                            break
                    except Exception:
                        traceback.print_exc()
                for r2 in r2s:
                    bucket = stats[(r1, r2)]
                    if entity_id not in bucket.entities:
                        bucket.entities.add(entity_id)
                        bucket.positive_ids.append(entity_id)
                    bucket.branchings.append(float(len(neighbors)))
                    bucket.midpoints += 1
                    if mediator:
                        bucket.cvt_hits += 1
                    if bucket.witness is None:
                        try:
                            second = probe.sample_neighbors(nid, r2, outgoing=True, limit=1)
                        except Exception:
                            second = []
                        hop2 = second[0] if second else ""
                        if hop2:
                            bucket.witness = [entity_id, r1, nid, r2, hop2]
                            try:
                                t2 = probe.entity_types([hop2] if is_mid(hop2) else [])
                                endpoints = [tid for tid in t2.get(hop2, []) if not is_excluded_type(tid)]
                                if endpoints:
                                    bucket.target_types[endpoints[0]] += 1
                            except Exception:
                                traceback.print_exc()
    return stats


def build_type_path_records(
    *,
    probe: KGProbe,
    source_type: str,
    direction: str,
    discovery_ids: list[str],
    validation_ids: list[str],
    seed_rels: list[str],
    neighbor_limit: int,
    max_r2: int,
    max_templates: int,
    min_support: int,
    min_coverage: float,
    endpoint: str,
    build_hash: str,
    built_at: str,
) -> list[dict[str, Any]]:
    first_outgoing = direction == "outgoing"
    discovered = discover_two_hop(
        probe, discovery_ids, seed_rels, neighbor_limit, max_r2, first_outgoing=first_outgoing
    )
    ranked = sorted(
        discovered.items(),
        key=lambda item: (-len(item[1].entities), item[0][0], item[0][1]),
    )[: max(1, max_templates)]
    records: list[dict[str, Any]] = []
    for (r1, r2), bucket in ranked:
        val_hits: list[str] = []
        for entity_id in validation_ids:
            try:
                if probe.two_hop_exists(entity_id, r1, r2, first_outgoing=first_outgoing):
                    val_hits.append(entity_id)
            except Exception:
                traceback.print_exc()
        target_type = ""
        if bucket.target_types:
            target_type = sorted(bucket.target_types.items(), key=lambda item: (-item[1], item[0]))[0][0]
        cvt_ratio = (bucket.cvt_hits / float(bucket.midpoints)) if bucket.midpoints else 0.0
        witness = bucket.witness
        template_id = f"two_hop_{direction}"
        qh = query_hash(f"{template_id}|{source_type}|{r1}|{r2}")
        record = make_path_template_record(
            source_type=source_type,
            direction=direction,
            relation_path=[r1, r2],
            target_type=target_type,
            discovery_n=len(discovery_ids),
            validation_n=len(validation_ids),
            discovery_support=len(bucket.entities),
            validation_support=len(val_hits),
            branchings=bucket.branchings,
            cvt_ratio=cvt_ratio,
            contains_cvt=cvt_ratio >= 0.5,
            positive_entity_ids=bucket.positive_ids[:8],
            witness_path=witness,
            query_template_id=template_id,
            query_hash_value=qh,
            endpoint_id=endpoint,
            build_config_hash=build_hash,
            min_support=min_support,
            min_coverage=min_coverage,
            built_at=built_at,
        )
        if record["status"] == "low_support":
            record["status"] = "unknown_or_low_support"
        records.append(record)
    return records


def main() -> int:
    args = parse_args()
    resolve_mode_defaults(args)
    schema_dir = os.path.abspath(args.schema_dir)
    schema_manifest_path = os.path.join(schema_dir, "build_manifest.json")
    if not os.path.isfile(schema_manifest_path):
        print(f"missing Protocol 1 manifest: {schema_manifest_path}")
        return 1
    from kg_structural_memory import load_json

    schema_manifest = load_json(schema_manifest_path)
    schema_hash = str(schema_manifest.get("build_config_hash") or "")
    schema_types = list(schema_manifest.get("source_types") or [])
    splits = load_splits(schema_dir)
    usable = [
        tid
        for tid in schema_types
        if (splits.get(tid) or {}).get("discovery") and (splits.get(tid) or {}).get("validation")
    ]
    chosen = usable[: int(args.n_types)]
    if not chosen:
        print("no Protocol 1 types with discovery/validation splits")
        return 1

    config = path_config_payload(args, schema_hash)
    cfg_hash = config_hash(config)
    if args.build_dir:
        build_dir = os.path.abspath(args.build_dir)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        build_dir = os.path.join(KG_MEMORY_ROOT, f"path_{args.mode}_{cfg_hash[:10]}_{stamp}")
    os.makedirs(build_dir, exist_ok=True)
    jsonl_path = os.path.join(build_dir, "kg_structural_memory.jsonl")
    index_path = os.path.join(build_dir, "kg_structural_memory.index.json")
    manifest_path = os.path.join(build_dir, "build_manifest.json")
    cache_dir = os.path.join(build_dir, "probe_cache")
    os.makedirs(cache_dir, exist_ok=True)

    probe = KGProbe(cache_dir=cache_dir, endpoint=SPARQLPATH)
    done = load_progress(build_dir)
    built_at = utc_now()
    manifest = {
        "memory_build_id": os.path.basename(build_dir),
        "mode": args.mode,
        "config": config,
        "build_config_hash": cfg_hash,
        "builder_version": PATH_BUILDER_VERSION,
        "created_at": built_at,
        "endpoint": SPARQLPATH,
        "status": "running",
        "source_types": [],
        "schema_build_id": schema_manifest.get("memory_build_id"),
        "schema_build_config_hash": schema_hash,
        "probe_stats": {},
    }
    print(f"[path] dir={build_dir}")
    print(
        f"[path] mode={args.mode} n_types={len(chosen)} directions={args.directions} "
        f"disc={args.discovery_n} val={args.validation_n} hash={cfg_hash[:12]}"
    )

    copy_needed = not os.path.isfile(os.path.join(build_dir, "discovery_entities.jsonl"))
    if copy_needed:
        copy_split_subset(schema_dir, build_dir, chosen, args.discovery_n, args.validation_n)

    splits_local = load_splits(build_dir)
    directions = ["outgoing", "incoming"] if args.directions == "both" else ["outgoing"]
    for source_type in chosen:
        if source_type in done:
            print(f"[path] skip completed {source_type}")
            if source_type not in manifest["source_types"]:
                manifest["source_types"].append(source_type)
            continue
        disc = (splits_local.get(source_type) or {}).get("discovery") or []
        val = (splits_local.get(source_type) or {}).get("validation") or []
        records: list[dict[str, Any]] = []
        for direction in directions:
            seed_records = load_seed_schema_records(schema_dir, source_type, args.max_seed_rels, direction)
            if not seed_records:
                continue
            seeds = [str(((rec.get("key") or {}).get("relation_path") or [""])[0]) for rec in seed_records]
            print(f"[path] {source_type} {direction} disc={len(disc)} val={len(val)} seeds={seeds}")
            records += [
                schema_record_to_one_hop_path(
                    rec,
                    build_hash=cfg_hash,
                    built_at=built_at,
                    min_support=args.min_support,
                    min_coverage=args.min_coverage,
                    endpoint=SPARQLPATH,
                )
                for rec in seed_records
            ]
            records += build_type_path_records(
                probe=probe,
                source_type=source_type,
                direction=direction,
                discovery_ids=disc,
                validation_ids=val,
                seed_rels=seeds,
                neighbor_limit=args.neighbor_sample_limit,
                max_r2=args.max_r2,
                max_templates=args.max_templates_per_type,
                min_support=args.min_support,
                min_coverage=args.min_coverage,
                endpoint=SPARQLPATH,
                build_hash=cfg_hash,
                built_at=built_at,
            )
        append_memory_records(jsonl_path, records)
        n_val = sum(1 for rec in records if rec.get("status") == "validated")
        n_low = sum(1 for rec in records if rec.get("status") != "validated")
        print(f"[path] {source_type} wrote {len(records)} templates (validated={n_val} low/unknown={n_low})")
        append_progress(build_dir, source_type)
        manifest["source_types"].append(source_type)
        write_manifest(manifest_path, manifest)

    records = list(iter_memory_records(jsonl_path))
    index = write_index(index_path, records)
    n_unknown = sum(1 for rec in records if rec.get("status") == "unknown_or_low_support")
    n_low = sum(1 for rec in records if rec.get("status") == "low_support")
    negative = [rec for rec in records if rec.get("status") not in {"validated", "low_support", "unknown_or_low_support", "deprecated"}]
    manifest["status"] = "complete"
    manifest["probe_stats"] = dict(probe.stats)
    manifest["index_summary"] = {
        "n_records": index.get("n_records", 0),
        "n_validated": index.get("n_validated", 0),
        "n_low_support": n_low,
        "n_unknown_or_low_support": n_unknown,
    }
    if negative:
        print(f"[path] unexpected statuses: {len(negative)}")
        return 1
    write_manifest(manifest_path, manifest)
    print(
        f"[path] done records={index.get('n_records')} validated={index.get('n_validated')} "
        f"queries={probe.stats.get('n_queries')} cache_hits={probe.stats.get('n_cache_hits')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
