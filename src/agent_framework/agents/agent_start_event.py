"""Agent start event."""

from __future__ import annotations

from dataclasses import dataclass

from .agent_invocation import AgentInvocation


@dataclass(frozen=True, slots=True)
class AgentStartEvent:
    """Hook event emitted before an agent invocation begins."""

    invocation: AgentInvocation

__all__ = ["AgentStartEvent"]
