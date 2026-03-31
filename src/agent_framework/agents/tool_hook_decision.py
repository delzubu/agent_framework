"""Pre-tool hook decision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_result import AgentResult


@dataclass(frozen=True, slots=True)
class ToolHookDecision:
    """Decision returned from a pre-tool callback."""

    continue_run: bool = True
    updated_tool_input: dict[str, Any] | None = None
    system_message: str | None = None
    final_result: AgentResult | None = None

__all__ = ["ToolHookDecision"]
