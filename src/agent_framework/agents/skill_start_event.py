"""Skill start event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_invocation import AgentInvocation


@dataclass(frozen=True, slots=True)
class SkillStartEvent:
    """Pre-skill hook payload — fired before skill body is loaded."""

    invocation: AgentInvocation
    skill_name: str
    parameters: dict[str, Any]

__all__ = ["SkillStartEvent"]
