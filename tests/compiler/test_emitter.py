"""Tests for the emitters: behavior.py, json_def.py, markdown.py."""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from agent_workflow_compiler.log_reader import read_events, planning_run_ids
from agent_workflow_compiler.models import CompiledStep, PlanCompilation, ReplanCheckpoint
from agent_workflow_compiler.plan_extractor import extract_plan
from agent_workflow_compiler.emitter.behavior import emit_behavior
from agent_workflow_compiler.emitter.json_def import emit_json
from agent_workflow_compiler.emitter.markdown import emit_markdown

_LOG_PATH = Path(__file__).parent.parent.parent.parent / "agent-adventure" / "logs" / "agent-host-20260502-071519.jsonl"
_FIXTURE_AVAILABLE = _LOG_PATH.exists()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_compilation(*, with_token: bool = False, with_replan: bool = False) -> PlanCompilation:
    params = {"msg": "{{fetch}}"} if with_token else {"msg": "hello"}
    steps = [
        CompiledStep(
            step_id="fetch",
            kind="call_tool",
            tool_name="fetcher",
            subagent_id=None,
            skill_name=None,
            parameters={"url": "https://example.com"},
            depends_on=[],
            next_step="process" if with_replan else None,
        ),
    ]
    if with_replan:
        steps.append(
            CompiledStep(
                step_id="process",
                kind="call_subagent",
                tool_name=None,
                subagent_id="processor",
                skill_name=None,
                parameters=params,
                depends_on=["fetch"],
                next_step=None,
            )
        )
    checkpoints = (
        [ReplanCheckpoint(
            after_step_id="fetch",
            trigger_message="Data required",
            plan_revision=2,
            added_step_ids=["process"],
        )]
        if with_replan
        else []
    )
    return PlanCompilation(
        source_run_id="run-1",
        source_agent_id="my_agent",
        invocation_parameters={"player_id": "p1"},
        invocation_prompt="do stuff",
        final_steps=steps,
        replan_checkpoints=checkpoints,
    )


# ---------------------------------------------------------------------------
# emit_behavior
# ---------------------------------------------------------------------------

def test_emit_behavior_creates_valid_python(tmp_path):
    """The generated file must be parseable as valid Python."""
    out = tmp_path / "agent_behavior.py"
    emit_behavior(_simple_compilation(), agent_id="my_agent", output_path=out)
    source = out.read_text(encoding="utf-8")
    ast.parse(source)  # raises SyntaxError if invalid


def test_emit_behavior_imports_agent_behavior(tmp_path):
    out = tmp_path / "agent_behavior.py"
    emit_behavior(_simple_compilation(), agent_id="my_agent", output_path=out)
    source = out.read_text(encoding="utf-8")
    assert "AgentBehavior" in source
    assert "AgentHookDecision" in source


def test_emit_behavior_class_name_pascal_case(tmp_path):
    out = tmp_path / "b.py"
    emit_behavior(_simple_compilation(), agent_id="player_controller", output_path=out)
    source = out.read_text(encoding="utf-8")
    assert "class PlayerControllerBehavior(AgentBehavior):" in source


def test_emit_behavior_has_on_step_end_with_replan(tmp_path):
    out = tmp_path / "b.py"
    emit_behavior(_simple_compilation(with_replan=True), agent_id="my_agent", output_path=out)
    source = out.read_text(encoding="utf-8")
    assert "def _on_step_end" in source
    assert "'fetch'" in source  # replan after_step_id


def test_emit_behavior_token_becomes_lambda(tmp_path):
    out = tmp_path / "b.py"
    emit_behavior(
        _simple_compilation(with_token=True, with_replan=True),
        agent_id="my_agent",
        output_path=out,
    )
    source = out.read_text(encoding="utf-8")
    assert "lambda s" in source
    assert "_ref" in source


def test_emit_behavior_no_replan_still_valid(tmp_path):
    out = tmp_path / "b.py"
    emit_behavior(_simple_compilation(with_replan=False), agent_id="my_agent", output_path=out)
    source = out.read_text(encoding="utf-8")
    ast.parse(source)
    assert "def _on_step_end" in source


def test_emit_behavior_has_wf_final_return_step(tmp_path):
    """The generated file must include a _wf_final WorkflowReturnStep."""
    out = tmp_path / "b.py"
    emit_behavior(_simple_compilation(), agent_id="my_agent", output_path=out)
    source = out.read_text(encoding="utf-8")
    assert "_wf_final" in source
    assert "WorkflowReturnStep" in source


def test_emit_behavior_importable(tmp_path):
    """The generated file imports cleanly and the class subclasses AgentBehavior."""
    out = tmp_path / "test_agent_behavior.py"
    emit_behavior(_simple_compilation(), agent_id="test_agent", output_path=out)

    spec = importlib.util.spec_from_file_location("_test_agent_behavior", out)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, "TestAgentBehavior")
    from agent_framework.agents.agent_behavior import AgentBehavior
    assert issubclass(mod.TestAgentBehavior, AgentBehavior)


# ---------------------------------------------------------------------------
# emit_json
# ---------------------------------------------------------------------------

def test_emit_json_produces_valid_json(tmp_path):
    out = tmp_path / "agent.workflow.json"
    emit_json(_simple_compilation(), agent_id="my_agent", output_path=out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["agent_id"] == "my_agent"
    assert "steps" in data
    assert "entry_step" in data


def test_emit_json_includes_all_steps(tmp_path):
    out = tmp_path / "agent.workflow.json"
    compilation = _simple_compilation(with_replan=True)
    emit_json(compilation, agent_id="my_agent", output_path=out)
    data = json.loads(out.read_text(encoding="utf-8"))
    step_ids = [s["step_id"] for s in data["steps"]]
    assert "fetch" in step_ids
    assert "process" in step_ids


def test_emit_json_replan_checkpoints(tmp_path):
    out = tmp_path / "agent.workflow.json"
    compilation = _simple_compilation(with_replan=True)
    emit_json(compilation, agent_id="my_agent", output_path=out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["replan_checkpoints"]) == 1
    assert data["replan_checkpoints"][0]["after_step_id"] == "fetch"


# ---------------------------------------------------------------------------
# emit_markdown
# ---------------------------------------------------------------------------

def test_emit_markdown_produces_md_file(tmp_path):
    out = tmp_path / "agent.md"
    emit_markdown(_simple_compilation(), agent_id="my_agent", output_path=out)
    source = out.read_text(encoding="utf-8")
    assert "---" in source
    assert "my_agent" in source


def test_emit_markdown_includes_workflow_steps(tmp_path):
    out = tmp_path / "agent.md"
    emit_markdown(_simple_compilation(with_replan=True), agent_id="my_agent", output_path=out)
    source = out.read_text(encoding="utf-8")
    assert "fetch" in source
    assert "process" in source


def test_emit_markdown_adapts_source_frontmatter(tmp_path):
    source_md = tmp_path / "original.md"
    source_md.write_text(
        "---\nid: original\nrole: planner\nallowed_tools:\n  - my_tool\n---\nBody.\n",
        encoding="utf-8",
    )
    out = tmp_path / "compiled.md"
    emit_markdown(
        _simple_compilation(), agent_id="compiled_agent",
        output_path=out, source_agent_path=source_md,
    )
    source = out.read_text(encoding="utf-8")
    assert "id: compiled_agent" in source
    assert "my_tool" in source  # allowed_tools preserved
    assert "id: original" not in source  # old id replaced


# ---------------------------------------------------------------------------
# Fixture log end-to-end emit
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _FIXTURE_AVAILABLE, reason="Fixture log not available.")
def test_fixture_log_emit_behavior_valid_python(tmp_path):
    events = read_events(_LOG_PATH)
    run_ids = planning_run_ids(events)
    compilation = extract_plan(events, run_ids[0])
    out = tmp_path / "pcc_behavior.py"
    emit_behavior(compilation, agent_id="player_controller_compiled", output_path=out)
    source = out.read_text(encoding="utf-8")
    ast.parse(source)  # must be valid Python
    assert "step_get_state_slice" in source
    assert "step_route_intent_look_around" in source


@pytest.mark.skipif(not _FIXTURE_AVAILABLE, reason="Fixture log not available.")
def test_fixture_log_token_references_emitted(tmp_path):
    events = read_events(_LOG_PATH)
    run_ids = planning_run_ids(events)
    compilation = extract_plan(events, run_ids[0])
    out = tmp_path / "pcc_behavior.py"
    emit_behavior(compilation, agent_id="player_controller_compiled", output_path=out)
    source = out.read_text(encoding="utf-8")
    # {{step_parse_intent.response.declared_intents.0}} should become a _ref() call
    assert "_ref(s, 'step_parse_intent', 'response', 'declared_intents', '0')" in source
    # {{step_get_state_slice}} should become _ref(s, 'step_get_state_slice')
    assert "_ref(s, 'step_get_state_slice')" in source


@pytest.mark.skipif(not _FIXTURE_AVAILABLE, reason="Fixture log not available.")
def test_fixture_log_emit_json_valid(tmp_path):
    events = read_events(_LOG_PATH)
    run_ids = planning_run_ids(events)
    compilation = extract_plan(events, run_ids[0])
    out = tmp_path / "pcc.workflow.json"
    emit_json(compilation, agent_id="player_controller_compiled", output_path=out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["entry_step"] == "step_get_state_slice"
    assert len(data["steps"]) == 5
    assert len(data["replan_checkpoints"]) == 1
