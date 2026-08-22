"""O0 prompt builder wrapping original PoG prompt_list.py templates."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .hashing import sha256_file, sha256_text
from .llm_client import PROMPT_VERSION
from .paths import Workspace
from .sp2b_guards import audit_prompt
from .visibility import OracleSecrets


def load_prompt_module(workspace: Workspace):
    path = workspace.self_play_root / "prompt_list.py"
    spec = importlib.util.spec_from_file_location("sp2b_prompt_list", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load prompt_list.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, sha256_file(path)


def prompt_inventory(module: Any, prompt_list_sha256: str) -> Dict[str, Any]:
    names = [
        "subobjective_prompt",
        "extract_relation_prompt",
        "answer_prompt",
        "prune_entity_prompt",
        "update_mem_prompt",
        "answer_depth_prompt",
        "judge_reverse",
        "add_ent_prompt",
        "cot_prompt",
    ]
    prompts = {}
    for name in names:
        text = getattr(module, name)
        prompts[name] = {"sha256": sha256_text(text), "chars": len(text)}
    return {
        "prompt_version": PROMPT_VERSION,
        "source": "prompt_list.py",
        "source_sha256": prompt_list_sha256,
        "prompts": prompts,
    }


class O0PromptBuilder:
    def __init__(self, workspace: Workspace, secrets: Optional[OracleSecrets] = None) -> None:
        self.module, self.source_sha256 = load_prompt_module(workspace)
        self.secrets = secrets or OracleSecrets(
            answer_entity_ids=[],
            normalized_answers=[],
            witness_tokens=[],
            logical_query="",
            future_neighbors=[],
        )
        self.prompt_version = PROMPT_VERSION

    def _emit(self, template: str, dynamic: str, *, allowed: Optional[Sequence[str]] = None) -> Dict[str, str]:
        text = template + dynamic
        audit_prompt(dynamic, self.secrets, allowed_values=allowed)
        return {
            "prompt": text,
            "prompt_hash": sha256_text(text),
            "prompt_version": self.prompt_version,
        }

    def subquestions(self, question: str) -> Dict[str, str]:
        return self._emit(self.module.subobjective_prompt, question, allowed=[question])

    def relation_prune(
        self,
        question: str,
        sub_questions: str,
        entity_name: str,
        relations: Sequence[str],
    ) -> Dict[str, str]:
        dynamic = (
            question
            + "\nSubobjectives: "
            + str(sub_questions)
            + "\nTopic Entity: "
            + entity_name
            + "\nRelations: "
            + "; ".join(relations)
        )
        return self._emit(
            self.module.extract_relation_prompt,
            dynamic,
            allowed=[question, sub_questions, entity_name, *relations],
        )

    def entity_prune(self, question: str, topic_name: str, relation: str, entities: Sequence[str]) -> Dict[str, str]:
        dynamic = question + "\nTriples: " + topic_name + " " + relation + " " + str(list(entities))
        return self._emit(
            self.module.prune_entity_prompt,
            dynamic,
            allowed=[question, topic_name, relation, *entities],
        )

    def update_memory(
        self,
        question: str,
        subquestions: str,
        his_mem: str,
        chain_prompt: str,
        extra_allowed: Optional[Sequence[str]] = None,
    ) -> Dict[str, str]:
        dynamic = (
            question
            + "\nSubobjectives: "
            + str(subquestions)
            + "\nMemory: "
            + his_mem
            + "\nKnowledge Triplets:\n"
            + chain_prompt
        )
        return self._emit(
            self.module.update_mem_prompt,
            dynamic,
            allowed=[question, subquestions, his_mem, chain_prompt, *(extra_allowed or [])],
        )

    def reasoning(
        self,
        question: str,
        his_mem: str,
        chain_prompt: str,
        extra_allowed: Optional[Sequence[str]] = None,
    ) -> Dict[str, str]:
        dynamic = question + "\nMemory: " + his_mem + "\nKnowledge Triplets:\n" + chain_prompt
        return self._emit(
            self.module.answer_depth_prompt,
            dynamic,
            allowed=[question, his_mem, chain_prompt, *(extra_allowed or [])],
        )

    def generate_answer(
        self,
        question: str,
        chain_prompt: str,
        extra_allowed: Optional[Sequence[str]] = None,
    ) -> Dict[str, str]:
        dynamic = question + "\nKnowledge Triplets: " + chain_prompt
        return self._emit(
            self.module.answer_prompt,
            dynamic,
            allowed=[question, chain_prompt, *(extra_allowed or [])],
        )

    def generate_without_paths(self, question: str, extra_allowed: Optional[Sequence[str]] = None) -> Dict[str, str]:
        return self._emit(self.module.cot_prompt, question, allowed=[question, *(extra_allowed or [])])

    def judge_reverse(
        self,
        question: str,
        entities: Sequence[str],
        his_mem: str,
        chain_prompt: str,
        extra_allowed: Optional[Sequence[str]] = None,
    ) -> Dict[str, str]:
        dynamic = (
            question
            + "\nEntities set to be retrieved: "
            + str(list(entities))
            + "\nMemory: "
            + his_mem
            + "\nKnowledge Triplets:"
            + chain_prompt
        )
        return self._emit(
            self.module.judge_reverse,
            dynamic,
            allowed=[question, his_mem, chain_prompt, *entities, *(extra_allowed or [])],
        )

    def add_entities(
        self,
        question: str,
        reason: str,
        candidates: Sequence[str],
        his_mem: str,
        extra_allowed: Optional[Sequence[str]] = None,
    ) -> Dict[str, str]:
        dynamic = (
            question
            + "\nReason: "
            + reason
            + "\nCandidate Entities: "
            + str(list(candidates))
            + "\nMemory: "
            + his_mem
        )
        return self._emit(
            self.module.add_ent_prompt,
            dynamic,
            allowed=[question, reason, his_mem, *candidates, *(extra_allowed or [])],
        )
