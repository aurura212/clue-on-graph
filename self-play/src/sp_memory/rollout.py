"""SP2-B no-experience-memory LLM+KG rollout controller."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .action_parser import (
    abstain_action,
    map_stop_or_continue,
    parse_add_flag,
    parse_entity_list,
    relations_to_expand_actions,
    select_frontier_action,
)
from .action_validator import validate_action
from .budget_ledger import CounterLedger
from .errors import ProtocolError, ViolationCode
from .hashing import canonical_hash
from .live_environment import LiveKgBinding
from .llm_client import LlmClient
from .o0_prompt import O0PromptBuilder
from .paths import PROTOCOL_VERSION
from .pog_adapter import PoGAdapter, make_sp1_snapshot
from .schemas import (
    Action,
    ActionType,
    AbstainReason,
    ActorRole,
    Budget,
    DecisionStage,
    Direction,
    FailureClass,
    TerminationReason,
    VisibleRelation,
)
from .sp2b_guards import Sp2bGuards, audit_actor_payload, public_task_view
from .visibility import OracleSecrets
from .working_memory import PogWorkingMemory

ABANDON_PREFIXES = ("http://", "common.", "freebase.")
ABANDON_EXACT = {"type.object.type", "type.object.name"}


def abandon_rels(relation: str) -> bool:
    if not relation:
        return True
    if relation in ABANDON_EXACT or relation.startswith(ABANDON_PREFIXES) or "sameAs" in relation:
        return True
    return False


def chain_prompt_from_dict(ent_rel_ent_dict: Mapping[str, Any], entid_name: Mapping[str, str]) -> str:
    lines = []
    for topic_e, h_t_dict in sorted(ent_rel_ent_dict.items()):
        for _h_t, r_e_dict in sorted((h_t_dict or {}).items()):
            for rela, e_list in sorted((r_e_dict or {}).items()):
                names = [entid_name.get(e_id, e_id) for e_id in sorted(e_list or [])]
                lines.append(f"{entid_name.get(topic_e, topic_e)} {rela} {names}")
    return "\n".join(lines)


def _unicode_cap(values: Sequence[str], limit: int) -> List[str]:
    unique = sorted({item for item in values if item})
    return unique[:limit]


class Sp2bRollout:
    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        adapter: PoGAdapter,
        env: LiveKgBinding,
        llm: LlmClient,
        prompts: O0PromptBuilder,
        working_memory: PogWorkingMemory,
        guards: Sp2bGuards,
        secrets: OracleSecrets,
        ledger: CounterLedger,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.env = env
        self.llm = llm
        self.prompts = prompts
        self.mem = working_memory
        self.guards = guards
        self.secrets = secrets
        self.ledger = ledger
        self.trace: List[Dict[str, Any]] = []
        self.illegal_kg = 0
        self.token_totals = {"total": 0, "input": 0, "output": 0}

    def _o0_allowed(self, snapshot, *extra: str) -> List[str]:
        values = [snapshot.question, *extra]
        values.extend(snapshot.source_entities)
        values.extend(snapshot.topic_entity.keys())
        values.extend(snapshot.topic_entity.values())
        values.extend(snapshot.entid_name.keys())
        values.extend(snapshot.entid_name.values())
        values.extend(snapshot.name_entid.keys())
        values.extend(snapshot.name_entid.values())
        values.extend(item.relation for item in snapshot.enumerated_relations)
        for triple in snapshot.observed_triples or []:
            values.extend(str(triple.get(key) or "") for key in ("subject", "relation", "object"))
        return [item for item in values if item]

    def _budget_ok(self, snapshot, extra_kg: int = 0, extra_llm: int = 0) -> Optional[str]:
        remaining = snapshot.budget.remaining()
        if remaining.get("kg_calls", 0) < extra_kg:
            return "kg_calls"
        if remaining.get("llm_calls", 0) < extra_llm:
            return "llm_calls"
        if remaining.get("steps", 0) < 1:
            return "steps"
        if remaining.get("depth", 0) < 0:
            return "depth"
        return None

    def _charge_llm(self, snapshot, token_num: Mapping[str, int]) -> None:
        snapshot.budget.used_llm_calls += 1
        for key in self.token_totals:
            self.token_totals[key] += int(token_num.get(key) or 0)

    def _call_llm(self, snapshot, built: Mapping[str, str], *, temperature: float, purpose: str) -> Dict[str, Any]:
        exhausted = self._budget_ok(snapshot, extra_llm=1)
        if exhausted:
            raise ProtocolError(ViolationCode.BUDGET_EXCEEDED, f"LLM budget exhausted: {exhausted}")
        audit_actor_payload({"prompt_hash": built["prompt_hash"], "purpose": purpose}, self.secrets, context="llm_request")
        result = self.llm.complete(built["prompt"], temperature=temperature, purpose=purpose)
        self._charge_llm(snapshot, result.get("token_num") or {})
        step = {
            "kind": "llm",
            "purpose": purpose,
            "prompt_hash": built["prompt_hash"],
            "response_hash": result.get("response_hash"),
            "replay": bool(result.get("replay")),
            "real_call": bool(result.get("real_call")),
            "token_num": dict(result.get("token_num") or {}),
            "state_id": self.adapter.project_visible_state(snapshot).state_id,
        }
        self.trace.append(step)
        return result

    def _validate_or_reject(self, snapshot, action: Action) -> Tuple[bool, Optional[str]]:
        state = self.adapter.project_visible_state(snapshot)
        try:
            copied = Action(
                action_id=action.action_id,
                action_type=action.action_type,
                params=dict(action.params),
                source_role=action.source_role,
                state_id=state.state_id,
            )
            validate_action(copied, state)
            return True, None
        except ProtocolError as exc:
            self.illegal_kg += 0
            self.ledger.record_invalid_action(
                task_id=snapshot.task_id,
                action_type=action.action_type.value,
                message=exc.message,
            )
            self.trace.append(
                {
                    "kind": "rejected_action",
                    "action": action.to_dict(),
                    "error": exc.to_dict(),
                    "physical_kg": 0,
                }
            )
            return False, exc.code.value

    def _apply(self, snapshot, action: Action):
        state = self.adapter.project_visible_state(snapshot)
        bound = Action(
            action_id=action.action_id,
            action_type=action.action_type,
            params=dict(action.params),
            source_role=action.source_role,
            state_id=state.state_id,
        )
        ok, _code = self._validate_or_reject(snapshot, bound)
        if not ok:
            return snapshot, None, None, "rejected"
        new_snap, outcome, env_result = self.adapter.apply_action(snapshot, bound)
        self.trace.append(
            {
                "kind": "action",
                "action": bound.to_dict(),
                "accepted": outcome.accepted,
                "state_id_before": outcome.state_id_before,
                "state_id_after": outcome.state_id_after,
                "environment_status": None if env_result is None else env_result.status.value,
                "kg_call_delta": 0 if env_result is None else env_result.kg_call_delta,
                "physical_kg": 0 if env_result is None else env_result.kg_call_delta,
            }
        )
        return new_snap, outcome, env_result, "applied" if outcome.accepted else "env_rejected"

    def _lookup_name(self, snapshot, entity_id: str, entid_name: Dict[str, str], name_entid: Dict[str, str]) -> str:
        if entity_id in entid_name:
            return entid_name[entity_id]
        if not (entity_id.startswith("m.") or entity_id.startswith("g.")):
            entid_name[entity_id] = entity_id
            name_entid[entity_id] = entity_id
            return entity_id
        if self._budget_ok(snapshot, extra_kg=1):
            entid_name[entity_id] = entity_id
            return entity_id
        result = self.env.lookup_name(entity_id)
        snapshot.budget.used_kg_calls += result.kg_call_delta
        self.trace.append(
            {
                "kind": "kg_name_lookup",
                "entity": entity_id,
                "status": result.status.value,
                "kg_call_delta": result.kg_call_delta,
            }
        )
        name = result.raw_targets[0] if result.raw_targets else entity_id
        entid_name[entity_id] = name
        name_entid[name] = entity_id
        return name

    def _enumerate_relations(self, snapshot, entity_id: str) -> Tuple[List[str], List[str], Optional[str]]:
        if self._budget_ok(snapshot, extra_kg=2):
            return [], [], "kg_calls"
        result = self.env.enumerate_relations(entity_id)
        snapshot.budget.used_kg_calls += result.kg_call_delta
        if result.failure_class is not None:
            return [], [], result.error_code
        heads = []
        tails = []
        for item in result.results:
            rel = str(item.get("relation") or "")
            direction = item.get("direction")
            if abandon_rels(rel):
                continue
            if direction == Direction.HEAD.value:
                heads.append(rel)
            elif direction == Direction.TAIL.value:
                tails.append(rel)
        heads = sorted(set(heads))
        tails = sorted(set(tails))
        relations = [
            VisibleRelation(entity=entity_id, relation=rel, direction=Direction.HEAD) for rel in heads
        ] + [VisibleRelation(entity=entity_id, relation=rel, direction=Direction.TAIL) for rel in tails]
        existing = {item.key() for item in snapshot.enumerated_relations}
        for item in relations:
            if item.key() not in existing:
                snapshot.enumerated_relations.append(item)
                existing.add(item.key())
        self.trace.append(
            {
                "kind": "enumerate_relations",
                "entity": entity_id,
                "head_count": len(heads),
                "tail_count": len(tails),
                "kg_call_delta": result.kg_call_delta,
            }
        )
        return heads, tails, None

    def run(self, task: Mapping[str, Any], oracle: Mapping[str, Any]) -> Dict[str, Any]:
        public = public_task_view(task)
        topic = dict(task.get("topic_entity") or task.get("source_entity_names") or {})
        source = list(task.get("source_entities") or list(topic.keys()))
        budgets = Budget.from_config(self.config["budgets"])
        snapshot = make_sp1_snapshot(
            task_id=str(task["task_id"]),
            question=str(task["question"]),
            source_entities=source,
            topic_entity=topic,
            frontier=list(source),
            enumerated_relations=[],
            budget=budgets.to_dict(),
            entid_name=dict(topic),
            name_entid={v: k for k, v in topic.items()},
            decision_stage=DecisionStage.RELATION_SELECTION.value,
        )
        self.env.set_task(snapshot.task_id)
        self.mem.create_empty()
        entid_name = dict(snapshot.entid_name)
        name_entid = dict(snapshot.name_entid)
        topic_entity = dict(snapshot.topic_entity)
        cluster_chain: List[Any] = []
        depth_store: Dict[int, Any] = {}
        failure_class = None
        termination = TerminationReason.FAILURE.value
        submitted = []
        verifier = None
        try:
            sub_built = self.prompts.subquestions(snapshot.question)
            sub_result = self._call_llm(
                snapshot,
                sub_built,
                temperature=float(self.config["llm"]["temperature_reasoning"]),
                purpose="subquestions",
            )
            sub_questions = sub_result["text"]
            self.mem.write_subquestions(sub_questions)
            flag_printed = False
            max_depth = int(self.config["budgets"]["max_depth"])
            for depth in range(1, max_depth + 1):
                snapshot.budget.used_depth = depth - 1
                current_relations = []
                for entity in list(topic_entity):
                    if entity == "[FINISH_ID]":
                        continue
                    heads, tails, enum_error = self._enumerate_relations(snapshot, entity)
                    if enum_error == "kg_calls":
                        failure_class = FailureClass.BUDGET_INSUFFICIENT.value
                        termination = TerminationReason.BUDGET_EXHAUSTED.value
                        flag_printed = True
                        break
                    if enum_error:
                        failure_class = FailureClass.SYSTEM_FAILURE.value
                        termination = TerminationReason.FAILURE.value
                        flag_printed = True
                        break
                    pre_relations = []
                    entity_name = self._lookup_name(snapshot, entity, entid_name, name_entid)
                    built = self.prompts.relation_prune(snapshot.question, sub_questions, entity_name, heads + tails)
                    llm_out = self._call_llm(
                        snapshot,
                        built,
                        temperature=float(self.config["llm"]["temperature_exploration"]),
                        purpose="relation_prune",
                    )
                    state = self.adapter.project_visible_state(snapshot)
                    parsed = relations_to_expand_actions(llm_out["text"], entity, heads, tails, state)
                    for rejected in parsed.get("rejected") or []:
                        self.illegal_kg += 0
                        self.ledger.record_invalid_action(
                            task_id=snapshot.task_id,
                            action_type="EXPAND",
                            message=str(rejected),
                        )
                    if not parsed["ok"]:
                        continue
                    current_relations.extend(parsed["actions"])
                if flag_printed:
                    break
                if not current_relations:
                    snapshot, submitted, failure_class, termination = self._half_stop(snapshot, cluster_chain)
                    flag_printed = True
                    break
                total_candidates = []
                new_ent_rel: Dict[str, Any] = {}
                for action in current_relations:
                    snapshot, outcome, env_result, status = self._apply(snapshot, action)
                    if status == "rejected":
                        continue
                    if env_result is None:
                        continue
                    entity = action.params["entity"]
                    relation = action.params["relation"]
                    direction = action.params["direction"]
                    head_or_tail = "head" if direction == Direction.HEAD.value else "tail"
                    targets = []
                    for triple in env_result.results or []:
                        other = triple["object"] if direction == Direction.HEAD.value else triple["subject"]
                        targets.append(other)
                    if not targets:
                        continue
                    named = []
                    ids = []
                    for target in targets:
                        name = self._lookup_name(snapshot, target, entid_name, name_entid)
                        named.append(name)
                        ids.append(target)
                    new_ent_rel.setdefault(entity, {}).setdefault(head_or_tail, {}).setdefault(relation, [])
                    for item in ids:
                        if item not in new_ent_rel[entity][head_or_tail][relation]:
                            new_ent_rel[entity][head_or_tail][relation].append(item)
                    total_candidates.extend(ids)
                    snapshot.entid_name = dict(entid_name)
                    snapshot.name_entid = dict(name_entid)
                depth_store[depth] = copy.deepcopy(new_ent_rel)
                snapshot.ent_rel_ent_dict = copy.deepcopy(new_ent_rel)
                snapshot.depth_ent_rel_ent_dict = {str(k): v for k, v in depth_store.items()}
                if not total_candidates:
                    snapshot, submitted, failure_class, termination = self._half_stop(snapshot, cluster_chain)
                    flag_printed = True
                    break
                snapshot, pruned, prune_fail = self._prune_entities(snapshot, new_ent_rel, entid_name, name_entid, sub_questions)
                if prune_fail:
                    failure_class = prune_fail
                    termination = TerminationReason.FAILURE.value
                    flag_printed = True
                    break
                cluster_chain.append([[entid_name.get(e, e), rel, entid_name.get(t, t)] for e, ht in pruned.items() for _d, rels in ht.items() for rel, ts in rels.items() for t in ts])
                snapshot.cluster_chain_of_entities = cluster_chain
                his = self.mem.read()
                chain = chain_prompt_from_dict(pruned, entid_name)
                mem_built = self.prompts.update_memory(
                    snapshot.question,
                    sub_questions,
                    his,
                    chain,
                    extra_allowed=self._o0_allowed(snapshot, sub_questions, his, chain, *entid_name.values()),
                )
                mem_out = self._call_llm(
                    snapshot,
                    mem_built,
                    temperature=float(self.config["llm"]["temperature_reasoning"]),
                    purpose="update_memory",
                )
                self.mem.write(self._extract_memory(mem_out["text"]))
                his_after = self.mem.read()
                reason_built = self.prompts.reasoning(
                    snapshot.question,
                    his_after,
                    chain,
                    extra_allowed=self._o0_allowed(snapshot, his_after, chain, *entid_name.values()),
                )
                reason_out = self._call_llm(
                    snapshot,
                    reason_built,
                    temperature=float(self.config["llm"]["temperature_reasoning"]),
                    purpose="reasoning",
                )
                state = self.adapter.project_visible_state(snapshot)
                mapped = map_stop_or_continue(
                    reason_out["text"],
                    state,
                    snapshot,
                    on_unresolvable=str(self.config.get("stop_on_unresolvable") or "failure"),
                )
                if mapped.status == "stop" and mapped.action is not None:
                    snapshot, _outcome, _env, status = self._apply(snapshot, mapped.action)
                    submitted = list(mapped.action.params.get("answer_candidates") or [])
                    if status == "applied":
                        termination = TerminationReason.STOP_SUBMITTED.value
                        failure_class = None
                    else:
                        failure_class = FailureClass.ANSWER_EXTRACTION_FAILURE.value
                        termination = TerminationReason.FAILURE.value
                    flag_printed = True
                    break
                if mapped.status == "continue" and mapped.action is not None:
                    snapshot, _outcome, _env, _status = self._apply(snapshot, mapped.action)
                if mapped.status == "failure":
                    failure_class = (mapped.failure_class.value if mapped.failure_class else FailureClass.ANSWER_EXTRACTION_FAILURE.value)
                    termination = TerminationReason.FAILURE.value
                    flag_printed = True
                    break
                next_ids = []
                for _e, ht in pruned.items():
                    for _d, rels in ht.items():
                        for _r, ids in rels.items():
                            next_ids.extend(ids)
                next_ids = [item for item in next_ids if item.startswith("m.") or item.startswith("g.")]
                snapshot, extra_ids, rec_fail = self._maybe_recover(
                    snapshot,
                    next_ids,
                    depth_store,
                    entid_name,
                    name_entid,
                    sub_questions,
                    cluster_chain,
                )
                if rec_fail == "unsupported_backtrack":
                    failure_class = FailureClass.ACTION_SPACE_FAILURE.value
                    termination = TerminationReason.PROTOCOL_VIOLATION.value
                    flag_printed = True
                    break
                topic_entity = {}
                for entity in extra_ids or next_ids:
                    if entity.startswith("m.") or entity.startswith("g."):
                        topic_entity[entity] = entid_name.get(entity, entity)
                snapshot.topic_entity = dict(topic_entity)
                snapshot.frontier = sorted(set(snapshot.frontier) | set(topic_entity))
                snapshot.budget.used_depth = depth
                if not topic_entity:
                    snapshot, submitted, failure_class, termination = self._half_stop(snapshot, cluster_chain)
                    flag_printed = True
                    break
            if not flag_printed:
                snapshot, submitted, failure_class, termination = self._no_path(snapshot, cluster_chain)
        except ProtocolError as exc:
            if exc.code is ViolationCode.BUDGET_EXCEEDED:
                failure_class = FailureClass.BUDGET_INSUFFICIENT.value
                termination = TerminationReason.BUDGET_EXHAUSTED.value
            elif exc.code is ViolationCode.ORACLE_LEAKAGE:
                failure_class = FailureClass.SYSTEM_FAILURE.value
                termination = TerminationReason.PROTOCOL_VIOLATION.value
            else:
                failure_class = FailureClass.SYSTEM_FAILURE.value
                termination = TerminationReason.FAILURE.value
            self.trace.append({"kind": "protocol_error", "error": exc.to_dict()})
        except Exception as exc:
            failure_class = FailureClass.SYSTEM_FAILURE.value
            termination = TerminationReason.FAILURE.value
            self.trace.append({"kind": "unclassified", "error": str(exc)})
        finally:
            self.mem.close()
        state = self.adapter.project_visible_state(snapshot)
        verifier = self._verify(public, oracle, submitted, state)
        success = termination in {
            TerminationReason.STOP_SUBMITTED.value,
            TerminationReason.ABSTAINED.value,
            TerminationReason.BUDGET_EXHAUSTED.value,
        } or failure_class in {
            FailureClass.EXPLORER_FAILURE.value,
            FailureClass.ANSWER_EXTRACTION_FAILURE.value,
            FailureClass.ACTION_SPACE_FAILURE.value,
            FailureClass.BUDGET_INSUFFICIENT.value,
        }
        if failure_class is None and termination == TerminationReason.STOP_SUBMITTED.value:
            classified = None
        elif failure_class is None:
            classified = FailureClass.EXPLORER_FAILURE.value
        else:
            classified = failure_class
        return {
            "task_id": snapshot.task_id,
            "question": snapshot.question,
            "termination_reason": termination,
            "failure_class": classified,
            "submitted_answers": submitted,
            "state_id": state.state_id,
            "trace": self.trace,
            "llm_real_calls": self.llm.real_calls,
            "llm_replay_calls": self.llm.replay_calls,
            "llm_records": self.llm.records,
            "token_totals": dict(self.token_totals),
            "ledger": self.ledger.snapshot(),
            "working_memory": self.mem.audit(),
            "illegal_kg_attempts": self.ledger.skipped_invalid_action,
            "budget": snapshot.budget.to_dict(),
            "verifier": verifier,
            "protocol_version": PROTOCOL_VERSION,
            "complete": True,
            "unclassified": any(item.get("kind") == "unclassified" for item in self.trace),
            "pipeline_ok": classified != FailureClass.SYSTEM_FAILURE.value and not any(
                item.get("kind") == "unclassified" for item in self.trace
            ),
        }

    def _extract_memory(self, text: str) -> str:
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            return text[first : last + 1]
        return text

    def _half_stop(self, snapshot, cluster_chain):
        chain = "\n".join(", ".join(str(x) for x in chain) for sub in cluster_chain for chain in sub)
        built = self.prompts.generate_answer(
            snapshot.question,
            chain,
            extra_allowed=self._o0_allowed(snapshot, chain),
        )
        result = self._call_llm(
            snapshot,
            built,
            temperature=float(self.config["llm"]["temperature_reasoning"]),
            purpose="generate_answer",
        )
        state = self.adapter.project_visible_state(snapshot)
        mapped = map_stop_or_continue(
            result["text"],
            state,
            snapshot,
            on_unresolvable=str(self.config.get("half_stop_on_unresolvable") or "abstain"),
        )
        if mapped.action is not None:
            snapshot, _outcome, _env, status = self._apply(snapshot, mapped.action)
            answers = list(mapped.action.params.get("answer_candidates") or [])
            if mapped.status == "stop" and status == "applied":
                return snapshot, answers, None, TerminationReason.STOP_SUBMITTED.value
            if mapped.status == "abstain" and status == "applied":
                return snapshot, answers, FailureClass.EXPLORER_FAILURE.value, TerminationReason.ABSTAINED.value
        return snapshot, [], FailureClass.ANSWER_EXTRACTION_FAILURE.value, TerminationReason.FAILURE.value

    def _no_path(self, snapshot, cluster_chain):
        built = self.prompts.generate_without_paths(
            snapshot.question,
            extra_allowed=self._o0_allowed(snapshot),
        )
        result = self._call_llm(
            snapshot,
            built,
            temperature=float(self.config["llm"]["temperature_reasoning"]),
            purpose="generate_without_paths",
        )
        state = self.adapter.project_visible_state(snapshot)
        mapped = map_stop_or_continue(
            result["text"],
            state,
            snapshot,
            on_unresolvable=str(self.config.get("half_stop_on_unresolvable") or "abstain"),
        )
        if mapped.action is not None:
            snapshot, _outcome, _env, status = self._apply(snapshot, mapped.action)
            answers = list(mapped.action.params.get("answer_candidates") or [])
            if mapped.status == "abstain" and status == "applied":
                return snapshot, answers, FailureClass.EXPLORER_FAILURE.value, TerminationReason.ABSTAINED.value
            if mapped.status == "stop" and status == "applied":
                return snapshot, answers, None, TerminationReason.STOP_SUBMITTED.value
        return snapshot, [], FailureClass.ANSWER_EXTRACTION_FAILURE.value, TerminationReason.FAILURE.value

    def _prune_entities(self, snapshot, ent_rel_ent_dict, entid_name, name_entid, sub_questions):
        pruned: Dict[str, Any] = {}
        no_prune = {"time", "number", "date"}
        for topic_e, h_t_dict in sorted(ent_rel_ent_dict.items()):
            for h_t, r_e_dict in sorted(h_t_dict.items()):
                for rela, e_list in sorted(r_e_dict.items()):
                    names = [entid_name.get(e_id, e_id) for e_id in sorted(e_list)]
                    if len(e_list) <= 1 or rela in no_prune or all(item.isdigit() for item in e_list):
                        selected_names = names
                    else:
                        capped_ids = list(e_list)
                        if all(entid_name.get(item, item).startswith("m.") for item in capped_ids) and len(capped_ids) > 10:
                            capped_ids = sorted(capped_ids)[:10]
                        if len(capped_ids) > 70:
                            capped_ids = _unicode_cap(capped_ids, 70)
                            self.trace.append(
                                {
                                    "kind": "entity_cap_fallback",
                                    "policy": "unicode_sort_truncate",
                                    "kept": len(capped_ids),
                                }
                            )
                        names = [entid_name.get(e_id, e_id) for e_id in sorted(capped_ids)]
                        built = self.prompts.entity_prune(
                            snapshot.question,
                            entid_name.get(topic_e, topic_e),
                            rela,
                            names,
                        )
                        out = self._call_llm(
                            snapshot,
                            built,
                            temperature=float(self.config["llm"]["temperature_reasoning"]),
                            purpose="entity_prune",
                        )
                        accepted, rejected = parse_entity_list(out["text"], names)
                        if rejected:
                            self.ledger.record_invalid_action(
                                task_id=snapshot.task_id,
                                action_type="SELECT_FRONTIER",
                                message=f"pruned unknown entities {rejected}",
                            )
                        selected_names = accepted or names
                    if not selected_names:
                        continue
                    pruned.setdefault(topic_e, {}).setdefault(h_t, {})[rela] = [
                        name_entid.get(name, name) for name in selected_names
                    ]
        return snapshot, pruned, None

    def _maybe_recover(self, snapshot, current_ids, depth_store, entid_name, name_entid, sub_questions, cluster_chain):
        policy = str(self.config.get("reverse_entity_recovery") or "original_pog_if_finish_list")
        if policy != "original_pog_if_finish_list":
            return snapshot, current_ids, None
        if self.config.get("backtrack_state_policy") != "unsupported":
            return snapshot, current_ids, "unsupported_backtrack"
        his = self.mem.read()
        chain = "\n".join(", ".join(str(x) for x in chain) for sub in cluster_chain for chain in sub)
        names = [entid_name.get(item, item) for item in current_ids]
        built = self.prompts.judge_reverse(
            snapshot.question,
            names,
            his,
            chain,
            extra_allowed=self._o0_allowed(snapshot, his, chain, *names),
        )
        out = self._call_llm(
            snapshot,
            built,
            temperature=float(self.config["llm"]["temperature_reasoning"]),
            purpose="judge_reverse",
        )
        flag, reason, error = parse_add_flag(out["text"])
        if error or flag is False or flag is None:
            return snapshot, current_ids, None
        all_ids = set()
        for _dep, mapping in depth_store.items():
            for topic_e, ht in mapping.items():
                all_ids.add(topic_e)
                for _d, rels in ht.items():
                    for _r, ids in rels.items():
                        all_ids.update(ids)
        candidates = sorted(all_ids - set(current_ids))
        cand_names = [entid_name.get(item, item) for item in candidates]
        add_built = self.prompts.add_entities(
            snapshot.question,
            reason or "",
            cand_names,
            his,
            extra_allowed=self._o0_allowed(snapshot, his, reason or "", *cand_names),
        )
        add_out = self._call_llm(
            snapshot,
            add_built,
            temperature=float(self.config["llm"]["temperature_reasoning"]),
            purpose="add_entities",
        )
        accepted, rejected = parse_entity_list(add_out["text"], cand_names)
        extra = [name_entid.get(name, name) for name in accepted]
        state = self.adapter.project_visible_state(snapshot)
        for entity in extra:
            if entity.startswith("state:"):
                return snapshot, current_ids, "unsupported_backtrack"
            action = select_frontier_action(state, entity)
            snapshot, _outcome, _env, status = self._apply(snapshot, action)
            state = self.adapter.project_visible_state(snapshot)
        if rejected:
            self.ledger.record_invalid_action(
                task_id=snapshot.task_id,
                action_type="SELECT_FRONTIER",
                message=f"recovery rejected unseen entities {rejected}",
            )
        return snapshot, list(current_ids) + extra, None

    def _verify(self, public: Mapping[str, Any], oracle: Mapping[str, Any], submitted: Sequence[str], state) -> Dict[str, Any]:
        expected_ids = list(oracle.get("answer_entity_ids") or [])
        expected_names = list(oracle.get("normalized_answers") or [])
        rule = str(oracle.get("verifier_rule") or "observed_optional")
        submitted_l = [str(item) for item in submitted]
        match = False
        if rule == "empty_or_abstain":
            match = not submitted_l
        elif rule == "exact_id_or_name":
            match = any(item in expected_ids or item in expected_names for item in submitted_l)
        elif rule == "literal_contains":
            lowered = [item.lower() for item in submitted_l]
            match = any(any(exp.lower() in item for item in lowered) for exp in expected_names if exp)
        else:
            observed = set(state.visible_entities) | set(state.frontier)
            for triple in state.observed_triples_or_summaries:
                observed.update(str(triple.get(k) or "") for k in ("subject", "object"))
            match = all(item in observed for item in submitted_l) if submitted_l else False
        return {
            "rule": rule,
            "match": match,
            "expected_ids": expected_ids,
            "expected_names": expected_names,
            "oracle_returned_to_actor": False,
        }
