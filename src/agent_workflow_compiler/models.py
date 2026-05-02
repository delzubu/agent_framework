"""Data models for the workflow compiler pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuditEvent:
    """A single event from a JSONL audit log."""

    event_id: str
    kind: str
    timestamp: str
    context: dict[str, Any]
    payload: dict[str, Any]


@dataclass
class CompiledStep:
    """One step extracted from the converged plan, ready for code generation."""

    step_id: str
    kind: str  # call_tool | call_subagent | call_subagents | invoke_skill
    tool_name: str | None
    subagent_id: str | None
    skill_name: str | None
    # Raw parameter values from the plan step; may contain "{{token}}" strings.
    parameters: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    # Next step in linear execution order (None if it is the last step or if deps drive order)
    next_step: str | None = None


@dataclass
class ReplanCheckpoint:
    """A point in the log where the planning agent issued a revised plan."""

    after_step_id: str
    trigger_message: str
    plan_revision: int
    added_step_ids: list[str] = field(default_factory=list)


@dataclass
class PlanCompilation:
    """Complete compilation result for one planning agent call."""

    source_run_id: str
    source_agent_id: str
    invocation_parameters: dict[str, Any]
    invocation_prompt: str
    final_steps: list[CompiledStep]
    replan_checkpoints: list[ReplanCheckpoint]
