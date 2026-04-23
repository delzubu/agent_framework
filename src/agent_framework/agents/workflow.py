"""Programmatic workflow execution primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .agent_decision import SubagentCallSpec
from .agent_result import AgentResult


WorkflowValueResolver = Callable[["ProgrammaticWorkflowState"], Any]
WorkflowNextStepResolver = Callable[["ProgrammaticWorkflowState"], str | None]


@dataclass(slots=True)
class ProgrammaticWorkflowState:
    """Mutable workflow execution state shared across deterministic steps."""

    initial_parameters: dict[str, Any]
    step_results: dict[str, Any] = field(default_factory=dict)
    last_step_id: str | None = None
    last_value: Any = None

    def require_step_result(self, step_id: str) -> Any:
        if step_id not in self.step_results:
            raise KeyError(f"Workflow step {step_id!r} has not produced a result.")
        return self.step_results[step_id]


@dataclass(frozen=True, slots=True)
class ProgrammaticWorkflow:
    """Structured deterministic workflow definition."""

    entry_step: str
    steps: dict[str, "ProgrammaticWorkflowStep"]
    max_steps: int = 100


@dataclass(frozen=True, slots=True)
class ProgrammaticWorkflowStep:
    """Base workflow step."""

    step_id: str


@dataclass(frozen=True, slots=True)
class WorkflowCallSubagentStep(ProgrammaticWorkflowStep):
    """Run a single child agent through framework-owned orchestration."""

    subagent_id: str | WorkflowValueResolver
    parameters: dict[str, Any] | WorkflowValueResolver = field(default_factory=dict)
    next_step: str | WorkflowNextStepResolver | None = None


@dataclass(frozen=True, slots=True)
class WorkflowCallSubagentsStep(ProgrammaticWorkflowStep):
    """Run a batch of child agents through framework-owned orchestration."""

    calls: tuple[SubagentCallSpec, ...] | WorkflowValueResolver
    mode: str = "parallel"
    timeout_seconds: float | None = None
    next_step: str | WorkflowNextStepResolver | None = None


@dataclass(frozen=True, slots=True)
class WorkflowBranchStep(ProgrammaticWorkflowStep):
    """Choose the next deterministic step from Python code."""

    condition: WorkflowValueResolver
    then_step: str | WorkflowNextStepResolver
    else_step: str | WorkflowNextStepResolver


@dataclass(frozen=True, slots=True)
class WorkflowReturnStep(ProgrammaticWorkflowStep):
    """Finish the workflow with an AgentResult or a result-compatible value."""

    value: Any = ""


@dataclass(frozen=True, slots=True)
class WorkflowRaiseStep(ProgrammaticWorkflowStep):
    """Fail the workflow with a specific exception or message."""

    error: BaseException | str | WorkflowValueResolver = "workflow step failed"


def resolve_workflow_value(value: Any, state: ProgrammaticWorkflowState) -> Any:
    """Resolve direct values and state-aware callables uniformly."""
    if callable(value):
        return value(state)
    return value


def coerce_workflow_result(value: Any) -> AgentResult:
    """Normalize workflow return values to AgentResult."""
    if isinstance(value, AgentResult):
        return value
    if isinstance(value, str):
        return AgentResult(status="completed", message=value)
    if value is None:
        return AgentResult(status="completed", message="")
    raise TypeError(
        "Workflow return value must be AgentResult, str, or None. "
        f"Received {type(value).__name__}."
    )


__all__ = [
    "ProgrammaticWorkflow",
    "ProgrammaticWorkflowState",
    "ProgrammaticWorkflowStep",
    "WorkflowBranchStep",
    "WorkflowCallSubagentStep",
    "WorkflowCallSubagentsStep",
    "WorkflowRaiseStep",
    "WorkflowReturnStep",
    "coerce_workflow_result",
    "resolve_workflow_value",
]
