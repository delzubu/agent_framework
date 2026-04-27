"""PlanStep, CompletedStep, and PlanState dataclasses.

These types hold the state of a planning run. PlanStep is immutable (frozen);
PlanState is mutable and updated by PlanningTurnDriver as execution progresses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_VALID_STEP_KINDS = frozenset({"call_tool", "call_subagent", "invoke_skill", "callback"})


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One step in an agent plan.

    A step describes a single action to execute: a tool call, a sub-agent
    delegation, a skill invocation, or a callback escalation. Parameters may
    contain ``{{ref}}`` tokens that are resolved at dispatch time.

    Fields:
        id: Unique step identifier within the plan. Must match
            ``^[a-zA-Z][a-zA-Z0-9_]*$``.
        kind: Action kind — one of ``call_tool``, ``call_subagent``,
            ``invoke_skill``, ``callback``.
        parameters: Action-specific arguments. May contain ``{{ref}}`` tokens
            referencing earlier step results or invocation parameters.
        tool_name: Required when ``kind == "call_tool"``.
        subagent_id: Required when ``kind == "call_subagent"``.
        skill_name: Required when ``kind == "invoke_skill"``.
        callback_intent: Required when ``kind == "callback"``.
        depends_on: Step IDs whose results must be available before this step
            runs. No forward references; must form a DAG.
        message: Optional message for callback steps.
    """

    id: str
    kind: str
    parameters: dict[str, Any]
    tool_name: str | None = None
    subagent_id: str | None = None
    skill_name: str | None = None
    callback_intent: str | None = None
    depends_on: tuple[str, ...] = ()
    message: str = ""


@dataclass(slots=True)
class CompletedStep:
    """Record of a completed plan step, including timing and optional error.

    Fields:
        step_id: The ID of the completed step (mirrors ``step.id``).
        step: The original ``PlanStep`` definition.
        result: Raw result payload returned by the step's action.
        started_at: Unix timestamp when execution began.
        finished_at: Unix timestamp when execution finished.
        plan_revision_at_start: ``PlanState.plan_revision`` at the time this
            step was dispatched (used to detect stale completions after a
            replan).
        error: Non-``None`` when the step raised an exception; ``result`` is
            then a stub error dict rather than a real result.
    """

    step_id: str
    step: PlanStep
    result: Any
    started_at: float
    finished_at: float
    plan_revision_at_start: int
    error: str | None = None


@dataclass(slots=True)
class PlanState:
    """Mutable per-run state for a planning invocation.

    Created lazily by ``PlanningTurnDriver`` on its first ``run_turn`` call.
    In-memory only in v1; persistence is a future feature.

    Fields:
        plan: Current plan — a tuple of ``PlanStep`` objects. Replaced (not
            mutated) each time the model emits ``submit_plan`` or
            ``amend_plan``.
        step_results: Map of ``step_id → result payload`` for all completed
            steps. Used by the ``{{ref}}`` resolver.
        completed_steps: Ordered log of all ``CompletedStep`` records.
        plan_revision: Number of times ``submit_plan`` or ``amend_plan`` has
            been emitted in this run. Incremented by the driver before
            executing each new plan.
        total_steps_executed: Cumulative count of step executions. Checked
            against ``PlanningConfig.max_steps``.
        pending_callback_step_id: When a step emits a model-bound callback,
            this is set to that step's ID so the reflect phase can route the
            resolution back.
        awaiting_caller_callback: Set to ``True`` when the plan is paused
            waiting for a user-bound callback response from the caller.
    """

    plan: tuple[PlanStep, ...] = ()
    step_results: dict[str, Any] = field(default_factory=dict)
    completed_steps: list[CompletedStep] = field(default_factory=list)
    plan_revision: int = 0
    total_steps_executed: int = 0
    pending_callback_step_id: str | None = None
    awaiting_caller_callback: bool = False
    consecutive_validation_failures: int = 0


__all__ = ["PlanStep", "CompletedStep", "PlanState"]
