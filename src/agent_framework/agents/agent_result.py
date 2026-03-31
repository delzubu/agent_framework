"""Agent result model."""

from __future__ import annotations

from dataclasses import dataclass

from .agent_decision import AgentDecision
from .call_context import CallContext


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Returned outcome of an agent run."""

    status: str
    message: str = ""
    decision: AgentDecision | None = None
    prompt: str = ""
    context: CallContext | None = None

__all__ = ["AgentResult"]
