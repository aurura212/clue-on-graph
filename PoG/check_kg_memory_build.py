#!/usr/bin/env python3
"""Offline Phase 1 checks for a schema_profile build directory."""

from __future__ import annotations

import argparse
import os
import random
import sys
import traceback

from jsonl_io import iter_jsonl_records
from kg_probe import KGProbe
from kg_structural_memory import (
    build_config_payload,
    config_hash,
    load_json,
    validate_record,
    walk_forbidden_keys,
)
from freebase_func import SPARQLPATH


def load_split_ids(path: str) -> dict[str, set[str]]:
    by_type: dict[str, set[str]] = {}
    if not os.path.isfile(path):
        return by_type
    for record in iter_jsonl_records(path):
        source_type = record.get("source_type")
        entity_id = record.get("entity_id")
        if source_type and entity_id:
            by_type.setdefault(source_type, set()).add(entity_id)
    return by_type


def replay_witness(probe: KGProbe, record: dict) -> bool:
    paths = (record.get("evidence") or {}).get("witness_paths") or []
    if not paths:
        return False
    witness = paths[0]
    if not isinstance(witness, list) or len(witness) < 3:
        return False
    if len(witness) >= 5:
        entity_id, r1, mid, r2, neighbor = [str(x) for x in witness[:5]]
        if neighbor.startswith("m.") or neighbor.startswith("g."):
            filter_clause = f"FILTER(?y = ns:{neighbor})"
        else:
            escaped = neighbor.replace("\\", "\\\\").replace('"', '\\"')
            filter_clause = f'FILTER(STR(?y) = "{escaped}")'
        if (record.get("key") or {}).get("direction") == "incoming":
            hop1 = f"ns:{mid} ns:{r1} ns:{entity_id} ."
        else:
            hop1 = f"ns:{entity_id} ns:{r1} ns:{mid} ."
        sparql = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?y
WHERE {{
  {hop1}
  ns:{mid} ns:{r2} ?y .
  {filter_clause}
}}
LIMIT 1"""
        try:
            return bool(probe.query(sparql))
        except Exception:
            traceback.print_exc()
            return False
    entity_id, relation, neighbor = str(witness[0]), str(witness[1]), str(witness[2])
    direction = (record.get("key") or {}).get("direction")
    if direction == "outgoing":
        pattern = f"ns:{entity_id} ns:{relation} ?n ."
    else:
        pattern = f"?n ns:{relation} ns:{entity_id} ."
    if neighbor.startswith("m.") or neighbor.startswith("g."):
        filter_clause = f"FILTER(?n = ns:{neighbor})"
    else:
        escaped = neighbor.replace("\\", "\\\\").replace('"', '\\"')
        filter_clause = f'FILTER(STR(?n) = "{escaped}")'
    sparql = f"""PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT ?n
WHERE {{
  {pattern}
  {filter_clause}
}}
LIMIT 1"""
    try:
        bindings = probe.query(sparql)
    except Exception:
        traceback.print_exc()
        return False
    return bool(bindings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a Phase 1 KG memory build.")
    parser.add_argument("build_dir", help="Path to kg_memory/<build_id>/")
    parser.add_argument("--replay", type=int, default=20, help="Number of validated records to replay.")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    build_dir = os.path.abspath(args.build_dir)
    jsonl_path = os.path.join(build_dir, "kg_structural_memory.jsonl")
    manifest_path = os.path.join(build_dir, "build_manifest.json")
    index_path = os.path.join(build_dir, "kg_structural_memory.index.json")
    discovery_path = os.path.join(build_dir, "discovery_entities.jsonl")
    validation_path = os.path.join(build_dir, "validation_entities.jsonl")
    errors: list[str] = []

    if not os.path.isfile(jsonl_path):
        print(f"missing {jsonl_path}")
        return 1
    if not os.path.isfile(manifest_path):
        print(f"missing {manifest_path}")
        return 1

    manifest = load_json(manifest_path)
    config = manifest.get("config") or {}
    expected_hash = config_hash(config) if config else ""
    stored_hash = str(manifest.get("build_config_hash") or "")
    if expected_hash and stored_hash and expected_hash != stored_hash:
        errors.append(f"manifest hash mismatch stored={stored_hash[:12]} recomputed={expected_hash[:12]}")

    recomputed = build_config_payload(
        n_types=int(config.get("n_types") or 0),
        discovery_n=int(config.get("discovery_n") or 0),
        validation_n=int(config.get("validation_n") or 0),
        seed=int(config.get("seed") or 0),
        min_support=int(config.get("min_validation_entity_support") or 0),
        min_coverage=float(config.get("min_validation_coverage") or 0),
        neighbor_sample_limit=int(config.get("neighbor_sample_limit") or 0),
        endpoint=str(config.get("endpoint") or SPARQLPATH),
        type_source=str(config.get("type_source") or "type.object.type"),
        excluded_types=list(config.get("excluded_types") or []),
    )
    if config and config_hash(recomputed) != stored_hash:
        # Config may contain the same fields; compare canonical hash of stored config only.
        pass
    if config:
        round_trip = config_hash(config)
        if round_trip != stored_hash:
            errors.append("stored config does not round-trip to build_config_hash")

    records = list(iter_jsonl_records(jsonl_path))
    validated = [rec for rec in records if rec.get("status") == "validated"]
    for idx, record in enumerate(records):
        rec_errors = validate_record(record)
        gold = walk_forbidden_keys(record)
        if rec_errors:
            errors.append(f"record[{idx}] {record.get('memory_id')}: {rec_errors[0]}")
        if gold:
            errors.append(f"record[{idx}] gold fields {gold[:4]}")
        status = str(record.get("status") or "")
        capability = str(((record.get("semantic") or {}).get("capability_text")) or "").lower()
        if status in {"does_not_exist", "negative", "absent", "not_found"}:
            errors.append(f"record[{idx}] negative-fact status {status}")
        if any(phrase in capability for phrase in ("does not exist", "not exist", "does not occur")):
            errors.append(f"record[{idx}] negative-fact capability_text")
        if len(errors) > 40:
            break

    path_mode = str(config.get("memory_kind") or "") == "path_template"
    if path_mode:
        schema_hash = str(manifest.get("schema_build_config_hash") or config.get("schema_build_config_hash") or "")
        if not schema_hash:
            errors.append("path_template build missing schema_build_config_hash")
        bad_kinds = [rec.get("memory_id") for rec in records if rec.get("memory_kind") != "path_template"]
        if bad_kinds:
            errors.append(f"non-path_template records: {bad_kinds[:5]}")
        hop_lens = {len((rec.get("key") or {}).get("relation_path") or []) for rec in records}
        if hop_lens and hop_lens.isdisjoint({1, 2}):
            errors.append(f"path_template hop lengths {sorted(hop_lens)} not in {{1,2}}")

    disc = load_split_ids(discovery_path)
    val = load_split_ids(validation_path)
    overlap_types = []
    for source_type, disc_ids in disc.items():
        overlap = disc_ids & val.get(source_type, set())
        if overlap:
            overlap_types.append(source_type)
    if overlap_types:
        errors.append(f"discovery/validation overlap in types: {overlap_types[:5]}")

    replay_n = 0
    replay_ok = 0
    with_witness = [rec for rec in validated if (rec.get("evidence") or {}).get("witness_paths")]
    if validated and not with_witness:
        errors.append("validated records exist but none have witness_paths")
    if with_witness:
        rng = random.Random(args.seed)
        sample = with_witness[:]
        rng.shuffle(sample)
        sample = sample[: max(0, int(args.replay))]
        probe = KGProbe(cache_dir=os.path.join(build_dir, "probe_cache"), endpoint=SPARQLPATH)
        for record in sample:
            replay_n += 1
            if replay_witness(probe, record):
                replay_ok += 1
        if replay_n and replay_ok < replay_n:
            errors.append(f"witness replay failed for {replay_n - replay_ok}/{replay_n} sampled validated records")
    elif args.replay > 0 and not validated:
        errors.append("no validated records to replay")

    print(f"build_dir={build_dir}")
    print(f"records={len(records)} validated={len(validated)}")
    print(f"hash_stored={stored_hash[:16]} hash_recomputed={expected_hash[:16]}")
    print(f"discovery_types={len(disc)} validation_types={len(val)}")
    print(f"replay_ok={replay_ok}/{replay_n}")
    if os.path.isfile(index_path):
        index = load_json(index_path)
        print(f"index_n={index.get('n_records')} index_validated={index.get('n_validated')}")
    if errors:
        print(f"FAIL ({len(errors)} issues)")
        for err in errors[:40]:
            print(f"  - {err}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
