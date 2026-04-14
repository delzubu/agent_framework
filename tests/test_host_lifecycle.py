"""Tests for AgentHost lifecycle: create, start, aclose, execute_command."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_framework.host import AgentHost
from agent_framework.config import HostConfig, load_host_config
from agent_framework.builtin_tools import BUILTIN_TOOL_NAMES
from agent_framework.model import ModelResponse, ModelContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeModelDriver:
    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        return ModelResponse(payload={"kind": "final_message", "message": "ok"}, raw_text="ok")

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass


def test_agent_host_uses_supplied_runtime_tracer(fake_model_driver):
    from agent_framework.tracing import CompositeRuntimeTracer

    tracer = CompositeRuntimeTracer()
    host = AgentHost.create(model_driver=fake_model_driver)
    host.runtime_tracer = tracer
    assert host.runtime_tracer is tracer


def _write_env(env_path: Path) -> None:
    env_path.write_text(
        "OPENAI_API_KEY=test-key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n",
        encoding="utf-8",
    )


def _write_cmd(cmds_dir: Path, name: str, description: str, body: str) -> None:
    md = cmds_dir / f"{name}.md"
    md.write_text(f"---\ndescription: {description}\n---\n{body}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# AgentHost.create()
# ---------------------------------------------------------------------------


class TestAgentHostCreate:
    def test_create_registers_builtin_tools(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        names = set(host.tool_registry.list_names())
        for expected in BUILTIN_TOOL_NAMES:
            assert expected in names

    def test_create_without_builtin_tools(self):
        host = AgentHost.create(model_driver=FakeModelDriver(), builtin_tools=False)
        names = set(host.tool_registry.list_names())
        assert len(names) == 0

    def test_create_with_null_user_comm_by_default(self):
        from agent_framework.user_communication import NullUserCommunication
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert isinstance(host.user_comm, NullUserCommunication)

    def test_create_with_custom_user_comm(self):
        from agent_framework.user_communication import NullUserCommunication
        comm = NullUserCommunication()
        host = AgentHost.create(model_driver=FakeModelDriver(), user_comm=comm)
        assert host.user_comm is comm

    def test_create_with_command_fallback(self):
        async def fallback(name, raw_args):
            return f"fallback:{name}"

        host = AgentHost.create(model_driver=FakeModelDriver(), command_fallback=fallback)
        assert host._command_fallback is fallback

    def test_create_without_mcp(self):
        host = AgentHost.create(model_driver=FakeModelDriver(), mcp_enabled=False)
        assert host.mcp_manager is None


# ---------------------------------------------------------------------------
# AgentHost.start()
# ---------------------------------------------------------------------------


class TestAgentHostStart:
    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, tmp_path: Path):
        host = AgentHost.create(model_driver=FakeModelDriver(), mcp_enabled=False)
        await host.start()
        assert host._started is True
        # Second call should not raise
        await host.start()
        assert host._started is True

    @pytest.mark.asyncio
    async def test_start_runs_discovery(self, tmp_path: Path):
        cmds_dir = tmp_path / "commands"
        cmds_dir.mkdir()
        _write_cmd(cmds_dir, "hello", "Say hello", "Hello $ARGUMENTS")

        config = HostConfig(commands_directories=(cmds_dir,))
        host = AgentHost.create(model_driver=FakeModelDriver(), config=config, mcp_enabled=False)
        await host.start()
        # After start, command should be discovered
        cmd = host.command_registry.get("hello")
        assert cmd.name == "hello"

    @pytest.mark.asyncio
    async def test_start_wraps_user_comm_with_tracing_when_audit_enabled(self, tmp_path: Path):
        from agent_framework.user_communication import NullUserCommunication

        host = AgentHost.create(model_driver=FakeModelDriver(), mcp_enabled=False)
        host.enable_audit_trace(output_dir=tmp_path)
        host.user_comm = NullUserCommunication()
        await host.start()
        # user_comm should be wrapped in _TracingUserCommunication
        from agent_framework.host import _TracingUserCommunication
        assert isinstance(host.user_comm, _TracingUserCommunication)

    @pytest.mark.asyncio
    async def test_start_does_not_wrap_without_audit_tracer(self):
        from agent_framework.user_communication import NullUserCommunication
        from agent_framework.host import _TracingUserCommunication

        host = AgentHost.create(model_driver=FakeModelDriver(), mcp_enabled=False)
        await host.start()
        assert not isinstance(host.user_comm, _TracingUserCommunication)


# ---------------------------------------------------------------------------
# AgentHost.aclose()
# ---------------------------------------------------------------------------


class TestAgentHostAclose:
    @pytest.mark.asyncio
    async def test_aclose_no_mcp_does_not_raise(self):
        host = AgentHost.create(model_driver=FakeModelDriver(), mcp_enabled=False)
        await host.aclose()  # Should not raise

    @pytest.mark.asyncio
    async def test_aclose_calls_mcp_stop_all(self):
        host = AgentHost.create(model_driver=FakeModelDriver(), mcp_enabled=False)
        mock_mcp = AsyncMock()
        mock_mcp.stop_all = AsyncMock()
        host.mcp_manager = mock_mcp
        await host.aclose()
        mock_mcp.stop_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_aclose_calls_driver_aclose_if_available(self):
        class AsyncDriver:
            async def decide(self, **kwargs): ...
            def set_trace_callbacks(self, **kwargs): ...
            async def aclose(self): ...

        mock_driver = AsyncMock(spec=AsyncDriver)
        host = AgentHost.create(model_driver=mock_driver, mcp_enabled=False)
        await host.aclose()
        mock_driver.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_aclose_skips_driver_without_aclose(self):
        host = AgentHost.create(model_driver=FakeModelDriver(), mcp_enabled=False)
        await host.aclose()  # FakeModelDriver has no aclose — must not raise


# ---------------------------------------------------------------------------
# execute_command()
# ---------------------------------------------------------------------------


class TestExecuteCommand:
    @pytest.mark.asyncio
    async def test_returns_rendered_prompt_for_known_command(self, tmp_path: Path):
        cmds_dir = tmp_path / "commands"
        cmds_dir.mkdir()
        _write_cmd(cmds_dir, "greet", "Greet someone", "Hello $1!")
        config = HostConfig(commands_directories=(cmds_dir,))
        host = AgentHost.create(model_driver=FakeModelDriver(), config=config, mcp_enabled=False)
        await host.start()
        result = await host.execute_command("greet", "World")
        assert result == "Hello World!"

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_without_fallback(self, tmp_path: Path):
        host = AgentHost.create(model_driver=FakeModelDriver(), mcp_enabled=False)
        await host.start()
        result = await host.execute_command("unknown", "args")
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_fallback_for_unknown_command(self):
        fallback_calls = []

        async def fallback(name, raw_args):
            fallback_calls.append((name, raw_args))
            return f"fallback:{name}"

        host = AgentHost.create(
            model_driver=FakeModelDriver(),
            mcp_enabled=False,
            command_fallback=fallback,
        )
        await host.start()
        result = await host.execute_command("unknown_cmd", "some args")
        assert result == "fallback:unknown_cmd"
        assert fallback_calls == [("unknown_cmd", "some args")]

    @pytest.mark.asyncio
    async def test_fallback_not_called_when_command_exists(self, tmp_path: Path):
        cmds_dir = tmp_path / "commands"
        cmds_dir.mkdir()
        _write_cmd(cmds_dir, "existing", "Existing", "Fixed prompt $ARGUMENTS")
        config = HostConfig(commands_directories=(cmds_dir,))

        fallback_calls = []

        async def fallback(name, raw_args):
            fallback_calls.append(name)

        host = AgentHost.create(
            model_driver=FakeModelDriver(),
            config=config,
            mcp_enabled=False,
            command_fallback=fallback,
        )
        await host.start()
        result = await host.execute_command("existing", "x y z")
        assert result == "Fixed prompt x y z"
        assert fallback_calls == []


# ---------------------------------------------------------------------------
# AgentHostProtocol compliance
# ---------------------------------------------------------------------------


class TestAgentHostProtocolCompliance:
    def test_has_get_tool(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "get_tool", None))

    def test_has_register_tool(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "register_tool", None))

    def test_has_get_agent(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "get_agent", None))

    def test_has_load_agent(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "load_agent", None))

    def test_has_execute_tool(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "execute_tool", None))

    def test_has_call_subagent(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "call_subagent", None))

    def test_has_open_context(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "open_context", None))

    def test_has_get_model_driver(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "get_model_driver", None))

    def test_has_request_user_input(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "request_user_input", None))

    def test_has_resolve_callback(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "resolve_callback", None))

    def test_has_get_skill_registry(self):
        host = AgentHost.create(model_driver=FakeModelDriver())
        assert callable(getattr(host, "get_skill_registry", None))

    def test_get_tool_raises_key_error_for_unknown(self):
        host = AgentHost.create(model_driver=FakeModelDriver(), builtin_tools=False)
        import pytest
        with pytest.raises(KeyError):
            host.get_tool("NonexistentTool")

    def test_register_tool_then_get_tool(self):
        from agent_framework.tool import Tool, ToolDefinition
        host = AgentHost.create(model_driver=FakeModelDriver(), builtin_tools=False)

        class MyTool(Tool):
            def invoke(self, arguments, host):
                return "result"

        defn = ToolDefinition(tool_id="MyTool", description="test")
        tool = MyTool(definition=defn)
        host.register_tool(tool)
        assert host.get_tool("MyTool") is tool


# ---------------------------------------------------------------------------
# from_env compatibility
# ---------------------------------------------------------------------------


class TestFromEnvCompat:
    def test_from_env_accepts_deprecated_kwargs(self, tmp_path: Path):
        """from_env() should not raise when legacy io callables are passed."""
        _write_env(tmp_path / ".env")
        host = AgentHost.from_env(
            tmp_path / ".env",
            model_driver=FakeModelDriver(),
            input_reader=lambda _: "",
            output_writer=lambda _: None,
        )
        assert host is not None

    def test_from_env_eager_discovery(self, tmp_path: Path):
        """from_env() runs discovery synchronously (without await start())."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        # No tools on disk yet — just verify the host is constructed
        _write_env(tmp_path / ".env")
        (tmp_path / ".env").write_text(
            f"OPENAI_API_KEY=x\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
            f"TOOLS_DIRECTORY={tools_dir}\n",
            encoding="utf-8",
        )
        host = AgentHost.from_env(tmp_path / ".env", model_driver=FakeModelDriver())
        # Discovery ran; tool_registry has the tools_directory set
        assert host.tool_registry.directories


# ---------------------------------------------------------------------------
# _run_user_comm_coro bridge
# ---------------------------------------------------------------------------


class TestRunUserCommCoro:
    def test_runs_coro_outside_event_loop(self):
        host = AgentHost.create(model_driver=FakeModelDriver())

        async def answer():
            return 42

        result = host._run_user_comm_coro(answer())
        assert result == 42

    @pytest.mark.asyncio
    async def test_runs_coro_inside_event_loop(self):
        host = AgentHost.create(model_driver=FakeModelDriver())

        async def answer():
            return "nested"

        # _run_user_comm_coro uses ThreadPoolExecutor when called from inside an event loop
        result = host._run_user_comm_coro(answer())
        assert result == "nested"
