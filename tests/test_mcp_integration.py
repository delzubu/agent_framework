"""Tests for MCP integration: types, config, manager, and tool bridging."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_framework.mcp.types import McpServerConfig, McpToolInfo, McpTransport, MCP_PROTOCOL_VERSION
from agent_framework.mcp.manager import McpManager, _parse_qualified_name
from agent_framework.mcp.tools import McpBridgeTool, bridge_mcp_tools
from agent_framework.tool import ToolDefinition
from agent_framework.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class TestMcpTypes:
    def test_protocol_version(self):
        assert MCP_PROTOCOL_VERSION == "2024-11-05"

    def test_mcp_server_config_defaults(self):
        cfg = McpServerConfig(name="test")
        assert cfg.transport == McpTransport.STDIO
        assert cfg.disabled is False
        assert cfg.timeout == 30

    def test_mcp_tool_info_qualified_name(self):
        name = McpToolInfo.make_qualified_name("my_server", "my_tool")
        assert name == "mcp__my_server__my_tool"

    def test_mcp_tool_info_qualified_name_sanitizes(self):
        name = McpToolInfo.make_qualified_name("my-server", "my-tool")
        assert name == "mcp__my_server__my_tool"

    def test_mcp_tool_info_fields(self):
        info = McpToolInfo(
            server_name="srv",
            tool_name="search",
            qualified_name="mcp__srv__search",
            description="Search tool",
            input_schema={"type": "object", "properties": {}},
        )
        assert info.server_name == "srv"
        assert info.tool_name == "search"
        assert info.qualified_name == "mcp__srv__search"


# ---------------------------------------------------------------------------
# _parse_qualified_name
# ---------------------------------------------------------------------------


class TestParseQualifiedName:
    def test_parses_valid_name(self):
        server, tool = _parse_qualified_name("mcp__myserver__mytool")
        assert server == "myserver"
        assert tool == "mytool"

    def test_parses_tool_with_underscores(self):
        server, tool = _parse_qualified_name("mcp__server__read_file")
        assert server == "server"
        assert tool == "read_file"

    def test_raises_on_missing_prefix(self):
        with pytest.raises(ValueError, match="Invalid MCP qualified name"):
            _parse_qualified_name("server__tool")

    def test_raises_on_missing_tool(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_qualified_name("mcp__serveronly")


# ---------------------------------------------------------------------------
# McpManager
# ---------------------------------------------------------------------------


class TestMcpManager:
    @pytest.mark.asyncio
    async def test_start_all_captures_connection_errors(self):
        cfg = McpServerConfig(name="bad", transport=McpTransport.STDIO, command="nonexistent_cmd_xyz")
        manager = McpManager(configs={"bad": cfg})
        errors = await manager.start_all()
        assert "bad" in errors
        assert isinstance(errors["bad"], Exception)

    @pytest.mark.asyncio
    async def test_start_all_returns_none_on_success(self, monkeypatch):
        """Mock a successful McpClient connection."""
        from agent_framework.mcp.client import McpClient
        from unittest.mock import AsyncMock as AM

        tool_info = McpToolInfo(
            server_name="ok",
            tool_name="ping",
            qualified_name="mcp__ok__ping",
            description="Ping tool",
        )
        mock_client = MagicMock(spec=McpClient)
        mock_client.connect = AM(return_value=None)
        mock_client.list_tools = AM(return_value=[tool_info])

        monkeypatch.setattr(
            "agent_framework.mcp.manager.McpClient",
            lambda config: mock_client,
        )

        cfg = McpServerConfig(name="ok", command="fake")
        manager = McpManager(configs={"ok": cfg})
        errors = await manager.start_all()
        assert errors.get("ok") is None

    @pytest.mark.asyncio
    async def test_stop_all_clears_clients(self):
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()
        manager = McpManager(configs={})
        manager._clients["srv"] = mock_client
        await manager.stop_all()
        assert manager._clients == {}

    def test_all_tools_returns_cached_tools(self):
        tool_info = McpToolInfo(
            server_name="srv",
            tool_name="search",
            qualified_name="mcp__srv__search",
            description="Search",
        )
        mock_client = MagicMock()
        mock_client._cached_tools = [tool_info]
        manager = McpManager(configs={})
        manager._clients["srv"] = mock_client
        tools = manager.all_tools()
        assert len(tools) == 1
        assert tools[0].qualified_name == "mcp__srv__search"

    @pytest.mark.asyncio
    async def test_call_tool_raises_for_unknown_server(self):
        manager = McpManager(configs={})
        with pytest.raises(KeyError, match="No MCP server named"):
            await manager.call_tool("mcp__unknown__tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_routes_to_correct_client(self):
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value="pong")
        manager = McpManager(configs={})
        manager._clients["srv"] = mock_client
        result = await manager.call_tool("mcp__srv__ping", {"arg": "val"})
        assert result == "pong"
        mock_client.call_tool.assert_called_once_with("ping", {"arg": "val"})


# ---------------------------------------------------------------------------
# McpBridgeTool
# ---------------------------------------------------------------------------


class TestMcpBridgeTool:
    def test_invoke_calls_manager(self):
        definition = ToolDefinition(
            tool_id="mcp__srv__search",
            description="MCP search",
            parameters_schema={"type": "object", "properties": {}},
        )
        mock_manager = MagicMock()
        result_coro = AsyncMock(return_value="found it")

        call_results = []

        def run_coro(coro):
            import asyncio, concurrent.futures
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            val = asyncio.run(coro) if loop is None else concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(asyncio.run, coro).result()
            call_results.append(val)
            return val

        async def fake_call_tool(name, args):
            return "found it"

        mock_manager.call_tool = fake_call_tool

        tool = McpBridgeTool(
            definition=definition,
            qualified_name="mcp__srv__search",
            manager=mock_manager,
            run_coro=run_coro,
        )
        result = tool.invoke({"query": "test"}, host=None)
        assert result == "found it"
        assert call_results == ["found it"]

    def test_invoke_returns_error_string_on_exception(self):
        definition = ToolDefinition(
            tool_id="mcp__srv__fail",
            description="Will fail",
            parameters_schema={"type": "object", "properties": {}},
        )

        def run_coro(coro):
            raise RuntimeError("connection lost")

        tool = McpBridgeTool(
            definition=definition,
            qualified_name="mcp__srv__fail",
            manager=MagicMock(),
            run_coro=run_coro,
        )
        result = tool.invoke({}, host=None)
        assert "Error" in result


# ---------------------------------------------------------------------------
# bridge_mcp_tools
# ---------------------------------------------------------------------------


class TestBridgeMcpTools:
    def test_registers_tools_in_registry(self):
        tool_info = McpToolInfo(
            server_name="srv",
            tool_name="read",
            qualified_name="mcp__srv__read",
            description="Read file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        mock_manager = MagicMock()
        mock_manager.all_tools.return_value = [tool_info]

        registry = ToolRegistry(directories=())

        def run_coro(coro):
            import asyncio, concurrent.futures
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            return asyncio.run(coro) if loop is None else concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(asyncio.run, coro).result()

        bridge_mcp_tools(mock_manager, registry, run_coro)
        assert "mcp__srv__read" in registry.list_names()

    def test_registered_tool_is_mcp_bridge_tool(self):
        tool_info = McpToolInfo(
            server_name="srv",
            tool_name="ping",
            qualified_name="mcp__srv__ping",
            description="Ping",
        )
        mock_manager = MagicMock()
        mock_manager.all_tools.return_value = [tool_info]

        registry = ToolRegistry(directories=())
        bridge_mcp_tools(mock_manager, registry, lambda coro: None)
        tool = registry.get("mcp__srv__ping")
        assert isinstance(tool, McpBridgeTool)

    def test_uses_input_schema_from_tool_info(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        tool_info = McpToolInfo(
            server_name="srv",
            tool_name="search",
            qualified_name="mcp__srv__search",
            description="Search",
            input_schema=schema,
        )
        mock_manager = MagicMock()
        mock_manager.all_tools.return_value = [tool_info]
        registry = ToolRegistry(directories=())
        bridge_mcp_tools(mock_manager, registry, lambda coro: None)
        tool = registry.get("mcp__srv__search")
        assert tool.definition.parameters_schema == schema


# ---------------------------------------------------------------------------
# MCP config loader
# ---------------------------------------------------------------------------


class TestLoadMcpConfigs:
    def test_load_from_explicit_path(self, tmp_path: Path):
        from agent_framework.mcp.config import load_mcp_configs

        config_file = tmp_path / "mcp.json"
        config_file.write_text(
            '{"mcpServers": {"myserver": {"command": "python", "args": ["-m", "server"]}}}',
            encoding="utf-8",
        )
        configs = load_mcp_configs(env_path=None, explicit_path=config_file)
        assert "myserver" in configs
        assert configs["myserver"].command == "python"

    def test_skips_disabled_servers(self, tmp_path: Path):
        from agent_framework.mcp.config import load_mcp_configs

        config_file = tmp_path / "mcp.json"
        config_file.write_text(
            '{"mcpServers": {"disabled_srv": {"command": "x", "disabled": true}}}',
            encoding="utf-8",
        )
        configs = load_mcp_configs(env_path=None, explicit_path=config_file)
        assert "disabled_srv" not in configs

    def test_returns_empty_when_no_file(self, tmp_path: Path):
        from agent_framework.mcp.config import load_mcp_configs

        configs = load_mcp_configs(env_path=tmp_path, explicit_path=None)
        assert isinstance(configs, dict)

    def test_http_transport_from_url(self, tmp_path: Path):
        from agent_framework.mcp.config import load_mcp_configs

        config_file = tmp_path / "mcp.json"
        config_file.write_text(
            '{"mcpServers": {"http_srv": {"url": "http://localhost:8080/mcp"}}}',
            encoding="utf-8",
        )
        configs = load_mcp_configs(env_path=None, explicit_path=config_file)
        assert "http_srv" in configs
        srv = configs["http_srv"]
        assert srv.url == "http://localhost:8080/mcp"
