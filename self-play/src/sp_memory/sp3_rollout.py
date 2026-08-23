"""SP3 rollout: Explorer plus optional O0/random Critic. Candidates are not retrieved."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .candidate_experience import CandidateReadGuard
from .critic import O0Critic
from .rollout import Sp2bRollout
from .schemas import Action, ActionType
from .sp2b_guards import public_task_view


class Sp3Rollout(Sp2bRollout):
    def __init__(self, *args: Any, critic: Optional[O0Critic] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.critic = critic
        self.candidate_guard = CandidateReadGuard()
        self.current_public_task: Dict[str, Any] = {}

    def run(self, task: Mapping[str, Any], oracle: Mapping[str, Any]) -> Dict[str, Any]:
        self.current_public_task = public_task_view(task)
        result = super().run(task, oracle)
        result["critic_mode"] = self.critic_mode
        result["candidate_retrievals"] = 0
        result["oracle_level_actor"] = "O0"
        result["oracle_level_critic"] = "O0"
        return result

    def _maybe_intervene(self, event: str, snapshot, mapped=None):
        if self.critic is None or self.critic_mode in {"none", "", "off", "explorer_only"}:
            return None
        remaining = snapshot.budget.remaining()
        if remaining.get("critic_rounds", 0) < 1:
            return None
        state = self.adapter.project_visible_state(snapshot)
        extra_llm = 0 if self.critic_mode == "random" else 1
        exhausted = self._budget_ok(snapshot, extra_llm=extra_llm)
        if exhausted == "llm_calls":
            return None
        if self.critic_mode == "random":
            decision = self.critic.decide(text="", task=self.current_public_task, state=state, event=event)
        else:
            built = self.critic.build_prompt(self.current_public_task, state, event)
            llm_out = self._call_llm(
                snapshot,
                built,
                temperature=float(self.config["llm"]["temperature_reasoning"]),
                purpose="critic_o0",
            )
            decision = self.critic.decide(
                text=llm_out["text"],
                task=self.current_public_task,
                state=state,
                event=event,
            )
        snapshot.budget.used_critic_rounds += 1
        self.trace.append(
            {
                "kind": "critic",
                "state_id": state.state_id,
                "visible_state": state.to_dict(),
                **decision,
            }
        )
        if not decision.get("accepted") or not decision.get("action"):
            return None
        action = Action.from_dict(decision["action"])
        if event == "early_stop" and action.action_type is ActionType.CONTINUE:
            return {"status": "continue", "action": action, "apply": True}
        return {"status": action.action_type.value.lower(), "action": action, "apply": True}
