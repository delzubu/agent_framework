"""Python logging.Handler that publishes LogRecord events into RuntimeTracer."""

from __future__ import annotations

import logging
from uuid import uuid4

from agent_framework.tracing import RuntimeTracer, TraceEvent, utc_now_iso

_LEVEL_MAP = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


class LoggingTraceHandler(logging.Handler):
    def __init__(self, tracer: RuntimeTracer) -> None:
        super().__init__()
        self._tracer = tracer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            exc_text = None
            if record.exc_info:
                exc_text = self.format(record)
            event = TraceEvent(
                event_id=str(uuid4()),
                parent_event_id=None,
                span_id=None,
                parent_span_id=None,
                timestamp=utc_now_iso(),
                channel="system",
                level=_LEVEL_MAP.get(record.levelno, "info"),
                kind="system.log",
                title=f"{record.name} {record.levelname}",
                summary=record.getMessage(),
                payload={
                    "logger_name": record.name,
                    "pathname": record.pathname,
                    "lineno": record.lineno,
                    "message": record.getMessage(),
                    "exc_info": exc_text if record.exc_info else None,
                },
            )
            self._tracer.publish(event)
        except Exception:
            self.handleError(record)


__all__ = ["LoggingTraceHandler"]
