"""Built-in Glob tool — find files matching a pattern."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework.builtin_tools.base import build_definition
from agent_framework.tool import Tool, ToolParameter

_MAX_RESULTS = 500

_DEFINITION = build_definition(
    "Glob",
    "Find files matching a glob pattern. Returns paths sorted by modification time (newest first).",
    [
        ToolParameter("pattern", "Glob pattern to match (e.g. '**/*.py').", required=True),
        ToolParameter("path", "Directory to search in. Defaults to current working directory.", required=False),
    ],
)


class GlobTool(Tool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        pattern = arguments.get("pattern", "")
        if not pattern:
            return "Error: pattern is required."
        search_root = Path(arguments.get("path") or ".")
        if not search_root.exists():
            return f"Error: directory not found: {search_root}"
        try:
            matches = list(search_root.glob(pattern))
            matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            if not matches:
                return "(no matches)"
            limited = matches[:_MAX_RESULTS]
            lines = [str(p) for p in limited]
            result = "\n".join(lines)
            if len(matches) > _MAX_RESULTS:
                result += f"\n... ({len(matches) - _MAX_RESULTS} more results not shown)"
            return result
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"


def build() -> GlobTool:
    return GlobTool(definition=_DEFINITION)
