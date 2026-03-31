"""Post-agent hook decision."""

from __future__ import annotations

from dataclasses import dataclass, field

from .agent_result import AgentResult


@dataclass(frozen=True, slots=True)
class AgentEndHookDecision:
    """Decision returned from a post-agent callback or behavior."""

    continue_run: bool = False
    prompt_fragments: tuple[str, ...] = field(default_factory=tuple)
    append_prompt_fragments: tuple[str, ...] = field(default_factory=tuple)
    final_result: AgentResult | None = None


__all__ = ["AgentEndHookDecision"]
