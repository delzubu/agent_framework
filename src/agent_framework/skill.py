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


_INVENTORY_EXTENSIONS = {".md", ".txt", ".py", ".sh", ".json", ".yaml", ".yml"}
_INVENTORY_EXCLUDE_DIRS = {"__pycache__", "node_modules", "dist", "build", ".git"}
_BACKTICK_PATH_RE = re.compile(r"`([^`\n]{3,200})`")


@dataclass(slots=True)
class SkillLoader:
    """Loads full SkillContent from a SkillDefinition on demand (Tier 2 load)."""

    def load(self, definition: SkillDefinition) -> SkillContent:
        body = self._read_body(definition.source_path)
        inventory = self._build_inventory(definition.skill_dir, body)
        return SkillContent(definition=definition, body=body, inventory=inventory)

    def _read_body(self, skill_md_path: Path) -> str:
        raw = skill_md_path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            return raw
        parts = raw.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else raw

    def _build_inventory(self, skill_dir: Path, body: str) -> tuple[SkillResource, ...]:
        seen: dict[str, SkillResource] = {}

        # Pass 1: recursive directory scan
        self._scan_dir(skill_dir, skill_dir, seen)

        # Pass 2: backtick body reference scan
        for match in _BACKTICK_PATH_RE.finditer(body):
            candidate = match.group(1).strip()
            if "/" not in candidate and "." not in candidate:
                continue  # not a file path
            self._try_add_reference(candidate, skill_dir, seen)

        resources = sorted(seen.values(), key=lambda r: r.relative_path)
        return tuple(resources)

    def _scan_dir(self, root: Path, current: Path, seen: dict[str, SkillResource], depth: int = 0) -> None:
        if depth > 5:
            return
        for item in sorted(current.iterdir()):
            if item.name.startswith(".") or item.name in _INVENTORY_EXCLUDE_DIRS:
                continue
            if item.is_dir():
                self._scan_dir(root, item, seen, depth + 1)
            elif item.is_file() and item.suffix.lower() in _INVENTORY_EXTENSIONS:
                if item.name == "SKILL.md" and item.parent == root:
                    continue  # exclude SKILL.md itself
                rel = item.relative_to(root).as_posix()
                seen[rel] = SkillResource(relative_path=rel, full_path=item.resolve())

    def _try_add_reference(self, path_str: str, skill_dir: Path, seen: dict[str, SkillResource]) -> None:
        candidate = Path(path_str)
        # 1. Relative to skill_dir
        resolved = (skill_dir / candidate).resolve()
        if resolved.exists():
            rel = path_str
            if rel not in seen:
                seen[rel] = SkillResource(relative_path=rel, full_path=resolved)
            return
        # 2. Absolute path
        if candidate.is_absolute() and candidate.exists():
            rel = path_str
            if rel not in seen:
                seen[rel] = SkillResource(relative_path=rel, full_path=candidate)
            return
        # 3. Relative to cwd
        cwd_resolved = (Path.cwd() / candidate).resolve()
        if cwd_resolved.exists():
            rel = path_str
            if rel not in seen:
                seen[rel] = SkillResource(relative_path=rel, full_path=cwd_resolved)


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


__all__ = ["SkillDefinition", "SkillResource", "SkillContent", "SkillRegistry", "SkillLoader"]
