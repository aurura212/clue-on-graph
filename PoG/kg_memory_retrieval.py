"""Retrieve schema_profile / path_template evidence and apply it to PoG relation selection.

M1 uses schema_profile; M2 uses path_template first hops (prefer 2-hop stats).
Soft rerank or prompt, no hard filter. Memory cannot add SPARQL-absent relations.
"""

from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass, field
from typing import Any

from kg_structural_memory import (
    ALLOWED_DIRECTIONS,
    MEMORY_KIND_PATH_TEMPLATE,
    MEMORY_KIND_SCHEMA_PROFILE,
    iter_memory_records,
    load_json,
)
from relation_memory import parse_list_arg
from utils import estimate_token_count


FROZEN_SCHEMA_FULL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "kg_memory",
    "schema_full_ee55ef9f17_20260818_114056",
)
FROZEN_PATH_FULL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "kg_memory",
    "path_full_1f16016919_20260819_134058",
)
JSONL_NAME = "kg_structural_memory.jsonl"
INDEX_NAME = "kg_structural_memory.index.json"
MANIFEST_NAME = "build_manifest.json"

DEFAULT_SEMANTIC_WEIGHT = 0.7
DEFAULT_STRUCTURE_WEIGHT = 0.3
DEFAULT_FUSION = "additive"
ALLOWED_FUSIONS = ("additive", "multiplicative", "gated")
BRANCHING_REF = 50.0
SUPPORT_REF = 10.0
TAIL_SEM_FLOOR = 0.1


@dataclass
class SchemaRelStat:
    memory_id: str
    source_type: str
    direction: str
    relation: str
    status: str
    coverage: float
    support: int
    validation_n: int
    median_branching: float
    confidence: float
    capability_text: str
    endpoint_types: list[str] = field(default_factory=list)
    hop_length: int = 1
    relation_path: list[str] = field(default_factory=list)
    target_type: str = ""
    memory_kind: str = MEMORY_KIND_SCHEMA_PROFILE

    def structural_score(self, explored: bool = False) -> float:
        support_norm = min(1.0, self.support / SUPPORT_REF)
        branch_penalty = min(1.0, math.log1p(max(0.0, self.median_branching)) / math.log1p(BRANCHING_REF))
        raw = (
            0.45 * _clip01(self.coverage)
            + 0.35 * _clip01(self.confidence)
            + 0.20 * support_norm
            - 0.15 * branch_penalty
            - (0.25 if explored else 0.0)
        )
        return _clip01(raw)


@dataclass
class KGMemoryBank:
    path: str
    jsonl_path: str
    manifest: dict[str, Any]
    build_config_hash: str
    memory_build_id: str
    stats: dict[str, SchemaRelStat]
    by_type: dict[str, list[SchemaRelStat]]
    source_types: list[str]
    n_records: int
    n_validated: int
    type_cache: dict[str, list[str]] = field(default_factory=dict)
    variants: dict[str, list[SchemaRelStat]] = field(default_factory=dict)
    record_kinds: set[str] = field(default_factory=set)
    raw_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw_by_type: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def eligible(self, stat: SchemaRelStat, min_confidence: float, validated_only: bool) -> bool:
        if validated_only and stat.status != "validated":
            return False
        return stat.confidence + 1e-12 >= min_confidence

    def lookup(
        self,
        source_types: list[str],
        direction: str,
        relation: str,
        min_confidence: float,
        validated_only: bool,
    ) -> SchemaRelStat | None:
        best: SchemaRelStat | None = None
        for source_type in source_types:
            stat = self.stats.get((source_type, direction, relation))
            if stat is None or not self.eligible(stat, min_confidence, validated_only):
                continue
            if best is None or stat.confidence > best.confidence:
                best = stat
        return best

    def lookup_variants(
        self,
        source_types: list[str],
        direction: str,
        relation: str,
        min_confidence: float,
        validated_only: bool,
    ) -> list[SchemaRelStat]:
        """All templates sharing this first hop, not just the folded winner."""
        out: list[SchemaRelStat] = []
        for source_type in source_types:
            for stat in self.variants.get((source_type, direction, relation), []):
                if self.eligible(stat, min_confidence, validated_only):
                    out.append(stat)
        out.sort(key=lambda item: (-item.hop_length, -item.confidence, -item.coverage, item.memory_id))
        return out

    def records_for_type(
        self,
        source_type: str,
        min_confidence: float,
        validated_only: bool,
    ) -> list[SchemaRelStat]:
        out = [
            stat
            for stat in self.by_type.get(source_type, [])
            if self.eligible(stat, min_confidence, validated_only)
        ]
        out.sort(key=lambda item: (-item.confidence, -item.coverage, item.relation))
        return out


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def compact_stat(record: dict[str, Any]) -> SchemaRelStat | None:
    kind = str(record.get("memory_kind") or "")
    if kind not in {MEMORY_KIND_SCHEMA_PROFILE, MEMORY_KIND_PATH_TEMPLATE}:
        return None
    key = record.get("key") or {}
    source_type = str(key.get("source_type") or "")
    direction = str(key.get("direction") or "")
    relation_path = [str(rel) for rel in (key.get("relation_path") or []) if rel]
    relation = relation_path[0] if relation_path else ""
    if not source_type or direction not in ALLOWED_DIRECTIONS or not relation:
        return None
    if kind == MEMORY_KIND_PATH_TEMPLATE and not (1 <= len(relation_path) <= 2):
        return None
    stats = record.get("statistics") or {}
    semantic = record.get("semantic") or {}
    endpoints = []
    target_type = str(key.get("target_type") or "")
    if target_type:
        endpoints.append(target_type)
    for item in stats.get("endpoint_type_top") or []:
        tid = ""
        if isinstance(item, dict) and item.get("type"):
            tid = str(item["type"])
        elif isinstance(item, str):
            tid = item
        if tid and tid not in endpoints:
            endpoints.append(tid)
    path_text = " -> ".join(relation_path)
    default_cap = f"{source_type} {direction} {path_text}"
    if target_type:
        default_cap += f" -> {target_type}"
    return SchemaRelStat(
        memory_id=str(record.get("memory_id") or ""),
        source_type=source_type,
        direction=direction,
        relation=relation,
        status=str(record.get("status") or ""),
        coverage=_as_float(stats.get("validation_coverage")),
        support=_as_int(stats.get("validation_entity_support")),
        validation_n=_as_int(stats.get("validation_n")),
        median_branching=_as_float(stats.get("median_branching")),
        confidence=_as_float(stats.get("confidence")),
        capability_text=str(semantic.get("capability_text") or default_cap),
        endpoint_types=endpoints[:6],
        hop_length=len(relation_path),
        relation_path=relation_path,
        target_type=target_type,
        memory_kind=kind,
    )


def _prefer_compact_stat(prev: SchemaRelStat | None, stat: SchemaRelStat) -> SchemaRelStat:
    """One first-hop key: prefer 2-hop path_template over 1-hop, then higher confidence."""
    if prev is None:
        return stat
    if stat.hop_length != prev.hop_length:
        return stat if stat.hop_length > prev.hop_length else prev
    if stat.confidence != prev.confidence:
        return stat if stat.confidence > prev.confidence else prev
    if stat.coverage != prev.coverage:
        return stat if stat.coverage > prev.coverage else prev
    return prev


def resolve_kg_memory_path(path: str) -> str:
    text = (path or "").strip()
    if not text:
        return ""
    if not os.path.isabs(text):
        here = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(here, text)
        if os.path.exists(candidate):
            text = candidate
        else:
            text = os.path.abspath(text)
    return text


def resolve_memory_files(path: str) -> tuple[str, str, str]:
    resolved = resolve_kg_memory_path(path)
    if not resolved:
        raise FileNotFoundError("kg_memory_path is empty")
    if os.path.isdir(resolved):
        jsonl_path = os.path.join(resolved, JSONL_NAME)
        index_path = os.path.join(resolved, INDEX_NAME)
        manifest_path = os.path.join(resolved, MANIFEST_NAME)
        return jsonl_path, index_path, manifest_path
    jsonl_path = resolved
    parent = os.path.dirname(resolved)
    return jsonl_path, os.path.join(parent, INDEX_NAME), os.path.join(parent, MANIFEST_NAME)


def bank_from_records(
    records: list[dict[str, Any]],
    *,
    path: str = "",
    jsonl_path: str = "",
    manifest: dict[str, Any] | None = None,
) -> KGMemoryBank:
    stats: dict[tuple[str, str, str], SchemaRelStat] = {}
    variants: dict[tuple[str, str, str], list[SchemaRelStat]] = {}
    by_type: dict[str, list[SchemaRelStat]] = {}
    raw_by_id: dict[str, dict[str, Any]] = {}
    raw_by_type: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        stat = compact_stat(record)
        if stat is None:
            continue
        key = (stat.source_type, stat.direction, stat.relation)
        stats[key] = _prefer_compact_stat(stats.get(key), stat)
        variants.setdefault(key, []).append(stat)
        if stat.memory_id:
            raw_by_id[stat.memory_id] = record
        raw_by_type.setdefault(stat.source_type, []).append(record)
    for items in variants.values():
        items.sort(key=lambda item: (-item.hop_length, -item.confidence, -item.coverage, item.memory_id))
        for stat in items:
            by_type.setdefault(stat.source_type, []).append(stat)
    for items in by_type.values():
        items.sort(key=lambda item: (-item.confidence, item.relation))
    manifest = manifest or {}
    config = manifest.get("config") or {}
    return KGMemoryBank(
        path=path or jsonl_path,
        jsonl_path=jsonl_path,
        manifest=manifest,
        build_config_hash=str(manifest.get("build_config_hash") or config.get("build_config_hash") or ""),
        memory_build_id=str(manifest.get("memory_build_id") or ""),
        stats=stats,
        by_type=by_type,
        source_types=sorted(by_type),
        n_records=len(stats),
        n_validated=sum(1 for item in stats.values() if item.status == "validated"),
        variants=variants,
        record_kinds={item.memory_kind for item in stats.values()},
        raw_by_id=raw_by_id,
        raw_by_type=raw_by_type,
    )


def load_kg_memory_bank(path: str) -> KGMemoryBank:
    jsonl_path, _index_path, manifest_path = resolve_memory_files(path)
    if not os.path.isfile(jsonl_path):
        raise FileNotFoundError(f"KG structural memory JSONL not found: {jsonl_path}")
    manifest: dict[str, Any] = {}
    if os.path.isfile(manifest_path):
        manifest = load_json(manifest_path)
    records = list(iter_memory_records(jsonl_path))
    bank = bank_from_records(
        records,
        path=os.path.dirname(jsonl_path),
        jsonl_path=jsonl_path,
        manifest=manifest,
    )
    if not bank.build_config_hash:
        raise ValueError(f"build_config_hash missing in {manifest_path or jsonl_path}")
    return bank


REFLECTION_STAGE_NAMES = {
    "reflection",
    "reflection_judge",
    "reflection_select",
    "reflection_a",
    "reflection_b",
}


def _normalized_memory_stages(args: Any) -> list[str]:
    stages = parse_list_arg(getattr(args, "kg_memory_stages", "relation"), ["relation"])
    return [str(name).strip().lower() for name in stages if str(name).strip()]


def should_use_kg_memory_at_stage(args: Any, stage: str) -> bool:
    mode = str(getattr(args, "kg_memory_mode", "none") or "none").strip().lower()
    stage = str(stage or "").strip().lower()
    if mode in {"", "none"}:
        return False
    stages = _normalized_memory_stages(args)
    if not stages or "none" in stages:
        return False
    if stage == "relation":
        if mode not in {"relation", "full"}:
            return False
        return "all" in stages or "relation" in stages
    if stage.startswith("reflection") or stage in REFLECTION_STAGE_NAMES:
        if mode not in {"reflection", "full"}:
            return False
        if "all" in stages or "reflection" in stages:
            return True
        reflection_requested = [name for name in stages if name in REFLECTION_STAGE_NAMES or name.startswith("reflection")]
        if not reflection_requested:
            # argparse default leftover: mode=reflection, stages=relation → enable A/B, not first-hop.
            return mode == "reflection"
        aliases = {stage}
        if stage in {"reflection_a", "reflection_judge"}:
            aliases.update({"reflection_a", "reflection_judge", "reflection"})
        if stage in {"reflection_b", "reflection_select"}:
            aliases.update({"reflection_b", "reflection_select", "reflection"})
        return bool(set(stages) & aliases)
    return stage in stages


def infer_bank_memory_kind(bank: KGMemoryBank | None, args: Any | None = None) -> str:
    kind = str(getattr(args, "kg_memory_kind", "") or "") if args is not None else ""
    if kind:
        return kind
    if bank is None:
        return ""
    cfg = (getattr(bank, "manifest", {}) or {}).get("config") or {}
    kind = str(cfg.get("memory_kind") or "")
    if kind:
        return kind
    kinds = getattr(bank, "record_kinds", None) or set()
    if len(kinds) == 1:
        return next(iter(kinds))
    builder = str((getattr(bank, "manifest", {}) or {}).get("builder_version") or "")
    if "path" in builder:
        return MEMORY_KIND_PATH_TEMPLATE
    return MEMORY_KIND_SCHEMA_PROFILE


def kg_memory_kind_tag(args: Any) -> str:
    bank = getattr(args, "kg_memory_bank", None)
    kind = infer_bank_memory_kind(bank, args)
    if kind in {MEMORY_KIND_PATH_TEMPLATE, "path"}:
        return "path"
    return ""


def kg_memory_runtime_meta(args: Any) -> dict[str, Any]:
    bank = getattr(args, "kg_memory_bank", None)
    return {
        "kg_memory_mode": getattr(args, "kg_memory_mode", "none"),
        "kg_memory_path": getattr(args, "kg_memory_path", ""),
        "kg_memory_stages": getattr(args, "kg_memory_stages", "relation"),
        "kg_memory_top_k": int(getattr(args, "kg_memory_top_k", 6)),
        "kg_memory_strategy": getattr(args, "kg_memory_strategy", "rerank"),
        "kg_memory_min_confidence": float(getattr(args, "kg_memory_min_confidence", 0.6)),
        "kg_memory_prompt_token_budget": int(getattr(args, "kg_memory_prompt_token_budget", 600)),
        "kg_memory_online_verify": int(getattr(args, "kg_memory_online_verify", 0)),
        "kg_memory_online_query_budget": int(getattr(args, "kg_memory_online_query_budget", 0)),
        "kg_memory_ablation": getattr(args, "kg_memory_ablation", "none"),
        "kg_memory_seed": int(getattr(args, "kg_memory_seed", 42)),
        "kg_memory_semantic_weight": float(getattr(args, "kg_memory_semantic_weight", DEFAULT_SEMANTIC_WEIGHT)),
        "kg_memory_structure_weight": float(getattr(args, "kg_memory_structure_weight", DEFAULT_STRUCTURE_WEIGHT)),
        "kg_memory_fusion": str(getattr(args, "kg_memory_fusion", DEFAULT_FUSION) or DEFAULT_FUSION).strip().lower(),
        "kg_memory_use_tail_sem": int(getattr(args, "kg_memory_use_tail_sem", 1)),
        "kg_memory_validated_only": int(getattr(args, "kg_memory_validated_only", 1)),
        "kg_memory_kind": infer_bank_memory_kind(bank, args),
        "kg_memory_hash": getattr(bank, "build_config_hash", "") if bank is not None else "",
        "kg_memory_build_id": getattr(bank, "memory_build_id", "") if bank is not None else "",
        "kg_memory_n_records": getattr(bank, "n_records", 0) if bank is not None else 0,
        "kg_memory_n_validated": getattr(bank, "n_validated", 0) if bank is not None else 0,
        "kg_memory_builder_version": ((getattr(bank, "manifest", {}) or {}).get("builder_version") if bank is not None else ""),
    }


def lookup_entity_types(entity_id: str, args: Any) -> list[str]:
    bank = getattr(args, "kg_memory_bank", None)
    cache = getattr(bank, "type_cache", None) if bank is not None else None
    if cache is None:
        cache = getattr(args, "kg_memory_type_cache", None)
        if cache is None:
            cache = {}
            setattr(args, "kg_memory_type_cache", cache)
    if entity_id in cache:
        return list(cache[entity_id])

    from constraint_compiler import build_types_query, entity_from_binding, is_mid
    from kg_probe import is_excluded_type, strip_ns

    types: list[str] = []
    if is_mid(entity_id):
        try:
            from freebase_func import execurte_sparql

            bindings = execurte_sparql(build_types_query([entity_id]))
        except Exception:
            bindings = []
        seen = set()
        for binding in bindings or []:
            type_id = strip_ns(entity_from_binding(binding, "type"))
            if not type_id or type_id in seen or is_excluded_type(type_id):
                continue
            seen.add(type_id)
            types.append(type_id)
    cache[entity_id] = types
    if bank is not None:
        bank.type_cache = cache
    return list(types)


def _minmax_norm(values: list[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi - lo < 1e-8:
        return [0.5 for _ in values]
    return [(value - lo) / (hi - lo) for value in values]


def relation_semantic_scores(question: str, relations: list[str], model: Any) -> dict[str, float]:
    if not relations:
        return {}
    if model is None:
        return {rel: 0.0 for rel in relations}
    from sentence_transformers import util

    query_emb = model.encode(question)
    doc_emb = model.encode(relations)
    scores = util.dot_score(query_emb, doc_emb)[0].cpu().tolist()
    return {rel: float(score) for rel, score in zip(relations, scores)}


def _rng_for(args: Any, entity_id: str) -> random.Random:
    seed = int(getattr(args, "kg_memory_seed", 42))
    material = f"{seed}|{entity_id}|{getattr(args, 'kg_memory_ablation', 'none')}"
    return random.Random(material)


def _pick_irrelevant_type(bank: KGMemoryBank, entity_types: list[str], rng: random.Random) -> str:
    current = set(entity_types)
    others = [tid for tid in bank.source_types if tid not in current]
    if not others:
        others = list(bank.source_types)
    if not others:
        return ""
    return others[rng.randrange(len(others))]


def _direction_for(relation: str, head_set: set[str], tail_set: set[str]) -> str | None:
    in_head = relation in head_set
    in_tail = relation in tail_set
    if in_head and not in_tail:
        return "outgoing"
    if in_tail and not in_head:
        return "incoming"
    if in_head and in_tail:
        return "both"
    return None


def retrieve_relation_stats(
    bank: KGMemoryBank,
    entity_types: list[str],
    relation: str,
    head_set: set[str],
    tail_set: set[str],
    min_confidence: float,
    validated_only: bool,
) -> SchemaRelStat | None:
    direction = _direction_for(relation, head_set, tail_set)
    if direction == "outgoing":
        return bank.lookup(entity_types, "outgoing", relation, min_confidence, validated_only)
    if direction == "incoming":
        return bank.lookup(entity_types, "incoming", relation, min_confidence, validated_only)
    if direction == "both":
        left = bank.lookup(entity_types, "outgoing", relation, min_confidence, validated_only)
        right = bank.lookup(entity_types, "incoming", relation, min_confidence, validated_only)
        candidates = [item for item in (left, right) if item is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.confidence, item.coverage))
    return None


def retrieve_relation_variants(
    bank: KGMemoryBank,
    entity_types: list[str],
    relation: str,
    head_set: set[str],
    tail_set: set[str],
    min_confidence: float,
    validated_only: bool,
) -> list[SchemaRelStat]:
    direction = _direction_for(relation, head_set, tail_set)
    if direction is None:
        return []
    directions = ["outgoing", "incoming"] if direction == "both" else [direction]
    out: list[SchemaRelStat] = []
    for item in directions:
        out += bank.lookup_variants(entity_types, item, relation, min_confidence, validated_only)
    out.sort(key=lambda stat: (-stat.hop_length, -stat.confidence, -stat.coverage, stat.memory_id))
    return out


def tail_text(stat: SchemaRelStat) -> str:
    """What the template leads to, i.e. the part a question can be matched against."""
    parts: list[str] = []
    if stat.hop_length >= 2 and len(stat.relation_path) >= 2:
        parts.append(stat.relation_path[1])
    if stat.target_type:
        parts.append(stat.target_type)
    if not parts:
        parts.append(stat.relation)
    return " ".join(parts).replace("_", " ").replace(".", " ")


def tail_semantic_norm(question: str, stats: list[SchemaRelStat], model: Any) -> dict[str, float]:
    """Min-max normalized question match per tail text, floored so a hit never scores 0."""
    texts = list(
        dict.fromkeys(
            tail_text(stat)
            for stat in stats
            if stat is not None and stat.memory_kind == MEMORY_KIND_PATH_TEMPLATE
        )
    )
    if not texts:
        return {}
    raw = relation_semantic_scores(question, texts, model)
    norm = _minmax_norm([float(raw.get(text, 0.0)) for text in texts])
    return {text: TAIL_SEM_FLOOR + (1.0 - TAIL_SEM_FLOOR) * value for text, value in zip(texts, norm)}


def score_relation_variants(
    variants: list[SchemaRelStat],
    tail_norm: dict[str, float],
    explored: bool,
) -> tuple[float, SchemaRelStat | None]:
    """Structural score of a first hop = best question-conditional template behind it."""
    best_score = 0.0
    best_stat: SchemaRelStat | None = None
    for stat in variants:
        base = stat.structural_score(explored=explored)
        if stat.memory_kind == MEMORY_KIND_PATH_TEMPLATE:
            base *= tail_norm.get(tail_text(stat), 1.0)
        if best_stat is None or base > best_score:
            best_score, best_stat = base, stat
    return best_score, best_stat


def format_stat_line(stat: SchemaRelStat) -> str:
    endpoints = ", ".join(stat.endpoint_types[:3]) or "unknown"
    path_txt = " -> ".join(stat.relation_path) if stat.relation_path else stat.relation
    return (
        f"- {stat.source_type} --{stat.direction}--> {path_txt} | "
        f"coverage={stat.coverage:.2f} support={stat.support}/{stat.validation_n or '?'} "
        f"branching={stat.median_branching:.1f} confidence={stat.confidence:.2f} "
        f"status={stat.status} hops={stat.hop_length} endpoints={endpoints}"
    )


def format_relation_evidence_prompt(
    stats: list[SchemaRelStat],
    entity_types: list[str],
    token_budget: int,
    engine: str = "",
) -> str:
    if not stats:
        return ""
    header = (
        "KG structural evidence is type-conditional coverage or a short path template, not the answer. "
        "Use it together with the current question; do not pick a relation only because it is frequent. "
        f"Entity types: {', '.join(entity_types) if entity_types else 'unknown'}."
    )
    lines = [header]
    for stat in stats:
        candidate = "\n".join(lines + [format_stat_line(stat)])
        if estimate_token_count(candidate, engine) > max(1, token_budget):
            break
        lines.append(format_stat_line(stat))
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def empty_relation_trace(
    *,
    entity_id: str,
    entity_types: list[str],
    strategy: str,
    ablation: str,
    order: list[str],
) -> dict[str, Any]:
    return {
        "query_key": {
            "entity_id": entity_id,
            "entity_types": list(entity_types),
            "stage": "relation",
        },
        "retrieved_ids": [],
        "scores": [],
        "order_before": list(order),
        "order_after": list(order),
        "strategy": strategy,
        "ablation": ablation,
        "prompt_tokens": 0,
        "added_relations": [],
        "dropped_relations": [],
        "n_memory_hits": 0,
    }


def _normalize_fusion(name: Any) -> str:
    fusion = str(name or DEFAULT_FUSION).strip().lower()
    return fusion if fusion in ALLOWED_FUSIONS else DEFAULT_FUSION


def combine_semantic_structure(sem_raw: float, struct: float, w_sem: float, w_str: float, fusion: str) -> float:
    """Combine question semantic and schema structure. Membership never changes."""
    if fusion == "multiplicative":
        # Structure only scales an already question-relevant candidate.
        return sem_raw * (w_sem + w_str * struct)
    return w_sem * sem_raw + w_str * struct


def protect_zero_struct_from_lower_semantic(
    order_idx: list[int],
    sem: list[float],
    struct: list[float],
) -> list[int]:
    """A no-hit relation may not sit below a hit with strictly lower semantic.

    High-coverage schema_profile edges can otherwise outrank gold that has no
    validated memory (struct=0) even when the question match is stronger.
    """
    order = list(order_idx)
    n = len(order)
    swapped = True
    while swapped:
        swapped = False
        for k in range(n - 1):
            left, right = order[k], order[k + 1]
            if struct[left] > 0.0 and struct[right] <= 0.0 and sem[right] > sem[left]:
                order[k], order[k + 1] = right, left
                swapped = True
    return order


def apply_relation_kg_memory(
    *,
    question: str,
    entity_id: str,
    retrieved_relations: list[str],
    head_relations: list[str],
    tail_relations: list[str],
    pre_relations: list[str] | None,
    args: Any,
) -> tuple[list[str], str, dict[str, Any]]:
    """Rerank or prompt-annotate SPARQL/semantic candidates. Never add or drop relations."""
    strategy = str(getattr(args, "kg_memory_strategy", "rerank") or "rerank").strip().lower()
    ablation = str(getattr(args, "kg_memory_ablation", "none") or "none").strip().lower()
    original = list(retrieved_relations)
    bank: KGMemoryBank | None = getattr(args, "kg_memory_bank", None)
    entity_types = lookup_entity_types(entity_id, args) if bank is not None else []
    indexed_types = [tid for tid in entity_types if bank is not None and tid in bank.by_type]
    trace = empty_relation_trace(
        entity_id=entity_id,
        entity_types=entity_types,
        strategy=strategy,
        ablation=ablation,
        order=original,
    )
    trace["query_key"]["indexed_types"] = indexed_types
    if bank is None or not original:
        return original, "", trace

    min_confidence = float(getattr(args, "kg_memory_min_confidence", 0.6))
    validated_only = bool(int(getattr(args, "kg_memory_validated_only", 1)))
    top_k = max(0, int(getattr(args, "kg_memory_top_k", 6)))
    token_budget = max(1, int(getattr(args, "kg_memory_prompt_token_budget", 600)))
    w_sem = float(getattr(args, "kg_memory_semantic_weight", DEFAULT_SEMANTIC_WEIGHT))
    w_str = float(getattr(args, "kg_memory_structure_weight", DEFAULT_STRUCTURE_WEIGHT))
    fusion = _normalize_fusion(getattr(args, "kg_memory_fusion", DEFAULT_FUSION))
    if w_sem + w_str <= 0:
        w_sem, w_str = DEFAULT_SEMANTIC_WEIGHT, DEFAULT_STRUCTURE_WEIGHT
    total_w = w_sem + w_str
    w_sem, w_str = w_sem / total_w, w_str / total_w

    head_set = set(head_relations)
    tail_set = set(tail_relations)
    explored = set(pre_relations or [])
    model = getattr(args, "sentence_model", None)
    unique_for_encode = list(dict.fromkeys(original))
    raw_semantic = relation_semantic_scores(question, unique_for_encode, model)
    sem_norm_map = dict(zip(unique_for_encode, _minmax_norm([raw_semantic[rel] for rel in unique_for_encode])))

    # M1 keeps the single-record lookup so its frozen results stay reproducible;
    # only path_template banks fan out to every template behind a first hop.
    fan_out = infer_bank_memory_kind(bank, args) == MEMORY_KIND_PATH_TEMPLATE
    variant_lists: list[list[SchemaRelStat]] = []
    for rel in original:
        if fan_out:
            variant_lists.append(
                retrieve_relation_variants(
                    bank, indexed_types, rel, head_set, tail_set, min_confidence, validated_only
                )
            )
            continue
        stat = retrieve_relation_stats(
            bank, indexed_types, rel, head_set, tail_set, min_confidence, validated_only
        )
        variant_lists.append([stat] if stat is not None else [])
    use_tail = bool(int(getattr(args, "kg_memory_use_tail_sem", 1)))
    if use_tail:
        tail_norm = tail_semantic_norm(question, [s for group in variant_lists for s in group], model)
    else:
        tail_norm = {}
    true_stats: list[SchemaRelStat | None] = []
    true_struct: list[float] = []
    for rel, variants in zip(original, variant_lists):
        score, stat = score_relation_variants(variants, tail_norm, rel in explored)
        true_stats.append(stat)
        true_struct.append(score if stat is not None else 0.0)

    rng = _rng_for(args, entity_id)
    struct_scores = list(true_struct)
    prompt_stats: list[SchemaRelStat] = []

    if ablation == "shuffle":
        hit_idx = [i for i, score in enumerate(struct_scores) if score > 0]
        values = [struct_scores[i] for i in hit_idx]
        rng.shuffle(values)
        for i, value in zip(hit_idx, values):
            struct_scores[i] = value
        prompt_stats = [stat for stat in true_stats if stat is not None][:top_k]
        rng.shuffle(prompt_stats)
    elif ablation == "irrelevant":
        other_type = _pick_irrelevant_type(bank, indexed_types, rng)
        other_records = bank.records_for_type(other_type, min_confidence, validated_only) if other_type else []
        other_tail_norm = tail_semantic_norm(question, other_records, model) if use_tail else {}
        other_scores = [
            score_relation_variants([item], other_tail_norm, False)[0] for item in other_records
        ]
        struct_scores = [0.0 for _ in original]
        alpha_idx = sorted(range(len(original)), key=lambda i: original[i])
        for rank, idx in enumerate(alpha_idx):
            if rank < len(other_scores):
                struct_scores[idx] = other_scores[rank]
        prompt_stats = other_records[:top_k]
        trace["query_key"]["irrelevant_type"] = other_type
    else:
        prompt_stats = [stat for stat in true_stats if stat is not None]
        prompt_stats.sort(key=lambda item: (-item.confidence, -item.coverage, item.relation))
        prompt_stats = prompt_stats[:top_k]

    score_rows = []
    fused = []
    for rel, stat, sem_raw, struct, variants in zip(
        original,
        true_stats,
        [sem_norm_map.get(rel, 0.5) for rel in original],
        struct_scores,
        variant_lists,
    ):
        fused_value = combine_semantic_structure(sem_raw, struct, w_sem, w_str, fusion)
        fused.append(fused_value)
        score_rows.append(
            {
                "relation": rel,
                "semantic": round(sem_raw, 4),
                "structural": round(struct, 4),
                "fused": round(fused_value, 4),
                "memory_id": (stat.memory_id if stat is not None else ""),
                "source_type": (stat.source_type if stat is not None else ""),
                "coverage": (stat.coverage if stat is not None else None),
                "confidence": (stat.confidence if stat is not None else None),
                "hop_length": (stat.hop_length if stat is not None else 0),
                "relation_path": (list(stat.relation_path) if stat is not None else []),
                "memory_kind": (stat.memory_kind if stat is not None else ""),
                "n_variants": len(variants),
                "tail_semantic": (
                    round(tail_norm.get(tail_text(stat), 1.0), 4)
                    if (stat is not None and use_tail)
                    else None
                ),
            }
        )

    ranked = list(range(len(original)))
    ranked.sort(key=lambda i: (-fused[i], i))
    order_after_unprotected = [original[i] for i in ranked]
    sem_for_protect = [sem_norm_map.get(rel, 0.5) for rel in original]
    if fusion == "gated":
        ranked = protect_zero_struct_from_lower_semantic(ranked, sem_for_protect, struct_scores)
    order_after = [original[i] for i in ranked]
    if strategy != "rerank":
        order_after = list(original)
        order_after_unprotected = list(original)

    prompt_text = ""
    if strategy == "prompt":
        prompt_text = format_relation_evidence_prompt(
            prompt_stats,
            indexed_types or entity_types,
            token_budget,
            engine=str(getattr(args, "LLM_type", "")),
        )

    before_set = list(original)
    after_set = list(order_after)
    added = [rel for rel in after_set if rel not in before_set]
    dropped = [rel for rel in before_set if rel not in after_set]
    if added or dropped:
        # Safety: never change membership. Fall back to original order.
        order_after = list(original)
        added, dropped = [], []

    trace.update(
        {
            "retrieved_ids": [stat.memory_id for stat in prompt_stats if stat is not None],
            "scores": score_rows,
            "order_before": list(original),
            "order_after": list(order_after),
            "prompt_tokens": estimate_token_count(prompt_text, str(getattr(args, "LLM_type", ""))) if prompt_text else 0,
            "added_relations": added,
            "dropped_relations": dropped,
            "n_memory_hits": sum(1 for stat in true_stats if stat is not None),
            "semantic_weight": round(w_sem, 4),
            "structure_weight": round(w_str, 4),
            "fusion": fusion,
            "order_after_unprotected": list(order_after_unprotected),
        }
    )
    return order_after, prompt_text, trace


def _reflection_record_rank(record: dict[str, Any]) -> tuple[float, float, int]:
    stats = record.get("statistics") or {}
    return (
        _as_float(stats.get("confidence")),
        _as_float(stats.get("validation_coverage")),
        _as_int(stats.get("validation_entity_support")),
    )


def retrieve_reflection_records(
    args: Any,
    entity_ids: list[str],
    *,
    types_by_entity: dict[str, list[str]] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Type-matched raw memory records for Decision A/B. Does not rerank relations.

    Returns (entity_id, record) pairs. Skips SPARQL when types_by_entity is provided.
    Caps per entity so reflection prompts stay bounded.
    """
    bank = getattr(args, "kg_memory_bank", None)
    if bank is None or not entity_ids:
        return []
    max_per_entity = max(8, int(getattr(args, "kg_memory_top_k", 6)) * 4)
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    raw_by_type = getattr(bank, "raw_by_type", None) or {}
    for entity_id in entity_ids:
        if types_by_entity is not None:
            types = list(types_by_entity.get(entity_id) or [])
        else:
            types = lookup_entity_types(entity_id, args)
        for source_type in types:
            for record in raw_by_type.get(source_type) or []:
                memory_id = str(record.get("memory_id") or "")
                key = (entity_id, memory_id)
                if memory_id and key in seen:
                    continue
                if memory_id:
                    seen.add(key)
                grouped.setdefault(entity_id, []).append(record)
    pairs: list[tuple[str, dict[str, Any]]] = []
    for entity_id, records in grouped.items():
        records.sort(key=_reflection_record_rank, reverse=True)
        for record in records[:max_per_entity]:
            pairs.append((entity_id, record))
    return pairs


def attach_kg_memory_relation_events(depth_record: dict[str, Any]) -> None:
    events = []
    for rel_trace in depth_record.get("relation_prune") or []:
        if isinstance(rel_trace, dict) and isinstance(rel_trace.get("kg_memory"), dict) and rel_trace["kg_memory"]:
            events.append(rel_trace["kg_memory"])
    kg_memory = depth_record.setdefault("kg_memory", {"relation": {}, "reflection_judge": {}, "reflection_select": {}})
    if not isinstance(kg_memory, dict):
        kg_memory = {"relation": {}, "reflection_judge": {}, "reflection_select": {}}
        depth_record["kg_memory"] = kg_memory
    kg_memory["relation"] = {
        "n_events": len(events),
        "n_memory_hits": sum(int(event.get("n_memory_hits") or 0) for event in events),
        "n_order_changed": sum(
            1 for event in events if event.get("order_before") != event.get("order_after")
        ),
        "events": events,
    }
    reverse = depth_record.get("reverse_retrieval") or {}
    if not isinstance(reverse, dict):
        reverse = {}
    decision_a = reverse.get("decision_a") or {}
    decision_b = reverse.get("decision_b") or {}
    if isinstance(decision_a, dict) and decision_a.get("evidence"):
        kg_memory["reflection_judge"] = decision_a.get("evidence")
    if isinstance(decision_b, dict) and decision_b.get("evidence"):
        kg_memory["reflection_select"] = decision_b.get("evidence")
