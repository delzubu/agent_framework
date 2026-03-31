"""Post-model hook payload."""

from __future__ import annotations

from dataclasses import dataclass

from agent_framework.model import ModelContext, ModelResponse

from .agent_invocation import AgentInvocation


@dataclass(frozen=True, slots=True)
class ModelEndEvent:
    """Hook event emitted immediately after the model call."""

    invocation: AgentInvocation
    context: ModelContext
    response: ModelResponse


__all__ = ["ModelEndEvent"]
