"""Planning support for agent_framework.

Opt-in planning capability that adds plan-generate → execute → reflect
lifecycle to agents. Activated via a `planning:` block in agent frontmatter
or via planning_override at call time.
"""

from .config import PlanningConfig
from .plan_state import CompletedStep, PlanState, PlanStep
from .step_reference import StepReferenceResolver, resolve as resolve_step_refs

__all__ = [
    "PlanningConfig",
    "PlanStep",
    "CompletedStep",
    "PlanState",
    "StepReferenceResolver",
    "resolve_step_refs",
]
