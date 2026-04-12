"""Tests for UserCommunication protocol, NullUserCommunication, and ConsoleUserCommunication."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from agent_framework.user_communication import (
    NullUserCommunication,
    PermissionDecision,
    PermissionRequest,
    UserCommunication,
)


# ---------------------------------------------------------------------------
# PermissionDecision / PermissionRequest data types
# ---------------------------------------------------------------------------


class TestPermissionDataTypes:
    def test_permission_decision_defaults(self):
        decision = PermissionDecision(allowed=True)
        assert decision.allowed is True
        assert decision.remember_for_session is False

    def test_permission_decision_with_remember(self):
        decision = PermissionDecision(allowed=False, remember_for_session=True)
        assert decision.allowed is False
        assert decision.remember_for_session is True

    def test_permission_request_required_fields(self):
        req = PermissionRequest(
            tool_name="Bash",
            action="execute",
            resource="echo hello",
            summary="Run echo",
        )
        assert req.tool_name == "Bash"
        assert req.action == "execute"
        assert req.resource == "echo hello"
        assert req.summary == "Run echo"
        assert req.details == {}


# ---------------------------------------------------------------------------
# NullUserCommunication
# ---------------------------------------------------------------------------


class TestNullUserCommunication:
    @pytest.mark.asyncio
    async def test_send_message_is_noop(self):
        null = NullUserCommunication()
        await null.send_message("hello")  # Should not raise

    @pytest.mark.asyncio
    async def test_ask_question_returns_empty_string(self):
        null = NullUserCommunication()
        result = await null.ask_question("What?")
        assert result == ""

    @pytest.mark.asyncio
    async def test_ask_confirmation_returns_default_false(self):
        null = NullUserCommunication()
        assert await null.ask_confirmation("Sure?") is False

    @pytest.mark.asyncio
    async def test_ask_confirmation_returns_default_true(self):
        null = NullUserCommunication()
        assert await null.ask_confirmation("Sure?", default=True) is True

    @pytest.mark.asyncio
    async def test_request_permission_always_allows(self):
        null = NullUserCommunication()
        req = PermissionRequest(tool_name="Write", action="write", resource="/tmp/f", summary="Write file")
        decision = await null.request_permission(req)
        assert decision.allowed is True
        assert decision.remember_for_session is False

    @pytest.mark.asyncio
    async def test_read_user_input_returns_none(self):
        null = NullUserCommunication()
        result = await null.read_user_input("Enter: ")
        assert result is None

    @pytest.mark.asyncio
    async def test_stream_text_consumes_without_error(self):
        null = NullUserCommunication()

        async def _chunks():
            yield "hello "
            yield "world"

        await null.stream_text(_chunks())  # Should not raise

    def test_null_satisfies_protocol(self):
        null = NullUserCommunication()
        assert isinstance(null, UserCommunication)


# ---------------------------------------------------------------------------
# ConsoleUserCommunication
# ---------------------------------------------------------------------------


class TestConsoleUserCommunication:
    @pytest.mark.asyncio
    async def test_send_message_prints_to_stdout(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        printed = []
        with patch("builtins.print", side_effect=lambda *a, **kw: printed.append(a)):
            await console.send_message("Hello!")
        assert any("Hello!" in str(a) for a in printed)

    @pytest.mark.asyncio
    async def test_read_user_input_returns_typed_text(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        with patch("builtins.input", return_value="my answer"):
            result = await console.read_user_input("Enter: ")
        assert result == "my answer"

    @pytest.mark.asyncio
    async def test_read_user_input_returns_none_on_eof(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        with patch("builtins.input", side_effect=EOFError):
            result = await console.read_user_input("Enter: ")
        assert result is None

    @pytest.mark.asyncio
    async def test_ask_confirmation_yes_returns_true(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        with patch("builtins.input", return_value="y"):
            result = await console.ask_confirmation("Continue?")
        assert result is True

    @pytest.mark.asyncio
    async def test_ask_confirmation_no_returns_false(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        with patch("builtins.input", return_value="n"):
            result = await console.ask_confirmation("Continue?")
        assert result is False

    @pytest.mark.asyncio
    async def test_request_permission_yes_allows(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        req = PermissionRequest(tool_name="Bash", action="execute", resource="rm -rf /", summary="Danger")
        with patch("builtins.input", return_value="y"):
            decision = await console.request_permission(req)
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_request_permission_no_denies(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        req = PermissionRequest(tool_name="Bash", action="execute", resource="bad cmd", summary="Bad")
        with patch("builtins.input", return_value="n"):
            decision = await console.request_permission(req)
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_request_permission_allow_all_sets_session(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        req = PermissionRequest(tool_name="Write", action="write", resource="/tmp/x", summary="Write")
        with patch("builtins.input", return_value="a"):
            decision = await console.request_permission(req)
        assert decision.allowed is True
        assert decision.remember_for_session is True

    @pytest.mark.asyncio
    async def test_request_permission_deny_all_sets_session(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        req = PermissionRequest(tool_name="Write", action="write", resource="/tmp/x", summary="Write")
        with patch("builtins.input", return_value="d"):
            decision = await console.request_permission(req)
        assert decision.allowed is False
        assert decision.remember_for_session is True

    @pytest.mark.asyncio
    async def test_session_cache_skips_second_prompt(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        req = PermissionRequest(tool_name="Write", action="write", resource="/tmp/x", summary="Write")

        prompt_calls = []
        original_input = __builtins__["input"] if isinstance(__builtins__, dict) else input

        with patch("builtins.input", side_effect=lambda *a: (prompt_calls.append(a), "a")[1]):
            first = await console.request_permission(req)
        # Now session remembers "allow" for (Write, write)
        assert first.allowed is True

        req2 = PermissionRequest(tool_name="Write", action="write", resource="/tmp/y", summary="Write 2")
        # Should not prompt again
        second = await console.request_permission(req2)
        assert second.allowed is True

    def test_console_satisfies_protocol(self):
        from agent_framework.console_communication import ConsoleUserCommunication
        console = ConsoleUserCommunication()
        assert isinstance(console, UserCommunication)
