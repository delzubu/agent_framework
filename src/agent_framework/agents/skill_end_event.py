"""Skill end event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_invocation import AgentInvocation
from agent_framework.skill import SkillContent


@dataclass(frozen=True, slots=True)
class SkillEndEvent:
    """Post-skill hook payload — fired after skill content is injected into conversation."""

    invocation: AgentInvocation
    skill_name: str
    parameters: dict[str, Any]
    content: SkillContent

__all__ = ["SkillEndEvent"]
