"""Built-in Edit tool — replace a string within a file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework.builtin_tools.base import PermissionGatedTool, build_definition
from agent_framework.tool import ToolParameter
from agent_framework.user_communication import PermissionRequest

_DEFINITION = build_definition(
    "Edit",
    "Replace a specific string within a file with new content.",
    [
        ToolParameter("file_path", "Path to the file to edit.", required=True),
        ToolParameter("old_string", "The exact string to find and replace.", required=True),
        ToolParameter("new_string", "The replacement string.", required=True),
        ToolParameter(
            "replace_all",
            "Replace all occurrences instead of only the first. Default: false.",
            required=False,
            value_type="boolean",
        ),
    ],
)


class EditTool(PermissionGatedTool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        file_path = arguments.get("file_path", "")
        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")
        replace_all = bool(arguments.get("replace_all", False))
        if not file_path:
            return "Error: file_path is required."
        path = Path(str(file_path))
        if not path.exists():
            return f"Error: file not found: {file_path}"
        request = PermissionRequest(
            tool_name="Edit",
            action="write",
            resource=str(file_path),
            summary=f"Edit {file_path}",
            details={"file_path": file_path},
        )
        if not self._request_permission(host, request):
            return f"Permission denied: edit {file_path}"
        try:
            original = path.read_text(encoding="utf-8")
        except OSError as exc:
            return f"Error reading file: {exc}"
        if old_string not in original:
            return f"Error: old_string not found in {file_path}"
        if replace_all:
            updated = original.replace(old_string, new_string)
            count = original.count(old_string)
        else:
            updated = original.replace(old_string, new_string, 1)
            count = 1
        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return f"Error writing file: {exc}"
        return f"Successfully replaced {count} occurrence(s) in {file_path}"


def build() -> EditTool:
    return EditTool(definition=_DEFINITION)
