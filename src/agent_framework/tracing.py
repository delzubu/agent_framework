"""Unified runtime tracing: structured events, fan-out tracer, no-op implementation."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Any, Literal, NotRequired, Protocol, TypedDict
from uuid import uuid4

TraceChannel = Literal["runtime", "llm", "log", "user"]
TraceLevel = Literal["debug", "info", "warning", "error"]


class LogEventPayload(TypedDict):
    """Fixed payload schema for ``channel="log"`` events (Python ``logging`` records).

    Log events are flat diagnostics: no ``span_id`` / ``parent_span_id`` semantics.
    """

    logger_name: str
    module: str
    pathname: str
    lineno: int
    message: str
    exc_text: str | None
    funcName: NotRequired[str]


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
    """A single trace row on the unified bus.

    Semantics by channel:

    - ``runtime``, ``llm``, ``user``: agent / model / user-interaction spans. Typically carry
      ``span_id``, ``parent_span_id``, and rich ``TraceContext`` (``run_id``, ``agent_id``, …).
    - ``log``: Python ``logging`` output. Flat record shape; ``span_id`` and ``parent_span_id``
      stay ``None``; ``context`` should only carry ``session_id`` when needed for routing.
    """

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


def make_trace_event(
    *,
    kind: str,
    title: str,
    summary: str = "",
    channel: TraceChannel = "runtime",
    level: TraceLevel = "info",
    span_id: str | None = None,
    parent_span_id: str | None = None,
    parent_event_id: str | None = None,
    context: TraceContext | None = None,
    payload: dict[str, Any] | None = None,
    tags: tuple[str, ...] = (),
) -> TraceEvent:
    """Build a ``TraceEvent`` with fresh id and timestamp (shared construction path)."""
    return TraceEvent(
        event_id=str(uuid4()),
        parent_event_id=parent_event_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        timestamp=utc_now_iso(),
        channel=channel,
        level=level,
        kind=kind,
        title=title,
        summary=summary,
        context=context if context is not None else TraceContext(),
        payload=payload if payload is not None else {},
        tags=tags,
    )


class TraceSubscriber(Protocol):
    """Optional ``trace_channels`` class attribute limits which channels are delivered."""

    def consume(self, event: TraceEvent) -> None:
        ...


def _subscriber_accepts_channel(subscriber: TraceSubscriber, channel: TraceChannel) -> bool:
    allowed = getattr(subscriber, "trace_channels", None)
    if allowed is None:
        return True
    return channel in allowed


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
            if not _subscriber_accepts_channel(subscriber, enriched.channel):
                continue
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
    "LogEventPayload",
    "NullRuntimeTracer",
    "RuntimeTracer",
    "TraceChannel",
    "TraceContext",
    "TraceEvent",
    "TraceLevel",
    "TraceSubscriber",
    "make_trace_event",
    "utc_now_iso",
]
