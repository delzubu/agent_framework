"""Built-in Read tool — read a file and return numbered lines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework.builtin_tools.base import build_definition
from agent_framework.tool import Tool, ToolParameter

_MAX_CHARS = 100_000
_DEFINITION = build_definition(
    "Read",
    "Read a file from the filesystem and return its contents with line numbers.",
    [
        ToolParameter("file_path", "Absolute or relative path to the file to read.", required=True),
        ToolParameter("limit", "Maximum number of lines to return.", required=False, value_type="integer"),
        ToolParameter("offset", "Line number to start reading from (1-based).", required=False, value_type="integer"),
    ],
)


class ReadTool(Tool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        file_path = arguments.get("file_path", "")
        if not file_path:
            return "Error: file_path is required."
        path = Path(str(file_path))
        if not path.exists():
            return f"Error: file not found: {file_path}"
        if not path.is_file():
            return f"Error: not a file: {file_path}"
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Error reading file: {exc}"
        lines = raw.splitlines()
        offset = max(1, int(arguments.get("offset") or 1))
        limit_raw = arguments.get("limit")
        limit = int(limit_raw) if limit_raw is not None else None
        selected = lines[offset - 1:]
        if limit is not None:
            selected = selected[:limit]
        numbered = "\n".join(f"{offset + i:>6}→{line}" for i, line in enumerate(selected))
        if len(numbered) > _MAX_CHARS:
            numbered = numbered[:_MAX_CHARS] + "\n... (truncated)"
        return numbered or "(empty file)"


def build() -> ReadTool:
    return ReadTool(definition=_DEFINITION)
