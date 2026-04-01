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


@dataclass(slots=True)
class SkillRegistry:
    """Discovers and caches SkillDefinitions from configured directories."""

    directories: tuple[Path, ...]
    _cache: dict[str, SkillDefinition] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: "Any") -> "SkillRegistry":
        return cls(directories=config.skills_directories)

    def discover(self) -> None:
        """Scan all directories, parse SKILL.md frontmatter, deduplicate by name."""
        candidates: list[SkillDefinition] = []
        for dir_index, directory in enumerate(self.directories):
            if not directory.is_dir():
                continue
            for skill_dir in sorted(directory.iterdir()):
                if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                defn = _parse_skill_frontmatter(skill_md, dir_index)
                if defn is not None:
                    candidates.append(defn)
        # Sort: lower dir_index (higher priority) first, then higher frontmatter priority
        candidates.sort(key=lambda d: (self.directories.index(d.skill_dir.parent) if d.skill_dir.parent in self.directories else 999, -d.priority))
        seen: dict[str, SkillDefinition] = {}
        for defn in candidates:
            if defn.name not in seen:
                seen[defn.name] = defn
        self._cache = seen

    def get(self, name: str) -> SkillDefinition:
        if name not in self._cache:
            raise KeyError(f"Unknown skill: {name!r}")
        return self._cache[name]

    def get_all(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._cache.values())

    def filter(self, allowed: tuple[str, ...]) -> tuple[SkillDefinition, ...]:
        if not allowed:
            return self.get_all()
        result = []
        for name in allowed:
            if name in self._cache:
                result.append(self._cache[name])
            else:
                _LOGGER.warning("Skill %r listed in agent frontmatter but not found in registry.", name)
        return tuple(result)

    def reload(self) -> None:
        self._cache.clear()
        self.discover()


def _parse_skill_frontmatter(skill_md: Path, _dir_index: int) -> SkillDefinition | None:
    """Parse SKILL.md frontmatter. Returns None and logs on any error."""
    try:
        raw = skill_md.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            _LOGGER.warning("Skill %s: missing frontmatter.", skill_md)
            return None
        parts = raw.split("---", 2)
        if len(parts) < 3:
            _LOGGER.warning("Skill %s: unclosed frontmatter.", skill_md)
            return None
        meta = yaml.safe_load(parts[1]) or {}
        name = str(meta.get("name", "")).strip()
        description = str(meta.get("description", "")).strip()
        if not name or not description:
            _LOGGER.warning("Skill %s: 'name' and 'description' are required.", skill_md)
            return None
        return SkillDefinition(
            name=name,
            description=description,
            version=str(meta["version"]) if "version" in meta else None,
            priority=int(meta.get("priority", 0)),
            source_path=skill_md.resolve(),
            skill_dir=skill_md.parent.resolve(),
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Skill %s: failed to parse — %s", skill_md, exc)
        return None


__all__ = ["SkillDefinition", "SkillResource", "SkillContent", "SkillRegistry"]
