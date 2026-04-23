"""Runtime LLM usage aggregation keyed by run id."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_framework.model import LlmUsage


@dataclass(slots=True)
class UsageTotals:
    """Mutable running token totals."""

    input_tokens: int = 0
    input_cached_tokens: int = 0
    output_tokens: int = 0
    output_cached_tokens: int = 0
    total_tokens: int = 0

    def add(self, usage: LlmUsage | dict[str, int] | None) -> None:
        """Add one normalized usage payload."""
        if usage is None:
            return
        payload = usage.to_dict() if isinstance(usage, LlmUsage) else dict(usage)
        self.input_tokens += int(payload.get("input_tokens") or 0)
        self.input_cached_tokens += int(payload.get("input_cached_tokens") or 0)
        self.output_tokens += int(payload.get("output_tokens") or 0)
        self.output_cached_tokens += int(payload.get("output_cached_tokens") or 0)
        self.total_tokens += int(payload.get("total_tokens") or 0)

    def copy(self) -> "UsageTotals":
        """Return an independent copy."""
        return UsageTotals(
            input_tokens=self.input_tokens,
            input_cached_tokens=self.input_cached_tokens,
            output_tokens=self.output_tokens,
            output_cached_tokens=self.output_cached_tokens,
            total_tokens=self.total_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-serializable mapping."""
        return {
            "input_tokens": self.input_tokens,
            "input_cached_tokens": self.input_cached_tokens,
            "output_tokens": self.output_tokens,
            "output_cached_tokens": self.output_cached_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(slots=True)
class _RunUsageNode:
    run_id: str
    agent_id: str
    parent_run_id: str | None
    self_totals: UsageTotals = field(default_factory=UsageTotals)
    inclusive_totals: UsageTotals | None = None
    child_run_ids: list[str] = field(default_factory=list)


class RuntimeUsageTracker:
    """Aggregate normalized LLM usage for a host session."""

    def __init__(self) -> None:
        self._runs: dict[str, _RunUsageNode] = {}
        self._session_totals = UsageTotals()

    def record_run_started(self, *, run_id: str, agent_id: str, parent_run_id: str | None) -> None:
        """Register a run and link it to its parent."""
        existing = self._runs.get(run_id)
        if existing is None:
            self._runs[run_id] = _RunUsageNode(
                run_id=run_id,
                agent_id=agent_id,
                parent_run_id=parent_run_id,
            )
        else:
            existing.agent_id = agent_id
            existing.parent_run_id = parent_run_id
        if parent_run_id:
            parent = self._runs.get(parent_run_id)
            if parent is None:
                parent = _RunUsageNode(run_id=parent_run_id, agent_id="", parent_run_id=None)
                self._runs[parent_run_id] = parent
            if run_id not in parent.child_run_ids:
                parent.child_run_ids.append(run_id)

    def record_llm_usage(self, *, run_id: str, usage: LlmUsage | None) -> None:
        """Record one model response for a run."""
        if not run_id or usage is None:
            return
        node = self._runs.get(run_id)
        if node is None:
            node = _RunUsageNode(run_id=run_id, agent_id="", parent_run_id=None)
            self._runs[run_id] = node
        node.self_totals.add(usage)
        node.inclusive_totals = None
        self._session_totals.add(usage)

    def finish_run(self, *, run_id: str) -> dict[str, dict[str, int]]:
        """Return canonical self and inclusive totals for a completed run."""
        node = self._runs.get(run_id)
        if node is None:
            empty = UsageTotals().to_dict()
            return {"usage_self": empty, "usage_inclusive": empty}
        inclusive = self._compute_inclusive(node)
        return {
            "usage_self": node.self_totals.to_dict(),
            "usage_inclusive": inclusive.to_dict(),
        }

    def session_totals(self) -> dict[str, int]:
        """Return session-wide totals across all runs."""
        return self._session_totals.to_dict()

    def _compute_inclusive(self, node: _RunUsageNode) -> UsageTotals:
        if node.inclusive_totals is not None:
            return node.inclusive_totals.copy()
        totals = node.self_totals.copy()
        for child_run_id in node.child_run_ids:
            child = self._runs.get(child_run_id)
            if child is None:
                continue
            totals.add(self._compute_inclusive(child).to_dict())
        node.inclusive_totals = totals.copy()
        return totals


__all__ = ["RuntimeUsageTracker", "UsageTotals"]
