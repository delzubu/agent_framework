"""Planning support for agent_framework.

Opt-in planning capability that adds plan-generate → execute → reflect
lifecycle to agents. Activated via a `planning:` block in agent frontmatter
or via planning_override at call time.
"""

from .config import PlanningConfig

__all__ = ["PlanningConfig"]
