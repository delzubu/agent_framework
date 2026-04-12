"""Shared helpers for built-in tools."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agent_framework.tool import Tool, ToolDefinition, ToolParameter

if TYPE_CHECKING:
    from agent_framework.host import AgentHost
    from agent_framework.user_communication import PermissionRequest


def build_definition(
    tool_id: str,
    description: str,
    parameters: list[ToolParameter],
) -> ToolDefinition:
    """Construct a ToolDefinition for a built-in tool."""
    return ToolDefinition(
        tool_id=tool_id,
        description=description,
        parameters=tuple(parameters),
    )


class PermissionGatedTool(Tool):
    """Base class for built-in tools that require a permission prompt."""

    def _request_permission(self, host: "AgentHost", request: "PermissionRequest") -> bool:
        """Ask host.user_comm for permission.  Returns True if allowed."""
        user_comm = getattr(host, "user_comm", None)
        if user_comm is None:
            return True
        run_coro = getattr(host, "_run_user_comm_coro", None)
        if run_coro is None:
            return True
        decision = run_coro(user_comm.request_permission(request))
        return decision.allowed
