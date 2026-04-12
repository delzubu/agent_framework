"""MCP protocol types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


MCP_PROTOCOL_VERSION = "2024-11-05"

INIT_PARAMS: dict[str, Any] = {
    "protocolVersion": MCP_PROTOCOL_VERSION,
    "capabilities": {
        "tools": {},
        "roots": {"listChanged": False},
    },
    "clientInfo": {
        "name": "agent_framework",
        "version": "1.0",
    },
}


class McpTransport(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: McpTransport = McpTransport.STDIO
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class McpToolInfo:
    """Metadata for a tool exposed by an MCP server."""

    server_name: str
    tool_name: str
    qualified_name: str    # mcp__<server>__<tool>
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def make_qualified_name(server_name: str, tool_name: str) -> str:
        """Build the canonical qualified name: mcp__<server>__<tool>."""
        import re
        safe_server = re.sub(r"[^a-zA-Z0-9]", "_", server_name)
        safe_tool = re.sub(r"[^a-zA-Z0-9]", "_", tool_name)
        return f"mcp__{safe_server}__{safe_tool}"


__all__ = ["MCP_PROTOCOL_VERSION", "INIT_PARAMS", "McpTransport", "McpServerConfig", "McpToolInfo"]
