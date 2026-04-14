"""Markdown-defined runnable agent."""

from __future__ import annotations

import importlib.util
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
import yaml

from agent_framework.errors import ModelDriverError
from agent_framework.model import (
    CapabilityDefinition,
    CapabilityParameter,
    DEFAULT_RESPONSE_MODE,
    ModelContext,
    ModelDriver,
    ModelResponse,
    assemble_system_prompt as _assemble_system_prompt,
    build_skills_catalog as _build_skills_catalog,
    runtime_prompt_source_paths as _runtime_prompt_source_paths,
)

from .agent_behavior import AgentBehavior
from .agent_decision import AgentDecision
from .agent_end_event import AgentEndEvent
from .agent_end_hook_decision import AgentEndHookDecision
from .agent_hook_decision import AgentHookDecision
from .agent_host_protocol import AgentHostProtocol
from .agent_invocation import AgentInvocation
from .agent_parameter import AgentParameter
from .agent_result import AgentResult
from .agent_run import AgentRun
from .agent_start_event import AgentStartEvent
from .helpers import (
    PLACEHOLDER_PATTERN as _PLACEHOLDER_PATTERN,
    apply_runtime_placeholders as _apply_runtime_placeholders,
    agent_to_capability_definition as _agent_to_capability_definition,
    decision_to_dict as _decision_to_dict,
    extract_prompt_value as _extract_prompt_value,
    load_runtime_metadata as _load_runtime_metadata,
    parse_allowed_tool_names as _parse_allowed_tool_names,
    parse_behavior_ids as _parse_behavior_ids,
    resolve_schema_path as _resolve_schema_path,
    split_markdown_sections as _split_markdown_sections,
    stringify_parameter_value as _stringify_parameter_value,
)
from .model_end_event import ModelEndEvent
from .model_start_event import ModelStartEvent
from .sequential_hook import SequentialHook
from .skill_end_event import SkillEndEvent
from .skill_start_event import SkillStartEvent
from .subagent_end_event import SubagentEndEvent
from .subagent_hook_decision import SubagentHookDecision
from .subagent_start_event import SubagentStartEvent
from .tool_end_event import ToolEndEvent
from .tool_hook_decision import ToolHookDecision
from .tool_start_event import ToolStartEvent

_LOGGER = logging.getLogger(__name__)


def _emit_context_updated(
    agent: "Agent", host: "AgentHostProtocol", run: AgentRun, message: dict[str, str], source: str
) -> None:
    from agent_framework.agent_event_publisher import agent_events

    agent_events.on_context_updated(
        host=host,
        run_id=run.run_id,
        agent_id=agent.agent_id,
        message=dict(message),
        source=source,
    )


@dataclass(slots=True)
class Agent:
    """Markdown-defined runnable agent.

    Attributes:
        agent_id: Stable runtime identifier for the agent.
        role: Human-readable role name.
        description: Caller-facing summary of what the agent does.
        system_prompt: Stable instruction block loaded from the Markdown file.
        user_prompt_template: Template rendered with invocation parameters.
        parameters: Declared invocation contract loaded from frontmatter.
        provider_name: Model provider selected for this agent.
        model_names: Ordered model list for this agent (first = highest priority).
        temperature: Sampling temperature passed to the model driver.
        allowed_tools: Tool names this agent may call.
        allowed_child_agents: Child agent ids this agent may invoke.
        allowed_skills: Future capability ids this agent may reference.
        can_query_caller: Whether the agent may request information from its
            caller at runtime.
        can_use_host_interaction: Whether the agent may ask the host for user
            input at runtime.
        onPreAgent: Sequential callbacks executed before the agent run starts.
        onPostAgent: Sequential callbacks executed after the agent run ends.
        onPreTool: Sequential callbacks executed before a tool call.
        onPostTool: Sequential callbacks executed after a tool call.
        onPreSubagent: Sequential callbacks executed before a child-agent call.
        onPostSubagent: Sequential callbacks executed after a child-agent call.
        onPreSkill: Sequential callbacks executed before a skill invocation.
        onPostSkill: Sequential callbacks executed after a skill invocation.
        behavior_ids: Optional ordered runtime behavior ids resolved from sidecar JSON.
        source_path: Source Markdown path used to load the agent definition.
    """

    agent_id: str
    role: str
    description: str
    system_prompt: str
    user_prompt_template: str
    parameters: tuple[AgentParameter, ...]
    provider_name: str
    model_names: tuple[str, ...]
    temperature: float = 0.2
    allowed_tools: tuple[str, ...] = ()
    allowed_child_agents: tuple[str, ...] = ()
    allowed_skills: tuple[str, ...] = ()
    can_query_caller: bool = True
    can_use_host_interaction: bool = True
    onPreAgent: SequentialHook = field(default_factory=SequentialHook)
    onPostAgent: SequentialHook = field(default_factory=SequentialHook)
    onPreTool: SequentialHook = field(default_factory=SequentialHook)
    onPostTool: SequentialHook = field(default_factory=SequentialHook)
    onPreSubagent: SequentialHook = field(default_factory=SequentialHook)
    onPostSubagent: SequentialHook = field(default_factory=SequentialHook)
    onPreSkill: SequentialHook = field(default_factory=SequentialHook)
    onPostSkill: SequentialHook = field(default_factory=SequentialHook)
    onPreModel: SequentialHook = field(default_factory=SequentialHook)
    onPostModel: SequentialHook = field(default_factory=SequentialHook)
    behavior_ids: tuple[str, ...] = ()
    behaviors: tuple[AgentBehavior, ...] = field(default=(), repr=False)
    source_path: Path | None = None
    terminal_tools: tuple[str, ...] = ()

    @classmethod
    def from_markdown(
        cls,
        path: str | Path,
        *,
        default_provider: str,
        default_model: tuple[str, ...],
        model_override: tuple[str, ...] | None = None,
    ) -> "Agent":
        """Load an agent definition from the Markdown file format."""
        source_path = Path(path).resolve()
        raw_text = source_path.read_text(encoding="utf-8")
        frontmatter, system_prompt, user_prompt_template = _split_markdown_sections(
            raw_text, source_path=source_path
        )
        metadata = yaml.safe_load(frontmatter) or {}
        runtime_metadata = _load_runtime_metadata(source_path)
        parameter_map = metadata.get("parameters", {}) or {}
        parameters = tuple(
            AgentParameter(
                name=name,
                description=str(spec.get("description", "")).strip(),
                required=bool(spec.get("required", True)),
                value_type=str(spec.get("type", "string")).strip(),
                default=spec.get("default"),
                schema_path=_resolve_schema_path(source_path, spec.get("schema")),
            )
            for name, spec in parameter_map.items()
        )
        behavior_ids = _parse_behavior_ids(runtime_metadata)
        if model_override is not None:
            model_names: tuple[str, ...] = model_override
        else:
            raw_model = runtime_metadata.get("model")
            if raw_model is not None:
                model_names = tuple(
                    m.strip() for m in str(raw_model).split(",") if m.strip()
                ) or default_model
            else:
                model_names = default_model
        agent = cls(
            agent_id=str(metadata.get("id") or metadata.get("name") or source_path.stem).strip(),
            role=str(metadata.get("role", source_path.stem)).strip(),
            description=str(metadata.get("description", "")).strip(),
            system_prompt=system_prompt.strip(),
            user_prompt_template=user_prompt_template.strip(),
            parameters=parameters,
            provider_name=str(runtime_metadata.get("provider", default_provider)).strip(),
            model_names=model_names,
            temperature=float(runtime_metadata.get("temperature", 0.2)),
            allowed_tools=_parse_allowed_tool_names(metadata.get("tools", []) or ()),
            allowed_child_agents=tuple(metadata.get("subagents", []) or ()),
            allowed_skills=tuple(metadata.get("skills", []) or ()),
            can_query_caller=bool(runtime_metadata.get("can_query_caller", True)),
            can_use_host_interaction=bool(runtime_metadata.get("can_use_host_interaction", True)),
            behavior_ids=behavior_ids,
            source_path=source_path,
            terminal_tools=tuple(metadata.get("terminal_tools", []) or ()),
        )
        agent._validate_template_contract()
        agent._attach_behaviors()
        return agent

    def get_parameter_spec(self) -> tuple[AgentParameter, ...]:
        """Expose the declared invocation contract for callers and tests."""
        return self.parameters

    def validate_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize invocation parameters against the contract."""
        spec_by_name = {item.name: item for item in self.parameters}
        unknown_keys = set(parameters) - set(spec_by_name)
        if unknown_keys:
            raise ValueError(f"Unknown parameters for {self.agent_id}: {sorted(unknown_keys)}")

        resolved: dict[str, Any] = {}
        for spec in self.parameters:
            if spec.name in parameters:
                resolved_value = parameters[spec.name]
                self._validate_parameter_value(spec, resolved_value)
                resolved[spec.name] = resolved_value
                continue
            if spec.default is not None:
                self._validate_parameter_value(spec, spec.default)
                resolved[spec.name] = spec.default
                continue
            if spec.required:
                raise ValueError(f"Missing required parameter '{spec.name}' for {self.agent_id}.")
            resolved[spec.name] = ""
        return resolved

    def render_user_prompt(self, parameters: dict[str, Any]) -> str:
        """Render the user prompt template using validated parameters."""
        resolved = self.validate_parameters(parameters)
        rendered = self.user_prompt_template
        for key, value in resolved.items():
            rendered = re.sub(
                rf"{{{{\s*{re.escape(key)}\s*}}}}",
                _stringify_parameter_value(value),
                rendered,
            )
        return rendered

    def try_parse_prompt_input(self, prompt: str) -> dict[str, Any] | None:
        """Try to recover declared parameter values from a composed prompt string.

        This is primarily used by the root host path so a user can pass the
        exact prompt text they want to send to the model while still allowing
        the runtime to validate extracted structured values.
        """
        extracted: dict[str, Any] = {}
        for spec in self.parameters:
            extracted_value = _extract_prompt_value(spec, prompt)
            if extracted_value is None:
                if spec.required:
                    return None
                continue
            extracted[spec.name] = extracted_value

        try:
            return self.validate_parameters(extracted)
        except ValueError:
            return None

    def run(
        self,
        *,
        host: "AgentHostProtocol",
        parameters: dict[str, Any] | None = None,
        caller_id: str | None = None,
        parent_run_id: str | None = None,
        rendered_prompt_override: str | None = None,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
        prompt_fragments: tuple[str, ...] | None = None,
    ) -> AgentResult:
        """Execute the agent loop for one invocation.

        Args:
            host: Runtime host supplying model access, I/O, tool calls, and
                subagent resolution.
            parameters: Optional structured seed parameters. These are helper
                values only; the prompt remains the authoritative invocation
                contract for extraction and validation.
            caller_id: Optional caller identifier used for callback requests.
            parent_run_id: When this run is a subagent, the parent agent's ``run_id``
                (used for trace/UI nesting; omit for root invocations).

        Returns:
            An `AgentResult` describing the completed invocation.
        """
        run = self._create_run(
            parameters or {},
            rendered_prompt_override=rendered_prompt_override,
            conversation_messages=conversation_messages,
            prompt_fragments=prompt_fragments,
        )
        # Bootstrap the invocation contract before any `before_run` behavior or
        # pre-agent hook executes. Those hooks act as gatekeepers and need access
        # to resolved parameters, missing required fields, and invalid values.
        self.refresh_parameter_state(run)
        from agent_framework.agent_event_publisher import agent_events

        initial_context = self.build_context(host=host, run=run)
        system_sources: list[str] = []
        if self.source_path is not None:
            system_sources.append(str(self.source_path))
        system_sources.extend(str(path) for path in _runtime_prompt_source_paths(initial_context.response_mode))
        user_sources = (str(self.source_path),) if self.source_path is not None else ()
        agent_events.audit_agent_call_started(
            host=host,
            run_id=run.run_id,
            parent_run_id=parent_run_id,
            caller_id=caller_id,
            agent_name=self.agent_id,
            system_prompt=_assemble_system_prompt(initial_context),
            system_prompt_sources=tuple(system_sources),
            user_prompt=initial_context.user_prompt,
            user_prompt_sources=user_sources,
        )
        try:
            early_result = self._run_pre_agent_hooks(host=host, run=run, caller_id=caller_id)
            if early_result is not None:
                return self._run_post_agent_hooks(
                    host=host,
                    run=run,
                    caller_id=caller_id,
                    result=early_result,
                )[0]
            while self.should_continue(run):
                # The base loop is intentionally small so subclasses can override
                # individual steps without replacing the lifecycle contract.
                self.before_iteration(run)
                decision = self.resolve_runtime_decision(run=run)
                if decision is None:
                    context = self.build_context(host=host, run=run)
                    decision = self.decide(host=host, run=run, context=context)
                outcome = self.dispatch_decision(host=host, run=run, decision=decision, caller_id=caller_id)
                self.after_iteration(run)
                if outcome is not None:
                    post_result, continue_run = self._run_post_agent_hooks(
                        host=host,
                        run=run,
                        caller_id=caller_id,
                        result=outcome,
                    )
                    if continue_run:
                        continue
                    return post_result
            return self._run_post_agent_hooks(
                host=host,
                run=run,
                caller_id=caller_id,
                result=self.complete_without_result(run),
            )[0]
        finally:
            agent_events.audit_agent_call_finished(host=host, run_id=run.run_id)

    def should_continue(self, run: AgentRun) -> bool:
        """Return whether another loop iteration should execute."""
        return True

    def before_iteration(self, run: AgentRun) -> None:
        """Hook executed before each model decision step."""
        run.history.append(f"before_iteration:{self.agent_id}")
        self.refresh_parameter_state(run)

    def resolve_runtime_decision(self, *, run: AgentRun) -> AgentDecision | None:
        """Return an internal runtime decision before consulting the model."""
        return None

    def build_context(self, *, host: "AgentHostProtocol", run: AgentRun) -> ModelContext:
        """Assemble the provider-facing model context for the current run."""
        system_prompt = _apply_runtime_placeholders(self.system_prompt, run.placeholder_values)
        prompt = _apply_runtime_placeholders(run.rendered_prompt, run.placeholder_values)
        if run.prompt_fragments:
            # System and helper augmentations stay outside the original template
            # and are appended separately from the transcript.
            prompt = f"{prompt}\n\n<augmentations>\n" + "\n".join(run.prompt_fragments) + "\n</augmentations>"
        resolve = getattr(host, "resolve_model_tool_definitions", None)
        if callable(resolve):
            tools = resolve(
                self.allowed_tools,
                agent_id=self.agent_id,
                run_id=run.run_id,
            )
        else:
            tools = tuple(host.get_tool(name).model_definition() for name in self.allowed_tools)
        if hasattr(host, "get_agent"):
            subagents = tuple(
                _agent_to_capability_definition(
                    host.get_agent(
                        name,
                        base_dir=self.source_path.parent if self.source_path is not None else None,
                    )
                )
                for name in self.allowed_child_agents
            )
        else:
            subagents = tuple(
                CapabilityDefinition(
                    capability_id=name,
                    description="",
                )
                for name in self.allowed_child_agents
            )
        skill_registry = getattr(host, "get_skill_registry", None)
        if callable(skill_registry):
            skill_defs = host.get_skill_registry().filter(self.allowed_skills)
            skills = tuple(
                CapabilityDefinition(capability_id=defn.name, description=defn.description, priority=defn.priority)
                for defn in skill_defs
            )
        else:
            skills = ()
        config = getattr(host, "config", None)
        max_tokens = getattr(config, "skills_catalog_max_tokens", 2000)
        skills_catalog = _build_skills_catalog(skills, max_tokens=max_tokens)
        message_history: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
            *(
                [{"role": "user", "content": skills_catalog}]
                if skills_catalog
                else []
            ),
            *run.conversation_messages,
        ]
        return ModelContext(
            system_prompt=system_prompt,
            user_prompt=prompt,
            messages=tuple(message_history),
            response_mode="json_object",
            tools=tools,
            subagents=subagents,
            skills=skills,
            run_id=run.run_id,
        )

    def decide(self, *, host: "AgentHostProtocol", run: AgentRun, context: ModelContext) -> AgentDecision:
        """Request and normalize the next decision from the configured model."""
        self._run_pre_model_hooks(run=run, caller_id=None, context=context)
        pre_model_hooks = getattr(host, "run_pre_model_hooks", None)
        if callable(pre_model_hooks):
            pre_model_hooks(ModelStartEvent(invocation=self._hook_invocation(run, None), context=context))
        try:
            response = host.get_model_driver(self).decide(
                agent_id=self.agent_id,
                provider_name=self.provider_name,
                model_names=self.model_names,
                temperature=self.temperature,
                context=context,
            )
        except BaseException as exc:
            from agent_framework.agent_event_publisher import agent_events

            sc = exc.status_code if isinstance(exc, ModelDriverError) else None
            ub = exc.upstream_body if isinstance(exc, ModelDriverError) else None
            agent_events.on_model_call_failed(
                host=host,
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=None,
                exc=exc,
                status_code=sc,
                upstream_body=ub,
            )
            raise
        self._run_post_model_hooks(run=run, caller_id=None, context=context, response=response)
        post_model_hooks = getattr(host, "run_post_model_hooks", None)
        if callable(post_model_hooks):
            post_model_hooks(
                ModelEndEvent(
                    invocation=self._hook_invocation(run, None),
                    context=context,
                    response=response,
                )
            )
        return AgentDecision.from_model_response(response)

    def dispatch_decision(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        decision: AgentDecision,
        caller_id: str | None,
    ) -> AgentResult | None:
        """Dispatch a normalized decision to the appropriate handler."""
        decision = self._normalize_decision_capabilities(decision)
        from agent_framework.agent_event_publisher import agent_events

        agent_events.audit_decision(host=host, run_id=run.run_id, agent_id=self.agent_id, decision=decision)
        handlers = {
            "final_message": self.handle_final_message,
            "callback": self.handle_callback,
            "call_subagent": self.handle_subagent_call,
            "call_tool": self.handle_tool_call,
            "invoke_skill": self.handle_skill_invocation,
        }
        handler = handlers.get(decision.kind)
        if handler is None:
            raise ValueError(f"Unsupported decision kind: {decision.kind}")
        run.transcript_entries.append(
            f"<assistant_decision>{_stringify_parameter_value(_decision_to_dict(decision))}</assistant_decision>"
        )
        run.conversation_messages.append(
            {"role": "assistant", "content": _stringify_parameter_value(_decision_to_dict(decision))}
        )
        _emit_context_updated(
            self,
            host,
            run,
            run.conversation_messages[-1],
            "assistant_decision",
        )
        return handler(host=host, run=run, decision=decision, caller_id=caller_id)

    def after_iteration(self, run: AgentRun) -> None:
        """Hook executed after each loop iteration."""
        run.history.append(f"after_iteration:{self.agent_id}")

    def complete_without_result(self, run: AgentRun) -> AgentResult:
        """Produce a fallback result if the loop exits without a final message."""
        return AgentResult(status="completed", message="", prompt=run.rendered_prompt)

    def handle_final_message(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        decision: AgentDecision,
        caller_id: str | None,
    ) -> AgentResult:
        """Return a completed result for a `final_message` decision."""
        return AgentResult(
            status="completed",
            message=decision.message,
            decision=decision,
            prompt=run.rendered_prompt,
        )

    def handle_callback(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        decision: AgentDecision,
        caller_id: str | None,
    ) -> AgentResult | None:
        """Handle all callback-style requests through one unified transport."""
        intent = decision.callback_intent or "information_request"
        parameter_name = str(decision.parameters.get("parameter_name", "")).strip()
        spec = self._parameter_spec_by_name().get(parameter_name) if parameter_name else None
        prompt = decision.message or (spec.description if spec is not None else "Please provide more information.")
        from agent_framework.agent_event_publisher import agent_events

        agent_events.on_callback_requested(
            host=host,
            run_id=run.run_id,
            agent_id=self.agent_id,
            caller_id=caller_id,
            intent=intent,
            prompt=prompt,
            to_caller=bool(caller_id and caller_id != "host" and self.can_query_caller),
        )
        answer = ""
        if caller_id and caller_id != "host" and self.can_query_caller:
            context = host.open_context(
                caller_id=self.agent_id,
                callee_id=caller_id,
                kind=f"callback:{intent}",
            )
            run.contexts.append(context)
            answer = host.resolve_callback(caller_id=caller_id, callee=self, prompt=prompt)
            context.status = "resolved"
            cb_event = {
                "type": "callback",
                "intent": intent,
                "target": f"caller:{caller_id}",
                "prompt": prompt,
                "response": answer,
            }
            agent_events.audit_callback(
                host=host,
                run_id=run.run_id,
                agent_id=self.agent_id,
                intent=intent,
                prompt=prompt,
                target=f"caller:{caller_id}",
                response=answer,
                event_dict=cb_event,
            )
            run.transcript_entries.append(f"<caller_request intent=\"{intent}\">{prompt}</caller_request>")
            run.transcript_entries.append(f"<caller_response>{answer}</caller_response>")
            run.conversation_messages.append({"role": "assistant", "content": prompt})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "callback_prompt")
            run.conversation_messages.append({"role": "user", "content": answer})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "callback_answer")
            agent_events.on_callback_answered(
                host=host,
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=caller_id,
                intent=intent,
                target=f"caller:{caller_id}",
                answer=answer,
            )
        else:
            if not self.can_use_host_interaction:
                raise ValueError(f"{self.agent_id} cannot request callback intent '{intent}' from host.")
            context = host.open_context(
                caller_id=self.agent_id,
                callee_id="host",
                kind=f"callback:{intent}",
            )
            run.contexts.append(context)
            answer = host.request_user_input(prompt)
            context.status = "resolved"
            cb_event = {
                "type": "callback",
                "intent": intent,
                "target": "host",
                "prompt": prompt,
                "response": answer,
            }
            agent_events.audit_callback(
                host=host,
                run_id=run.run_id,
                agent_id=self.agent_id,
                intent=intent,
                prompt=prompt,
                target="host",
                response=answer,
                event_dict=cb_event,
            )
            run.transcript_entries.append(f"<host_request intent=\"{intent}\">{prompt}</host_request>")
            run.transcript_entries.append(f"<host_response>{answer}</host_response>")
            run.conversation_messages.append({"role": "assistant", "content": prompt})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "callback_prompt")
            run.conversation_messages.append({"role": "user", "content": answer})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "callback_answer")
            agent_events.on_callback_answered(
                host=host,
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=caller_id,
                intent=intent,
                target="host",
                answer=answer,
            )

        if intent == "information_request" and parameter_name:
            run.prompt_fragments.append(f"<{parameter_name}>{answer}</{parameter_name}>")
        else:
            run.prompt_fragments.append(f"<callback_response intent=\"{intent}\">{answer}</callback_response>")
        return None

    def handle_subagent_call(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        decision: AgentDecision,
        caller_id: str | None,
    ) -> AgentResult | None:
        """Handle a child-agent call and merge its result into this run."""
        if not decision.subagent_id:
            error_text = (
                "call_subagent requires subagent_id. "
                f"Legal subagent ids: {sorted(self.allowed_child_agents)}."
            )
            run.prompt_fragments.append(f"<subagent_error>{error_text}</subagent_error>")
            run.transcript_entries.append(f"<subagent_error>{error_text}</subagent_error>")
            run.conversation_messages.append({"role": "user", "content": error_text})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_validation_error")
            return None
        if decision.subagent_id not in self.allowed_child_agents:
            error_text = (
                f"{self.agent_id} is not allowed to call subagent {decision.subagent_id}. "
                f"Legal subagent ids: {sorted(self.allowed_child_agents)}."
            )
            run.prompt_fragments.append(f"<subagent_error>{error_text}</subagent_error>")
            run.transcript_entries.append(f"<subagent_error>{error_text}</subagent_error>")
            run.conversation_messages.append({"role": "user", "content": error_text})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_validation_error")
            return None

        subagent_call_id = str(uuid4())
        event = SubagentStartEvent(
            invocation=self._hook_invocation(run, caller_id),
            subagent_call_id=subagent_call_id,
            subagent_id=decision.subagent_id,
            subagent_input=dict(decision.parameters),
            decision=decision,
        )
        pre_decision = self._run_pre_subagent_hooks(
            host=host,
            run=run,
            caller_id=caller_id,
            event=event,
        )
        if pre_decision.final_result is not None:
            return pre_decision.final_result
        if not pre_decision.continue_run:
            return AgentResult(status="stopped", message="", prompt=run.rendered_prompt)

        subagent_id = pre_decision.updated_subagent_id or event.subagent_id
        subagent_input = pre_decision.updated_subagent_input or dict(event.subagent_input)
        from agent_framework.agent_event_publisher import agent_events

        agent_events.audit_named_event(
            host=host,
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "subagent_call",
                "subagent_id": subagent_id,
                "parameters": dict(subagent_input),
            },
        )
        run.transcript_entries.append(
            f"<subagent_call id=\"{subagent_id}\">{_stringify_parameter_value(subagent_input)}</subagent_call>"
        )
        run.conversation_messages.append(
            {
                "role": "assistant",
                "content": f"Subagent call {subagent_id}: {_stringify_parameter_value(subagent_input)}",
            }
        )
        _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_call")
        try:
            result = host.call_subagent(
                caller=self,
                callee_id=subagent_id,
                parameters=subagent_input,
                parent_run_id=run.run_id,
            )
        except Exception as exc:
            agent_events.audit_named_event(
                host=host,
                run_id=run.run_id,
                agent_id=self.agent_id,
                event={
                    "type": "subagent_error",
                    "subagent_id": subagent_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            if pre_decision.system_message:
                run.prompt_fragments.append(f"<system_message>{pre_decision.system_message}</system_message>")
            run.prompt_fragments.append(
                f"<subagent_error id=\"{subagent_id}\">{type(exc).__name__}: {exc}</subagent_error>"
            )
            run.transcript_entries.append(
                f"<subagent_error id=\"{subagent_id}\">{type(exc).__name__}: {exc}</subagent_error>"
            )
            run.conversation_messages.append(
                {"role": "user", "content": f"Subagent error {subagent_id}: {type(exc).__name__}: {exc}"}
            )
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_error")
            return None
        self._run_post_subagent_hooks(
            host=host,
            run=run,
            caller_id=caller_id,
            event=SubagentEndEvent(
                invocation=event.invocation,
                subagent_call_id=subagent_call_id,
                subagent_id=subagent_id,
                subagent_input=subagent_input,
                result=result,
            ),
        )
        if pre_decision.system_message:
            run.prompt_fragments.append(f"<system_message>{pre_decision.system_message}</system_message>")
        agent_events.audit_named_event(
            host=host,
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "subagent_result",
                "subagent_id": subagent_id,
                "result": result.message,
                "status": result.status,
            },
        )
        run.transcript_entries.append(
            f"<subagent_result id=\"{subagent_id}\">{result.message}</subagent_result>"
        )
        run.conversation_messages.append(
            {"role": "user", "content": f"Subagent result {subagent_id}: {result.message}"}
        )
        run.prompt_fragments.append(
            f"<subagent_result id=\"{subagent_id}\">{result.message}</subagent_result>"
        )
        return None

    def handle_skill_invocation(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        decision: AgentDecision,
        caller_id: str | None,
    ) -> AgentResult | None:
        """Load and inject skill content into the conversation, then continue the loop."""
        from agent_framework.skill import SkillLoader

        skill_name = decision.skill_name or ""
        skill_registry = getattr(host, "get_skill_registry", None)

        # 1. Resolve definition
        try:
            skill_def = host.get_skill_registry().get(skill_name) if callable(skill_registry) else None
            if skill_def is None:
                raise KeyError(skill_name)
        except KeyError:
            error_text = f"Unknown skill: {skill_name!r}. Check available skills in <available_skills>."
            run.conversation_messages.append({"role": "user", "content": f"<skill_error>{error_text}</skill_error>"})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "skill_error")
            return None

        # 2. Validate allowed
        if self.allowed_skills and skill_def.name not in self.allowed_skills:
            error_text = f"Skill {skill_name!r} is not in this agent's allowed skills: {sorted(self.allowed_skills)}."
            run.conversation_messages.append({"role": "user", "content": f"<skill_error>{error_text}</skill_error>"})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "skill_error")
            return None

        # 3. Pre-skill hook
        self._run_pre_skill_hooks(
            run=run,
            event=SkillStartEvent(
                invocation=self._hook_invocation(run, caller_id),
                skill_name=skill_def.name,
                parameters=dict(decision.parameters),
            ),
        )

        # 4. Load skill content
        content = SkillLoader().load(skill_def)

        # 5. Build injected fragment with base directory
        base_dir_line = f"\nBase directory: {content.definition.skill_dir}"
        inventory_lines = "\n".join(f"- {r.relative_path}" for r in content.inventory)
        inventory_block = (
            f"\n\n<skill_files>\n{inventory_lines}\n</skill_files>"
        ) if content.inventory else ""
        skill_fragment = (
            f'<skill_invocation_result name="{skill_def.name}">\n'
            f"{content.body}"
            f"{base_dir_line}"
            f"{inventory_block}\n"
            f"</skill_invocation_result>"
        )

        # 6. Inject skill content as a user message (dispatch already added the assistant message)
        run.conversation_messages.append({"role": "user", "content": skill_fragment})
        _emit_context_updated(self, host, run, run.conversation_messages[-1], "skill_injection")

        from agent_framework.agent_event_publisher import agent_events

        agent_events.audit_skill_invocation(
            host=host,
            run_id=run.run_id,
            agent_id=self.agent_id,
            skill_name=skill_def.name,
            parameters=dict(decision.parameters),
            inventory=[r.relative_path for r in content.inventory],
        )

        # 7. Post-skill hook
        self._run_post_skill_hooks(
            run=run,
            event=SkillEndEvent(
                invocation=self._hook_invocation(run, caller_id),
                skill_name=skill_def.name,
                parameters=dict(decision.parameters),
                content=content,
            ),
        )

        return None  # continue loop — model now has skill instructions in context

    def handle_tool_call(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        decision: AgentDecision,
        caller_id: str | None,
    ) -> AgentResult | None:
        """Handle a tool call and append the tool output as an augmentation."""
        if not decision.tool_name:
            error_text = f"call_tool requires tool_name. Legal tool names: {sorted(self.allowed_tools)}."
            run.prompt_fragments.append(f"<tool_error>{error_text}</tool_error>")
            run.transcript_entries.append(f"<tool_error>{error_text}</tool_error>")
            run.conversation_messages.append({"role": "user", "content": error_text})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "tool_validation_error")
            return None
        if decision.tool_name not in self.allowed_tools:
            error_text = (
                f"{self.agent_id} is not allowed to call tool {decision.tool_name}. "
                f"Legal tool names: {sorted(self.allowed_tools)}."
            )
            run.prompt_fragments.append(f"<tool_error>{error_text}</tool_error>")
            run.transcript_entries.append(f"<tool_error>{error_text}</tool_error>")
            run.conversation_messages.append({"role": "user", "content": error_text})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "tool_validation_error")
            return None

        # Terminal tool check — exit loop immediately without executing the tool
        if decision.tool_name in self.terminal_tools:
            return AgentResult(
                status="completed",
                message=json.dumps(decision.parameters) if decision.parameters else "",
                decision=decision,
                prompt=run.rendered_prompt,
            )

        tool_call_id = str(uuid4())
        event = ToolStartEvent(
            invocation=self._hook_invocation(run, caller_id),
            tool_call_id=tool_call_id,
            tool_name=decision.tool_name,
            tool_input=dict(decision.parameters),
            decision=decision,
        )
        pre_decision = self._run_pre_tool_hooks(
            host=host,
            run=run,
            caller_id=caller_id,
            event=event,
        )
        if pre_decision.final_result is not None:
            return pre_decision.final_result
        if not pre_decision.continue_run:
            return AgentResult(status="stopped", message="", prompt=run.rendered_prompt)

        tool_input = pre_decision.updated_tool_input or dict(event.tool_input)
        from agent_framework.agent_event_publisher import agent_events

        agent_events.audit_named_event(
            host=host,
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "tool_call",
                "tool_name": event.tool_name,
                "parameters": dict(tool_input),
            },
        )
        run.transcript_entries.append(
            f"<tool_call name=\"{event.tool_name}\">{_stringify_parameter_value(tool_input)}</tool_call>"
        )
        run.conversation_messages.append(
            {"role": "assistant", "content": f"Tool call {event.tool_name}: {_stringify_parameter_value(tool_input)}"}
        )
        _emit_context_updated(self, host, run, run.conversation_messages[-1], "tool_call")
        try:
            result = host.execute_tool(event.tool_name, tool_input)
        except Exception as exc:
            agent_events.on_tool_execution_failed(
                host=host,
                run_id=run.run_id,
                agent_id=self.agent_id,
                tool_name=event.tool_name,
                exc=exc,
            )
            agent_events.audit_named_event(
                host=host,
                run_id=run.run_id,
                agent_id=self.agent_id,
                event={
                    "type": "tool_error",
                    "tool_name": event.tool_name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            if pre_decision.system_message:
                run.prompt_fragments.append(f"<system_message>{pre_decision.system_message}</system_message>")
            run.prompt_fragments.append(
                f"<tool_error name=\"{event.tool_name}\">{type(exc).__name__}: {exc}</tool_error>"
            )
            run.transcript_entries.append(
                f"<tool_error name=\"{event.tool_name}\">{type(exc).__name__}: {exc}</tool_error>"
            )
            run.conversation_messages.append(
                {"role": "user", "content": f"Tool error {event.tool_name}: {type(exc).__name__}: {exc}"}
            )
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "tool_error")
            return None
        self._run_post_tool_hooks(
            host=host,
            run=run,
            caller_id=caller_id,
            event=ToolEndEvent(
                invocation=event.invocation,
                tool_call_id=tool_call_id,
                tool_name=event.tool_name,
                tool_input=tool_input,
                result=result,
            ),
        )
        if pre_decision.system_message:
            run.prompt_fragments.append(f"<system_message>{pre_decision.system_message}</system_message>")
        agent_events.audit_named_event(
            host=host,
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "tool_result",
                "tool_name": event.tool_name,
                "result": result,
            },
        )
        run.transcript_entries.append(f"<tool_result name=\"{event.tool_name}\">{result}</tool_result>")
        run.conversation_messages.append({"role": "user", "content": f"Tool result {event.tool_name}: {result}"})
        _emit_context_updated(self, host, run, run.conversation_messages[-1], "tool_result")
        run.prompt_fragments.append(f"<tool_result name=\"{event.tool_name}\">{result}</tool_result>")
        return None

    def _create_run(
        self,
        parameters: dict[str, Any],
        *,
        rendered_prompt_override: str | None = None,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
        prompt_fragments: tuple[str, ...] | None = None,
    ) -> AgentRun:
        """Create runtime state for a new invocation."""
        seed_parameters = dict(parameters)
        return AgentRun(
            run_id=str(uuid4()),
            # Persist the base prompt per run so execution is traceable even if
            # the agent definition changes later.
            rendered_prompt=rendered_prompt_override or self._render_seed_prompt(seed_parameters),
            seed_parameters=seed_parameters,
            parameter_values={},
            placeholder_values={},
            prompt_fragments=list(prompt_fragments or ()),
            conversation_messages=list(conversation_messages or ()),
        )

    def refresh_parameter_state(self, run: AgentRun) -> None:
        """Extract and validate parameter values from the current prompt state."""
        prompt = self._prompt_for_parameter_extraction(run)
        resolved: dict[str, Any] = {}
        missing: list[str] = []
        invalid: dict[str, str] = {}
        for spec in self.parameters:
            value = _extract_prompt_value(spec, prompt)
            if value is None and spec.name in run.seed_parameters:
                value = run.seed_parameters[spec.name]
            if value is None:
                if spec.default is not None:
                    value = spec.default
                elif spec.required:
                    missing.append(spec.name)
                    continue
                else:
                    continue
            try:
                self._validate_parameter_value(spec, value)
            except ValueError as exc:
                invalid[spec.name] = str(exc)
                continue
            resolved[spec.name] = value
        run.parameter_values = resolved
        run.missing_parameters = missing
        run.invalid_parameters = invalid

    def _run_pre_tool_hooks(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        event: ToolStartEvent,
    ) -> ToolHookDecision:
        """Execute all subscribed pre-tool callbacks sequentially."""
        run.history.append(f"before_tool:{event.tool_name}")
        decision = ToolHookDecision()
        for callback in self.onPreTool:
            outcome = callback(event)
            if outcome is None:
                continue
            if not isinstance(outcome, ToolHookDecision):
                raise TypeError("Pre-tool callbacks must return ToolHookDecision or None.")
            if outcome.system_message:
                decision = ToolHookDecision(
                    continue_run=outcome.continue_run,
                    updated_tool_input=outcome.updated_tool_input or decision.updated_tool_input,
                    system_message=outcome.system_message,
                    final_result=outcome.final_result,
                )
            else:
                decision = ToolHookDecision(
                    continue_run=outcome.continue_run,
                    updated_tool_input=outcome.updated_tool_input or decision.updated_tool_input,
                    system_message=decision.system_message,
                    final_result=outcome.final_result,
                )
            if not decision.continue_run or decision.final_result is not None:
                break
        return decision

    def _run_post_tool_hooks(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        event: ToolEndEvent,
    ) -> None:
        """Execute all subscribed post-tool callbacks sequentially."""
        run.history.append(f"after_tool:{event.tool_name}")
        for callback in self.onPostTool:
            callback(event)

    def _run_pre_subagent_hooks(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        event: SubagentStartEvent,
    ) -> SubagentHookDecision:
        """Execute all subscribed pre-subagent callbacks sequentially."""
        run.history.append(f"before_subagent:{event.subagent_id}")
        decision = SubagentHookDecision()
        for callback in self.onPreSubagent:
            outcome = callback(event)
            if outcome is None:
                continue
            if not isinstance(outcome, SubagentHookDecision):
                raise TypeError("Pre-subagent callbacks must return SubagentHookDecision or None.")
            if outcome.system_message:
                decision = SubagentHookDecision(
                    continue_run=outcome.continue_run,
                    updated_subagent_id=outcome.updated_subagent_id or decision.updated_subagent_id,
                    updated_subagent_input=outcome.updated_subagent_input or decision.updated_subagent_input,
                    system_message=outcome.system_message,
                    final_result=outcome.final_result,
                )
            else:
                decision = SubagentHookDecision(
                    continue_run=outcome.continue_run,
                    updated_subagent_id=outcome.updated_subagent_id or decision.updated_subagent_id,
                    updated_subagent_input=outcome.updated_subagent_input or decision.updated_subagent_input,
                    system_message=decision.system_message,
                    final_result=outcome.final_result,
                )
            if not decision.continue_run or decision.final_result is not None:
                break
        return decision

    def _run_post_subagent_hooks(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        event: SubagentEndEvent,
    ) -> None:
        """Execute all subscribed post-subagent callbacks sequentially."""
        run.history.append(f"after_subagent:{event.subagent_id}")
        for callback in self.onPostSubagent:
            callback(event)

    def _run_pre_skill_hooks(self, *, run: AgentRun, event: SkillStartEvent) -> None:
        """Execute all subscribed pre-skill callbacks sequentially."""
        run.history.append(f"before_skill:{event.skill_name}")
        for callback in self.onPreSkill:
            callback(event)

    def _run_post_skill_hooks(self, *, run: AgentRun, event: SkillEndEvent) -> None:
        """Execute all subscribed post-skill callbacks sequentially."""
        run.history.append(f"after_skill:{event.skill_name}")
        for callback in self.onPostSkill:
            callback(event)

    def _run_pre_model_hooks(
        self,
        *,
        run: AgentRun,
        caller_id: str | None,
        context: ModelContext,
    ) -> None:
        """Execute all subscribed pre-model callbacks sequentially."""
        run.history.append(f"before_model:{self.agent_id}")
        event = ModelStartEvent(invocation=self._hook_invocation(run, caller_id), context=context)
        for callback in self.onPreModel:
            callback(event)

    def _run_post_model_hooks(
        self,
        *,
        run: AgentRun,
        caller_id: str | None,
        context: ModelContext,
        response: ModelResponse,
    ) -> None:
        """Execute all subscribed post-model callbacks sequentially."""
        run.history.append(f"after_model:{self.agent_id}")
        event = ModelEndEvent(
            invocation=self._hook_invocation(run, caller_id),
            context=context,
            response=response,
        )
        for callback in self.onPostModel:
            callback(event)

    def _validate_template_contract(self) -> None:
        """Ensure all template placeholders are declared in frontmatter."""
        placeholders = set(_PLACEHOLDER_PATTERN.findall(self.user_prompt_template))
        declared = {item.name for item in self.parameters}
        undeclared = placeholders - declared
        if undeclared:
            raise ValueError(
                f"Agent {self.agent_id} template uses undeclared parameters: {sorted(undeclared)}"
            )

    def _attach_behaviors(self) -> None:
        """Load and attach optional behavior implementations in configured order."""
        if not self.behavior_ids:
            return
        if self.source_path is None:
            raise ValueError(f"Cannot resolve behaviors for {self.agent_id} without source path.")

        attached: list[AgentBehavior] = []
        for behavior_id in self.behavior_ids:
            behavior_path = self._resolve_behavior_path(behavior_id)
            if not behavior_path.exists():
                raise ValueError(
                    f"Behavior '{behavior_id}' for {self.agent_id} was not found at {behavior_path}."
                )

            module_name = f"agent_behavior_{self.agent_id}_{behavior_id}_{uuid4().hex}"
            spec = importlib.util.spec_from_file_location(module_name, behavior_path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Could not load behavior module from {behavior_path}.")

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            build_behavior = getattr(module, "build_behavior", None)
            if not callable(build_behavior):
                raise ValueError(
                    f"Behavior module {behavior_path} must export a callable 'build_behavior'."
                )

            behavior = build_behavior()
            if not isinstance(behavior, AgentBehavior):
                raise ValueError(
                    f"Behavior module {behavior_path} returned {type(behavior).__name__}, expected AgentBehavior."
                )
            behavior.attach(self)
            attached.append(behavior)
        self.behaviors = tuple(attached)

    def _resolve_behavior_path(self, behavior_id: str) -> Path:
        """Resolve a behavior id to either an agent-local or shared behavior module."""
        if self.source_path is None:
            raise ValueError(f"Cannot resolve behavior for {self.agent_id} without source path.")

        local_path = self.source_path.with_name(f"{behavior_id}.py").resolve()
        if local_path.exists():
            return local_path

        shared_path = self.source_path.parent.parent / "behaviors" / f"{behavior_id}.py"
        return shared_path.resolve()

    def respond_to_callback(
        self,
        host: "AgentHostProtocol",
        *,
        callee_id: str,
        prompt: str,
    ) -> str | None:
        """Return an agent-specific callback response if any behavior provides one."""
        for behavior in self.behaviors:
            response = behavior.respond_to_callback(self, host, callee_id=callee_id, prompt=prompt)
            if response is not None:
                return response
        return None

    def _hook_invocation(self, run: AgentRun, caller_id: str | None) -> AgentInvocation:
        """Build shared invocation metadata for lifecycle hooks."""
        return AgentInvocation(
            run_id=run.run_id,
            agent_id=self.agent_id,
            caller_id=caller_id,
            parameters=dict(run.parameter_values),
            rendered_prompt=run.rendered_prompt,
        )

    def _run_pre_agent_hooks(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
    ) -> AgentResult | None:
        """Execute all subscribed pre-agent callbacks sequentially."""
        run.history.append(f"before_agent:{self.agent_id}")
        for behavior in self.behaviors:
            outcome = behavior.before_run(self, host, run=run, caller_id=caller_id)
            if outcome is None:
                continue
            if not isinstance(outcome, AgentHookDecision):
                raise TypeError("Behavior before_run must return AgentHookDecision or None.")
            if outcome.system_message:
                run.prompt_fragments.append(f"<system_message>{outcome.system_message}</system_message>")
            if outcome.final_result is not None:
                return outcome.final_result
            if not outcome.continue_run:
                return AgentResult(status="stopped", message="", prompt=run.rendered_prompt)

        event = AgentStartEvent(invocation=self._hook_invocation(run, caller_id))
        for callback in self.onPreAgent:
            outcome = callback(event)
            if outcome is None:
                continue
            if not isinstance(outcome, AgentHookDecision):
                raise TypeError("Pre-agent callbacks must return AgentHookDecision or None.")
            if outcome.system_message:
                run.prompt_fragments.append(f"<system_message>{outcome.system_message}</system_message>")
            if outcome.final_result is not None:
                return outcome.final_result
            if not outcome.continue_run:
                return AgentResult(status="stopped", message="", prompt=run.rendered_prompt)
        return None

    def _run_post_agent_hooks(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        result: AgentResult,
    ) -> tuple[AgentResult, bool]:
        """Execute all subscribed post-agent callbacks sequentially."""
        run.history.append(f"after_agent:{self.agent_id}")
        current_result = result
        continue_run = False
        for behavior in self.behaviors:
            outcome = behavior.after_run(self, host, run=run, caller_id=caller_id, result=current_result)
            if outcome is None:
                continue
            if isinstance(outcome, AgentResult):
                current_result = outcome
                continue
            if not isinstance(outcome, AgentEndHookDecision):
                raise TypeError("Behavior after_run must return AgentEndHookDecision, AgentResult, or None.")
            for fragment in outcome.prompt_fragments:
                self._upsert_prompt_fragment(run, fragment)
            for fragment in outcome.append_prompt_fragments:
                run.prompt_fragments.append(fragment)
            if outcome.final_result is not None:
                current_result = outcome.final_result
            if outcome.continue_run:
                continue_run = True

        for callback in self.onPostAgent:
            event = AgentEndEvent(invocation=self._hook_invocation(run, caller_id), result=current_result)
            outcome = callback(event)
            if isinstance(outcome, AgentResult):
                current_result = outcome
        return current_result, continue_run

    def _upsert_prompt_fragment(self, run: AgentRun, fragment: str) -> None:
        """Replace an existing prompt fragment with the same tag name, else append."""
        tag_name = self._fragment_tag_name(fragment)
        if tag_name is None:
            run.prompt_fragments.append(fragment)
            return
        replacement_index: int | None = None
        for index, existing in enumerate(run.prompt_fragments):
            if self._fragment_tag_name(existing) == tag_name:
                replacement_index = index
        if replacement_index is None:
            run.prompt_fragments.append(fragment)
            return
        run.prompt_fragments[replacement_index] = fragment

    @staticmethod
    def _fragment_tag_name(fragment: str) -> str | None:
        """Return the leading XML-like tag name used for prompt-fragment replacement."""
        match = re.match(r"\s*<([a-zA-Z0-9_:-]+)(?:\s|>)", fragment)
        if match is None:
            return None
        return match.group(1)

    def _validate_parameter_value(self, spec: AgentParameter, value: Any) -> None:
        """Validate one parameter value against the declared type and schema."""
        type_checks = {
            "string": lambda candidate: isinstance(candidate, (str, dict, list)),
            "integer": lambda candidate: isinstance(candidate, int) and not isinstance(candidate, bool),
            "number": lambda candidate: isinstance(candidate, (int, float)) and not isinstance(candidate, bool),
            "boolean": lambda candidate: isinstance(candidate, bool),
            "object": lambda candidate: isinstance(candidate, (dict, str)),
            "array": lambda candidate: isinstance(candidate, list),
        }
        checker = type_checks.get(spec.value_type)
        if checker is not None and not checker(value):
            raise ValueError(
                f"Parameter '{spec.name}' for {self.agent_id} must be of type {spec.value_type}."
            )

        if spec.schema_path is not None and not isinstance(value, str):
            schema = json.loads(spec.schema_path.read_text(encoding="utf-8"))
            try:
                validate_json_schema(instance=value, schema=schema)
            except JsonSchemaValidationError as exc:
                raise ValueError(
                    f"Parameter '{spec.name}' for {self.agent_id} failed schema validation: {exc.message}"
                ) from exc

    def _render_seed_prompt(self, parameters: dict[str, Any]) -> str:
        """Render a prompt using only the seed values supplied so far."""
        rendered = self.user_prompt_template
        for key, value in parameters.items():
            rendered = re.sub(
                rf"{{{{\s*{re.escape(key)}\s*}}}}",
                _stringify_parameter_value(value),
                rendered,
            )
        return rendered

    def _prompt_for_parameter_extraction(self, run: AgentRun) -> str:
        """Return the current prompt text used to recover parameter values."""
        if not run.prompt_fragments:
            return run.rendered_prompt
        return f"{run.rendered_prompt}\n\n" + "\n".join(run.prompt_fragments)

    def _parameter_spec_by_name(self) -> dict[str, AgentParameter]:
        """Return the parameter spec keyed by parameter name."""
        return {item.name: item for item in self.parameters}

    def _normalize_decision_capabilities(self, decision: AgentDecision) -> AgentDecision:
        """Repair tool vs subagent *slots* when they disagree with declared capabilities.

        **Intentional and confirmed:** This is not open-ended semantic inference on unknown
        ``kind`` strings. Only the branches below apply: the model used an allowed ``kind``
        but put a declared child-agent id or tool name in the wrong field, or used
        ``callback`` while filling a slot that uniquely matches a declared capability.
        Repair is keyed solely against ``allowed_child_agents`` / ``allowed_tools``.

        Both ``subagent_id`` and ``tool_name`` non-empty is always rejected (ambiguous).
        """
        if decision.subagent_id is not None and decision.tool_name is not None:
            raise ValueError(
                "Invalid model decision: both subagent_id and tool_name are set; "
                "use exactly one of call_tool, call_subagent, or callback with a single target."
            )

        if decision.kind == "callback":
            if decision.subagent_id in self.allowed_child_agents:
                _LOGGER.warning(
                    "Agent %s: decision kind mismatch — model emitted callback but subagent_id=%r "
                    "matches a declared child agent; normalizing to call_subagent (intentional slot repair).",
                    self.agent_id,
                    decision.subagent_id,
                )
                return AgentDecision(
                    kind="call_subagent",
                    message=decision.message,
                    parameters=dict(decision.parameters),
                    subagent_id=decision.subagent_id,
                    callback_intent=decision.callback_intent,
                )
            if decision.tool_name in self.allowed_tools:
                _LOGGER.warning(
                    "Agent %s: decision kind mismatch — model emitted callback but tool_name=%r "
                    "matches a declared tool; normalizing to call_tool (intentional slot repair).",
                    self.agent_id,
                    decision.tool_name,
                )
                return AgentDecision(
                    kind="call_tool",
                    message=decision.message,
                    parameters=dict(decision.parameters),
                    tool_name=decision.tool_name,
                    callback_intent=decision.callback_intent,
                )
        if decision.kind == "call_tool":
            if decision.tool_name is None and decision.subagent_id in self.allowed_child_agents:
                _LOGGER.warning(
                    "Agent %s: model emitted call_tool with no tool_name but subagent_id=%r "
                    "matches a declared child agent; normalizing to call_subagent (intentional slot repair).",
                    self.agent_id,
                    decision.subagent_id,
                )
                return AgentDecision(
                    kind="call_subagent",
                    message=decision.message,
                    parameters=dict(decision.parameters),
                    subagent_id=decision.subagent_id,
                    callback_intent=decision.callback_intent,
                )
            if (
                decision.tool_name is not None
                and decision.tool_name not in self.allowed_tools
                and decision.tool_name in self.allowed_child_agents
            ):
                _LOGGER.warning(
                    "Agent %s: model put a child-agent id in tool_name (%r); "
                    "normalizing to call_subagent (intentional slot repair).",
                    self.agent_id,
                    decision.tool_name,
                )
                return AgentDecision(
                    kind="call_subagent",
                    message=decision.message,
                    parameters=dict(decision.parameters),
                    subagent_id=decision.tool_name,
                    callback_intent=decision.callback_intent,
                )
        if decision.kind == "call_subagent":
            if decision.subagent_id is None and decision.tool_name in self.allowed_tools:
                _LOGGER.warning(
                    "Agent %s: model emitted call_subagent with no subagent_id but tool_name=%r "
                    "matches a declared tool; normalizing to call_tool (intentional slot repair).",
                    self.agent_id,
                    decision.tool_name,
                )
                return AgentDecision(
                    kind="call_tool",
                    message=decision.message,
                    parameters=dict(decision.parameters),
                    tool_name=decision.tool_name,
                    callback_intent=decision.callback_intent,
                )
            if (
                decision.subagent_id is not None
                and decision.subagent_id not in self.allowed_child_agents
                and decision.subagent_id in self.allowed_tools
            ):
                _LOGGER.warning(
                    "Agent %s: model put a tool id in subagent_id (%r); "
                    "normalizing to call_tool (intentional slot repair).",
                    self.agent_id,
                    decision.subagent_id,
                )
                return AgentDecision(
                    kind="call_tool",
                    message=decision.message,
                    parameters=dict(decision.parameters),
                    tool_name=decision.subagent_id,
                    callback_intent=decision.callback_intent,
                )
        return decision

__all__ = ["Agent"]
