"""Python logging.Handler that publishes log records into a fixed RuntimeTracer."""

from __future__ import annotations

import logging
import traceback

from agent_framework.tracing import RuntimeTracer, TraceContext, make_trace_event

_LEVEL_MAP = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


class LoggingTraceHandler(logging.Handler):
    """Publish :class:`logging.LogRecord` as ``channel="log"`` events to one tracer (tests, ad-hoc wiring)."""

    def __init__(self, tracer: RuntimeTracer) -> None:
        super().__init__()
        self._tracer = tracer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            exc_text = None
            if record.exc_info:
                exc_text = "".join(traceback.format_exception(*record.exc_info))
            payload = {
                "logger_name": record.name,
                "module": record.module,
                "pathname": record.pathname,
                "lineno": record.lineno,
                "message": record.getMessage(),
                "exc_text": exc_text,
                "funcName": record.funcName,
            }
            extra_payload = getattr(record, "trace_payload", None)
            if isinstance(extra_payload, dict):
                payload.update(extra_payload)
            level = _LEVEL_MAP.get(record.levelno, "info")
            event = make_trace_event(
                channel="log",
                level=level,  # type: ignore[arg-type]
                kind=str(getattr(record, "trace_kind", "log.record") or "log.record"),
                title=str(getattr(record, "trace_title", "") or f"{record.name} {record.levelname}"),
                summary=record.getMessage(),
                span_id=None,
                parent_span_id=None,
                context=TraceContext(),
                payload=payload,
            )
            self._tracer.publish(event)
        except Exception:
            self.handleError(record)


__all__ = ["LoggingTraceHandler"]
