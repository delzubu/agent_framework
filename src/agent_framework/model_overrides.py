from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AgentModelOverrideScope = Literal["root_only", "all_agents"]
DEFAULT_AGENT_MODEL_OVERRIDE_SCOPE: AgentModelOverrideScope = "root_only"


@dataclass(frozen=True, slots=True)
class AgentModelOverride:
    """Run-scoped model override for agent execution."""

    model_names: tuple[str, ...]
    scope: AgentModelOverrideScope = DEFAULT_AGENT_MODEL_OVERRIDE_SCOPE


def normalize_model_override_names(
    value: str | tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    """Normalize a comma-separated string/tuple into a non-empty model tuple."""
    if value is None:
        return None
    if isinstance(value, str):
        parsed = tuple(m.strip() for m in value.split(",") if m.strip())
        return parsed or None
    return value or None


def normalize_agent_model_override_scope(value: str | None) -> AgentModelOverrideScope:
    """Normalize override scope, defaulting invalid/empty values to ``root_only``."""
    scope = str(value or DEFAULT_AGENT_MODEL_OVERRIDE_SCOPE).strip().lower()
    return "all_agents" if scope == "all_agents" else DEFAULT_AGENT_MODEL_OVERRIDE_SCOPE


def make_agent_model_override(
    model_override: str | tuple[str, ...] | None,
    *,
    scope: str | None = None,
) -> AgentModelOverride | None:
    """Build a typed agent-model override from raw user/input values."""
    model_names = normalize_model_override_names(model_override)
    if model_names is None:
        return None
    return AgentModelOverride(
        model_names=model_names,
        scope=normalize_agent_model_override_scope(scope),
    )

