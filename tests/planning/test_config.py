"""Tests for PlanningConfig frontmatter parsing."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_framework.planning.config import PlanningConfig
from agent_framework.agent import Agent


# ---------------------------------------------------------------------------
# from_frontmatter — basic cases
# ---------------------------------------------------------------------------

def test_from_frontmatter_none_returns_none():
    assert PlanningConfig.from_frontmatter(None) is None


def test_from_frontmatter_empty_dict_returns_none():
    assert PlanningConfig.from_frontmatter({}) is None


def test_from_frontmatter_enabled_false_returns_none():
    assert PlanningConfig.from_frontmatter({"enabled": False}) is None


def test_from_frontmatter_enabled_true_minimal():
    cfg = PlanningConfig.from_frontmatter({"enabled": True})
    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.parallel_execution is True
    assert cfg.ref_resolution == "lenient"
    assert cfg.max_steps == 50
    assert cfg.max_plan_revisions == 3
    assert cfg.step_timeout_seconds == 60.0
    assert cfg.reflect_after_each_batch is False


def test_from_frontmatter_all_fields():
    cfg = PlanningConfig.from_frontmatter({
        "enabled": True,
        "parallel_execution": False,
        "ref_resolution": "lenient",
        "max_steps": 20,
        "max_plan_revisions": 2,
        "step_timeout_seconds": 120,
        "reflect_after_each_batch": False,
    })
    assert cfg is not None
    assert cfg.parallel_execution is False
    assert cfg.max_steps == 20
    assert cfg.max_plan_revisions == 2
    assert cfg.step_timeout_seconds == 120.0
    assert cfg.reflect_after_each_batch is False


def test_from_frontmatter_step_timeout_zero_allowed():
    cfg = PlanningConfig.from_frontmatter({"enabled": True, "step_timeout_seconds": 0})
    assert cfg is not None
    assert cfg.step_timeout_seconds == 0.0


# ---------------------------------------------------------------------------
# from_frontmatter — validation errors
# ---------------------------------------------------------------------------

def test_unknown_key_raises():
    with pytest.raises(ValueError, match="unknown frontmatter key"):
        PlanningConfig.from_frontmatter({"enabled": True, "turbo_mode": True})


def test_enabled_not_bool_raises():
    with pytest.raises(ValueError, match="'enabled' must be a boolean"):
        PlanningConfig.from_frontmatter({"enabled": "yes"})


def test_parallel_execution_not_bool_raises():
    with pytest.raises(ValueError, match="'parallel_execution' must be a boolean"):
        PlanningConfig.from_frontmatter({"enabled": True, "parallel_execution": 1})


def test_ref_resolution_unsupported_value_raises():
    with pytest.raises(ValueError, match="unsupported 'ref_resolution'"):
        PlanningConfig.from_frontmatter({"enabled": True, "ref_resolution": "fuzzy"})


def test_ref_resolution_strict_reserved_raises():
    with pytest.raises(ValueError, match="not yet supported"):
        PlanningConfig.from_frontmatter({"enabled": True, "ref_resolution": "strict"})


def test_max_steps_non_int_raises():
    with pytest.raises(ValueError, match="'max_steps' must be an integer"):
        PlanningConfig.from_frontmatter({"enabled": True, "max_steps": 1.5})


def test_max_steps_zero_raises():
    with pytest.raises(ValueError, match="must be a positive integer"):
        PlanningConfig.from_frontmatter({"enabled": True, "max_steps": 0})


def test_max_steps_negative_raises():
    with pytest.raises(ValueError, match="must be a positive integer"):
        PlanningConfig.from_frontmatter({"enabled": True, "max_steps": -1})


def test_max_plan_revisions_non_int_raises():
    with pytest.raises(ValueError, match="'max_plan_revisions' must be an integer"):
        PlanningConfig.from_frontmatter({"enabled": True, "max_plan_revisions": "three"})


def test_max_plan_revisions_zero_raises():
    with pytest.raises(ValueError, match="must be a positive integer"):
        PlanningConfig.from_frontmatter({"enabled": True, "max_plan_revisions": 0})


def test_step_timeout_negative_raises():
    with pytest.raises(ValueError, match="must be >= 0"):
        PlanningConfig.from_frontmatter({"enabled": True, "step_timeout_seconds": -1.0})


def test_reflect_after_each_batch_true_raises():
    with pytest.raises(ValueError, match="not yet supported"):
        PlanningConfig.from_frontmatter({"enabled": True, "reflect_after_each_batch": True})


def test_reflect_after_each_batch_non_bool_raises():
    with pytest.raises(ValueError, match="'reflect_after_each_batch' must be a boolean"):
        PlanningConfig.from_frontmatter({"enabled": True, "reflect_after_each_batch": "yes"})


# ---------------------------------------------------------------------------
# default_enabled
# ---------------------------------------------------------------------------

def test_default_enabled_has_enabled_true():
    cfg = PlanningConfig.default_enabled()
    assert cfg.enabled is True
    assert cfg.parallel_execution is True
    assert cfg.ref_resolution == "lenient"
    assert cfg.max_steps == 50
    assert cfg.max_plan_revisions == 3
    assert cfg.step_timeout_seconds == 60.0
    assert cfg.reflect_after_each_batch is False


# ---------------------------------------------------------------------------
# Agent.planning_config wired from frontmatter
# ---------------------------------------------------------------------------

def _agent_md(frontmatter: str, system: str = "You are an agent.", user: str = "") -> str:
    return f"{frontmatter}\n---\n{system}\n---\n{user}"


def test_agent_with_planning_block_has_config(tmp_path: Path):
    agent_path = tmp_path / "planner.md"
    agent_path.write_text(
        _agent_md("id: planner\nrole: planner\nplanning:\n  enabled: true\n  max_steps: 10"),
        encoding="utf-8",
    )
    agent = Agent.from_markdown(
        agent_path, default_provider="openai", default_model=("gpt-4o-mini",)
    )
    assert agent.planning_config is not None
    assert agent.planning_config.enabled is True
    assert agent.planning_config.max_steps == 10


def test_agent_without_planning_block_has_none_config(tmp_path: Path):
    agent_path = tmp_path / "simple.md"
    agent_path.write_text(
        _agent_md("id: simple\nrole: assistant"),
        encoding="utf-8",
    )
    agent = Agent.from_markdown(
        agent_path, default_provider="openai", default_model=("gpt-4o-mini",)
    )
    assert agent.planning_config is None


def test_agent_with_invalid_planning_block_raises(tmp_path: Path):
    from agent_framework.agents.helpers import AgentMarkdownError

    agent_path = tmp_path / "bad_planner.md"
    agent_path.write_text(
        _agent_md("id: bad_planner\nrole: planner\nplanning:\n  enabled: true\n  unknown_key: true"),
        encoding="utf-8",
    )
    with pytest.raises(AgentMarkdownError):
        Agent.from_markdown(
            agent_path, default_provider="openai", default_model=("gpt-4o-mini",)
        )


def test_agent_planning_config_drives_select_turn_driver(tmp_path: Path):
    """When planning_config.enabled is True, _select_turn_driver returns PlanningTurnDriver."""
    from agent_framework.planning.turn_driver import PlanningTurnDriver

    agent_path = tmp_path / "planner.md"
    agent_path.write_text(
        _agent_md("id: planner\nrole: planner\nplanning:\n  enabled: true"),
        encoding="utf-8",
    )
    agent = Agent.from_markdown(
        agent_path, default_provider="openai", default_model=("gpt-4o-mini",)
    )
    driver = agent._select_turn_driver(planning_override=None)
    assert isinstance(driver, PlanningTurnDriver)
