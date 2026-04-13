"""Factory for constructing an :class:`AgentHost` tuned for web sessions."""

from __future__ import annotations

from typing import Any

from agent_framework.host import AgentHost
from agent_framework.tracing import NullRuntimeTracer, RuntimeTracer


def create_web_host(
    *,
    model_driver: Any,
    config: Any | None = None,
    user_comm: Any | None = None,
    runtime_tracer: RuntimeTracer | None = None,
    conversation_store: Any | None = None,
) -> AgentHost:
    host = AgentHost.create(
        model_driver=model_driver,
        config=config,
        user_comm=user_comm,
        conversation_store=conversation_store,
    )
    host.runtime_tracer = runtime_tracer or NullRuntimeTracer()
    return host
