"""Host-level tracing for exact model request and response payloads."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_framework.model import ProviderRequestTrace, ProviderResponseTrace
from agent_framework.tracing import TraceContext, TraceEvent, make_trace_event

_EVENT_COLOR = "\033[95m"
_PAYLOAD_COLOR = "\033[97m"
_RESET = "\033[0m"


def _usage_payload(value: Any) -> dict[str, Any] | None:
    """Convert normalized usage values to plain dicts for trace payloads."""
    if value is None:
        return None
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(value, dict):
        return dict(value)
    return None


def build_llm_trace_event(trace: Any, *, kind: str, level: str = "info") -> TraceEvent:
    payload: dict[str, Any] = {
        "run_id": getattr(trace, "run_id", None),
        "agent_id": getattr(trace, "agent_id", None),
        "provider_name": getattr(trace, "provider_name", None),
        "model_name": getattr(trace, "model_name", None),
    }
    if hasattr(trace, "temperature"):
        payload["temperature"] = trace.temperature
    if hasattr(trace, "input_payload"):
        payload["input_payload"] = trace.input_payload
    if hasattr(trace, "raw_text"):
        payload["raw_text"] = trace.raw_text
    if hasattr(trace, "parsed_payload"):
        payload["parsed_payload"] = trace.parsed_payload
    if hasattr(trace, "usage"):
        payload["usage"] = _usage_payload(trace.usage)
    if hasattr(trace, "raw_usage"):
        payload["raw_usage"] = trace.raw_usage
    agent_label = trace.agent_id or "host"
    run_id = getattr(trace, "run_id", None)
    return make_trace_event(
        channel="llm",
        level=level,  # type: ignore[arg-type]
        kind=kind,
        title=f"{agent_label} {kind}",
        span_id=str(uuid4()),
        parent_span_id=run_id,
        context=TraceContext(run_id=trace.run_id, agent_id=trace.agent_id),
        payload=payload,
    )


def _llm_response_trace_kind_level(event: ProviderResponseTrace) -> tuple[str, str]:
    """Map provider response trace to unified trace kind/level (HTTP errors use ``llm.error``)."""
    if event.parsed_payload and event.parsed_payload.get("error"):
        return "llm.error", "error"
    return "llm.response", "info"


def wire_llm_traces_to_runtime_tracer(host: Any) -> None:
    """Chain driver I/O callbacks so ``llm.request`` / ``llm.response`` / ``llm.error`` reach ``host.runtime_tracer``.

    Preserves existing callbacks (e.g. audit trace from ``enable_audit_trace``). Safe to call when
    ``runtime_tracer`` is null or ``NullRuntimeTracer`` (no-op).
    Idempotent per host instance unless ``host._llm_traces_wired`` is cleared (e.g. after replacing
    ``runtime_tracer``).
    """
    from agent_framework.tracing import NullRuntimeTracer

    if getattr(host, "_llm_traces_wired", False):
        return
    runtime_tracer = getattr(host, "runtime_tracer", None)
    if runtime_tracer is None or isinstance(runtime_tracer, NullRuntimeTracer):
        return
    driver = getattr(host, "model_driver", None)
    if driver is None or not hasattr(driver, "set_trace_callbacks"):
        return
    existing_request = getattr(driver, "on_request_trace", None)
    existing_response = getattr(driver, "on_response_trace", None)

    def on_request(event: ProviderRequestTrace) -> None:
        if callable(existing_request):
            existing_request(event)
        runtime_tracer.publish(build_llm_trace_event(event, kind="llm.request"))

    def on_response(event: ProviderResponseTrace) -> None:
        if callable(existing_response):
            existing_response(event)
        record_usage = getattr(host, "record_runtime_llm_usage", None)
        if callable(record_usage):
            record_usage(run_id=event.run_id, usage=event.usage)
        kind, level = _llm_response_trace_kind_level(event)
        runtime_tracer.publish(build_llm_trace_event(event, kind=kind, level=level))

    driver.set_trace_callbacks(on_request=on_request, on_response=on_response)
    host._llm_traces_wired = True


def attach_to_host(host, *, target: str = "file", output_dir: str | Path = "logs") -> None:
    """Attach LLM I/O tracing callbacks to a host."""
    tracer = LlmTraceLogger(target=target, output_dir=output_dir)
    model_driver = getattr(host, "model_driver", None)
    if model_driver is None or not hasattr(model_driver, "set_trace_callbacks"):
        raise ValueError("Host model driver does not support exact provider I/O tracing.")
    existing_request = getattr(model_driver, "on_request_trace", None)
    existing_response = getattr(model_driver, "on_response_trace", None)
    runtime_tracer = getattr(host, "runtime_tracer", None)

    def on_request(event: ProviderRequestTrace) -> None:
        if callable(existing_request):
            existing_request(event)
        tracer.log_provider_request(event)
        if runtime_tracer is not None:
            runtime_tracer.publish(build_llm_trace_event(event, kind="llm.request"))

    def on_response(event: ProviderResponseTrace) -> None:
        if callable(existing_response):
            existing_response(event)
        tracer.log_provider_response(event)
        if runtime_tracer is not None:
            kind, level = _llm_response_trace_kind_level(event)
            runtime_tracer.publish(build_llm_trace_event(event, kind=kind, level=level))

    model_driver.set_trace_callbacks(
        on_request=on_request,
        on_response=on_response,
    )


class LlmTraceLogger:
    """Log exact model request and response payloads from host-level hooks."""

    def __init__(self, *, target: str = "file", output_dir: str | Path = "logs") -> None:
        self.target = target.strip().lower()
        self.output_dir = Path(output_dir)

    def log_provider_request(self, event: ProviderRequestTrace) -> None:
        payload = {
            "agent_id": event.agent_id,
            "provider_name": event.provider_name,
            "model_name": event.model_name,
            "temperature": event.temperature,
            "input": event.input_payload,
        }
        self._emit("PRE MODEL", event.agent_id, payload)

    def log_provider_response(self, event: ProviderResponseTrace) -> None:
        payload = {
            "agent_id": event.agent_id,
            "provider_name": event.provider_name,
            "model_name": event.model_name,
            "raw_text": event.raw_text,
            "payload": event.parsed_payload,
            "usage": _usage_payload(event.usage),
            "raw_usage": event.raw_usage,
        }
        self._emit("POST MODEL", event.agent_id, payload)

    def _emit(self, label: str, agent_id: str | None, payload: Any) -> None:
        rendered = json.dumps(payload, indent=2, ensure_ascii=False)
        if self.target in {"console", "both"}:
            print(f"{_EVENT_COLOR}[{label}]{_RESET}")
            print(f"{_PAYLOAD_COLOR}{rendered}{_RESET}")
        if self.target in {"file", "both"}:
            root = self.output_dir
            if not root.is_absolute():
                root = Path.cwd() / root
            root.mkdir(parents=True, exist_ok=True)
            filename = f"{agent_id}.log" if agent_id else "llm-trace.log"
            path = root / filename
            timestamp = datetime.now(timezone.utc).isoformat()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{timestamp}] {label}\n{rendered}\n\n")
