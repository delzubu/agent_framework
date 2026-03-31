"""Subagent end event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_invocation import AgentInvocation
from .agent_result import AgentResult


@dataclass(frozen=True, slots=True)
class SubagentEndEvent:
    """Post-subagent hook payload."""

    invocation: AgentInvocation
    subagent_call_id: str
    subagent_id: str
    subagent_input: dict[str, Any]
    result: AgentResult

__all__ = ["SubagentEndEvent"]
