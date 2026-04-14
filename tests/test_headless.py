"""Tests for AgentHost.complete(), complete_async(), and run_tool_loop()."""

import pytest

from agent_framework.config import HostConfig
from agent_framework.conversation import InMemoryConversationStore
from agent_framework.host import AgentHost, run_tool_loop
from agent_framework.model import ModelContext, ModelResponse


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDriver:
    """Sync driver that returns a configurable response."""

    def __init__(self, response: ModelResponse):
        self._response = response

    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        return self._response

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass


class FakeAsyncDriver:
    """Async driver that returns a configurable response."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._call_count = 0

    async def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass


def _make_host(driver, store=None) -> AgentHost:
    config = HostConfig()
    return AgentHost.create(model_driver=driver, config=config, conversation_store=store)


# ---------------------------------------------------------------------------
# AgentHost.complete()
# ---------------------------------------------------------------------------


class TestComplete:
    def test_returns_model_response(self):
        expected = ModelResponse(payload={}, raw_text="hello")
        host = _make_host(FakeDriver(expected))
        result = host.complete(messages=[{"role": "user", "content": "hi"}])
        assert result.raw_text == "hello"

    def test_with_async_driver(self):
        expected = ModelResponse(payload={}, raw_text="from_async")
        host = _make_host(FakeAsyncDriver([expected]))
        result = host.complete(messages=[{"role": "user", "content": "hi"}])
        assert result.raw_text == "from_async"

    def test_with_conversation_store(self):
        store = InMemoryConversationStore()
        cid = store.create([{"role": "system", "content": "Be helpful"}])
        response = ModelResponse(payload={}, raw_text="answer")
        host = _make_host(FakeDriver(response), store=store)

        result = host.complete(
            messages=[{"role": "user", "content": "q"}],
            conversation_id=cid,
        )
        # Prior messages + new messages + assistant response should all be stored
        msgs = store.get_messages(cid)
        assert any(m["role"] == "assistant" for m in msgs)


# ---------------------------------------------------------------------------
# AgentHost.complete_async()
# ---------------------------------------------------------------------------


class TestCompleteAsync:
    @pytest.mark.asyncio
    async def test_returns_model_response(self):
        expected = ModelResponse(payload={}, raw_text="async_response")
        host = _make_host(FakeAsyncDriver([expected]))
        result = await host.complete_async(messages=[{"role": "user", "content": "hi"}])
        assert result.raw_text == "async_response"

    @pytest.mark.asyncio
    async def test_with_sync_driver(self):
        expected = ModelResponse(payload={}, raw_text="sync_via_async")
        host = _make_host(FakeDriver(expected))
        result = await host.complete_async(messages=[{"role": "user", "content": "x"}])
        assert result.raw_text == "sync_via_async"

    @pytest.mark.asyncio
    async def test_with_conversation_store(self):
        store = InMemoryConversationStore()
        cid = store.create([])
        response = ModelResponse(payload={}, raw_text="r")
        host = _make_host(FakeAsyncDriver([response]), store=store)
        await host.complete_async(
            messages=[{"role": "user", "content": "hi"}],
            conversation_id=cid,
        )
        msgs = store.get_messages(cid)
        assert len(msgs) >= 2  # user + assistant


# ---------------------------------------------------------------------------
# AgentHost.create()
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_without_env(self):
        driver = FakeDriver(ModelResponse(payload={}, raw_text=""))
        host = AgentHost.create(model_driver=driver)
        assert host.model_driver is driver

    def test_create_with_custom_config(self):
        from pathlib import Path
        config = HostConfig(default_model=("gpt-test",))
        driver = FakeDriver(ModelResponse(payload={}, raw_text=""))
        host = AgentHost.create(model_driver=driver, config=config)
        assert host.config.default_model == ("gpt-test",)


# ---------------------------------------------------------------------------
# run_tool_loop()
# ---------------------------------------------------------------------------


class TestRunToolLoop:
    @pytest.mark.asyncio
    async def test_stops_on_no_tool_calls(self):
        response = ModelResponse(payload={}, raw_text="done", finish_reason="stop")
        host = _make_host(FakeAsyncDriver([response]))
        result = await run_tool_loop(
            host,
            messages=[{"role": "user", "content": "go"}],
            tools=[],
        )
        assert result.raw_text == "done"

    @pytest.mark.asyncio
    async def test_terminal_tool_exits_immediately(self):
        tool_call_response = ModelResponse(
            payload={},
            raw_text="",
            tool_calls=(
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "ask_clarification", "arguments": '{"q":"what?"}'},
                },
            ),
            finish_reason="tool_calls",
        )
        host = _make_host(FakeAsyncDriver([tool_call_response]))
        result = await run_tool_loop(
            host,
            messages=[{"role": "user", "content": "go"}],
            tools=[],
            terminal_tools=["ask_clarification"],
        )
        assert result.finish_reason == "terminal_tool"
        assert '"q"' in result.raw_text

    @pytest.mark.asyncio
    async def test_executes_non_terminal_tool(self):
        tool_call = {
            "id": "c1",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q":"python"}'},
        }
        tool_response = ModelResponse(
            payload={}, raw_text="", tool_calls=(tool_call,), finish_reason="tool_calls"
        )
        final_response = ModelResponse(payload={}, raw_text="found it", finish_reason="stop")

        executed = []

        async def executor(name, args):
            executed.append((name, args))
            return "search result"

        host = _make_host(FakeAsyncDriver([tool_response, final_response]))
        result = await run_tool_loop(
            host,
            messages=[{"role": "user", "content": "search"}],
            tools=[],
            tool_executor=executor,
        )
        assert result.raw_text == "found it"
        assert executed == [("search", {"q": "python"})]

    @pytest.mark.asyncio
    async def test_invalid_tool_arguments_raise(self):
        tool_call = {
            "id": "c1",
            "type": "function",
            "function": {"name": "search", "arguments": "not-json"},
        }
        tool_response = ModelResponse(
            payload={}, raw_text="", tool_calls=(tool_call,), finish_reason="tool_calls"
        )
        host = _make_host(FakeAsyncDriver([tool_response]))
        with pytest.raises(ValueError, match="valid JSON"):
            await run_tool_loop(
                host,
                messages=[{"role": "user", "content": "search"}],
                tools=[],
                tool_executor=lambda n, a: "x",
            )

    @pytest.mark.asyncio
    async def test_raises_on_max_iterations(self):
        always_tool = ModelResponse(
            payload={},
            raw_text="",
            tool_calls=(
                {"id": "c1", "type": "function", "function": {"name": "loop", "arguments": "{}"}},
            ),
            finish_reason="tool_calls",
        )
        # Return always-tool-calling response forever
        host = _make_host(FakeAsyncDriver([always_tool] * 20))
        with pytest.raises(RuntimeError, match="max_iterations"):
            await run_tool_loop(
                host,
                messages=[{"role": "user", "content": "start"}],
                tools=[],
                max_iterations=3,
            )
