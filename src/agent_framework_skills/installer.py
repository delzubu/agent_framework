"""Skill installer: copies bundled skills into known agentic tool directories."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import NamedTuple

from agent_framework_skills import SKILLS_DIR

# Well-known agentic tool skill directories (relative to home unless absolute).
# Each entry is (label, path_relative_to_home).
_KNOWN_DIRS: list[tuple[str, str]] = [
    ("Claude Code (user)", "~/.claude/skills"),
    ("Claude Code (project)", "./.claude/skills"),
    ("Codex", "~/.codex/skills"),
    ("Cursor", "~/.cursor/skills"),
    ("Windsurf", "~/.codeium/windsurf/skills"),
    ("Gemini CLI", "~/.gemini/skills"),
]


class InstallTarget(NamedTuple):
    label: str
    path: Path
    exists: bool


def _resolve_known_target(raw: str) -> tuple[Path, Path]:
    """Resolve a configured target path and its installation marker directory.

    The first path segment after the anchor is treated as the installation
    marker. If that marker exists, the agentic tool is considered installed and
    any missing trailing directories may be created during installation.
    """
    if raw.startswith("~/"):
        anchor = Path.home()
        relative = Path(raw[2:])
    elif raw.startswith("./"):
        anchor = Path.cwd()
        relative = Path(raw[2:])
    else:
        expanded = Path(raw).expanduser()
        if expanded.is_absolute():
            anchor = Path(expanded.anchor)
            relative = Path(*expanded.parts[1:])
        else:
            anchor = Path.cwd()
            relative = expanded

    destination = (anchor / relative).resolve()
    if not relative.parts:
        return destination, destination
    indicator = (anchor / relative.parts[0]).resolve()
    return destination, indicator


def list_targets() -> list[InstallTarget]:
    """Return all known targets with resolved paths and availability status.

    Availability is based on the marker directory for the agentic tool, not the
    full skills directory. For example, ``~/.cursor`` is enough to treat
    ``~/.cursor/skills`` as installable.
    """
    targets = []
    for label, raw in _KNOWN_DIRS:
        destination, indicator = _resolve_known_target(raw)
        targets.append(InstallTarget(label=label, path=destination, exists=indicator.exists()))
    return targets


def install(
    *,
    target: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> list[tuple[str, str]]:
    """Copy all bundled skills into target directories.

    Returns a list of (path, status) tuples where status is one of
    'installed', 'skipped', 'dry-run', 'error'.
    """
    if target is not None:
        destinations = [("custom", target)]
    else:
        destinations = [
            (t.label, t.path) for t in list_targets() if t.exists
        ]

    results: list[tuple[str, str]] = []

    # Resolve the bundled skills directory from package data.
    try:
        skills_root = Path(str(SKILLS_DIR))
    except Exception as exc:
        return [("package", f"error: cannot locate bundled skills — {exc}")]

    for label, dest_dir in destinations:
        try:
            dest_dir = Path(dest_dir)
            for skill_dir in skills_root.iterdir():
                if not skill_dir.is_dir():
                    continue
                dest_skill = dest_dir / skill_dir.name
                if dest_skill.exists() and not force:
                    results.append((str(dest_skill), "skipped (use --force to overwrite)"))
                    continue
                if dry_run:
                    results.append((str(dest_skill), "dry-run"))
                    continue
                dest_dir.mkdir(parents=True, exist_ok=True)
                if dest_skill.exists():
                    shutil.rmtree(dest_skill)
                shutil.copytree(str(skill_dir), str(dest_skill))
                results.append((str(dest_skill), "installed"))
        except Exception as exc:
            results.append((label, f"error: {exc}"))

    return results
