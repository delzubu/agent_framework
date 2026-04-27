"""PlanningConfig — configuration for the planning feature."""

from __future__ import annotations

import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

_KNOWN_KEYS = frozenset({
    "enabled",
    "parallel_execution",
    "ref_resolution",
    "max_steps",
    "max_plan_revisions",
    "step_timeout_seconds",
    "reflect_after_each_batch",
})

_SUPPORTED_REF_RESOLUTIONS = frozenset({"lenient", "strict"})


@dataclass(frozen=True, slots=True)
class PlanningConfig:
    """Parsed planning configuration from a `planning:` frontmatter block.

    All fields have defaults; only `enabled=True` is required to activate
    planning for an agent. Obtain an instance via `from_frontmatter()` or
    `default_enabled()`.
    """

    enabled: bool = False
    parallel_execution: bool = True
    ref_resolution: str = "lenient"
    max_steps: int = 50
    max_plan_revisions: int = 3
    step_timeout_seconds: float = 60.0
    reflect_after_each_batch: bool = False

    @classmethod
    def from_frontmatter(cls, raw: dict | None) -> "PlanningConfig | None":
        """Parse a `planning:` frontmatter block.

        Returns None if `raw` is None, empty, or `enabled` is false.
        Raises ValueError on unknown fields, invalid types, or unsupported
        values.
        """
        if not raw:
            return None

        unknown = set(raw.keys()) - _KNOWN_KEYS
        if unknown:
            raise ValueError(
                f"PlanningConfig: unknown frontmatter key(s): {sorted(unknown)}. "
                f"Supported keys: {sorted(_KNOWN_KEYS)}"
            )

        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError(
                f"PlanningConfig: 'enabled' must be a boolean, got {type(enabled).__name__!r}"
            )
        if not enabled:
            return None

        parallel_execution = raw.get("parallel_execution", True)
        if not isinstance(parallel_execution, bool):
            raise ValueError(
                f"PlanningConfig: 'parallel_execution' must be a boolean, "
                f"got {type(parallel_execution).__name__!r}"
            )

        ref_resolution = raw.get("ref_resolution", "lenient")
        if not isinstance(ref_resolution, str):
            raise ValueError(
                f"PlanningConfig: 'ref_resolution' must be a string, "
                f"got {type(ref_resolution).__name__!r}"
            )
        if ref_resolution not in _SUPPORTED_REF_RESOLUTIONS:
            raise ValueError(
                f"PlanningConfig: unsupported 'ref_resolution' value {ref_resolution!r}. "
                f"Supported: {sorted(_SUPPORTED_REF_RESOLUTIONS)}"
            )
        if ref_resolution == "strict":
            raise ValueError(
                "PlanningConfig: ref_resolution='strict' is reserved for a future release "
                "and is not yet supported in this version."
            )

        max_steps = raw.get("max_steps", 50)
        if not isinstance(max_steps, int) or isinstance(max_steps, bool):
            raise ValueError(
                f"PlanningConfig: 'max_steps' must be an integer, "
                f"got {type(max_steps).__name__!r}"
            )
        if max_steps <= 0:
            raise ValueError(
                f"PlanningConfig: 'max_steps' must be a positive integer, got {max_steps}"
            )

        max_plan_revisions = raw.get("max_plan_revisions", 3)
        if not isinstance(max_plan_revisions, int) or isinstance(max_plan_revisions, bool):
            raise ValueError(
                f"PlanningConfig: 'max_plan_revisions' must be an integer, "
                f"got {type(max_plan_revisions).__name__!r}"
            )
        if max_plan_revisions <= 0:
            raise ValueError(
                f"PlanningConfig: 'max_plan_revisions' must be a positive integer, "
                f"got {max_plan_revisions}"
            )

        raw_timeout = raw.get("step_timeout_seconds", 60.0)
        if isinstance(raw_timeout, bool):
            raise ValueError(
                "PlanningConfig: 'step_timeout_seconds' must be a number, got bool"
            )
        if not isinstance(raw_timeout, (int, float)):
            raise ValueError(
                f"PlanningConfig: 'step_timeout_seconds' must be a number, "
                f"got {type(raw_timeout).__name__!r}"
            )
        step_timeout_seconds = float(raw_timeout)
        if step_timeout_seconds < 0:
            raise ValueError(
                f"PlanningConfig: 'step_timeout_seconds' must be >= 0, "
                f"got {step_timeout_seconds}"
            )

        reflect_after_each_batch = raw.get("reflect_after_each_batch", False)
        if not isinstance(reflect_after_each_batch, bool):
            raise ValueError(
                f"PlanningConfig: 'reflect_after_each_batch' must be a boolean, "
                f"got {type(reflect_after_each_batch).__name__!r}"
            )
        if reflect_after_each_batch:
            raise ValueError(
                "PlanningConfig: reflect_after_each_batch=True is reserved for a future "
                "release and is not yet supported in this version (FEAT #73)."
            )

        config = cls(
            enabled=enabled,
            parallel_execution=parallel_execution,
            ref_resolution=ref_resolution,
            max_steps=max_steps,
            max_plan_revisions=max_plan_revisions,
            step_timeout_seconds=step_timeout_seconds,
            reflect_after_each_batch=reflect_after_each_batch,
        )
        _LOGGER.info("PlanningConfig parsed: %r", config)
        return config

    @classmethod
    def default_enabled(cls) -> "PlanningConfig":
        """Default config for planning_override=True with no frontmatter block."""
        return cls(enabled=True)


__all__ = ["PlanningConfig"]
