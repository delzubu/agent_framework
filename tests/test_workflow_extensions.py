"""Tests for WorkflowCallToolStep, WorkflowMutation, and on_step_end callback."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_framework.agent import (
    Agent,
    AgentBehavior,
    AgentHookDecision,
    AgentResult,
)
from agent_framework.agents.workflow import (
    ProgrammaticWorkflow,
    ProgrammaticWorkflowState,
    WorkflowAbort,
    WorkflowAbortedError,
    WorkflowCallSubagentStep,
    WorkflowCallToolStep,
    WorkflowContinue,
    WorkflowGoto,
    WorkflowReplace,
    WorkflowReturnStep,
    coerce_workflow_result,
)
from agent_framework.host import AgentHost
from agent_framework.model import ModelResponse, ModelContext
from agent_framework.tool import Tool, ToolDefinition, ToolParameter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SeqModelDriver:
    """Return canned decisions one by one."""

    def __init__(self, payloads):
        self._payloads = list(payloads)

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass

    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=str(payload))


def _make_host(
    tmp_path: Path,
    *,
    agent_id: str = "root",
    decisions=(),
    subagents: list[str] | None = None,
    allowed_tools: list[str] | None = None,
) -> AgentHost:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)

    subagent_block = ""
    if subagents:
        subagent_block = "subagents:\n" + "".join(f"  - {s}\n" for s in subagents)
    tool_block = ""
    if allowed_tools:
        tool_block = "allowed_tools:\n" + "".join(f"  - {t}\n" for t in allowed_tools)

    (agents_dir / f"{agent_id}.md").write_text(
        f"---\nid: {agent_id}\nrole: tester\n{subagent_block}{tool_block}---\nSys.\n---\nrun\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "OPENAI_API_KEY=test-key",
            "DEFAULT_PROVIDER=openai",
            "DEFAULT_MODEL=gpt-4o-mini",
            f"AGENT_DIRECTORY={agents_dir}",
            f"ROOT_AGENT={agent_id}",
        ]),
        encoding="utf-8",
    )
    driver = _SeqModelDriver(list(decisions))
    host = AgentHost.from_env(env, model_driver=driver, input_reader=lambda _: "", output_writer=lambda _: None)
    return host


def _register_tool(host: AgentHost, tool_id: str, fn) -> None:
    """Register a simple functional tool on the host."""
    defn = ToolDefinition(tool_id=tool_id, description=f"{tool_id} tool", parameters=())

    class _FnTool(Tool):
        def invoke(self, parameters: dict, host: Any) -> str:
            return str(fn(parameters))

    host.tool_registry.register(_FnTool(definition=defn))


# ---------------------------------------------------------------------------
# WorkflowCallToolStep
# ---------------------------------------------------------------------------

def test_workflow_call_tool_step_executes_and_stores_result(tmp_path):
    """A WorkflowCallToolStep executes the named tool and stores its result."""
    host = _make_host(tmp_path, allowed_tools=["echo"])
    _register_tool(host, "echo", lambda params: f"echo:{params.get('msg', '')}")

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="say_hi",
                steps={
                    "say_hi": WorkflowCallToolStep(
                        step_id="say_hi",
                        tool_name="echo",
                        arguments={"msg": "hello"},
                        next_step="done",
                    ),
                    "done": WorkflowReturnStep(
                        step_id="done",
                        value=lambda s: s.require_step_result("say_hi"),
                    ),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "echo:hello"


def test_workflow_call_tool_step_resolver_lambda(tmp_path):
    """WorkflowCallToolStep arguments may use resolver lambdas over state."""
    host = _make_host(tmp_path, allowed_tools=["multiply"])
    _register_tool(host, "multiply", lambda p: int(p.get("a", 0)) * int(p.get("b", 0)))

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="calc",
                steps={
                    "calc": WorkflowCallToolStep(
                        step_id="calc",
                        tool_name="multiply",
                        arguments=lambda s: {"a": s.initial_parameters.get("x", 3), "b": 7},
                        next_step="done",
                    ),
                    "done": WorkflowReturnStep(
                        step_id="done",
                        value=lambda s: s.require_step_result("calc"),
                    ),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id,
                    workflow=workflow,
                    initial_parameters={"x": 6},
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "42"


def test_workflow_call_tool_step_result_available_to_next_step(tmp_path):
    """Result of WorkflowCallToolStep is available via step_results in later steps."""
    host = _make_host(tmp_path, allowed_tools=["greet"])
    _register_tool(host, "greet", lambda p: f"Hello, {p.get('name', 'stranger')}!")

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="step1",
                steps={
                    "step1": WorkflowCallToolStep(
                        step_id="step1",
                        tool_name="greet",
                        arguments={"name": "world"},
                        next_step="step2",
                    ),
                    "step2": WorkflowCallToolStep(
                        step_id="step2",
                        tool_name="greet",
                        # Use the result of step1 as input
                        arguments=lambda s: {"name": s.require_step_result("step1")},
                        next_step="done",
                    ),
                    "done": WorkflowReturnStep(
                        step_id="done",
                        value=lambda s: s.require_step_result("step2"),
                    ),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "Hello, Hello, world!!"


# ---------------------------------------------------------------------------
# on_step_end — WorkflowContinue / None (pass-through)
# ---------------------------------------------------------------------------

def test_on_step_end_continue_falls_through(tmp_path):
    """WorkflowContinue from on_step_end uses the step's own next_step."""
    host = _make_host(tmp_path, allowed_tools=["ping"])
    _register_tool(host, "ping", lambda _: "pong")

    callbacks: list[str] = []

    def on_step_end(step_id, result, state, workflow):
        callbacks.append(step_id)
        return WorkflowContinue()

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="ping",
                on_step_end=on_step_end,
                steps={
                    "ping": WorkflowCallToolStep(
                        step_id="ping", tool_name="ping", arguments={}, next_step="done",
                    ),
                    "done": WorkflowReturnStep(step_id="done", value="finished"),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "finished"
    assert callbacks == ["ping"]


def test_on_step_end_none_falls_through(tmp_path):
    """Returning None from on_step_end is identical to WorkflowContinue."""
    host = _make_host(tmp_path, allowed_tools=["ping"])
    _register_tool(host, "ping", lambda _: "pong")

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="ping",
                on_step_end=lambda step_id, result, state, wf: None,
                steps={
                    "ping": WorkflowCallToolStep(
                        step_id="ping", tool_name="ping", arguments={}, next_step="done",
                    ),
                    "done": WorkflowReturnStep(step_id="done", value="ok"),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "ok"


# ---------------------------------------------------------------------------
# on_step_end — WorkflowGoto
# ---------------------------------------------------------------------------

def test_on_step_end_goto_reroutes(tmp_path):
    """WorkflowGoto from on_step_end skips the step's own next_step."""
    host = _make_host(tmp_path, allowed_tools=["counter"])

    call_count = {"n": 0}

    def counter_fn(params):
        call_count["n"] += 1
        return str(call_count["n"])

    _register_tool(host, "counter", counter_fn)

    visited: list[str] = []

    def on_step_end(step_id, result, state, workflow):
        visited.append(step_id)
        if step_id == "count_step" and int(result) < 2:
            return WorkflowGoto("count_step")
        return WorkflowContinue()

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="count_step",
                on_step_end=on_step_end,
                steps={
                    "count_step": WorkflowCallToolStep(
                        step_id="count_step", tool_name="counter",
                        arguments={}, next_step="done",
                    ),
                    "done": WorkflowReturnStep(step_id="done", value="done"),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "done"
    assert call_count["n"] == 2
    assert visited.count("count_step") == 2


# ---------------------------------------------------------------------------
# on_step_end — WorkflowReplace
# ---------------------------------------------------------------------------

def test_on_step_end_replace_swaps_workflow(tmp_path):
    """WorkflowReplace swaps the active workflow; execution continues from new entry_step."""
    host = _make_host(tmp_path, allowed_tools=["tag"])
    _register_tool(host, "tag", lambda p: p.get("label", "?"))

    replacement_workflow = ProgrammaticWorkflow(
        entry_step="phase2",
        steps={
            "phase2": WorkflowReturnStep(step_id="phase2", value="phase2-result"),
        },
    )

    def on_step_end(step_id, result, state, workflow):
        if step_id == "phase1":
            return WorkflowReplace(replacement_workflow)
        return WorkflowContinue()

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="phase1",
                on_step_end=on_step_end,
                steps={
                    "phase1": WorkflowCallToolStep(
                        step_id="phase1", tool_name="tag",
                        arguments={"label": "phase1"}, next_step="never_reached",
                    ),
                    "never_reached": WorkflowReturnStep(step_id="never_reached", value="wrong"),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "phase2-result"


def test_on_step_end_replace_preserves_state(tmp_path):
    """After WorkflowReplace, state.step_results from the first workflow are still accessible."""
    host = _make_host(tmp_path, allowed_tools=["tag"])
    _register_tool(host, "tag", lambda p: p.get("label", "?"))

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            # Build the replacement at test-run time, capturing a reference to check state
            captured_state = {}

            def on_step_end(step_id, result, state, workflow):
                if step_id == "phase1":
                    replacement = ProgrammaticWorkflow(
                        entry_step="phase2",
                        steps={
                            "phase2": WorkflowReturnStep(
                                step_id="phase2",
                                # Access the phase1 result from shared state
                                value=lambda s: f"saw:{s.step_results.get('phase1', '?')}",
                            ),
                        },
                    )
                    return WorkflowReplace(replacement)
                return WorkflowContinue()

            workflow = ProgrammaticWorkflow(
                entry_step="phase1",
                on_step_end=on_step_end,
                steps={
                    "phase1": WorkflowCallToolStep(
                        step_id="phase1", tool_name="tag",
                        arguments={"label": "p1data"}, next_step="never",
                    ),
                    "never": WorkflowReturnStep(step_id="never", value="wrong"),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "saw:p1data"


# ---------------------------------------------------------------------------
# on_step_end — WorkflowAbort
# ---------------------------------------------------------------------------

def test_on_step_end_abort_raises_workflow_aborted_error(tmp_path):
    """WorkflowAbort from on_step_end raises WorkflowAbortedError."""
    host = _make_host(tmp_path, allowed_tools=["noop"])
    _register_tool(host, "noop", lambda _: "data")

    def on_step_end(step_id, result, state, workflow):
        return WorkflowAbort("intentional abort")

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="step1",
                on_step_end=on_step_end,
                steps={
                    "step1": WorkflowCallToolStep(
                        step_id="step1", tool_name="noop", arguments={}, next_step="done",
                    ),
                    "done": WorkflowReturnStep(step_id="done", value="unreachable"),
                },
            )
            with pytest.raises(WorkflowAbortedError, match="intentional abort"):
                agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            return AgentHookDecision(final_result=AgentResult(status="error", message="aborted"))

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.status == "error"
    assert result.message == "aborted"


# ---------------------------------------------------------------------------
# coerce_workflow_result — WorkflowAbortedError
# ---------------------------------------------------------------------------

def test_coerce_workflow_result_handles_aborted_error():
    err = WorkflowAbortedError("oops")
    result = coerce_workflow_result(err)
    assert result.status == "error"
    assert "oops" in result.message


# ---------------------------------------------------------------------------
# on_step_end fires for WorkflowCallSubagentStep too
# ---------------------------------------------------------------------------

def test_on_step_end_fires_after_subagent_step(tmp_path):
    """on_step_end is invoked after WorkflowCallSubagentStep completes."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "root.md").write_text(
        "---\nid: root\nrole: tester\nsubagents:\n  - child\n---\nSys.\n---\nrun\n",
        encoding="utf-8",
    )
    (agents_dir / "child.md").write_text(
        "---\nid: child\nrole: helper\n---\nSys.\n---\nrun\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "OPENAI_API_KEY=test-key",
            "DEFAULT_PROVIDER=openai",
            "DEFAULT_MODEL=gpt-4o-mini",
            f"AGENT_DIRECTORY={agents_dir}",
            "ROOT_AGENT=root",
        ]),
        encoding="utf-8",
    )
    driver = _SeqModelDriver([{"kind": "final_message", "message": "child-ok"}])
    host = AgentHost.from_env(env, model_driver=driver, input_reader=lambda _: "", output_writer=lambda _: None)

    hook_calls: list[str] = []

    def on_step_end(step_id, result, state, workflow):
        hook_calls.append(step_id)
        return WorkflowContinue()

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="delegate",
                on_step_end=on_step_end,
                steps={
                    "delegate": WorkflowCallSubagentStep(
                        step_id="delegate",
                        subagent_id="child",
                        parameters={"instruction": "hi"},
                        next_step="done",
                    ),
                    "done": WorkflowReturnStep(
                        step_id="done",
                        value=lambda s: s.require_step_result("delegate").message,
                    ),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "child-ok"
    assert "delegate" in hook_calls


# ---------------------------------------------------------------------------
# Workflow without on_step_end still works normally
# ---------------------------------------------------------------------------

def test_workflow_without_on_step_end_works(tmp_path):
    """ProgrammaticWorkflow without on_step_end executes normally."""
    host = _make_host(tmp_path, allowed_tools=["echo"])
    _register_tool(host, "echo", lambda p: p.get("msg", ""))

    class WorkflowBehavior(AgentBehavior):
        def attach(self, agent): pass

        def before_run(self, agent, host, *, run, caller_id):
            workflow = ProgrammaticWorkflow(
                entry_step="say",
                steps={
                    "say": WorkflowCallToolStep(
                        step_id="say", tool_name="echo",
                        arguments={"msg": "no-hook"}, next_step="done",
                    ),
                    "done": WorkflowReturnStep(
                        step_id="done", value=lambda s: s.require_step_result("say"),
                    ),
                },
            )
            return AgentHookDecision(
                final_result=agent.execute_programmatic_workflow(
                    host=host, run=run, caller_id=caller_id, workflow=workflow,
                )
            )

    root = host.get_agent("root")
    root.behaviors = (WorkflowBehavior(),)
    result = host.run_root(initial_instruction="go")
    assert result.message == "no-hook"
