from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_framework import AgentHost, CompositeRuntimeTracer, ModelContext, ModelResponse, TraceEvent
from agent_framework.prompt_reference import (
    PromptResolveContext,
    parse_prompt_reference,
)


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


def _write_env(tmp_path: Path) -> Path:
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
                "ROOT_AGENT=root",
            ]
        ),
        encoding="utf-8",
    )
    return env


def _write_referenced_agent(agents_dir: Path, *, duplicate_workflow_heading: bool = False) -> None:
    extra = """
# Notes

## Workflow

Notes workflow text.
""" if duplicate_workflow_heading else ""
    (agents_dir / "axis_audience.md").write_text(
        f"""---
id: axis_audience
role: audience reviewer
parameters: {{}}
tools: []
subagents: []
---
# Agent

## Role

Evaluate audience alignment.

## Rubric

Score clarity and relevance.

## Memory Access

Call memory_get for the deck.

## Workflow

Standalone workflow note.
{extra}
---
Standalone user prompt.
""",
        encoding="utf-8",
    )


def test_agent_prompt_reference_projects_workflow_sections_in_source_order(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_referenced_agent(agents_dir)
    (agents_dir / "axis_audience.json").write_text(
        json.dumps(
            {
                "workflow-compose": {
                    "pre-load-skills": ["presentation-strategist"],
                    "include-sections": ["/Agent/Role", "/Agent/Rubric", "/Agent/Memory Access"],
                    "exclude-sections": ["/Agent/Memory Access"],
                    "append": "Use <deck_json> already present in chat history.",
                }
            }
        ),
        encoding="utf-8",
    )
    host = AgentHost.from_env(env, model_driver=_SeqModelDriver([]))

    result = host.resolve_prompt_reference(
        parse_prompt_reference("agent:axis_audience#workflow"),
        PromptResolveContext(host=host, agent_id="root", base_dir=agents_dir),
    )

    assert result.content == (
        "## Role\n\nEvaluate audience alignment.\n\n"
        "## Rubric\n\nScore clarity and relevance.\n\n"
        "Use <deck_json> already present in chat history."
    )
    assert result.metadata["source_agent"] == "axis_audience"
    assert result.metadata["projection"] == "workflow"
    assert result.metadata["included_sections"] == ["/Agent/Role", "/Agent/Rubric"]
    assert result.metadata["excluded_sections"] == ["/Agent/Memory Access"]
    assert result.metadata["preloaded_skills"] == ["presentation-strategist"]
    assert result.preloads[0].scheme == "skill"
    assert result.preloads[0].target == "presentation-strategist"


def test_agent_prompt_reference_ambiguous_shorthand_fails_with_candidates(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_referenced_agent(agents_dir, duplicate_workflow_heading=True)
    (agents_dir / "axis_audience.json").write_text(
        json.dumps({"workflow-compose": {"include-sections": ["Workflow"]}}),
        encoding="utf-8",
    )
    host = AgentHost.from_env(env, model_driver=_SeqModelDriver([]))

    with pytest.raises(ValueError, match="Ambiguous workflow-compose section title 'Workflow'"):
        host.resolve_prompt_reference(
            parse_prompt_reference("@agent:axis_audience#workflow"),
            PromptResolveContext(host=host, agent_id="root", base_dir=agents_dir),
        )


def test_workflow_model_step_can_use_prompt_ref_and_audits_projection_metadata(tmp_path: Path) -> None:
    env = _write_env(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_referenced_agent(agents_dir)
    (agents_dir / "axis_audience.json").write_text(
        json.dumps(
            {
                "workflow-compose": {
                    "include-sections": ["/Agent/Role", "/Agent/Rubric"],
                    "append": "Use <deck_json> from chat history.",
                }
            }
        ),
        encoding="utf-8",
    )
    (agents_dir / "root.md").write_text(
        """---
id: root
role: workflow
parameters:
  instruction:
    description: instruction
    required: true
tools: []
subagents: []
---
Workflow system.
---
<instruction>{{instruction}}</instruction>
""",
        encoding="utf-8",
    )
    (agents_dir / "root.json").write_text(
        json.dumps({"agent_type": "workflow", "workflow": {"path": "root_workflow.py"}}),
        encoding="utf-8",
    )
    (agents_dir / "root_workflow.py").write_text(
        """
from agent_framework import ProgrammaticWorkflow, PromptRef, WorkflowModelStep, WorkflowReturnStep

def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="audience",
        steps={
            "audience": WorkflowModelStep(
                step_id="audience",
                phase_id="audience",
                prompt_fragment=PromptRef("agent:axis_audience#workflow"),
                prompt_history_policy="ephemeral",
                allowed_decision_kinds=frozenset({"final_message"}),
                next_step="done",
            ),
            "done": WorkflowReturnStep(step_id="done", value="done"),
        },
    )
""",
        encoding="utf-8",
    )
    driver = _SeqModelDriver([{"kind": "final_message", "message": "ok"}])
    recorder = _TraceRecorder()
    host = AgentHost.from_env(env, model_driver=driver)
    host.runtime_tracer = CompositeRuntimeTracer(subscribers=[recorder])

    host.run_root(initial_instruction="<instruction><deck_json>{}</deck_json></instruction>")

    context_text = "\n".join(message["content"] for message in driver.contexts[0].messages)
    assert "agent:axis_audience#workflow" not in context_text
    assert "Evaluate audience alignment." in context_text
    assert "Score clarity and relevance." in context_text
    assert "Use <deck_json> from chat history." in context_text
    assert "Call memory_get" not in context_text

    audit_events = [
        event.payload["event"]
        for event in recorder.events
        if event.kind == "runtime.audit.named_event"
        and event.payload.get("event", {}).get("type") == "prompt_reference_resolved"
    ]
    assert audit_events
    assert audit_events[0]["target"] == "axis_audience"
    assert audit_events[0]["projection"] == "workflow"
    assert audit_events[0]["included_sections"] == ["/Agent/Role", "/Agent/Rubric"]
    assert audit_events[0]["workflow_step_id"] == "audience"
    assert audit_events[0]["phase_id"] == "audience"
    assert audit_events[0]["token_estimate"] > 0
