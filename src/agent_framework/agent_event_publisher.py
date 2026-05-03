"""Singleton publisher for agent lifecycle trace events and Python log bridging."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Final
from uuid import uuid4

from agent_framework.agents.agent_decision import AgentDecision
from agent_framework.tracing import (
    LogEventPayload,
    NullRuntimeTracer,
    TraceContext,
    TraceEvent,
    TraceLevel,
    make_trace_event,
)
from agent_framework.tracing_bridge import get_active_tracer

_LOG_LEVEL_TO_TRACE: dict[int, TraceLevel] = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}

_DEFAULT_LOGGERS: Final[tuple[str, ...]] = ("agent_framework", "agent_framework_evaluator")


class _ContextAwareLogHandler(logging.Handler):
    """Publishes :class:`logging.LogRecord` as ``channel="log"`` events via the active tracer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if bool(getattr(record, "trace_skip_bridge", False)):
                return
            exc_text = None
            if record.exc_info:
                exc_text = "".join(traceback.format_exception(*record.exc_info))
            payload: LogEventPayload = {
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
                payload.update(extra_payload)  # type: ignore[arg-type]
            level = _LOG_LEVEL_TO_TRACE.get(record.levelno, "info")
            pair = get_active_tracer()
            if not pair:
                return
            tracer, overlay = pair
            if tracer is None or isinstance(tracer, NullRuntimeTracer):
                return
            ctx = TraceContext()
            if isinstance(overlay, TraceContext):
                ctx = TraceContext(session_id=overlay.session_id)
            event = make_trace_event(
                channel="log",
                level=level,
                kind=str(getattr(record, "trace_kind", "log.record") or "log.record"),
                title=str(getattr(record, "trace_title", "") or f"{record.name} {record.levelname}"),
                summary=record.getMessage()[:500],
                span_id=None,
                parent_span_id=None,
                context=ctx,
                payload=dict(payload),
            )
            tracer.publish(event)
        except Exception:
            self.handleError(record)


class AgentEventPublisher:
    """Typed trace emission for agent runs; uses :func:`get_active_tracer` (no host reference)."""

    def __init__(self) -> None:
        self._log_handler = _ContextAwareLogHandler()
        self._attached_loggers: list[logging.Logger] = []
        self._previous_levels: dict[logging.Logger, int] = {}

    def attach_log_sources(self, logger_names: list[str] | None = None) -> None:
        names = list(logger_names) if logger_names is not None else list(_DEFAULT_LOGGERS)
        for name in names:
            log = logging.getLogger(name)
            if log not in self._previous_levels:
                self._previous_levels[log] = log.level
                log.setLevel(logging.DEBUG)
            if self._log_handler not in log.handlers:
                log.addHandler(self._log_handler)
                self._attached_loggers.append(log)

    def detach_log_sources(self) -> None:
        for log in self._attached_loggers:
            if self._log_handler in log.handlers:
                log.removeHandler(self._log_handler)
            if log in self._previous_levels:
                log.setLevel(self._previous_levels.pop(log))
        self._attached_loggers.clear()

    def _publish(self, event: TraceEvent) -> None:
        pair = get_active_tracer()
        if not pair:
            return
        tracer, overlay = pair
        if tracer is None or isinstance(tracer, NullRuntimeTracer):
            return
        merged = event
        if overlay is not None and isinstance(overlay, TraceContext):
            merged = event.with_context(overlay)
        tracer.publish(merged)

    # --- Audit JSONL (consumed by AuditTraceSubscriber) ---

    def audit_agent_call_started(
        self,
        *,
        run_id: str,
        parent_run_id: str | None = None,
        caller_id: str | None,
        agent_name: str,
        system_prompt: str,
        system_prompt_sources: tuple[str, ...],
        user_prompt: str,
        user_prompt_sources: tuple[str, ...],
    ) -> None:
        """Emit audit start. ``parent_run_id`` is the caller agent's run id when this is a subagent (parallel-safe nesting)."""
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.audit.agent_call_started",
                title="Audit: agent call started",
                span_id=run_id,
                parent_span_id=parent_run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_name),
                payload={
                    "run_id": run_id,
                    "parent_run_id": parent_run_id,
                    "caller_id": caller_id,
                    "agent_name": agent_name,
                    "system_prompt": system_prompt,
                    "system_prompt_sources": list(system_prompt_sources),
                    "user_prompt": user_prompt,
                    "user_prompt_sources": list(user_prompt_sources),
                },
            ),
        )

    def audit_parameters_bound(
        self,
        *,
        run_id: str,
        agent_id: str,
        bound_parameters: dict[str, Any],
    ) -> None:
        """Emit after all pre-run hooks have executed and parameters are fully resolved.

        Unlike ``runtime.agent_started`` (which fires before on_pre_agent hooks),
        this event captures the complete post-binding parameter set, including values
        injected by hooks or extracted from rendered prompt fragments.
        """
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.parameters_bound",
                title=f"Parameters fully bound for {agent_id}",
                span_id=run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_id),
                payload={"bound_parameters": bound_parameters},
            ),
        )

    def audit_agent_call_finished(
        self,
        *,
        run_id: str,
        usage_self: dict[str, int] | None = None,
        usage_inclusive: dict[str, int] | None = None,
    ) -> None:
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.audit.agent_call_finished",
                title="Audit: agent call finished",
                span_id=run_id,
                context=TraceContext(run_id=run_id),
                payload={
                    "run_id": run_id,
                    "usage_self": dict(usage_self or {}),
                    "usage_inclusive": dict(usage_inclusive or {}),
                },
            ),
        )

    def audit_decision(self, *, run_id: str, agent_id: str, decision: AgentDecision) -> None:
        d: dict[str, Any] = {
            "kind": decision.kind,
            "message": decision.message,
            "parameters": dict(decision.parameters),
            "subagent_id": decision.subagent_id,
            "tool_name": decision.tool_name,
            "callback_intent": decision.callback_intent,
            "skill_name": decision.skill_name,
        }
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.audit.decision",
                title=f"Audit: decision ({decision.kind})",
                span_id=run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_id),
                payload={"decision": d},
            ),
        )

    def audit_callback(
        self,
        *,
        run_id: str,
        agent_id: str,
        intent: str,
        prompt: str,
        target: str,
        response: str | None,
        event_dict: dict[str, Any],
    ) -> None:
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.audit.callback",
                title="Audit: callback",
                span_id=run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_id),
                payload={
                    "intent": intent,
                    "prompt": prompt,
                    "target": target,
                    "response": response,
                    "event": event_dict,
                },
            ),
        )

    def audit_named_event(self, *, run_id: str, agent_id: str, event: dict[str, Any]) -> None:
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.audit.named_event",
                title=f"Audit: {event.get('type', 'event')}",
                span_id=run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_id),
                payload={"event": event},
            )
        )

    def audit_skill_invocation(
        self,
        *,
        run_id: str,
        agent_id: str,
        skill_name: str,
        parameters: dict[str, Any],
        inventory: list[str],
    ) -> None:
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.audit.skill_invocation",
                title=f"Audit: skill {skill_name}",
                span_id=run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_id),
                payload={
                    "skill_name": skill_name,
                    "parameters": dict(parameters),
                    "inventory": list(inventory),
                },
            ),
        )

    # --- Runtime observability (UI + optional audit) ---

    def on_context_updated(
        self,
        *,
        run_id: str,
        agent_id: str,
        message: dict[str, Any],
        source: str,
    ) -> None:
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.context_updated",
                title=f"Context updated ({source})",
                span_id=run_id,
                parent_span_id=run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_id),
                payload={"source": source, "message": message},
            )
        )

    def on_model_call_failed(
        self,
        *,
        run_id: str,
        agent_id: str,
        caller_id: str | None,
        exc: BaseException,
        status_code: int | None = None,
        upstream_body: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        if status_code is not None:
            payload["status_code"] = status_code
        if upstream_body is not None:
            payload["upstream_body"] = upstream_body
        self._publish(
            make_trace_event(
                channel="runtime",
                level="error",
                kind="runtime.model_call_failed",
                title=f"Model call failed: {type(exc).__name__}",
                summary=str(exc),
                span_id=f"{run_id}:model",
                parent_span_id=run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_id, caller_id=caller_id),
                payload=payload,
            ),
        )

    def on_tool_execution_failed(
        self,
        *,
        run_id: str,
        agent_id: str,
        tool_name: str,
        exc: BaseException,
    ) -> None:
        self._publish(
            make_trace_event(
                channel="runtime",
                level="error",
                kind="runtime.tool_execution_failed",
                title=f"Tool execution failed: {tool_name}",
                summary=str(exc),
                context=TraceContext(run_id=run_id, agent_id=agent_id, tool_name=tool_name),
                payload={
                    "tool_name": tool_name,
                    "agent_id": agent_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            ),
        )

    def on_callback_requested(
        self,
        *,
        run_id: str,
        agent_id: str,
        caller_id: str | None,
        intent: str,
        prompt: str,
        to_caller: bool,
    ) -> None:
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.callback_requested",
                title=f"Callback requested ({intent})",
                payload={
                    "intent": intent,
                    "prompt_preview": prompt[:500],
                    "to_caller": to_caller,
                },
                span_id=str(uuid4()),
                parent_span_id=run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_id, caller_id=caller_id),
            )
        )

    def on_callback_answered(
        self,
        *,
        run_id: str,
        agent_id: str,
        caller_id: str | None,
        intent: str,
        target: str,
        answer: str,
    ) -> None:
        self._publish(
            make_trace_event(
                channel="runtime",
                kind="runtime.callback_answered",
                title=f"Callback answered ({intent})",
                payload={
                    "intent": intent,
                    "target": target,
                    "answer_preview": answer[:500],
                },
                span_id=str(uuid4()),
                parent_span_id=run_id,
                context=TraceContext(run_id=run_id, agent_id=agent_id, caller_id=caller_id),
            ),
        )


agent_events = AgentEventPublisher()

__all__ = ["AgentEventPublisher", "agent_events"]
