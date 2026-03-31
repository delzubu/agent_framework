"""Agent end event."""

from __future__ import annotations

from dataclasses import dataclass

from .agent_invocation import AgentInvocation
from .agent_result import AgentResult


@dataclass(frozen=True, slots=True)
class AgentEndEvent:
    """Hook event emitted after an agent invocation produces a result."""

    invocation: AgentInvocation
    result: AgentResult

__all__ = ["AgentEndEvent"]
