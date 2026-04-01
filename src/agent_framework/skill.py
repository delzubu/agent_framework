"""Skill data types, registry, loader, and read_skill_resource tool."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from agent_framework.host import AgentHost

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """Lightweight catalog entry for one skill — loaded at discovery time."""

    name: str           # from frontmatter — canonical identifier
    description: str    # from frontmatter — injected into model catalog
    version: str | None
    priority: int       # from frontmatter, default 0
    source_path: Path   # path to SKILL.md
    skill_dir: Path     # source_path.parent


@dataclass(frozen=True, slots=True)
class SkillResource:
    """One file entry in the skill's file inventory (path only, no content)."""

    relative_path: str  # display path shown to model in inventory
    full_path: Path     # resolved absolute path used when loading content


@dataclass(frozen=True, slots=True)
class SkillContent:
    """Fully resolved skill content, built on invocation (Tier 2 load)."""

    definition: SkillDefinition
    body: str                            # SKILL.md body (after frontmatter)
    inventory: tuple[SkillResource, ...]  # available files — paths only


__all__ = ["SkillDefinition", "SkillResource", "SkillContent"]
