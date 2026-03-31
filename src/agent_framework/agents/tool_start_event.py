"""Tool start event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_decision import AgentDecision
from .agent_invocation import AgentInvocation


@dataclass(frozen=True, slots=True)
class ToolStartEvent:
    """Pre-tool hook payload."""

    invocation: AgentInvocation
    tool_call_id: str
    tool_name: str
    tool_input: dict[str, Any]
    decision: AgentDecision

__all__ = ["ToolStartEvent"]
