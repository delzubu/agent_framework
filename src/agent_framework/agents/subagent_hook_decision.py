"""Pre-subagent hook decision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_result import AgentResult


@dataclass(frozen=True, slots=True)
class SubagentHookDecision:
    """Decision returned from a pre-subagent callback."""

    continue_run: bool = True
    updated_subagent_id: str | None = None
    updated_subagent_input: dict[str, Any] | None = None
    system_message: str | None = None
    final_result: AgentResult | None = None

__all__ = ["SubagentHookDecision"]
