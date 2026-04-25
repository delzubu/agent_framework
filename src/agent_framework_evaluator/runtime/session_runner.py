from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agent_framework.agent import AgentResult
from agent_framework.agent_registry import normalize_agent_id
from agent_framework.host import AgentHost
from agent_framework.tracing import TraceContext, make_trace_event
from agent_framework.tracing_bridge import active_tracer_scope

from agent_framework_evaluator.models import SessionContext
from agent_framework_evaluator.runtime.setup_loader import load_setup_module
from agent_framework_evaluator.usage import EvaluatorUsageTracker

HostFactory = Callable[..., Any]
_LOGGER = logging.getLogger(__name__)


class _UsageTraceSubscriber:
    """Adapter so the evaluator usage tracker can subscribe to runtime events."""

    def __init__(self, tracker: EvaluatorUsageTracker) -> None:
        self._tracker = tracker

    def consume(self, event: Any) -> None:
        self._tracker.consume_trace_event(event)


def _agent_result_payload(result: AgentResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": result.status,
        "message": result.message,
    }
    if result.decision is not None:
        payload["kind"] = result.decision.kind
        payload["parameters"] = dict(result.decision.parameters)
        if result.decision.subagent_id is not None:
            payload["subagent_id"] = result.decision.subagent_id
        if result.decision.tool_name is not None:
            payload["tool_name"] = result.decision.tool_name
        if result.decision.callback_intent is not None:
            payload["intent"] = result.decision.callback_intent
        if result.decision.skill_name is not None:
            payload["skill_name"] = result.decision.skill_name
    return payload


def _wire_one_shot_capture(host: Any, callback: Callable[[Any], None]) -> None:
    """Invoke ``callback`` once on the first ``on_request_trace`` (LLM request), then chain."""
    try:
        raw = host.get_model_driver_raw()
    except Exception:
        return
    prev_req = getattr(raw, "on_request_trace", None)
    prev_resp = getattr(raw, "on_response_trace", None)
    fired = [False]

    def one_shot(trace: Any) -> None:
        if not fired[0]:
            fired[0] = True
            callback(trace)
        if prev_req is not None:
            prev_req(trace)

    raw.set_trace_callbacks(on_request=one_shot, on_response=prev_resp)


class SessionRunner:
    def __init__(
        self,
        env_path: str | Path,
        *,
        host_factory: HostFactory | None = None,
    ) -> None:
        self.env_path = Path(env_path)
        self._host_factory = host_factory
        self._last_setup_module: Any | None = None
        self._last_session_context: SessionContext | None = None
        self._suite_teardown_done: bool = False
        self._last_usage_summary: dict[str, Any] | None = None

    def _new_session_id(self) -> str:
        return str(uuid4())

    def _create_host(
        self,
        session_context: SessionContext,
        *,
        user_comm: Any | None = None,
        runtime_tracer: Any | None = None,
        all_agents_model_override: str | tuple[str, ...] | None = None,
    ) -> Any:
        if self._host_factory is not None:
            return self._host_factory(
                session_context=session_context,
                env_path=self.env_path,
                user_comm=user_comm,
                runtime_tracer=runtime_tracer,
                all_agents_model_override=all_agents_model_override,
            )
        host = AgentHost.from_env(
            str(self.env_path),
            user_comm=user_comm,
            all_agents_model_override=all_agents_model_override,
        )
        audit_sub = host._audit_trace_subscriber
        receive_sub = host._host_receive_log_subscriber
        if runtime_tracer is not None:
            host.runtime_tracer = runtime_tracer
            if audit_sub is not None:
                host.runtime_tracer.subscribe(audit_sub)
            if receive_sub is not None:
                host.runtime_tracer.subscribe(receive_sub)
        return host

    def _build_usage_summary(
        self,
        *,
        local_usage_tracker: EvaluatorUsageTracker | None,
        session_usage_totals: dict[str, Any],
    ) -> dict[str, Any]:
        summary = (
            local_usage_tracker.snapshot()
            if local_usage_tracker is not None
            else {
                "session_totals": {},
                "agents": {},
                "runs": {},
            }
        )
        summary["session_totals"] = dict(session_usage_totals)
        summary.setdefault("agents", {})
        summary.setdefault("runs", {})
        return summary

    def run_once(
        self,
        *,
        agent_id: str,
        prompt: str,
        setup_path: Path | None = None,
        user_comm: Any | None = None,
        runtime_tracer: Any | None = None,
        session_id: str | None = None,
        on_first_llm_call: Callable[[Any], None] | None = None,
        agent_model_override: str | tuple[str, ...] | None = None,
        agent_model_override_scope: str = "root_only",
    ) -> dict[str, object]:
        _LOGGER.debug(
            "SessionRunner.run_once entry",
            extra={
                "trace_kind": "runtime.session_runner.entry",
                "trace_title": "SessionRunner.run_once entry",
                "trace_payload": {
                    "agent_id": agent_id,
                    "prompt": prompt,
                    "setup_path": str(setup_path) if setup_path else None,
                    "session_id": session_id,
                },
            },
        )
        self._suite_teardown_done = False
        sid = session_id or self._new_session_id()
        local_usage_tracker: EvaluatorUsageTracker | None = None
        effective_runtime_tracer = runtime_tracer
        if effective_runtime_tracer is None:
            from agent_framework.tracing import CompositeRuntimeTracer

            local_usage_tracker = EvaluatorUsageTracker()
            effective_runtime_tracer = CompositeRuntimeTracer(
                subscribers=[_UsageTraceSubscriber(local_usage_tracker)]
            )
        resolved_agent_id = normalize_agent_id(agent_id)
        session_context = SessionContext(
            session_id=sid,
            agent_id=resolved_agent_id,
            env_path=self.env_path.resolve(),
            setup_path=setup_path,
        )
        setup_module = None
        if setup_path is not None and setup_path.exists() and setup_path.suffix == ".py":
            setup_module = load_setup_module(setup_path)

        self._last_setup_module = setup_module
        self._last_session_context = session_context

        if runtime_tracer is not None:
            runtime_tracer.publish(
                make_trace_event(
                    channel="runtime",
                    level="info",
                    kind="runtime.session_started",
                    title="Session started",
                    span_id=sid,
                    context=TraceContext(
                        session_id=session_context.session_id,
                        agent_id=resolved_agent_id,
                    ),
                )
            )
        _LOGGER.info(
            "session started",
            extra={
                "trace_kind": "runtime.session_runner.session_started",
                "trace_title": "SessionRunner session started",
                "trace_payload": {
                    "session_id": session_context.session_id,
                    "agent_id": resolved_agent_id,
                    "setup_path": str(setup_path) if setup_path else None,
                },
            },
        )

        host = self._create_host(
            session_context,
            user_comm=user_comm,
            runtime_tracer=effective_runtime_tracer,
            all_agents_model_override=(
                agent_model_override
                if agent_model_override and str(agent_model_override_scope).strip().lower() == "all_agents"
                else None
            ),
        )
        if effective_runtime_tracer is not None:
            from agent_framework.agent_event_publisher import agent_events
            from agent_framework.llm_trace_logging import wire_llm_traces_to_runtime_tracer

            host._llm_traces_wired = False
            wire_llm_traces_to_runtime_tracer(host)
            agent_events.attach_log_sources()
        if on_first_llm_call is not None:
            _wire_one_shot_capture(host, on_first_llm_call)
        prev_overlay = host.trace_context_overlay
        host.trace_context_overlay = TraceContext(session_id=sid)
        result: AgentResult | None = None
        run_error: BaseException | None = None
        try:
            with active_tracer_scope(effective_runtime_tracer, host.trace_context_overlay):
                _LOGGER.debug(
                    "SessionRunner.run_once active entry",
                    extra={
                        "trace_kind": "runtime.session_runner.entry",
                        "trace_title": "SessionRunner.run_once entry",
                        "trace_payload": {
                            "agent_id": resolved_agent_id,
                            "prompt": prompt,
                            "setup_path": str(setup_path) if setup_path else None,
                            "session_id": sid,
                        },
                    },
                )
                _LOGGER.info(
                    "session started",
                    extra={
                        "trace_kind": "runtime.session_runner.session_started",
                        "trace_title": "SessionRunner session started",
                        "trace_payload": {
                            "session_id": session_context.session_id,
                            "agent_id": resolved_agent_id,
                            "setup_path": str(setup_path) if setup_path else None,
                        },
                    },
                )
                if setup_module and hasattr(setup_module, "register"):
                    _LOGGER.debug("setup register entry")
                    setup_module.register(host, session_context)
                    _LOGGER.debug("setup register exit")
                if setup_module and hasattr(setup_module, "suite_setup"):
                    _LOGGER.debug("suite_setup entry")
                    setup_module.suite_setup(session_context)
                    _LOGGER.debug("suite_setup exit")
                if setup_module and hasattr(setup_module, "test_setup"):
                    _LOGGER.debug("test_setup entry")
                    setup_module.test_setup({"prompt": prompt}, session_context)
                    _LOGGER.debug("test_setup exit")
                run_agent_kwargs: dict[str, Any] = {
                    "initial_instruction": prompt,
                }
                if (
                    agent_model_override
                    and str(agent_model_override_scope).strip().lower() != "all_agents"
                ):
                    run_agent_kwargs["model_override"] = agent_model_override
                result = host.run_agent(
                    resolved_agent_id,
                    **run_agent_kwargs,
                )
                if setup_module and hasattr(setup_module, "test_teardown"):
                    _LOGGER.debug("test_teardown entry")
                    setup_module.test_teardown({"prompt": prompt}, session_context)
                    _LOGGER.debug("test_teardown exit")
        except Exception as exc:
            run_error = exc
            _LOGGER.error(
                "SessionRunner.run_once failed",
                exc_info=True,
                extra={
                    "trace_kind": "runtime.session_runner.failed",
                    "trace_title": "SessionRunner.run_once failed",
                    "trace_payload": {
                        "session_id": sid,
                        "agent_id": resolved_agent_id,
                        "traceback": traceback.format_exc(),
                    },
                },
            )
            _LOGGER.debug("SessionRunner.run_once failure stack", exc_info=True)
        finally:
            host.trace_context_overlay = prev_overlay
        session_usage_totals: dict[str, Any] = {}
        session_usage_totals_fn = getattr(host, "session_usage_totals", None)
        if callable(session_usage_totals_fn):
            session_usage_totals = dict(session_usage_totals_fn())
        self._last_usage_summary = self._build_usage_summary(
            local_usage_tracker=local_usage_tracker,
            session_usage_totals=session_usage_totals,
        )
        status = result.status if result is not None else "failed"
        if effective_runtime_tracer is not None:
            effective_runtime_tracer.publish(
                make_trace_event(
                    channel="runtime",
                    level="info",
                    kind="runtime.session_finished",
                    title="Session finished",
                    span_id=sid,
                    context=TraceContext(
                        session_id=session_context.session_id,
                        agent_id=resolved_agent_id,
                    ),
                    payload={
                        "status": status,
                        "usage_session_totals": session_usage_totals,
                    },
                )
            )
            self._last_usage_summary = self._build_usage_summary(
                local_usage_tracker=local_usage_tracker,
                session_usage_totals=session_usage_totals,
            )
        with active_tracer_scope(effective_runtime_tracer, TraceContext(session_id=sid)):
            _LOGGER.info(
                "session finished",
                extra={
                    "trace_kind": "runtime.session_runner.session_finished",
                    "trace_title": "SessionRunner session finished",
                    "trace_payload": {
                        "session_id": sid,
                        "agent_id": resolved_agent_id,
                        "status": status,
                        "usage_session_totals": session_usage_totals,
                    },
                },
            )
            if result is not None:
                payload = _agent_result_payload(result)
                _LOGGER.debug(
                    "SessionRunner.run_once exit",
                    extra={
                        "trace_kind": "runtime.session_runner.exit",
                        "trace_title": "SessionRunner.run_once exit",
                        "trace_payload": {
                            "session_id": sid,
                            "agent_id": resolved_agent_id,
                            "result": payload,
                        },
                    },
                )
        if run_error is not None:
            raise run_error
        assert result is not None
        return _agent_result_payload(result)

    def suite_teardown_if_any(self) -> None:
        """Invoke ``suite_teardown`` on the last loaded setup module, if present."""
        if self._suite_teardown_done:
            return
        mod = self._last_setup_module
        ctx = self._last_session_context
        if mod is None or ctx is None or not hasattr(mod, "suite_teardown"):
            return
        try:
            mod.suite_teardown(ctx)
        except Exception as exc:
            _LOGGER.warning("suite_teardown failed: %s", exc, exc_info=True)
        self._suite_teardown_done = True
