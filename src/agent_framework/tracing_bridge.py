"""Context-local bridge so sync code (hooks, user comm) can reach the active runtime tracer."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from agent_framework.tracing import RuntimeTracer, TraceContext

_active: ContextVar[tuple[Any, Any] | None] = ContextVar("agent_framework_active_tracer", default=None)


def get_active_tracer() -> tuple[Any, Any] | None:
    """Return ``(runtime_tracer, trace_context_overlay)`` for the current agent run, or ``None``."""
    return _active.get()


@contextmanager
def active_tracer_scope(tracer: Any, overlay: Any | None) -> Iterator[None]:
    """Bind ``tracer`` and optional ``trace_context_overlay`` for the duration of the block."""
    token = _active.set((tracer, overlay))
    try:
        yield None
    finally:
        _active.reset(token)


def try_publish_trace(
    *,
    channel: str,
    kind: str,
    title: str,
    summary: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Publish a trace event if an active non-null tracer is bound (no-op otherwise)."""
    from uuid import uuid4

    from agent_framework.tracing import NullRuntimeTracer, TraceContext, TraceEvent, utc_now_iso

    pair = get_active_tracer()
    if not pair:
        return
    tracer, overlay = pair
    if tracer is None or isinstance(tracer, NullRuntimeTracer):
        return
    ctx: TraceContext = overlay if isinstance(overlay, TraceContext) else TraceContext()
    tracer.publish(
        TraceEvent(
            event_id=str(uuid4()),
            parent_event_id=None,
            span_id=None,
            parent_span_id=None,
            timestamp=utc_now_iso(),
            channel=channel,  # type: ignore[arg-type]
            level="info",
            kind=kind,
            title=title,
            summary=summary,
            context=ctx,
            payload=payload or {},
        )
    )


__all__ = ["active_tracer_scope", "get_active_tracer", "try_publish_trace"]
