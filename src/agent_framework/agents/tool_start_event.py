"""Tool start event."""

from __future__ import annotations

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .agent_invocation import AgentInvocation

if TYPE_CHECKING:
    from .agent_decision import AgentDecision


@dataclass(frozen=True, slots=True)
class ToolStartEvent:
    """Pre-tool hook payload."""

    invocation: AgentInvocation
    tool_call_id: str
    tool_name: str
    tool_input: dict[str, Any]
    decision: AgentDecision | None = None

__all__ = ["ToolStartEvent"]
