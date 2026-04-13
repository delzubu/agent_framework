"""Ingress adapters that feed external systems into the unified tracer."""

from agent_framework.tracing_consumers.log_handler import LoggingTraceHandler

__all__ = ["LoggingTraceHandler"]
