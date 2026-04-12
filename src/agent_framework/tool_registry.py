"""Formal tool registry for AgentHost."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from agent_framework.tool import Tool

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolRegistry:
    """Discovers and caches Tool instances from configured directories.

    Tools are discovered eagerly (catalog built at startup) but loaded lazily
    (Python sidecar imported only on first ``get()``).  Programmatically
    registered tools (built-in tools, MCP-bridged tools) take priority over
    disk-discovered tools.

    Attributes:
        directories: Directories to scan for ``<name>.md`` tool definitions.
        _catalog: Maps tool name → markdown path (disk-discovered tools).
        _cache: Maps tool name → loaded Tool instance.
        _programmatic: Maps tool name → directly registered Tool instance.
    """

    directories: tuple[Path, ...]
    _catalog: dict[str, Path] = field(default_factory=dict, repr=False)
    _cache: dict[str, "Tool"] = field(default_factory=dict, repr=False)
    _programmatic: dict[str, "Tool"] = field(default_factory=dict, repr=False)

    @classmethod
    def from_config(cls, config: Any) -> "ToolRegistry":
        """Build a ToolRegistry from a HostConfig."""
        tools_dir = getattr(config, "tools_directory", None)
        directories: tuple[Path, ...] = (Path(tools_dir),) if tools_dir else ()
        return cls(directories=directories)

    def discover(self) -> None:
        """Scan all directories and build the name→path catalog.

        Does NOT load Python sidecars — that happens lazily on ``get()``.
        """
        catalog: dict[str, Path] = {}
        for directory in self.directories:
            if not Path(directory).is_dir():
                continue
            for md_path in sorted(Path(directory).glob("*.md")):
                name = _extract_tool_id(md_path)
                if name and name not in catalog:
                    catalog[name] = md_path.resolve()
        self._catalog = catalog

    def register(self, tool: "Tool") -> None:
        """Register a Tool instance directly (built-ins, MCP bridges, tests)."""
        self._programmatic[tool.name] = tool

    def get(self, name: str) -> "Tool":
        """Return a Tool by name, loading it lazily from the catalog if needed.

        Resolution order: programmatic → cache → catalog → KeyError.
        """
        if name in self._programmatic:
            return self._programmatic[name]
        if name in self._cache:
            return self._cache[name]
        if name in self._catalog:
            from agent_framework.tool import Tool
            tool = Tool.from_name(name, self._catalog[name].parent)
            self._cache[name] = tool
            return tool
        raise KeyError(f"Unknown tool: {name!r}")

    def list_names(self) -> tuple[str, ...]:
        """Return all known tool names (programmatic + catalog)."""
        names = set(self._programmatic) | set(self._catalog)
        return tuple(sorted(names))

    def get_all(self) -> tuple["Tool", ...]:
        """Load and return all known tools."""
        result = []
        for name in self.list_names():
            try:
                result.append(self.get(name))
            except (KeyError, Exception) as exc:  # noqa: BLE001
                _LOGGER.warning("ToolRegistry: failed to load tool %r — %s", name, exc)
        return tuple(result)

    def reload(self) -> None:
        """Clear all caches and re-discover from disk."""
        self._catalog.clear()
        self._cache.clear()
        self._programmatic.clear()
        self.discover()


def _extract_tool_id(md_path: Path) -> str | None:
    """Extract the tool id from markdown frontmatter, falling back to stem."""
    try:
        raw = md_path.read_text(encoding="utf-8")
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 2:
                meta = yaml.safe_load(parts[1]) or {}
                tool_id = str(meta.get("id", "")).strip()
                if tool_id:
                    return tool_id
        return md_path.stem
    except Exception:  # noqa: BLE001
        return md_path.stem


__all__ = ["ToolRegistry"]
