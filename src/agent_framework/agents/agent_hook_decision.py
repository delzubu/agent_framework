"""Pre-agent hook decision."""

from __future__ import annotations

from dataclasses import dataclass

from .agent_result import AgentResult


@dataclass(frozen=True, slots=True)
class AgentHookDecision:
    """Decision returned from a pre-agent callback."""

    continue_run: bool = True
    system_message: str | None = None
    final_result: AgentResult | None = None

__all__ = ["AgentHookDecision"]
