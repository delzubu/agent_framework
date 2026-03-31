"""Subagent start event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_decision import AgentDecision
from .agent_invocation import AgentInvocation


@dataclass(frozen=True, slots=True)
class SubagentStartEvent:
    """Pre-subagent hook payload."""

    invocation: AgentInvocation
    subagent_call_id: str
    subagent_id: str
    subagent_input: dict[str, Any]
    decision: AgentDecision

__all__ = ["SubagentStartEvent"]
