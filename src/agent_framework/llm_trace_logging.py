"""Host-level tracing for exact model request and response payloads."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_framework.model import ProviderRequestTrace, ProviderResponseTrace

_EVENT_COLOR = "\033[95m"
_PAYLOAD_COLOR = "\033[97m"
_RESET = "\033[0m"


def attach_to_host(host, *, target: str = "file", output_dir: str | Path = "logs") -> None:
    """Attach LLM I/O tracing callbacks to a host."""
    tracer = LlmTraceLogger(target=target, output_dir=output_dir)
    model_driver = getattr(host, "model_driver", None)
    if model_driver is None or not hasattr(model_driver, "set_trace_callbacks"):
        raise ValueError("Host model driver does not support exact provider I/O tracing.")
    existing_request = getattr(model_driver, "on_request_trace", None)
    existing_response = getattr(model_driver, "on_response_trace", None)

    def on_request(event: ProviderRequestTrace) -> None:
        if callable(existing_request):
            existing_request(event)
        tracer.log_provider_request(event)

    def on_response(event: ProviderResponseTrace) -> None:
        if callable(existing_response):
            existing_response(event)
        tracer.log_provider_response(event)

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
