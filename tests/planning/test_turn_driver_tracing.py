"""Tests that PlanningTurnDriver emits plan_updated named events."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse
from agent_framework.tracing import CompositeRuntimeTracer, TraceEvent


class _TraceRecorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def consume(self, event: TraceEvent) -> None:
        self.events.append(event)


class _ScriptedDriver:
    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)

    def set_trace_callbacks(self, *, on_request=None, on_response=None) -> None:
        pass

    def decide(self, *, agent_id, provider_name, model_names, temperature, context: ModelContext):
        if not self._payloads:
            raise RuntimeError("Scripted driver exhausted")
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=str(payload))


def _make_host(tmp_path: Path, payloads: list[dict], *, agent_id: str = "planner") -> tuple[AgentHost, _TraceRecorder]:
    agent_path = tmp_path / f"{agent_id}.md"
    agent_path.write_text(
        f"---\nid: {agent_id}\nrole: planner\nplanning:\n  enabled: true\n---\nYou are a planner.\n---\nExecute.\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "OPENAI_API_KEY=test-key",
            "DEFAULT_PROVIDER=openai",
            "DEFAULT_MODEL=gpt-4o-mini",
            f"AGENT_DIRECTORY={tmp_path}",
            f"ROOT_AGENT={agent_id}",
        ]),
        encoding="utf-8",
    )
    host = AgentHost.from_env(env)
    recorder = _TraceRecorder()
    host.runtime_tracer = CompositeRuntimeTracer(subscribers=[recorder])
    host.model_driver = _ScriptedDriver(payloads)

    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    class EchoTool(Tool):
        def invoke(self, parameters: dict, host: Any) -> str:
            return str(parameters.get("msg", ""))

    host.tool_registry.register(
        EchoTool(definition=ToolDefinition(
            tool_id="echo",
            description="Echo msg.",
            parameters=(ToolParameter("msg", "msg", required=False),),
        ))
    )
    return host, recorder


_STEP_A = {"id": "step_a", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "a"}}
_STEP_B = {"id": "step_b", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "b"}, "depends_on": ["step_a"]}
_STEP_C = {"id": "step_c", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "c"}}


def _named_plan_events(recorder: _TraceRecorder, run_id: str) -> list[dict]:
    return [
        e.payload["event"]
        for e in recorder.events
        if e.kind == "runtime.audit.named_event"
        and e.context.run_id == run_id
        and isinstance(e.payload.get("event"), dict)
        and e.payload["event"].get("type") == "plan_updated"
    ]


def test_plan_updated_event_emitted_on_initial_submit(tmp_path: Path) -> None:
    host, recorder = _make_host(tmp_path, payloads=[
        {"kind": "submit_plan", "plan": [_STEP_A, _STEP_B]},
        {"kind": "final_message", "message": "done"},
    ])
    result = host.run_root(initial_instruction="go")
    assert result.status == "completed"

    parent_run_id = next(
        run_id for run_id, reg in host._run_registry.items()
        if reg.agent_id == "planner"
    )
    plan_events = _named_plan_events(recorder, parent_run_id)
    assert len(plan_events) == 1
    ev = plan_events[0]
    assert ev["is_initial"] is True
    assert ev["plan_revision"] == 1
    assert ev["step_count"] == 2
    assert set(ev["added_step_ids"]) == {"step_a", "step_b"}
    assert ev["dropped_step_ids"] == []
    assert len(ev["plan"]) == 2
    step_ids = {s["id"] for s in ev["plan"]}
    assert step_ids == {"step_a", "step_b"}


def test_plan_updated_event_emitted_on_replan(tmp_path: Path) -> None:
    host, recorder = _make_host(tmp_path, payloads=[
        # Initial plan: step_a, step_b — both complete.
        {"kind": "submit_plan", "plan": [_STEP_A, _STEP_B]},
        # Reflect: replan with only the new pending step (step_c).
        # Completed steps (step_a, step_b) are not re-listed — new semantics.
        {"kind": "submit_plan", "plan": [_STEP_C]},
        {"kind": "final_message", "message": "replanned done"},
    ])
    result = host.run_root(initial_instruction="go")
    assert result.status == "completed"

    parent_run_id = next(
        run_id for run_id, reg in host._run_registry.items()
        if reg.agent_id == "planner"
    )
    plan_events = _named_plan_events(recorder, parent_run_id)
    assert len(plan_events) == 2

    initial = plan_events[0]
    assert initial["is_initial"] is True
    assert initial["plan_revision"] == 1

    replan = plan_events[1]
    assert replan["is_initial"] is False
    assert replan["plan_revision"] == 2
    # Completed steps are preserved — nothing is dropped.
    assert replan["dropped_step_ids"] == []
    # step_c is the newly added pending step.
    assert "step_c" in replan["added_step_ids"]
    # Merged plan = completed prefix [step_a, step_b] + new pending [step_c].
    assert len(replan["plan"]) == 3
