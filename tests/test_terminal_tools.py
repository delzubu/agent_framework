"""Tests for terminal_tools support in the markdown agent loop."""

import json


from agent_framework.agent import Agent
from agent_framework.host import AgentHost
from agent_framework.model import ModelResponse


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeModelDriver:
    """Sync driver that pops from a list of payloads."""

    def __init__(self, payloads):
        self._payloads = list(payloads)

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass

    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=str(payload))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tool(tools_dir, tool_id, invoke_body="return 'ok'", *, params=("q",)):
    """Write a minimal tool .md + .py pair with the build_tool factory pattern."""
    tools_dir.mkdir(parents=True, exist_ok=True)
    params_yaml = "\n".join(
        f"  {p}:\n    description: param\n    required: true" for p in params
    )
    (tools_dir / f"{tool_id}.md").write_text(
        f"---\nid: {tool_id}\ndescription: {tool_id}\nparameters:\n{params_yaml}\n---\ndoes {tool_id}\n",
        encoding="utf-8",
    )
    (tools_dir / f"{tool_id}.py").write_text(
        f"""from agent_framework.tool import Tool

class {tool_id.capitalize()}Tool(Tool):
    def invoke(self, arguments, host):
        {invoke_body}

def build_tool(definition):
    t = {tool_id.capitalize()}Tool(definition=definition)
    return t
""",
        encoding="utf-8",
    )


def _make_env(tmp_path):
    """Write a .env that points to tmp_path subdirs. Returns (env_path, tools_dir)."""
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(exist_ok=True)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join([
            "OPENAI_API_KEY=test-key",
            "DEFAULT_PROVIDER=openai",
            "DEFAULT_MODEL=gpt-4o-mini",
            f"AGENT_DIRECTORY={tmp_path / 'agents'}",
            f"TOOLS_DIRECTORY={tools_dir}",
            f"WORLD_DIRECTORY={tmp_path / 'world'}",
            "ROOT_AGENT=root",
        ]),
        encoding="utf-8",
    )
    return env_path, tools_dir


def _make_agent(terminal_tools=(), allowed_tools=()) -> Agent:
    return Agent(
        agent_id="tester",
        role="tester",
        description="",
        system_prompt="sys",
        user_prompt_template="Hello",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
        terminal_tools=terminal_tools,
        allowed_tools=allowed_tools,
    )


def _make_host(env_path, payloads):
    return AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver(payloads),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )


# ---------------------------------------------------------------------------
# Tests — Agent.terminal_tools field
# ---------------------------------------------------------------------------


class TestTerminalToolsField:
    def test_default_is_empty_tuple(self):
        agent = _make_agent()
        assert agent.terminal_tools == ()

    def test_set_terminal_tools(self):
        agent = _make_agent(terminal_tools=("ask_user", "clarify"))
        assert "ask_user" in agent.terminal_tools
        assert "clarify" in agent.terminal_tools

    def test_from_markdown_parses_terminal_tools(self, tmp_path):
        md = tmp_path / "agent.md"
        md.write_text(
            "---\nid: test\nrole: tester\ndescription: x\n"
            "tools:\n  - ask_user\nterminal_tools:\n  - ask_user\n---\nsys\n---\nHello\n",
            encoding="utf-8",
        )
        agent = Agent.from_markdown(md, default_provider="openai", default_model=("gpt-4o-mini",))
        assert "ask_user" in agent.terminal_tools

    def test_from_markdown_no_terminal_tools_key(self, tmp_path):
        md = tmp_path / "agent.md"
        md.write_text(
            "---\nid: test\nrole: tester\ndescription: x\n---\nsys\n---\nHello\n",
            encoding="utf-8",
        )
        agent = Agent.from_markdown(md, default_provider="openai", default_model=("gpt-4o-mini",))
        assert agent.terminal_tools == ()


# ---------------------------------------------------------------------------
# Tests — handle_tool_call() terminal tool behavior
# ---------------------------------------------------------------------------


class TestTerminalToolHandleToolCall:
    def test_terminal_tool_returns_completed_result(self, tmp_path):
        """When the model calls a terminal tool, the agent exits immediately."""
        env_path, tools_dir = _make_env(tmp_path)
        _write_tool(tools_dir, "ask_user", params=("question",))

        agent = _make_agent(
            terminal_tools=("ask_user",),
            allowed_tools=("ask_user",),
        )
        host = _make_host(env_path, [
            {"kind": "call_tool", "tool_name": "ask_user", "parameters": {"question": "How many?"}},
            {"kind": "final_message", "message": "never reached"},
        ])
        result = agent.run(host=host, parameters={}, caller_id="host")
        assert result.status == "completed"
        data = json.loads(result.message)
        assert data["question"] == "How many?"

    def test_terminal_tool_does_not_execute_implementation(self, tmp_path):
        """Terminal tool call must NOT invoke the tool's invoke() function."""
        env_path, tools_dir = _make_env(tmp_path)
        # Tool whose invoke() raises if called — should never be reached
        _write_tool(
            tools_dir, "ask_user",
            invoke_body="raise AssertionError('invoke() must not be called')",
            params=("question",),
        )

        agent = _make_agent(
            terminal_tools=("ask_user",),
            allowed_tools=("ask_user",),
        )
        host = _make_host(env_path, [
            {"kind": "call_tool", "tool_name": "ask_user", "parameters": {"question": "x"}},
        ])
        result = agent.run(host=host, parameters={}, caller_id="host")
        assert result.status == "completed"

    def test_non_terminal_tool_still_executes(self, tmp_path):
        """A tool not listed in terminal_tools must be executed normally."""
        env_path, tools_dir = _make_env(tmp_path)
        _write_tool(tools_dir, "lookup", invoke_body="return f'result for {arguments[\"q\"]}'", params=("q",))

        agent = _make_agent(
            terminal_tools=("ask_user",),  # "lookup" is NOT a terminal tool
            allowed_tools=("lookup",),
        )
        host = _make_host(env_path, [
            {"kind": "call_tool", "tool_name": "lookup", "parameters": {"q": "python"}},
            {"kind": "final_message", "message": "found it"},
        ])
        result = agent.run(host=host, parameters={}, caller_id="host")
        assert result.status == "completed"
        assert result.message == "found it"

    def test_terminal_tool_message_contains_arguments_json(self, tmp_path):
        """The result message must be the JSON-serialized tool arguments."""
        env_path, tools_dir = _make_env(tmp_path)
        _write_tool(tools_dir, "escalate", params=("reason", "severity"))

        agent = _make_agent(
            terminal_tools=("escalate",),
            allowed_tools=("escalate",),
        )
        host = _make_host(env_path, [
            {"kind": "call_tool", "tool_name": "escalate", "parameters": {"reason": "blocked", "severity": "high"}},
        ])
        result = agent.run(host=host, parameters={}, caller_id="host")
        assert result.status == "completed"
        data = json.loads(result.message)
        assert data["reason"] == "blocked"
        assert data["severity"] == "high"

    def test_multiple_terminal_tools_configured(self, tmp_path):
        """Agent with multiple terminal tools exits on whichever is called first."""
        env_path, tools_dir = _make_env(tmp_path)
        _write_tool(tools_dir, "stop", params=("code",))
        _write_tool(tools_dir, "escalate", params=("reason",))

        agent = _make_agent(
            terminal_tools=("stop", "escalate"),
            allowed_tools=("stop", "escalate"),
        )
        host = _make_host(env_path, [
            {"kind": "call_tool", "tool_name": "stop", "parameters": {"code": "0"}},
        ])
        result = agent.run(host=host, parameters={}, caller_id="host")
        assert result.status == "completed"
        data = json.loads(result.message)
        assert data["code"] == "0"
