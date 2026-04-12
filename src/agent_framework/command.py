"""Command definitions, registry, and prompt rendering.

Commands are parametrized Markdown prompts stored in a dedicated directory.
Each command file follows the Claude Code frontmatter format:

    ---
    description: Short description of what this command does
    argument-hint: <argument description>    # optional
    allowed-tools:                           # optional
      - Read
      - Bash
    model: gpt-4o                            # optional model override
    ---
    The prompt template. Use $ARGUMENTS for the full raw argument string,
    or $1, $2, … $9 for positional tokens.

Command name = filename stem.  Nested directories are not supported in this
iteration (flat directory only).  Unknown commands dispatch to a
consumer-supplied callback registered on the host.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_LOGGER = logging.getLogger(__name__)

# Regex to match $1–$9 and $ARGUMENTS placeholders
_PLACEHOLDER_RE = re.compile(r"\$(ARGUMENTS|[1-9])")


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    """A single discovered command, fully loaded at discovery time.

    Attributes:
        name: Command name (filename stem, e.g. ``hello``).
        description: Short human-readable description.
        argument_hint: Optional hint shown to the user for arguments.
        allowed_tools: Optional set of tool names the command may use.
        model: Optional model override for this command.
        prompt_template: The raw prompt body with ``$ARGUMENTS`` / ``$1``–``$9``
            placeholders.
        source_path: Absolute path to the ``.md`` file.
    """

    name: str
    description: str
    argument_hint: str = ""
    allowed_tools: tuple[str, ...] = ()
    model: str | None = None
    prompt_template: str = ""
    source_path: Path = field(default_factory=Path)


@dataclass(slots=True)
class CommandRegistry:
    """Discovers and caches CommandDefinitions from configured directories.

    Commands are fully loaded at discovery time (prompts are cheap; no Python
    sidecars).

    Attributes:
        directories: Directories to scan for ``*.md`` command files.
        _cache: Maps command name → CommandDefinition.
    """

    directories: tuple[Path, ...]
    _cache: dict[str, CommandDefinition] = field(default_factory=dict, repr=False)

    @classmethod
    def from_config(cls, config: Any) -> "CommandRegistry":
        """Build a CommandRegistry from a HostConfig."""
        dirs = getattr(config, "commands_directories", ()) or ()
        return cls(directories=tuple(dirs))

    def discover(self) -> None:
        """Scan all directories and fully parse every ``*.md`` command file.

        Missing or malformed frontmatter is logged as WARNING and skipped.
        First directory wins on duplicate command names.
        """
        cache: dict[str, CommandDefinition] = {}
        for directory in self.directories:
            if not Path(directory).is_dir():
                continue
            for md_path in sorted(Path(directory).glob("*.md")):
                defn = _parse_command_file(md_path)
                if defn is not None and defn.name not in cache:
                    cache[defn.name] = defn
        self._cache = cache

    def get(self, name: str) -> CommandDefinition:
        """Return a CommandDefinition by name.  Raises KeyError if not found."""
        if name not in self._cache:
            raise KeyError(f"Unknown command: {name!r}")
        return self._cache[name]

    def get_all(self) -> tuple[CommandDefinition, ...]:
        """Return all discovered commands."""
        return tuple(self._cache.values())

    def reload(self) -> None:
        """Clear cache and re-discover from disk."""
        self._cache.clear()
        self.discover()


def render(cmd: CommandDefinition, raw_args: str) -> str:
    """Render a command prompt by substituting argument placeholders.

    - ``$ARGUMENTS`` is replaced with the full ``raw_args`` string.
    - ``$1``–``$9`` are replaced with whitespace-split positional tokens
      (missing tokens expand to an empty string).

    Args:
        cmd: The command whose ``prompt_template`` is rendered.
        raw_args: Raw argument string supplied by the user (e.g. ``"World"``).

    Returns:
        The rendered prompt string ready to be injected as a user message.
    """
    tokens = raw_args.split()

    def _substitute(match: re.Match) -> str:
        key = match.group(1)
        if key == "ARGUMENTS":
            return raw_args
        idx = int(key)  # 1–9
        return tokens[idx - 1] if idx <= len(tokens) else ""

    return _PLACEHOLDER_RE.sub(_substitute, cmd.prompt_template)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_command_file(md_path: Path) -> CommandDefinition | None:
    """Parse a command markdown file.  Returns None and logs on any error."""
    try:
        raw = md_path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            _LOGGER.warning("Command %s: missing YAML frontmatter — skipped.", md_path)
            return None
        parts = raw.split("---", 2)
        if len(parts) < 3:
            _LOGGER.warning("Command %s: unclosed frontmatter — skipped.", md_path)
            return None
        meta = yaml.safe_load(parts[1]) or {}
        prompt_body = parts[2].strip()
        name = md_path.stem
        description = str(meta.get("description", "")).strip()
        if not description:
            _LOGGER.warning("Command %s: 'description' is required — skipped.", md_path)
            return None
        argument_hint = str(meta.get("argument-hint", "")).strip()
        raw_tools = meta.get("allowed-tools", []) or []
        if isinstance(raw_tools, str):
            raw_tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
        allowed_tools = tuple(str(t).strip() for t in raw_tools if str(t).strip())
        model_raw = meta.get("model", None)
        model = str(model_raw).strip() if model_raw else None
        return CommandDefinition(
            name=name,
            description=description,
            argument_hint=argument_hint,
            allowed_tools=allowed_tools,
            model=model,
            prompt_template=prompt_body,
            source_path=md_path.resolve(),
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Command %s: failed to parse — %s", md_path, exc)
        return None


__all__ = ["CommandDefinition", "CommandRegistry", "render"]
