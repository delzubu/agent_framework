"""Pre-model hook payload."""

from __future__ import annotations

from dataclasses import dataclass

from agent_framework.model import ModelContext

from .agent_invocation import AgentInvocation


@dataclass(frozen=True, slots=True)
class ModelStartEvent:
    """Hook event emitted immediately before the model call."""

    invocation: AgentInvocation
    context: ModelContext


__all__ = ["ModelStartEvent"]
