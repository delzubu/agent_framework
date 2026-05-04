from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_framework.agent import WorkflowAgent
from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse
from agent_framework.tracing import CompositeRuntimeTracer, TraceEvent


class _SeqModelDriver:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.contexts: list[ModelContext] = []

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        self.on_request_trace = on_request
        self.on_response_trace = on_response

    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        self.contexts.append(context)
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=json.dumps(payload))


class _TraceRecorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def consume(self, event: TraceEvent) -> None:
        self.events.append(event)


def _write_env(tmp_path: Path, root_agent: str = "root") -> Path:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=test-key",
                "DEFAULT_PROVIDER=openai",
                "DEFAULT_MODEL=gpt-4o-mini",
                f"AGENT_DIRECTORY={agents_dir}",
                f"ROOT_AGENT={root_agent}",
            ]
        ),
        encoding="utf-8",
    )
    return env


def _write_agent(path: Path, agent_id: str = "root") -> None:
    path.write_text(
        f"""---
id: {agent_id}
role: tester
parameters:
  instruction:
    description: instruction
    required: true
tools: []
subagents: []
---
System prompt.
---
<instruction>{{{{instruction}}}}</instruction>
""",
        encoding="utf-8",
    )


def test_sidecar_agent_type_workflow_loads_workflow_agent(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    _write_agent(agent_path)
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root_workflow.py").write_text(
        """
from agent_framework import ProgrammaticWorkflow, WorkflowReturnStep

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="done",
        steps={"done": WorkflowReturnStep(step_id="done", value="ok")},
    )
""",
        encoding="utf-8",
    )
    host = AgentHost.from_env(env, model_driver=_SeqModelDriver([]))

    agent = host.get_agent("root")

    assert isinstance(agent, WorkflowAgent)
    assert host.run_root(initial_instruction="<instruction>go</instruction>").message == "ok"


def test_workflow_agent_reuses_existing_behavior_sidecar_schema(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    _write_agent(agent_path)
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({
            "agent_type": "workflow",
            "workflow": {"path": "root_workflow.py"},
            "behaviors": ["root_behavior"],
        }),
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root_workflow.py").write_text(
        """
from agent_framework import ProgrammaticWorkflow, WorkflowReturnStep

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="done",
        steps={"done": WorkflowReturnStep(step_id="done", value="workflow")},
    )
""",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root_behavior.py").write_text(
        """
from agent_framework import AgentBehavior, AgentResult

class Behavior(AgentBehavior):
    def attach(self, agent):
        pass

    def after_run(self, agent, host, *, run, caller_id, result):
        return AgentResult(status=result.status, message=result.message + "|behavior", response=result.response)

def build_behavior():
    return Behavior()
""",
        encoding="utf-8",
    )
    host = AgentHost.from_env(env, model_driver=_SeqModelDriver([]))

    assert host.run_root(initial_instruction="<instruction>go</instruction>").message == "workflow|behavior"


def test_workflow_model_phases_share_context_and_finish_only_on_return(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    _write_agent(agent_path)
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root_workflow.py").write_text(
        """
from agent_framework import AgentResult, ProgrammaticWorkflow, WorkflowModelStep, WorkflowReturnStep

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="phase_one",
        steps={
            "phase_one": WorkflowModelStep(
                step_id="phase_one",
                phase_id="phase_one",
                prompt_fragment="Return the first result.",
                allowed_decision_kinds=frozenset({"final_message"}),
                next_step="phase_two",
            ),
            "phase_two": WorkflowModelStep(
                step_id="phase_two",
                phase_id="phase_two",
                prompt_fragment=lambda s: "Use phase one: " + s.require_step_result("phase_one").message,
                allowed_decision_kinds=frozenset({"final_message"}),
                final_response_schema={
                    "type": "object",
                    "required": ["answer"],
                    "properties": {"answer": {"type": "string"}},
                },
                next_step="done",
            ),
            "done": WorkflowReturnStep(
                step_id="done",
                value=lambda s: AgentResult(
                    status="completed",
                    message="workflow done",
                    response=s.require_step_result("phase_two").response,
                ),
            ),
        },
    )
""",
        encoding="utf-8",
    )
    driver = _SeqModelDriver(
        [
            {"kind": "final_message", "message": "phase-one"},
            {"kind": "final_message", "message": "phase-two", "response": {"answer": "ok"}},
        ]
    )
    host = AgentHost.from_env(env, model_driver=driver)

    result = host.run_root(initial_instruction="<instruction>go</instruction>")

    assert result.message == "workflow done"
    assert result.response == {"answer": "ok"}
    assert len(driver.contexts) == 2
    second_context = "\n".join(message["content"] for message in driver.contexts[1].messages)
    assert "phase-one" in second_context
    assert "Use phase one: phase-one" in second_context


def test_workflow_phase_trace_events_and_nested_metadata(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    _write_agent(agent_path)
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root_workflow.py").write_text(
        """
from agent_framework import ProgrammaticWorkflow, WorkflowModelStep, WorkflowReturnStep

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="phase",
        steps={
            "phase": WorkflowModelStep(
                step_id="phase",
                phase_id="phase",
                prompt_fragment="finish",
                allowed_decision_kinds=frozenset({"final_message"}),
                next_step="done",
            ),
            "done": WorkflowReturnStep(step_id="done", value="done"),
        },
    )
""",
        encoding="utf-8",
    )
    recorder = _TraceRecorder()
    host = AgentHost.from_env(
        env,
        model_driver=_SeqModelDriver([{"kind": "final_message", "message": "phase done"}]),
    )
    host.runtime_tracer = CompositeRuntimeTracer(subscribers=[recorder])

    host.run_root(initial_instruction="<instruction>go</instruction>")

    kinds = [event.kind for event in recorder.events]
    assert "workflow.phase_started" in kinds
    assert "workflow.phase_completed" in kinds
    nested_payloads = [event.payload for event in recorder.events if event.kind == "runtime.model_call_started"]
    assert nested_payloads
    assert nested_payloads[0]["workflow_step_id"] == "phase"
    assert nested_payloads[0]["phase_id"] == "phase"


def test_workflow_agent_requires_valid_workflow_metadata(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    _write_agent(agent_path)
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow"}),
        encoding="utf-8",
    )
    host = AgentHost.from_env(env, model_driver=_SeqModelDriver([]))

    with pytest.raises(ValueError, match="requires a 'workflow' metadata object"):
        host.get_agent("root")
