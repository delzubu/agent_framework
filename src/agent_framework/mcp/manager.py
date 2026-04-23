"""MCP manager: orchestrates multiple MCP server connections."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from agent_framework.mcp.client import McpClient
from agent_framework.mcp.types import McpServerConfig, McpToolInfo

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class McpManager:
    """Manages the lifecycle of multiple MCP server connections.

    Attributes:
        configs: Server name → McpServerConfig mapping.
        _clients: Connected McpClient instances.
        connect_errors: Per-server connection errors from the last start_all().
    """

    configs: dict[str, McpServerConfig]
    _clients: dict[str, McpClient] = field(default_factory=dict, repr=False)
    connect_errors: dict[str, Exception | None] = field(default_factory=dict, repr=False)

    async def start_all(self) -> dict[str, Exception | None]:
        """Connect all configured servers in parallel.

        Returns a dict mapping server name → Exception (or None on success).
        Never raises; individual failures are captured and logged.
        """
        tasks = {
            name: asyncio.create_task(self._connect_one(name, cfg))
            for name, cfg in self.configs.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        errors: dict[str, Exception | None] = {}
        for name, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                _LOGGER.warning("MCP server %r failed to connect: %s", name, result)
                errors[name] = result
            else:
                errors[name] = None
        self.connect_errors = errors
        return errors

    async def stop_all(self) -> None:
        """Disconnect all connected servers."""
        await asyncio.gather(
            *(client.disconnect() for client in self._clients.values()),
            return_exceptions=True,
        )
        self._clients.clear()

    def all_tools(self) -> list[McpToolInfo]:
        """Return all tools from all connected servers (cached on connect)."""
        tools: list[McpToolInfo] = []
        for client in self._clients.values():
            tools.extend(getattr(client, "_cached_tools", []))
        return tools

    async def call_tool(self, qualified_name: str, arguments: dict) -> str:
        """Call an MCP tool by its qualified name (mcp__server__tool).

        Auto-reconnects once if the connection appears to have dropped.
        """
        server_name, tool_name = _parse_qualified_name(qualified_name)
        client = self._clients.get(server_name)
        if client is None:
            raise KeyError(f"No MCP server named {server_name!r} is connected.")
        try:
            return await client.call_tool(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "MCP call_tool failed for %r, attempting reconnect: %s", qualified_name, exc
            )
            try:
                await client.reconnect()
                return await client.call_tool(tool_name, arguments)
            except Exception as retry_exc:  # noqa: BLE001
                return f"Error calling MCP tool {qualified_name!r}: {retry_exc}"

    async def _connect_one(self, name: str, config: McpServerConfig) -> None:
        client = McpClient(config)
        await client.connect()
        tools = await client.list_tools()
        client._cached_tools = tools  # type: ignore[attr-defined]
        self._clients[name] = client
        _LOGGER.info("MCP server %r connected, %d tool(s) available.", name, len(tools))


def _parse_qualified_name(qualified_name: str) -> tuple[str, str]:
    """Parse 'mcp__server__tool' → ('server', 'tool')."""
    if not qualified_name.startswith("mcp__"):
        raise ValueError(f"Invalid MCP qualified name: {qualified_name!r}")
    rest = qualified_name[5:]
    parts = rest.split("__", 1)
    if len(parts) != 2 or not parts[1]:
        raise ValueError(f"Cannot parse MCP qualified name: {qualified_name!r}")
    return parts[0], parts[1]


__all__ = ["McpManager"]
