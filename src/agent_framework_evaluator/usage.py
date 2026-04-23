"""Evaluator-side usage aggregation from trace events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_framework.model import LlmUsage
from agent_framework.tracing import TraceEvent


@dataclass(slots=True)
class UsageTotals:
    input_tokens: int = 0
    input_cached_tokens: int = 0
    output_tokens: int = 0
    output_cached_tokens: int = 0
    total_tokens: int = 0

    def add(self, usage: LlmUsage | dict[str, int] | None) -> None:
        if usage is None:
            return
        payload = usage.to_dict() if isinstance(usage, LlmUsage) else dict(usage)
        self.input_tokens += int(payload.get("input_tokens") or 0)
        self.input_cached_tokens += int(payload.get("input_cached_tokens") or 0)
        self.output_tokens += int(payload.get("output_tokens") or 0)
        self.output_cached_tokens += int(payload.get("output_cached_tokens") or 0)
        self.total_tokens += int(payload.get("total_tokens") or 0)

    def copy(self) -> "UsageTotals":
        return UsageTotals(
            input_tokens=self.input_tokens,
            input_cached_tokens=self.input_cached_tokens,
            output_tokens=self.output_tokens,
            output_cached_tokens=self.output_cached_tokens,
            total_tokens=self.total_tokens,
        )

    def replace(self, usage: LlmUsage | dict[str, int] | None) -> None:
        self.input_tokens = 0
        self.input_cached_tokens = 0
        self.output_tokens = 0
        self.output_cached_tokens = 0
        self.total_tokens = 0
        self.add(usage)

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "input_cached_tokens": self.input_cached_tokens,
            "output_tokens": self.output_tokens,
            "output_cached_tokens": self.output_cached_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(slots=True)
class AgentUsageSummary:
    agent_id: str
    run_id: str
    parent_run_id: str | None
    self_totals: UsageTotals = field(default_factory=UsageTotals)
    inclusive_totals: UsageTotals = field(default_factory=UsageTotals)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "self_totals": self.self_totals.to_dict(),
            "inclusive_totals": self.inclusive_totals.to_dict(),
            "llm_calls": list(self.llm_calls),
        }


@dataclass(slots=True)
class SessionUsageSummary:
    session_totals: UsageTotals = field(default_factory=UsageTotals)
    agents: dict[str, Any] = field(default_factory=dict)
    runs: dict[str, AgentUsageSummary] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_totals": self.session_totals.to_dict(),
            "agents": dict(self.agents),
            "runs": {run_id: summary.to_dict() for run_id, summary in self.runs.items()},
        }


class EvaluatorUsageTracker:
    """Consume trace events and expose a stable usage summary snapshot."""

    def __init__(self) -> None:
        self._runs: dict[str, AgentUsageSummary] = {}
        self._session_totals = UsageTotals()
        self._session_totals_from_runtime = False

    def reset(self) -> None:
        self._runs.clear()
        self._session_totals = UsageTotals()
        self._session_totals_from_runtime = False

    def consume_trace_event(self, event: dict[str, Any] | TraceEvent) -> None:
        raw = event if isinstance(event, dict) else {
            "kind": event.kind,
            "context": {
                "run_id": getattr(event.context, "run_id", None),
                "agent_id": getattr(event.context, "agent_id", None),
            },
            "payload": event.payload or {},
        }
        kind = str(raw.get("kind") or "")
        payload = raw.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        context = raw.get("context")
        context = context if isinstance(context, dict) else {}

        if kind == "runtime.audit.agent_call_started":
            run_id = str(payload.get("run_id") or context.get("run_id") or "")
            if not run_id:
                return
            agent_id = str(payload.get("agent_name") or context.get("agent_id") or "")
            parent_run_id = payload.get("parent_run_id")
            self._runs.setdefault(
                run_id,
                AgentUsageSummary(
                    agent_id=agent_id,
                    run_id=run_id,
                    parent_run_id=str(parent_run_id) if parent_run_id else None,
                ),
            )
            return

        if kind == "llm.response":
            run_id = str(payload.get("run_id") or context.get("run_id") or "")
            if not run_id:
                return
            summary = self._ensure_run(
                run_id=run_id,
                agent_id=str(payload.get("agent_id") or context.get("agent_id") or ""),
                parent_run_id=None,
            )
            usage = payload.get("usage")
            usage_dict = dict(usage) if isinstance(usage, dict) else None
            summary.llm_calls.append(
                {
                    "provider_name": payload.get("provider_name"),
                    "model_name": payload.get("model_name"),
                    "usage": usage_dict,
                    "raw_usage": payload.get("raw_usage"),
                }
            )
            summary.self_totals.add(usage_dict)
            if not self._session_totals_from_runtime:
                self._session_totals.add(usage_dict)
            return

        if kind == "runtime.agent_finished":
            run_id = str(context.get("run_id") or payload.get("run_id") or "")
            if not run_id:
                return
            summary = self._ensure_run(
                run_id=run_id,
                agent_id=str(context.get("agent_id") or payload.get("agent_id") or ""),
                parent_run_id=None,
            )
            if isinstance(payload.get("usage_self"), dict):
                summary.self_totals.replace(payload["usage_self"])
            if isinstance(payload.get("usage_inclusive"), dict):
                summary.inclusive_totals.replace(payload["usage_inclusive"])
            return

        if kind == "runtime.session_finished":
            usage_session_totals = payload.get("usage_session_totals")
            if isinstance(usage_session_totals, dict):
                self._session_totals.replace(usage_session_totals)
                self._session_totals_from_runtime = True

    def snapshot(self) -> dict[str, Any]:
        runs: dict[str, AgentUsageSummary] = {}
        for run_id, summary in self._runs.items():
            if summary.inclusive_totals.total_tokens == 0:
                summary.inclusive_totals.replace(self._recompute_inclusive(run_id).to_dict())
            runs[run_id] = summary

        agents: dict[str, Any] = {}
        for summary in runs.values():
            agent_id = summary.agent_id or "(unknown)"
            entry = agents.setdefault(
                agent_id,
                {
                    "agent_id": agent_id,
                    "run_ids": [],
                    "self_totals": UsageTotals(),
                    "inclusive_totals": UsageTotals(),
                },
            )
            entry["run_ids"].append(summary.run_id)
            entry["self_totals"].add(summary.self_totals.to_dict())
            entry["inclusive_totals"].add(summary.inclusive_totals.to_dict())

        agent_payloads = {
            agent_id: {
                "agent_id": data["agent_id"],
                "run_ids": data["run_ids"],
                "self_totals": data["self_totals"].to_dict(),
                "inclusive_totals": data["inclusive_totals"].to_dict(),
            }
            for agent_id, data in agents.items()
        }

        return SessionUsageSummary(
            session_totals=self._session_totals.copy(),
            agents=agent_payloads,
            runs=runs,
        ).to_dict()

    def _recompute_inclusive(self, run_id: str) -> UsageTotals:
        summary = self._runs.get(run_id)
        if summary is None:
            return UsageTotals()
        totals = summary.self_totals.copy()
        child_ids = [
            child_run_id
            for child_run_id, child in self._runs.items()
            if child.parent_run_id == run_id
        ]
        for child_run_id in child_ids:
            totals.add(self._recompute_inclusive(child_run_id).to_dict())
        return totals

    def _ensure_run(self, *, run_id: str, agent_id: str, parent_run_id: str | None) -> AgentUsageSummary:
        summary = self._runs.get(run_id)
        if summary is None:
            summary = AgentUsageSummary(agent_id=agent_id, run_id=run_id, parent_run_id=parent_run_id)
            self._runs[run_id] = summary
        elif agent_id and not summary.agent_id:
            summary.agent_id = agent_id
        if parent_run_id and not summary.parent_run_id:
            summary.parent_run_id = parent_run_id
        return summary


__all__ = [
    "AgentUsageSummary",
    "EvaluatorUsageTracker",
    "SessionUsageSummary",
    "UsageTotals",
]
