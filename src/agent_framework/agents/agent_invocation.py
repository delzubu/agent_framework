"""Shared invocation payload for hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentInvocation:
    """Shared invocation details exposed to lifecycle hooks."""

    run_id: str
    agent_id: str
    caller_id: str | None
    parameters: dict[str, Any]
    rendered_prompt: str

__all__ = ["AgentInvocation"]
