"""Built-in Write tool — write content to a file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework.builtin_tools.base import PermissionGatedTool, build_definition
from agent_framework.tool import ToolParameter
from agent_framework.user_communication import PermissionRequest

_DEFINITION = build_definition(
    "Write",
    "Write content to a file, overwriting it if it already exists.",
    [
        ToolParameter("file_path", "Path to the file to write.", required=True),
        ToolParameter("content", "Content to write to the file.", required=True),
    ],
)


class WriteTool(PermissionGatedTool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        file_path = arguments.get("file_path", "")
        content = arguments.get("content", "")
        if not file_path:
            return "Error: file_path is required."
        request = PermissionRequest(
            tool_name="Write",
            action="write",
            resource=str(file_path),
            summary=f"Write to {file_path}",
            details={"file_path": file_path, "content_length": len(str(content))},
        )
        if not self._request_permission(host, request):
            return f"Permission denied: write to {file_path}"
        path = Path(str(file_path))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content), encoding="utf-8")
            return f"Successfully wrote {len(str(content))} characters to {file_path}"
        except OSError as exc:
            return f"Error writing file: {exc}"


def build() -> WriteTool:
    return WriteTool(definition=_DEFINITION)
