"""Runtime helpers for constraint provenance, prompt injection, and stop gating.

All helpers are inert when constraint_pushdown is off.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from constraint_compiler import (
    MENTION_STOPWORDS,
    TYPE_UNIGRAMS,
    format_constraints_for_prompt,
    is_constraint_pushdown_enabled,
    normalize_text,
)


def parse_stage_set(raw: Any, default: str) -> set[str]:
    text = str(raw if raw not in (None, "") else default)
    return {part.strip().lower() for part in re.split(r"[,;\s]+", text) if part.strip()}


def should_inject_constraint_prompt(args: Any, stage: str) -> bool:
    if not is_constraint_pushdown_enabled(args):
        return False
    stages = parse_stage_set(getattr(args, "constraint_prompt_stages", ""), "relation,memory,reasoning,answer")
    return stage.lower() in stages or "all" in stages


def answer_gate_mode(args: Any) -> str:
    if not is_constraint_pushdown_enabled(args):
        return "off"
    mode = str(getattr(args, "constraint_answer_gate", "hard") or "hard").lower()
    if mode not in {"off", "soft", "hard"}:
        return "hard"
    return mode


def constraint_key(item: dict[str, Any]) -> str:
    mid = str(item.get("mid") or "").strip()
    if mid:
        return "entity:" + mid
    kind = str(item.get("kind") or "").strip()
    if kind in {"current", "year", "range"} or item.get("start") or item.get("asof_date"):
        return "time:" + str(item.get("asof_date") or item.get("start") or item.get("raw_text") or kind)
    if kind in {"min", "max"}:
        return "rank:" + kind
    return ""


def keys_from_applied_constraints(applied: Any) -> set[str]:
    keys = set()
    for item in applied or []:
        if isinstance(item, dict):
            key = constraint_key(item)
            if key:
                keys.add(key)
    return keys


def required_entity_keys(args: Any) -> set[str]:
    compiled = getattr(args, "current_constraints", {}) or {}
    return {
        "entity:" + str(item.get("mid")).strip()
        for item in compiled.get("entity_constraints", [])
        if item.get("mid")
    }


def get_coverage_map(args: Any) -> dict[str, set[str]]:
    coverage = getattr(args, "entity_constraint_coverage", None)
    if coverage is None:
        coverage = {}
        setattr(args, "entity_constraint_coverage", coverage)
    return coverage


def reset_coverage_map(args: Any) -> None:
    setattr(args, "entity_constraint_coverage", {})


def coverage_for(args: Any, entity_id: str) -> set[str]:
    return set(get_coverage_map(args).get(str(entity_id), set()))


def add_coverage(args: Any, entity_id: str, keys: set[str]) -> None:
    if not entity_id or not keys:
        return
    coverage = get_coverage_map(args)
    eid = str(entity_id)
    coverage[eid] = set(coverage.get(eid, set())) | set(keys)


def covers_required_entity_keys(args: Any, entity_id: str) -> bool:
    required = required_entity_keys(args)
    if not required:
        return False
    return required <= coverage_for(args, entity_id)


def covering_entity_ids(args: Any) -> set[str]:
    required = required_entity_keys(args)
    if not required:
        return set()
    return {eid for eid, keys in get_coverage_map(args).items() if required <= set(keys)}


def covering_answer_names(args: Any, entid_name: dict[str, str]) -> set[str]:
    names: set[str] = set()
    for eid in covering_entity_ids(args):
        names.add(str(eid))
        name = str(entid_name.get(eid, "") or "")
        if name and not name.startswith("m.") and not name.startswith("g."):
            names.add(name)
    return names


def normalize_answer_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def answer_in_covering_set(answer: Any, covering_names: set[str]) -> bool:
    if not covering_names:
        return True
    answer_key = normalize_answer_key(answer)
    if not answer_key or answer_key in {"null", "none"}:
        return False
    parts = [part.strip() for part in str(answer).replace("[", "").replace("]", "").replace("'", '"').split(",") if part.strip()]
    if not parts:
        parts = [str(answer)]
    for part in parts:
        part_key = normalize_answer_key(part.strip().strip('"').strip("'"))
        for name in covering_names:
            name_key = normalize_answer_key(name)
            if name_key and part_key and (part_key == name_key or part_key in name_key or name_key in part_key):
                return True
    return False


def constraint_prompt_suffix(args: Any, stage: str, covering_names: Optional[set[str]] = None) -> str:
    if not should_inject_constraint_prompt(args, stage):
        return ""
    context = format_constraints_for_prompt(getattr(args, "current_constraints", {}) or {})
    if not context:
        return ""
    parts = [
        "\nQuestion Constraints:\n" + context,
        "Answers must satisfy these constraints. Prefer facts from paths that already verified them.",
    ]
    if covering_names and answer_gate_mode(args) != "off":
        readable = sorted(name for name in covering_names if not str(name).startswith(("m.", "g.")))
        if readable:
            parts.append("Constraint-covering entities: " + "; ".join(readable[:20]))
    return "\n".join(parts) + "\n"


def append_constraint_prompt(prompt: str, args: Any, stage: str, covering_names: Optional[set[str]] = None) -> str:
    suffix = constraint_prompt_suffix(args, stage, covering_names=covering_names)
    if not suffix:
        return prompt
    return prompt + suffix


def filter_ent_rel_ent_dict_to_covering(
    ent_rel_ent_dict: dict,
    covering_ids: set[str],
) -> dict:
    if not covering_ids or not ent_rel_ent_dict:
        return ent_rel_ent_dict
    filtered: dict = {}
    for topic_e, h_t_dict in ent_rel_ent_dict.items():
        keep_topic = str(topic_e) in covering_ids
        for h_t, r_e_dict in (h_t_dict or {}).items():
            for rela, e_list in (r_e_dict or {}).items():
                kept = [eid for eid in e_list if keep_topic or str(eid) in covering_ids]
                if not kept:
                    continue
                filtered.setdefault(topic_e, {}).setdefault(h_t, {})[rela] = kept
    return filtered or ent_rel_ent_dict


def filter_cluster_chains_to_covering(cluster_chain_of_entities: list, covering_names: set[str]) -> list:
    if not covering_names or not cluster_chain_of_entities:
        return cluster_chain_of_entities
    name_keys = {normalize_answer_key(name) for name in covering_names}

    def keep_triple(triple) -> bool:
        if not isinstance(triple, (list, tuple)) or len(triple) < 3:
            return False
        for item in (triple[0], triple[2]):
            if normalize_answer_key(item) in name_keys:
                return True
        return False

    filtered = []
    for chain in cluster_chain_of_entities:
        kept = [triple for triple in (chain or []) if keep_triple(triple)]
        if kept:
            filtered.append(kept)
    return filtered or cluster_chain_of_entities


def apply_frontier_bias(entity_ids: list[str], args: Any) -> list[str]:
    if not is_constraint_pushdown_enabled(args):
        return entity_ids
    if not int(getattr(args, "constraint_frontier_bias", 1)):
        return entity_ids
    required = required_entity_keys(args)
    if not required or not entity_ids:
        return entity_ids
    covering = [eid for eid in entity_ids if covers_required_entity_keys(args, eid)]
    rest = [eid for eid in entity_ids if eid not in covering]
    if not covering:
        return entity_ids
    rest_cap = min(len(rest), max(2, len(covering)))
    return covering + rest[:rest_cap]


def quoted_literals(text: str) -> list[str]:
    return [item.strip() for item in re.findall(r"[\"']([^\"']+)[\"']", str(text or "")) if item.strip()]


def is_type_like_literal(text: str) -> bool:
    tokens = [part for part in normalize_text(text).split() if part]
    if not tokens:
        return False
    return all(token in TYPE_UNIGRAMS or token in MENTION_STOPWORDS for token in tokens)


def allowed_grounding_literals(question: str, args: Any) -> set[str]:
    allowed = set()
    for name in (getattr(args, "current_topic_entity", {}) or {}).values():
        if name:
            allowed.add(normalize_text(name))
    compiled = getattr(args, "current_constraints", {}) or {}
    for item in compiled.get("entity_constraints", []):
        for field in ("mention", "name"):
            if item.get(field):
                allowed.add(normalize_text(item.get(field)))
    allowed.add(normalize_text(question))
    return {item for item in allowed if item}


def literal_is_grounded(literal: str, question: str, allowed: set[str]) -> bool:
    norm = normalize_text(literal)
    if not norm:
        return True
    if norm in allowed:
        return True
    question_norm = normalize_text(question)
    if norm in question_norm and not is_type_like_literal(literal):
        return True
    return False


def ground_subobjectives(steps: list[str], question: str, args: Any) -> tuple[list[str], dict[str, Any]]:
    trace = {"stripped_literals": [], "dropped_steps": [], "kept_steps": []}
    if not is_constraint_pushdown_enabled(args) or not int(getattr(args, "decomposition_grounding_check", 1)):
        return steps, trace
    allowed = allowed_grounding_literals(question, args)
    grounded = []
    for step in steps:
        original = str(step)
        updated = original
        for literal in quoted_literals(original):
            if literal_is_grounded(literal, question, allowed) and not is_type_like_literal(literal):
                continue
            if is_type_like_literal(literal) or not literal_is_grounded(literal, question, allowed):
                trace["stripped_literals"].append(literal)
                updated = updated.replace(f'"{literal}"', "").replace(f"'{literal}'", "")
        updated = re.sub(r"\s+", " ", updated).strip(" :-,").strip()
        leftover_quotes = quoted_literals(updated)
        boilerplate = bool(re.search(r"\b(only include|filter the|filter to)\b", updated, flags=re.I))
        if len(updated.split()) < 3 or (boilerplate and not leftover_quotes):
            trace["dropped_steps"].append(original)
            continue
        grounded.append(updated)
        if updated != original:
            trace["kept_steps"].append({"before": original, "after": updated})
    return grounded or steps, trace


def mask_planning_steps(steps: list[str], topic_names: list[str]) -> list[str]:
    masked = []
    names = sorted({str(name) for name in topic_names if name}, key=len, reverse=True)
    for step in steps:
        text = str(step)
        for name in names:
            text = re.sub(re.escape(name), "<TOPIC>", text, flags=re.IGNORECASE)
        text = re.sub(r"[\"'][^\"']+[\"']", "<VALUE>", text)
        masked.append(text)
    return masked


def extract_names_from_text(text: str, entid_name: dict[str, str]) -> list[str]:
    found = []
    blob = str(text or "").lower()
    for eid, name in sorted(entid_name.items(), key=lambda item: -len(str(item[1]))):
        label = str(name or "")
        if label and not label.startswith(("m.", "g.")) and label.lower() in blob:
            found.append(eid)
    return found


def coverage_score_for_text(text: str, args: Any, entid_name: dict[str, str]) -> int:
    required = required_entity_keys(args)
    if not required:
        return 0
    best = 0
    for eid in extract_names_from_text(text, entid_name):
        best = max(best, len(required & coverage_for(args, eid)))
    return best


def merge_memory_conflicts(before: str, after: str, args: Any, entid_name: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    conflicts: list[dict[str, Any]] = []
    policy = str(getattr(args, "memory_conflict_policy", "keep_both") or "keep_both").lower()
    if policy != "keep_both" or not is_constraint_pushdown_enabled(args):
        return after, conflicts
    try:
        before_obj = json.loads(before) if str(before).strip().startswith("{") else {}
        after_obj = json.loads(after) if str(after).strip().startswith("{") else {}
    except Exception:
        try:
            import ast
            before_obj = ast.literal_eval(before) if str(before).strip().startswith("{") else {}
            after_obj = ast.literal_eval(after) if str(after).strip().startswith("{") else {}
        except Exception:
            return after, conflicts
    if not isinstance(before_obj, dict) or not isinstance(after_obj, dict):
        return after, conflicts
    merged = dict(after_obj)
    for key, old_value in before_obj.items():
        new_value = after_obj.get(key)
        if new_value is None or str(new_value).strip() == str(old_value).strip():
            continue
        old_score = coverage_score_for_text(str(old_value), args, entid_name)
        new_score = coverage_score_for_text(str(new_value), args, entid_name)
        if old_score == 0 and new_score == 0:
            continue
        conflicts.append({
            "slot": str(key),
            "previous": old_value,
            "new": new_value,
            "previous_coverage": old_score,
            "new_coverage": new_score,
        })
        if old_score > new_score:
            merged[key] = f"{old_value} Also mentioned: {new_value}"
        else:
            merged[key] = f"{new_value} Previously: {old_value}"
    return json.dumps(merged, ensure_ascii=False, indent=4), conflicts
