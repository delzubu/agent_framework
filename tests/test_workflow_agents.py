from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_framework.agent import WorkflowAgent
from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse
from agent_framework.tool import Tool, ToolDefinition
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
    assert "<workflow_state_summary>" not in second_context
    assert driver.contexts[1].user_prompt == ""
    assert "<augmentations>" not in driver.contexts[1].user_prompt
    assistant_messages = [
        message["content"]
        for message in driver.contexts[1].messages
        if message["role"] == "assistant"
    ]
    assert assistant_messages == ["phase-one"]


def test_workflow_model_phase_can_project_response_history(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    _write_agent(agent_path)
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root_workflow.py").write_text(
        """
from agent_framework import (
    AgentResult,
    ProgrammaticWorkflow,
    WorkflowHistoryProjection,
    WorkflowModelStep,
    WorkflowReturnStep,
)

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="phase_one",
        steps={
            "phase_one": WorkflowModelStep(
                step_id="phase_one",
                phase_id="audience_review",
                prompt_fragment="Review the audience.",
                allowed_decision_kinds=frozenset({"final_message"}),
                history_projection=WorkflowHistoryProjection(
                    final_message="response",
                    wrapper_tag="audience_review",
                ),
                next_step="phase_two",
            ),
            "phase_two": WorkflowModelStep(
                step_id="phase_two",
                phase_id="summary",
                prompt_fragment="Use <audience_review> from history.",
                allowed_decision_kinds=frozenset({"final_message"}),
                next_step="done",
            ),
            "done": WorkflowReturnStep(
                step_id="done",
                value=lambda s: AgentResult(status="completed", message="done"),
            ),
        },
    )
""",
        encoding="utf-8",
    )
    driver = _SeqModelDriver(
        [
            {
                "kind": "final_message",
                "message": "Audience review complete.",
                "response": {"rating": 6, "findings": []},
            },
            {"kind": "final_message", "message": "summary"},
        ]
    )
    host = AgentHost.from_env(env, model_driver=driver)

    host.run_root(initial_instruction="<instruction>go</instruction>")

    second_context = "\n".join(message["content"] for message in driver.contexts[1].messages)
    assert '<audience_review>{"findings":[],"rating":6}</audience_review>' in second_context
    assert "Audience review complete." not in [
        message["content"]
        for message in driver.contexts[1].messages
        if message["role"] == "assistant"
    ]


def _register_tool(host: AgentHost, tool_id: str, result: str) -> None:
    defn = ToolDefinition(tool_id=tool_id, description=f"{tool_id} tool", parameters=())

    class _StaticTool(Tool):
        def invoke(self, arguments: dict[str, Any], host: AgentHost) -> str:
            return result

    host.tool_registry.register(_StaticTool(definition=defn))


def _write_child_workflow_agent(
    agents_dir: Path,
    agent_id: str,
    message: str,
    response: dict[str, Any] | None = None,
) -> None:
    (agents_dir / f"{agent_id}.md").write_text(
        f"""---
id: {agent_id}
role: child
parameters: {{}}
tools: []
subagents: []
---
Child system.
---
Child prompt.
""",
        encoding="utf-8",
    )
    (agents_dir / f"{agent_id}.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": f"{agent_id}_workflow.py"}}),
        encoding="utf-8",
    )
    response_expr = repr(response) if response is not None else "None"
    (agents_dir / f"{agent_id}_workflow.py").write_text(
        f"""
from agent_framework import AgentResult, ProgrammaticWorkflow, WorkflowReturnStep

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="done",
        steps={{
            "done": WorkflowReturnStep(
                step_id="done",
                value=AgentResult(status="completed", message={message!r}, response={response_expr}),
            )
        }},
    )
""",
        encoding="utf-8",
    )


def _assert_provider_prefix_is_append_only(contexts: list[ModelContext]) -> None:
    assert len(contexts) >= 2
    for previous, current in zip(contexts, contexts[1:]):
        assert list(current.messages[: len(previous.messages)]) == list(previous.messages)
        assert current.user_prompt == ""
        assert "<augmentations>" not in "\n".join(message["content"] for message in current.messages)


def _write_two_phase_action_workflow(tmp_path: Path, first_decision_kind: str, extra: str = "") -> None:
    (tmp_path / "agents" / "root_workflow.py").write_text(
        f"""
from agent_framework import ProgrammaticWorkflow, WorkflowModelStep, WorkflowReturnStep

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="action_phase",
        steps={{
            "action_phase": WorkflowModelStep(
                step_id="action_phase",
                phase_id="action_phase",
                prompt_fragment="Perform the action, then finish.",
                allowed_decision_kinds=frozenset({{{first_decision_kind!r}, "final_message"}}),
                next_step="followup",
                max_turns=4,
            ),
            "followup": WorkflowModelStep(
                step_id="followup",
                phase_id="followup",
                prompt_fragment="Use the action result from history.",
                allowed_decision_kinds=frozenset({{"final_message"}}),
                next_step="done",
            ),
            "done": WorkflowReturnStep(step_id="done", value="done"),
        }},
    )
{extra}
""",
        encoding="utf-8",
    )


class _StaticUserCommunication:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def read_user_input(self, prompt: str = "", *, prompt_id: str | None = None, metadata: Any = None) -> str:
        return self.answer


def test_workflow_chat_history_keeps_prefix_stable_across_transform_steps(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    _write_agent(agent_path)
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root_workflow.py").write_text(
        """
from agent_framework import AgentResult, ProgrammaticWorkflow, WorkflowModelStep, WorkflowReturnStep, WorkflowTransformStep

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="intake",
        steps={
            "intake": WorkflowModelStep(
                step_id="intake",
                phase_id="intake",
                prompt_fragment="Run intake.",
                allowed_decision_kinds=frozenset({"final_message"}),
                next_step="validate_intake",
            ),
            "validate_intake": WorkflowTransformStep(
                step_id="validate_intake",
                transform=lambda s: {"valid": True, "source": s.require_step_result("intake").message},
                next_step="audience",
            ),
            "audience": WorkflowModelStep(
                step_id="audience",
                phase_id="audience",
                prompt_fragment="Use prior history.",
                allowed_decision_kinds=frozenset({"final_message"}),
                next_step="done",
            ),
            "done": WorkflowReturnStep(
                step_id="done",
                value=lambda s: AgentResult(status="completed", message="done"),
            ),
        },
    )
""",
        encoding="utf-8",
    )
    driver = _SeqModelDriver(
        [
            {"kind": "final_message", "message": "intake-ok"},
            {"kind": "final_message", "message": "audience-ok"},
        ]
    )
    host = AgentHost.from_env(env, model_driver=driver)

    host.run_root(initial_instruction="<instruction>go</instruction>")

    assert len(driver.contexts) == 2
    first_messages = list(driver.contexts[0].messages)
    second_messages = list(driver.contexts[1].messages)
    assert second_messages[: len(first_messages)] == first_messages
    assert driver.contexts[1].user_prompt == ""
    assert "<augmentations>" not in "\n".join(message["content"] for message in second_messages)
    assert any(
        message["role"] == "user" and "<workflow_transform_result step_id=\"validate_intake\">" in message["content"]
        for message in second_messages
    )


def test_workflow_chat_history_keeps_prefix_stable_across_tool_calls(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    agent_path.write_text(
        """---
id: root
role: tester
parameters:
  instruction:
    description: instruction
    required: true
tools: [echo]
subagents: []
---
System prompt.
---
<instruction>{{instruction}}</instruction>
""",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    _write_two_phase_action_workflow(tmp_path, "call_tool")
    driver = _SeqModelDriver(
        [
            {"kind": "call_tool", "tool_name": "echo", "parameters": {}},
            {"kind": "final_message", "message": "tool phase done"},
            {"kind": "final_message", "message": "followup done"},
        ]
    )
    host = AgentHost.from_env(env, model_driver=driver)
    _register_tool(host, "echo", "tool-output")

    host.run_root(initial_instruction="<instruction>go</instruction>")

    _assert_provider_prefix_is_append_only(driver.contexts)
    assert any("Tool result echo: tool-output" in message["content"] for message in driver.contexts[-1].messages)


def test_workflow_chat_history_keeps_prefix_stable_across_subagent_calls(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agents_dir = tmp_path / "agents"
    agent_path = agents_dir / "root.md"
    agent_path.write_text(
        """---
id: root
role: tester
parameters:
  instruction:
    description: instruction
    required: true
tools: []
subagents: [child]
---
System prompt.
---
<instruction>{{instruction}}</instruction>
""",
        encoding="utf-8",
    )
    (agents_dir / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    _write_child_workflow_agent(agents_dir, "child", "child-output", {"child": "ok"})
    _write_two_phase_action_workflow(tmp_path, "call_subagent")
    driver = _SeqModelDriver(
        [
            {"kind": "call_subagent", "subagent_id": "child", "parameters": {}},
            {"kind": "final_message", "message": "subagent phase done"},
            {"kind": "final_message", "message": "followup done"},
        ]
    )
    host = AgentHost.from_env(env, model_driver=driver)

    host.run_root(initial_instruction="<instruction>go</instruction>")

    _assert_provider_prefix_is_append_only(driver.contexts)
    assert any('"child":"ok"' in message["content"] for message in driver.contexts[-1].messages)


def test_workflow_chat_history_keeps_prefix_stable_across_subagent_batches(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agents_dir = tmp_path / "agents"
    agent_path = agents_dir / "root.md"
    agent_path.write_text(
        """---
id: root
role: tester
parameters:
  instruction:
    description: instruction
    required: true
tools: []
subagents: [child_a, child_b]
---
System prompt.
---
<instruction>{{instruction}}</instruction>
""",
        encoding="utf-8",
    )
    (agents_dir / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    _write_child_workflow_agent(agents_dir, "child_a", "a-output")
    _write_child_workflow_agent(agents_dir, "child_b", "b-output")
    _write_two_phase_action_workflow(tmp_path, "call_subagents")
    driver = _SeqModelDriver(
        [
            {
                "kind": "call_subagents",
                "mode": "parallel",
                "calls": [
                    {"subagent_id": "child_a", "parameters": {}, "output_key": "a"},
                    {"subagent_id": "child_b", "parameters": {}, "output_key": "b"},
                ],
            },
            {"kind": "final_message", "message": "batch phase done"},
            {"kind": "final_message", "message": "followup done"},
        ]
    )
    host = AgentHost.from_env(env, model_driver=driver)

    host.run_root(initial_instruction="<instruction>go</instruction>")

    _assert_provider_prefix_is_append_only(driver.contexts)
    assert any("a-output" in message["content"] for message in driver.contexts[-1].messages)
    assert any("b-output" in message["content"] for message in driver.contexts[-1].messages)


def test_workflow_chat_history_keeps_prefix_stable_across_callbacks(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    _write_agent(agent_path)
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    _write_two_phase_action_workflow(tmp_path, "request_user_input")
    driver = _SeqModelDriver(
        [
            {"kind": "request_user_input", "intent": "information_request", "message": "Need input"},
            {"kind": "final_message", "message": "callback phase done"},
            {"kind": "final_message", "message": "followup done"},
        ]
    )
    host = AgentHost.from_env(env, model_driver=driver, user_comm=_StaticUserCommunication("callback-answer"))

    host.run_root(initial_instruction="<instruction>go</instruction>")

    _assert_provider_prefix_is_append_only(driver.contexts)
    assert any(message["content"] == "callback-answer" for message in driver.contexts[-1].messages)


def test_workflow_chat_history_keeps_prefix_stable_across_skill_invocations(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: my-skill
description: Test skill
---
Skill body.
""",
        encoding="utf-8",
    )
    env = _write_env(tmp_path)
    with env.open("a", encoding="utf-8") as handle:
        handle.write(f"\nSKILLS_DIRECTORY={skills_dir}\n")
    agent_path = tmp_path / "agents" / "root.md"
    agent_path.write_text(
        """---
id: root
role: tester
parameters:
  instruction:
    description: instruction
    required: true
tools: []
subagents: []
skills: [my-skill]
---
System prompt.
---
<instruction>{{instruction}}</instruction>
""",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    _write_two_phase_action_workflow(tmp_path, "invoke_skill")
    driver = _SeqModelDriver(
        [
            {"kind": "invoke_skill", "skill_name": "my-skill"},
            {"kind": "final_message", "message": "skill phase done"},
            {"kind": "final_message", "message": "followup done"},
        ]
    )
    host = AgentHost.from_env(env, model_driver=driver)

    host.run_root(initial_instruction="<instruction>go</instruction>")

    _assert_provider_prefix_is_append_only(driver.contexts)
    assert any("Skill body." in message["content"] for message in driver.contexts[-1].messages)


def test_workflow_agent_uses_partitioned_markdown_prompts_by_phase_id(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agent_path = tmp_path / "agents" / "root.md"
    agent_path.write_text(
        """---
id: root
role: tester
parameters:
  instruction:
    description: instruction
    required: true
tools: []
subagents: []
---
<workflow_system>
Shared workflow system prompt.
</workflow_system>
<phase_one>
Run only phase one.
</phase_one>
---
<instruction>{{instruction}}</instruction>
""",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    (tmp_path / "agents" / "root_workflow.py").write_text(
        """
from agent_framework import ProgrammaticWorkflow, WorkflowModelStep, WorkflowReturnStep

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="phase_one",
        steps={
            "phase_one": WorkflowModelStep(
                step_id="phase_one",
                phase_id="phase_one",
                allowed_decision_kinds=frozenset({"final_message"}),
                next_step="done",
            ),
            "done": WorkflowReturnStep(step_id="done", value="done"),
        },
    )
""",
        encoding="utf-8",
    )
    driver = _SeqModelDriver([{"kind": "final_message", "message": "phase done"}])
    host = AgentHost.from_env(env, model_driver=driver)

    host.run_root(initial_instruction="<instruction>go</instruction>")

    context = driver.contexts[0]
    assert "Shared workflow system prompt." in context.system_prompt
    assert "Run only phase one." not in context.system_prompt
    assert any("Run only phase one." in message["content"] for message in context.messages)


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
