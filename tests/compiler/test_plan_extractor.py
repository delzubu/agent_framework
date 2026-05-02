"""Tests for plan_extractor — extracting PlanCompilation from audit events."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_workflow_compiler.log_reader import read_events, planning_run_ids
from agent_workflow_compiler.models import AuditEvent
from agent_workflow_compiler.plan_extractor import extract_plan

_LOG_PATH = Path(__file__).parent.parent.parent.parent / "agent-adventure" / "logs" / "agent-host-20260502-071519.jsonl"
_FIXTURE_AVAILABLE = _LOG_PATH.exists()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_events(
    *,
    run_id: str = "test-run",
    agent_name: str = "planner",
    invocation_params: dict | None = None,
    prompt: str = "test",
    plans: list[dict],  # list of plan_updated event dicts
) -> list[AuditEvent]:
    """Build a minimal list of AuditEvents for a planning run."""
    events = [
        AuditEvent(
            "e-start", "runtime.audit.agent_call_started", "t",
            {"run_id": run_id},
            {"agent_name": agent_name, "parameters": invocation_params or {}},
        ),
        AuditEvent(
            "e-started", "runtime.agent_started", "t",
            {"run_id": run_id},
            {"prompt": prompt, "parameters": invocation_params or {}},
        ),
    ]
    for i, plan_update in enumerate(plans):
        events.append(
            AuditEvent(
                f"e-plan-{i}", "runtime.audit.named_event", "t",
                {"run_id": run_id},
                {"event": {"type": "plan_updated", **plan_update}},
            )
        )
    return events


def _tool_step(step_id: str, tool_name: str, params: dict | None = None, deps: list | None = None) -> dict:
    return {
        "id": step_id, "kind": "call_tool", "tool_name": tool_name,
        "subagent_id": None, "skill_name": None,
        "parameters": params or {},
        "depends_on": deps or [],
    }


def _subagent_step(step_id: str, subagent_id: str, params: dict | None = None, deps: list | None = None) -> dict:
    return {
        "id": step_id, "kind": "call_subagent", "tool_name": None,
        "subagent_id": subagent_id, "skill_name": None,
        "parameters": params or {},
        "depends_on": deps or [],
    }


# ---------------------------------------------------------------------------
# Simple single-plan extraction
# ---------------------------------------------------------------------------

def test_extract_single_plan_no_replan():
    events = _make_events(
        run_id="run-1",
        agent_name="my_planner",
        plans=[{
            "is_initial": True, "plan_revision": 1,
            "added_step_ids": ["fetch", "parse"],
            "plan": [
                _tool_step("fetch", "web_fetch"),
                _subagent_step("parse", "parser", deps=["fetch"]),
            ],
        }],
    )
    compilation = extract_plan(events, "run-1")
    assert compilation.source_run_id == "run-1"
    assert compilation.source_agent_id == "my_planner"
    assert len(compilation.final_steps) == 2
    assert compilation.final_steps[0].step_id == "fetch"
    assert compilation.final_steps[1].step_id == "parse"
    assert compilation.replan_checkpoints == []


def test_extract_step_order_respects_deps():
    """Steps are ordered by dependency graph (topological), not insertion order."""
    events = _make_events(
        plans=[{
            "is_initial": True, "plan_revision": 1,
            "added_step_ids": ["step_c", "step_a", "step_b"],
            "plan": [
                # Insert in reverse dependency order to test topo sort
                _tool_step("step_c", "tool_c", deps=["step_a", "step_b"]),
                _tool_step("step_a", "tool_a"),
                _tool_step("step_b", "tool_b", deps=["step_a"]),
            ],
        }],
    )
    compilation = extract_plan(events, "test-run")
    step_ids = [s.step_id for s in compilation.final_steps]
    # step_a must come before step_b, step_b before step_c
    assert step_ids.index("step_a") < step_ids.index("step_b")
    assert step_ids.index("step_b") < step_ids.index("step_c")


def test_extract_next_step_pointers():
    """CompiledStep.next_step points to the following step in execution order."""
    events = _make_events(
        plans=[{
            "is_initial": True, "plan_revision": 1,
            "added_step_ids": ["a", "b", "c"],
            "plan": [
                _tool_step("a", "ta"),
                _tool_step("b", "tb", deps=["a"]),
                _tool_step("c", "tc", deps=["b"]),
            ],
        }],
    )
    compilation = extract_plan(events, "test-run")
    steps = {s.step_id: s for s in compilation.final_steps}
    assert steps["a"].next_step == "b"
    assert steps["b"].next_step == "c"
    assert steps["c"].next_step is None


def test_extract_invocation_parameters():
    events = _make_events(
        invocation_params={"player_id": "p-42", "topic": "dragons"},
        plans=[{
            "is_initial": True, "plan_revision": 1,
            "added_step_ids": ["s"],
            "plan": [_tool_step("s", "t")],
        }],
    )
    compilation = extract_plan(events, "test-run")
    assert compilation.invocation_parameters == {"player_id": "p-42", "topic": "dragons"}


# ---------------------------------------------------------------------------
# Replan checkpoint extraction
# ---------------------------------------------------------------------------

def test_extract_replan_checkpoint():
    """A non-initial plan_updated creates a ReplanCheckpoint."""
    events = _make_events(
        plans=[
            {
                "is_initial": True, "plan_revision": 1,
                "added_step_ids": ["fetch", "parse"],
                "plan": [
                    _tool_step("fetch", "fetcher"),
                    _subagent_step("parse", "parser", deps=["fetch"]),
                ],
            },
            {
                "is_initial": False, "plan_revision": 2,
                "added_step_ids": ["route"],
                "plan": [
                    _tool_step("fetch", "fetcher"),
                    _subagent_step("parse", "parser", deps=["fetch"]),
                    _subagent_step("route", "router", deps=["parse"]),
                ],
            },
        ],
    )
    compilation = extract_plan(events, "test-run")
    assert len(compilation.replan_checkpoints) == 1
    cp = compilation.replan_checkpoints[0]
    assert cp.plan_revision == 2
    assert "route" in cp.added_step_ids
    # The trigger step is the last completed step before the replan
    assert cp.after_step_id in {"fetch", "parse"}


def test_extract_final_plan_is_last_revision():
    """final_steps always reflects the LAST plan_updated, not the initial one."""
    events = _make_events(
        plans=[
            {
                "is_initial": True, "plan_revision": 1,
                "added_step_ids": ["step_a"],
                "plan": [_tool_step("step_a", "tool_a")],
            },
            {
                "is_initial": False, "plan_revision": 2,
                "added_step_ids": ["step_b"],
                "plan": [
                    _tool_step("step_a", "tool_a"),
                    _tool_step("step_b", "tool_b", deps=["step_a"]),
                ],
            },
        ],
    )
    compilation = extract_plan(events, "test-run")
    step_ids = [s.step_id for s in compilation.final_steps]
    assert "step_a" in step_ids
    assert "step_b" in step_ids


def test_extract_raises_on_no_plan_events():
    """ValueError is raised when no plan_updated events exist for the run."""
    events = [
        AuditEvent("e", "runtime.agent_started", "t", {"run_id": "run-x"}, {"prompt": "hi", "parameters": {}})
    ]
    with pytest.raises(ValueError, match="No plan_updated events"):
        extract_plan(events, "run-x")


# ---------------------------------------------------------------------------
# Fixture log integration
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _FIXTURE_AVAILABLE, reason="Fixture log not available.")
def test_fixture_log_extracts_5_steps():
    events = read_events(_LOG_PATH)
    run_ids = planning_run_ids(events)
    compilation = extract_plan(events, run_ids[0])
    assert len(compilation.final_steps) == 5


@pytest.mark.skipif(not _FIXTURE_AVAILABLE, reason="Fixture log not available.")
def test_fixture_log_has_replan_checkpoint():
    events = read_events(_LOG_PATH)
    run_ids = planning_run_ids(events)
    compilation = extract_plan(events, run_ids[0])
    assert len(compilation.replan_checkpoints) == 1
    cp = compilation.replan_checkpoints[0]
    assert cp.after_step_id == "step_parse_intent"
    assert "step_route_intent_look_around" in cp.added_step_ids


@pytest.mark.skipif(not _FIXTURE_AVAILABLE, reason="Fixture log not available.")
def test_fixture_log_step_kinds():
    events = read_events(_LOG_PATH)
    run_ids = planning_run_ids(events)
    compilation = extract_plan(events, run_ids[0])
    kinds = {s.kind for s in compilation.final_steps}
    assert "call_tool" in kinds
    assert "call_subagent" in kinds
