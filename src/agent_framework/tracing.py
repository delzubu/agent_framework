"""Unified runtime tracing: structured events, fan-out tracer, no-op implementation."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

TraceChannel = Literal["runtime", "llm", "system", "user"]
TraceLevel = Literal["debug", "info", "warning", "error"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class TraceContext:
    session_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    caller_id: str | None = None
    tool_name: str | None = None
    subagent_id: str | None = None
    conversation_id: str | None = None

    def merged(self, **updates: Any) -> TraceContext:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        for key, value in updates.items():
            if value is not None:
                data[key] = value
        return TraceContext(**data)


@dataclass(frozen=True, slots=True)
class TraceEvent:
    event_id: str
    parent_event_id: str | None
    span_id: str | None
    parent_span_id: str | None
    timestamp: str
    channel: TraceChannel
    level: TraceLevel
    kind: str
    title: str
    summary: str = ""
    context: TraceContext = field(default_factory=TraceContext)
    payload: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    def with_context(self, overlay: TraceContext) -> TraceEvent:
        overlay_kwargs = {f.name: getattr(overlay, f.name) for f in fields(overlay)}
        overlay_kwargs = {k: v for k, v in overlay_kwargs.items() if v is not None}
        merged_ctx = self.context.merged(**overlay_kwargs)
        return replace(self, context=merged_ctx)


class TraceSubscriber(Protocol):
    def consume(self, event: TraceEvent) -> None:
        ...


class RuntimeTracer(Protocol):
    def publish(self, event: TraceEvent) -> None:
        ...

    def child(self, **context_updates: Any) -> RuntimeTracer:
        ...

    def subscribe(self, subscriber: TraceSubscriber) -> None:
        ...

    def unsubscribe(self, subscriber: TraceSubscriber) -> None:
        ...


@dataclass(slots=True)
class CompositeRuntimeTracer:
    subscribers: list[TraceSubscriber] = field(default_factory=list)
    base_context: TraceContext = field(default_factory=TraceContext)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def publish(self, event: TraceEvent) -> None:
        enriched = event.with_context(self.base_context)
        with self._lock:
            subs = tuple(self.subscribers)
        for subscriber in subs:
            subscriber.consume(enriched)

    def child(self, **context_updates: Any) -> CompositeRuntimeTracer:
        return CompositeRuntimeTracer(
            subscribers=self.subscribers,
            base_context=self.base_context.merged(**context_updates),
        )

    def subscribe(self, subscriber: TraceSubscriber) -> None:
        self.subscribers.append(subscriber)

    def unsubscribe(self, subscriber: TraceSubscriber) -> None:
        self.subscribers = [item for item in self.subscribers if item is not subscriber]


@dataclass(slots=True)
class NullRuntimeTracer:
    def publish(self, event: TraceEvent) -> None:
        return None

    def child(self, **context_updates: Any) -> NullRuntimeTracer:
        return self

    def subscribe(self, subscriber: TraceSubscriber) -> None:
        return None

    def unsubscribe(self, subscriber: TraceSubscriber) -> None:
        return None


__all__ = [
    "CompositeRuntimeTracer",
    "NullRuntimeTracer",
    "RuntimeTracer",
    "TraceChannel",
    "TraceContext",
    "TraceEvent",
    "TraceLevel",
    "TraceSubscriber",
    "utc_now_iso",
]
