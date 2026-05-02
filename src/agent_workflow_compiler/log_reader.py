"""Read JSONL audit logs into structured AuditEvent objects."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import AuditEvent


def read_events(log_path: str | Path) -> list[AuditEvent]:
    """Parse a JSONL audit log and return all events in order."""
    path = Path(log_path)
    events: list[AuditEvent] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON — {exc}") from exc
            events.append(
                AuditEvent(
                    event_id=raw.get("event_id", ""),
                    kind=raw.get("kind", ""),
                    timestamp=raw.get("timestamp", ""),
                    context=raw.get("context") or {},
                    payload=raw.get("payload") or {},
                )
            )
    return events


def events_for_run(events: list[AuditEvent], run_id: str) -> list[AuditEvent]:
    """Return only events whose context.run_id starts with run_id."""
    return [e for e in events if (e.context.get("run_id") or "").startswith(run_id)]


def planning_run_ids(events: list[AuditEvent]) -> list[str]:
    """Return run_ids of top-level planning calls (those with at least one plan_updated event)."""
    runs_with_plan: set[str] = set()
    for e in events:
        if e.kind != "runtime.audit.named_event":
            continue
        ev = e.payload.get("event", {})
        if ev.get("type") == "plan_updated":
            run_id = e.context.get("run_id", "")
            if run_id:
                runs_with_plan.add(run_id)

    # Preserve insertion order from the log
    seen: dict[str, None] = {}
    for e in events:
        run_id = e.context.get("run_id", "")
        if run_id in runs_with_plan and run_id not in seen:
            seen[run_id] = None
    return list(seen)
