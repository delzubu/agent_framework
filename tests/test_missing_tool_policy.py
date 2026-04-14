"""Tests for missing-tool handling when building model context."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_framework.config import HostConfig, load_host_config
from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse
from agent_framework.tracing import CompositeRuntimeTracer, TraceEvent


class _Recorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def consume(self, event: TraceEvent) -> None:
        self.events.append(event)


class _FakeDriver:
    def __init__(self) -> None:
        self._calls = 0

    def set_trace_callbacks(self, **_: object) -> None:
        pass

    def decide(self, **kwargs: object) -> ModelResponse:
        self._calls += 1
        ctx: ModelContext = kwargs["context"]
        if self._calls == 1:
            return ModelResponse(
                payload={"kind": "final_message", "message": "done"},
                raw_text='{"kind":"final_message","message":"done"}',
            )
        return ModelResponse(payload={"kind": "final_message", "message": "unexpected"}, raw_text="{}")


def _write_minimal_agent(path: Path, *, tools: str = "missing_tool_xyz") -> None:
    path.write_text(
        f"""---
id: root
role: test
tools:
  - {tools}
---
Test agent.
---
<agent_input></agent_input>
""",
        encoding="utf-8",
    )


def test_graceful_missing_tool_omits_definition_and_emits_trace(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=x",
                "AGENT_DIRECTORY=agents",
                "TOOLS_DIRECTORY=tools",
                "WORLD_DIRECTORY=world",
                "ROOT_AGENT=root",
                "MISSING_TOOL_POLICY=graceful",
            ]
        ),
        encoding="utf-8",
    )
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_minimal_agent(agents / "root.md")
    (tmp_path / "tools").mkdir()
    (tmp_path / "world").mkdir()

    recorder = _Recorder()
    tracer = CompositeRuntimeTracer(subscribers=[recorder])
    cfg = load_host_config(env)
    assert cfg.missing_tool_policy == "graceful"
    host = AgentHost.create(
        model_driver=_FakeDriver(),
        config=cfg,
        mcp_enabled=False,
        builtin_tools=False,
    )
    host.runtime_tracer = tracer
    host.agent_registry.discover()
    host.tool_registry.discover()

    result = host.run_agent("root", initial_instruction="hi")
    assert result.status == "completed"
    kinds = [e.kind for e in recorder.events]
    assert "runtime.tool_unavailable" in kinds
    assert any(e.payload.get("tool_name") == "missing_tool_xyz" for e in recorder.events)


def test_strict_missing_tool_raises(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_minimal_agent(agents / "root.md")
    (tmp_path / "tools").mkdir()
    (tmp_path / "world").mkdir()

    cfg = HostConfig(
        agent_directory=agents.resolve(),
        tools_directory=(tmp_path / "tools").resolve(),
        world_directory=(tmp_path / "world").resolve(),
        root_agent_id="root",
        missing_tool_policy="strict",
    )
    host = AgentHost.create(
        model_driver=_FakeDriver(),
        config=cfg,
        mcp_enabled=False,
        builtin_tools=False,
    )
    host.agent_registry.discover()
    host.tool_registry.discover()

    with pytest.raises(KeyError):
        host.run_agent("root", initial_instruction="hi")


def test_tool_execution_failure_emits_trace(tmp_path: Path) -> None:
    """Runtime execute_tool failure should surface in unified tracer."""

    class _BoomDriver:
        def __init__(self) -> None:
            self._turn = 0

        def set_trace_callbacks(self, **_: object) -> None:
            pass

        def decide(self, **kwargs: object) -> ModelResponse:
            self._turn += 1
            if self._turn == 1:
                return ModelResponse(
                    payload={
                        "kind": "call_tool",
                        "tool_name": "boom_tool",
                        "parameters": {},
                    },
                    raw_text="{}",
                )
            return ModelResponse(
                payload={"kind": "final_message", "message": "after tool error"},
                raw_text='{"kind":"final_message","message":"after tool error"}',
            )

    agents = tmp_path / "agents"
    agents.mkdir()
    agents.joinpath("root.md").write_text(
        """---
id: root
role: test
tools:
  - boom_tool
---
---
<agent_input></agent_input>
""",
        encoding="utf-8",
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    tools_dir.joinpath("boom_tool.md").write_text(
        """---
id: boom_tool
description: x
parameters: {}
---
""",
        encoding="utf-8",
    )
    tools_dir.joinpath("boom_tool.py").write_text(
        "def build_tool(definition):\n"
        "    from agent_framework.tool import Tool\n"
        "    class T(Tool):\n"
        "        def invoke(self, parameters, host):\n"
        "            raise RuntimeError('simulated tool crash')\n"
        "    return T(definition)\n",
        encoding="utf-8",
    )
    (tmp_path / "world").mkdir()

    cfg = HostConfig(
        agent_directory=agents.resolve(),
        tools_directory=tools_dir.resolve(),
        world_directory=(tmp_path / "world").resolve(),
        root_agent_id="root",
    )
    recorder = _Recorder()
    host = AgentHost.create(
        model_driver=_BoomDriver(),
        config=cfg,
        mcp_enabled=False,
        builtin_tools=False,
    )
    host.runtime_tracer = CompositeRuntimeTracer(subscribers=[recorder])
    host.agent_registry.discover()
    host.tool_registry.discover()

    host.run_agent("root", initial_instruction="go")
    kinds = [e.kind for e in recorder.events]
    assert "runtime.tool_execution_failed" in kinds
