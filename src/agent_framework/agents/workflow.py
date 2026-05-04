"""Programmatic workflow execution primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Union

from .agent_decision import SubagentCallSpec
from .agent_result import AgentResult


WorkflowValueResolver = Callable[["ProgrammaticWorkflowState"], Any]
WorkflowNextStepResolver = Callable[["ProgrammaticWorkflowState"], str | None]


@dataclass(slots=True)
class ProgrammaticWorkflowState:
    """Mutable workflow execution state shared across deterministic steps."""

    initial_parameters: dict[str, Any]
    step_results: dict[str, Any] = field(default_factory=dict)
    context_entries: list[str] = field(default_factory=list)
    last_step_id: str | None = None
    last_value: Any = None

    def require_step_result(self, step_id: str) -> Any:
        if step_id not in self.step_results:
            raise KeyError(f"Workflow step {step_id!r} has not produced a result.")
        return self.step_results[step_id]


# ---------------------------------------------------------------------------
# Mutation types for on_step_end callbacks
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WorkflowContinue:
    """Continue to the step's own next_step (default behaviour)."""


@dataclass(frozen=True, slots=True)
class WorkflowGoto:
    """Jump to a specific step instead of the step's own next_step."""

    step_id: str


@dataclass(frozen=True, slots=True)
class WorkflowReplace:
    """Swap the active workflow; execution resumes at the new workflow's entry_step."""

    workflow: ProgrammaticWorkflow


@dataclass(frozen=True, slots=True)
class WorkflowAbort:
    """Abort the workflow immediately with a failure message."""

    reason: str


WorkflowMutation = Union[WorkflowContinue, WorkflowGoto, WorkflowReplace, WorkflowAbort]

OnStepEnd = Callable[
    [str, Any, "ProgrammaticWorkflowState", "ProgrammaticWorkflow"],
    "WorkflowMutation | None",
]


class WorkflowAbortedError(RuntimeError):
    """Raised when a workflow is aborted via WorkflowAbort mutation."""


@dataclass(frozen=True)
class ProgrammaticWorkflow:
    """Structured deterministic workflow definition."""

    entry_step: str
    steps: dict[str, "ProgrammaticWorkflowStep"]
    max_steps: int = 100
    on_step_end: OnStepEnd | None = None


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
class WorkflowCallToolStep(ProgrammaticWorkflowStep):
    """Invoke a registered tool by name."""

    tool_name: str
    arguments: dict[str, Any] | WorkflowValueResolver = field(default_factory=dict)
    next_step: str | WorkflowNextStepResolver | None = None


@dataclass(frozen=True, slots=True)
class WorkflowCallSubagentsStep(ProgrammaticWorkflowStep):
    """Run a batch of child agents through framework-owned orchestration."""

    calls: tuple[SubagentCallSpec, ...] | WorkflowValueResolver
    mode: str = "parallel"
    timeout_seconds: float | None = None
    next_step: str | WorkflowNextStepResolver | None = None


@dataclass(frozen=True, slots=True)
class WorkflowModelStep(ProgrammaticWorkflowStep):
    """Run a phase-scoped model loop in the workflow agent's own context."""

    phase_id: str
    prompt_fragment: str | WorkflowValueResolver
    allowed_decision_kinds: frozenset[str] | None = None
    final_response_schema: dict[str, Any] | None = None
    max_turns: int = 8
    next_step: str | WorkflowNextStepResolver | None = None


@dataclass(frozen=True, slots=True)
class WorkflowTransformStep(ProgrammaticWorkflowStep):
    """Run deterministic Python state transformation or validation."""

    transform: WorkflowValueResolver
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
    """Resolve direct values and state-aware callables uniformly.

    Callables are invoked with *state*. Dict values are resolved recursively
    so per-key resolver lambdas work alongside plain values.
    """
    if callable(value):
        return value(state)
    if isinstance(value, dict):
        return {k: resolve_workflow_value(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_workflow_value(v, state) for v in value]
    if isinstance(value, tuple):
        return tuple(resolve_workflow_value(v, state) for v in value)
    return value


def coerce_workflow_result(value: Any) -> AgentResult:
    """Normalize workflow return values to AgentResult."""
    if isinstance(value, AgentResult):
        return value
    if isinstance(value, WorkflowAbortedError):
        return AgentResult(status="error", message=str(value))
    if isinstance(value, str):
        return AgentResult(status="completed", message=value)
    if value is None:
        return AgentResult(status="completed", message="")
    raise TypeError(
        "Workflow return value must be AgentResult, str, or None. "
        f"Received {type(value).__name__}."
    )


__all__ = [
    "OnStepEnd",
    "ProgrammaticWorkflow",
    "ProgrammaticWorkflowState",
    "ProgrammaticWorkflowStep",
    "WorkflowAbort",
    "WorkflowAbortedError",
    "WorkflowBranchStep",
    "WorkflowCallSubagentStep",
    "WorkflowCallSubagentsStep",
    "WorkflowCallToolStep",
    "WorkflowContinue",
    "WorkflowGoto",
    "WorkflowModelStep",
    "WorkflowMutation",
    "WorkflowRaiseStep",
    "WorkflowReplace",
    "WorkflowReturnStep",
    "WorkflowTransformStep",
    "coerce_workflow_result",
    "resolve_workflow_value",
]
