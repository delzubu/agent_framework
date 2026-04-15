"""Publish structured runtime.* trace events from agent lifecycle hooks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agent_framework.agents.agent_behavior import AgentBehavior
from agent_framework.agents.agent_decision import AgentDecision
from agent_framework.agents.agent_end_event import AgentEndEvent
from agent_framework.agents.agent_start_event import AgentStartEvent
from agent_framework.agents.model_end_event import ModelEndEvent
from agent_framework.agents.model_start_event import ModelStartEvent
from agent_framework.agents.skill_end_event import SkillEndEvent
from agent_framework.agents.skill_start_event import SkillStartEvent
from agent_framework.agents.subagent_end_event import SubagentEndEvent
from agent_framework.agents.subagent_start_event import SubagentStartEvent
from agent_framework.agents.tool_end_event import ToolEndEvent
from agent_framework.agents.tool_start_event import ToolStartEvent
from agent_framework.tracing import NullRuntimeTracer, TraceContext

if TYPE_CHECKING:
    from agent_framework.agents.agent import Agent
    from agent_framework.agents.agent_host_protocol import AgentHostProtocol
    from agent_framework.agents.agent_hook_decision import AgentHookDecision
    from agent_framework.agents.agent_result import AgentResult
    from agent_framework.agents.agent_run import AgentRun
    from agent_framework.agents.agent_end_hook_decision import AgentEndHookDecision


def _merge_context(host: Any, **kwargs: Any) -> TraceContext:
    overlay = getattr(host, "trace_context_overlay", None)
    base: TraceContext = overlay if isinstance(overlay, TraceContext) else TraceContext()
    filtered = {k: v for k, v in kwargs.items() if v is not None}
    return base.merged(**filtered)


def _decision_summary(decision: AgentDecision) -> dict[str, Any]:
    return {
        "kind": decision.kind,
        "tool_name": decision.tool_name,
        "subagent_id": decision.subagent_id,
        "skill_name": decision.skill_name,
        "callback_intent": decision.callback_intent,
    }


class RuntimeTraceBehavior(AgentBehavior):
    """Emits runtime-channel TraceEvents via ``host.publish_trace_event`` when tracing is active."""

    _host: Any | None

    def __init__(self) -> None:
        self._host = None

    def attach(self, agent: Agent) -> None:
        agent.on_pre_agent += self._on_pre_agent
        agent.on_post_agent += self._on_post_agent
        agent.on_pre_tool += self._on_pre_tool
        agent.on_post_tool += self._on_post_tool
        agent.on_pre_subagent += self._on_pre_subagent
        agent.on_post_subagent += self._on_post_subagent
        agent.on_pre_skill += self._on_pre_skill
        agent.on_post_skill += self._on_post_skill
        agent.on_pre_model += self._on_pre_model
        agent.on_post_model += self._on_post_model

    def _tracing_host(self) -> Any | None:
        h = self._host
        if h is None:
            return None
        rt = getattr(h, "runtime_tracer", None)
        if rt is None or isinstance(rt, NullRuntimeTracer):
            return None
        pub = getattr(h, "publish_trace_event", None)
        return h if callable(pub) else None

    def before_run(
        self,
        agent: Agent,
        host: AgentHostProtocol,
        *,
        run: AgentRun,
        caller_id: str | None,
    ) -> AgentHookDecision | None:
        self._host = host
        h = self._tracing_host()
        if h is not None:
            h.publish_trace_event(
                kind="runtime.agent_started",
                title=f"Agent {agent.agent_id} started",
                span_id=run.run_id,
                payload={"caller_id": caller_id},
                context=_merge_context(h, run_id=run.run_id, agent_id=agent.agent_id, caller_id=caller_id),
            )
        return None

    def after_run(
        self,
        agent: Agent,
        host: AgentHostProtocol,
        *,
        run: AgentRun,
        caller_id: str | None,
        result: AgentResult,
    ) -> AgentEndHookDecision | AgentResult | None:
        h = self._tracing_host()
        if h is not None:
            h.publish_trace_event(
                kind="runtime.agent_finished",
                title=f"Agent {agent.agent_id} finished",
                span_id=run.run_id,
                payload={"status": result.status, "caller_id": caller_id},
                context=_merge_context(h, run_id=run.run_id, agent_id=agent.agent_id, caller_id=caller_id),
            )
        self._host = None
        return None

    def _on_pre_agent(self, event: AgentStartEvent) -> None:
        return None

    def _on_post_agent(self, event: AgentEndEvent) -> None:
        return None

    def _on_pre_tool(self, event: ToolStartEvent) -> None:
        h = self._tracing_host()
        if h is None:
            return None
        inv = event.invocation
        h.publish_trace_event(
            kind="runtime.tool_call_started",
            title=f"Tool {event.tool_name}",
            span_id=event.tool_call_id,
            parent_span_id=inv.run_id,
            payload={"tool_name": event.tool_name, "tool_input": event.tool_input},
            context=_merge_context(
                h,
                run_id=inv.run_id,
                agent_id=inv.agent_id,
                tool_name=event.tool_name,
                caller_id=inv.caller_id,
            ),
        )
        return None

    def _on_post_tool(self, event: ToolEndEvent) -> None:
        h = self._tracing_host()
        if h is None:
            return None
        inv = event.invocation
        preview = event.result[:500] + ("…" if len(event.result) > 500 else "")
        h.publish_trace_event(
            kind="runtime.tool_call_finished",
            title=f"Tool {event.tool_name} done",
            span_id=event.tool_call_id,
            parent_span_id=inv.run_id,
            payload={"tool_name": event.tool_name, "result_preview": preview},
            context=_merge_context(
                h,
                run_id=inv.run_id,
                agent_id=inv.agent_id,
                tool_name=event.tool_name,
                caller_id=inv.caller_id,
            ),
        )
        return None

    def _on_pre_subagent(self, event: SubagentStartEvent) -> None:
        h = self._tracing_host()
        if h is None:
            return None
        inv = event.invocation
        h.publish_trace_event(
            kind="runtime.subagent_call_started",
            title=f"Subagent {event.subagent_id}",
            span_id=event.subagent_call_id,
            parent_span_id=inv.run_id,
            payload={"subagent_id": event.subagent_id, "input": event.subagent_input},
            context=_merge_context(
                h,
                run_id=inv.run_id,
                agent_id=inv.agent_id,
                subagent_id=event.subagent_id,
                caller_id=inv.caller_id,
            ),
        )
        return None

    def _on_post_subagent(self, event: SubagentEndEvent) -> None:
        h = self._tracing_host()
        if h is None:
            return None
        inv = event.invocation
        h.publish_trace_event(
            kind="runtime.subagent_call_finished",
            title=f"Subagent {event.subagent_id} done",
            span_id=event.subagent_call_id,
            parent_span_id=inv.run_id,
            payload={"subagent_id": event.subagent_id, "status": event.result.status},
            context=_merge_context(
                h,
                run_id=inv.run_id,
                agent_id=inv.agent_id,
                subagent_id=event.subagent_id,
                caller_id=inv.caller_id,
            ),
        )
        return None

    def _on_pre_skill(self, event: SkillStartEvent) -> None:
        h = self._tracing_host()
        if h is None:
            return None
        inv = event.invocation
        h.publish_trace_event(
            kind="runtime.skill_invoked",
            title=f"Skill {event.skill_name}",
            span_id=f"{inv.run_id}:skill:{event.skill_name}",
            parent_span_id=inv.run_id,
            payload={"skill_name": event.skill_name, "parameters": event.parameters},
            context=_merge_context(h, run_id=inv.run_id, agent_id=inv.agent_id, caller_id=inv.caller_id),
        )
        return None

    def _on_post_skill(self, event: SkillEndEvent) -> None:
        h = self._tracing_host()
        if h is None:
            return None
        inv = event.invocation
        h.publish_trace_event(
            kind="runtime.skill_injected",
            title=f"Skill {event.skill_name} injected",
            span_id=f"{inv.run_id}:skill:{event.skill_name}",
            parent_span_id=inv.run_id,
            payload={"skill_name": event.skill_name},
            context=_merge_context(h, run_id=inv.run_id, agent_id=inv.agent_id, caller_id=inv.caller_id),
        )
        return None

    def _on_pre_model(self, event: ModelStartEvent) -> None:
        h = self._tracing_host()
        if h is None:
            return None
        inv = event.invocation
        model_names: tuple[str, ...] = ()
        provider_name = ""
        temperature = 0.0
        if hasattr(h, "get_agent"):
            try:
                agent = h.get_agent(inv.agent_id)
                model_names = tuple(agent.model_names)
                provider_name = str(agent.provider_name)
                temperature = float(agent.temperature)
            except Exception:
                pass
        summary = ", ".join(model_names) if model_names else ""
        h.publish_trace_event(
            kind="runtime.model_call_started",
            title=f"Model call ({inv.agent_id})",
            summary=summary,
            span_id=f"{inv.run_id}:model",
            parent_span_id=inv.run_id,
            payload={
                "model_names": list(model_names),
                "provider_name": provider_name,
                "temperature": temperature,
            },
            context=_merge_context(
                h,
                run_id=inv.run_id,
                agent_id=inv.agent_id,
                caller_id=inv.caller_id,
            ),
        )
        return None

    def _on_post_model(self, event: ModelEndEvent) -> None:
        h = self._tracing_host()
        if h is None:
            return None
        inv = event.invocation
        try:
            decision = AgentDecision.from_model_response(event.response)
            payload = _decision_summary(decision)
        except Exception:
            payload = {"error": "could_not_parse_decision"}
        h.publish_trace_event(
            kind="runtime.decision_made",
            title=f"Decision: {payload.get('kind', '?')}",
            summary=json.dumps(payload, ensure_ascii=False),
            span_id=str(uuid4()),
            parent_span_id=inv.run_id,
            payload=payload,
            context=_merge_context(h, run_id=inv.run_id, agent_id=inv.agent_id, caller_id=inv.caller_id),
        )
        return None


__all__ = ["RuntimeTraceBehavior"]
