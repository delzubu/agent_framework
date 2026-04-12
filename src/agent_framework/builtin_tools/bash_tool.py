"""Built-in Bash tool — execute a shell command."""

from __future__ import annotations

import subprocess
from typing import Any

from agent_framework.builtin_tools.base import PermissionGatedTool, build_definition
from agent_framework.tool import ToolParameter
from agent_framework.user_communication import PermissionRequest

_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 600
_MAX_OUTPUT = 50_000

_DEFINITION = build_definition(
    "Bash",
    "Execute a shell command and return its stdout and stderr output.",
    [
        ToolParameter("command", "The shell command to execute.", required=True),
        ToolParameter(
            "timeout",
            f"Timeout in seconds (default: {_DEFAULT_TIMEOUT}, max: {_MAX_TIMEOUT}).",
            required=False,
            value_type="integer",
        ),
    ],
)


class BashTool(PermissionGatedTool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        command = arguments.get("command", "")
        if not command:
            return "Error: command is required."
        timeout_raw = arguments.get("timeout")
        timeout = min(int(timeout_raw), _MAX_TIMEOUT) if timeout_raw is not None else _DEFAULT_TIMEOUT
        request = PermissionRequest(
            tool_name="Bash",
            action="execute",
            resource=str(command),
            summary=f"Execute: {command[:120]}",
            details={"command": command, "timeout": timeout},
        )
        if not self._request_permission(host, request):
            return f"Permission denied: execute command"
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout)
            if result.stderr:
                output_parts.append(f"[stderr]\n{result.stderr}")
            combined = "\n".join(output_parts) if output_parts else "(no output)"
            if len(combined) > _MAX_OUTPUT:
                combined = combined[:_MAX_OUTPUT] + "\n... (truncated)"
            if result.returncode != 0:
                combined = f"[exit code {result.returncode}]\n{combined}"
            return combined
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        except OSError as exc:
            return f"Error executing command: {exc}"


def build() -> BashTool:
    return BashTool(definition=_DEFINITION)
