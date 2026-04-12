"""Bridge MCP tools into the ToolRegistry as standard Tool instances."""

from __future__ import annotations

import logging
from typing import Any, Callable

from agent_framework.tool import Tool, ToolDefinition

_LOGGER = logging.getLogger(__name__)


class McpBridgeTool(Tool):
    """A Tool whose invoke() delegates to an MCP server via a coroutine runner."""

    def __init__(
        self,
        definition: ToolDefinition,
        qualified_name: str,
        manager: Any,
        run_coro: Callable,
    ) -> None:
        super().__init__(definition=definition)
        self._qualified_name = qualified_name
        self._manager = manager
        self._run_coro = run_coro

    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        try:
            return self._run_coro(self._manager.call_tool(self._qualified_name, arguments))
        except Exception as exc:  # noqa: BLE001
            return f"Error calling MCP tool {self._qualified_name!r}: {exc}"


def bridge_mcp_tools(
    manager: Any,
    tool_registry: Any,
    run_coro: Callable,
) -> None:
    """Register McpBridgeTool instances for every connected MCP tool.

    Args:
        manager: An McpManager instance.
        tool_registry: A ToolRegistry to register tools into.
        run_coro: A callable(coro) that runs an async coroutine synchronously.
    """
    for tool_info in manager.all_tools():
        definition = ToolDefinition(
            tool_id=tool_info.qualified_name,
            description=tool_info.description or f"MCP tool: {tool_info.qualified_name}",
            parameters_schema=tool_info.input_schema or {"type": "object", "properties": {}},
        )
        tool = McpBridgeTool(
            definition=definition,
            qualified_name=tool_info.qualified_name,
            manager=manager,
            run_coro=run_coro,
        )
        tool_registry.register(tool)
        _LOGGER.debug("Registered MCP tool: %s", tool_info.qualified_name)


__all__ = ["McpBridgeTool", "bridge_mcp_tools"]
