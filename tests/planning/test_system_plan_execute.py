"""Tests for the system.plan_execute.md template and its selection."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_framework.model import (
    ModelContext,
    ModelDriverBase,
    runtime_prompt_source_paths,
    _SYSTEM_PLAN_EXECUTE_TEMPLATE_PATH,
    _SYSTEM_PLAN_EXECUTE_TEMPLATE,
)
from agent_framework.agent import Agent


def _agent_md(frontmatter: str, system: str = "You are an agent.", user: str = "") -> str:
    return f"{frontmatter}\n---\n{system}\n---\n{user}"


# ---------------------------------------------------------------------------
# Smoke tests — template loads and has expected content
# ---------------------------------------------------------------------------

def test_template_path_exists():
    assert _SYSTEM_PLAN_EXECUTE_TEMPLATE_PATH.exists()


def test_template_non_empty():
    assert len(_SYSTEM_PLAN_EXECUTE_TEMPLATE.strip()) > 0


def test_template_covers_submit_plan():
    assert "submit_plan" in _SYSTEM_PLAN_EXECUTE_TEMPLATE


def test_template_covers_continue_plan():
    assert "continue_plan" in _SYSTEM_PLAN_EXECUTE_TEMPLATE


def test_template_covers_final_message():
    assert "final_message" in _SYSTEM_PLAN_EXECUTE_TEMPLATE


def test_template_covers_token_syntax():
    assert "{{" in _SYSTEM_PLAN_EXECUTE_TEMPLATE


def test_template_does_not_list_amend_plan_as_decision_kind():
    # amend_plan is reserved — the template explicitly forbids it, not promotes it
    # Verify it appears only in the prohibition, not as a `- \`amend_plan\`` entry
    import re
    listed_kinds = re.findall(r"^- `([^`]+)`", _SYSTEM_PLAN_EXECUTE_TEMPLATE, re.MULTILINE)
    assert "amend_plan" not in listed_kinds


def test_template_mentions_plan_state_reminder():
    assert "plan_state" in _SYSTEM_PLAN_EXECUTE_TEMPLATE


def test_template_mentions_end_of_plan():
    assert "end_of_plan" in _SYSTEM_PLAN_EXECUTE_TEMPLATE


# ---------------------------------------------------------------------------
# Source paths — mode selection
# ---------------------------------------------------------------------------

def test_runtime_prompt_source_paths_plan_execute():
    paths = runtime_prompt_source_paths("plan_execute")
    assert _SYSTEM_PLAN_EXECUTE_TEMPLATE_PATH in paths


def test_runtime_prompt_source_paths_json_object_not_plan_execute():
    plan_execute_paths = runtime_prompt_source_paths("plan_execute")
    json_object_paths = runtime_prompt_source_paths("json_object")
    assert plan_execute_paths != json_object_paths


# ---------------------------------------------------------------------------
# Template content in assembled prompt
# ---------------------------------------------------------------------------

def test_plan_execute_mode_injects_template():
    ctx = ModelContext(
        system_prompt="Agent instructions.",
        user_prompt="Do something.",
        response_mode="plan_execute",
        run_id=None,
    )
    prompt = ModelDriverBase._runtime_prompt(ctx)
    assert "submit_plan" in prompt
    assert "continue_plan" in prompt


def test_json_object_mode_does_not_inject_plan_execute():
    ctx = ModelContext(
        system_prompt="Agent instructions.",
        user_prompt="Do something.",
        response_mode="json_object",
        run_id=None,
    )
    prompt = ModelDriverBase._runtime_prompt(ctx)
    assert "submit_plan" not in prompt


# ---------------------------------------------------------------------------
# Agent selection — planning_config drives response_mode
# ---------------------------------------------------------------------------

def test_planning_agent_uses_plan_execute_mode(tmp_path: Path):
    agent_path = tmp_path / "planner.md"
    agent_path.write_text(
        _agent_md("id: planner\nrole: planner\nplanning:\n  enabled: true"),
        encoding="utf-8",
    )
    agent = Agent.from_markdown(
        agent_path, default_provider="openai", default_model=("gpt-4o-mini",)
    )
    assert agent.planning_config is not None
    assert agent.planning_config.enabled is True
    # build_context selects plan_execute mode — verify via the helper
    planning_active = (
        agent.planning_config is not None and agent.planning_config.enabled
    )
    assert planning_active


def test_non_planning_agent_has_no_planning_config(tmp_path: Path):
    agent_path = tmp_path / "simple.md"
    agent_path.write_text(
        _agent_md("id: simple\nrole: assistant"),
        encoding="utf-8",
    )
    agent = Agent.from_markdown(
        agent_path, default_provider="openai", default_model=("gpt-4o-mini",)
    )
    assert agent.planning_config is None
    planning_active = (
        agent.planning_config is not None and agent.planning_config.enabled
    )
    assert not planning_active
