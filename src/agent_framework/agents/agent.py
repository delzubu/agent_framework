"""Markdown-defined runnable agent."""

from __future__ import annotations

import importlib.util
import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
import yaml

from agent_framework.errors import ModelDriverError
from agent_framework.file_reference import expand_file_refs
from agent_framework.model import (
    CapabilityDefinition,
    ModelContext,
    ModelResponse,
    build_skills_catalog as _build_skills_catalog,
    merge_runtime_system_into_messages as _merge_runtime_system_into_messages,
    runtime_prompt_source_paths as _runtime_prompt_source_paths,
)
from agent_framework.model_validation import ModelValidationContext

from .agent_behavior import AgentBehavior

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from agent_framework.planning.config import PlanningConfig
from .agent_decision import AgentDecision, SubagentCallSpec
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
    AgentMarkdownError,
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
from .workflow import (
    ProgrammaticWorkflow,
    ProgrammaticWorkflowState,
    WorkflowAbort,
    WorkflowAbortedError,
    WorkflowBranchStep,
    WorkflowCallSubagentStep,
    WorkflowCallSubagentsStep,
    WorkflowCallToolStep,
    WorkflowContinue,
    WorkflowGoto,
    WorkflowHistoryEvent,
    WorkflowHistoryProjection,
    WorkflowHistoryProjector,
    WorkflowProjectionSelector,
    WorkflowModelStep,
    WorkflowReplace,
    WorkflowRaiseStep,
    WorkflowReturnStep,
    WorkflowTransformStep,
    coerce_workflow_result,
    resolve_workflow_value,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _ToolCallResult:
    """Internal holder returned by ``Agent._execute_tool_step``."""

    effective_input: dict[str, Any]
    result: Any
    system_message: str | None
    error_exc: Exception | None
    early_exit: "AgentResult | None"


@dataclass(frozen=True, slots=True)
class CallbackRoutingPolicy:
    """Caller-escalation policy loaded from agent runtime metadata.

    These defaults apply when a child emits a caller-mediated interaction kind
    and does not provide a more specific routing override in the decision
    parameters.
    """

    passthrough_child_callbacks: bool = False
    max_bubble_hops: int | None = None
    fallback_target: str = "user"


def _emit_context_updated(
    agent: "Agent", host: "AgentHostProtocol", run: AgentRun, message: dict[str, str], source: str
) -> None:
    from agent_framework.agent_event_publisher import agent_events

    agent_events.on_context_updated(
        run_id=run.run_id,
        agent_id=agent.agent_id,
        message=dict(message),
        source=source,
    )


def _parse_planning_config(raw: Any, source_path: Path | None = None) -> "PlanningConfig | None":
    """Parse the optional `planning:` frontmatter block into a PlanningConfig."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        from agent_framework.agents.helpers import AgentMarkdownError
        raise AgentMarkdownError(
            source_path=source_path or Path("<unknown>"),
            detail=f"'planning' must be a YAML mapping, got {type(raw).__name__!r}.",
            hint="Example: planning:\\n  enabled: true",
        )
    from agent_framework.planning.config import PlanningConfig
    try:
        return PlanningConfig.from_frontmatter(raw)
    except ValueError as exc:
        from agent_framework.agents.helpers import AgentMarkdownError
        raise AgentMarkdownError(
            source_path=source_path or Path("<unknown>"),
            detail=str(exc),
            hint="Check the 'planning:' block in the agent frontmatter.",
        ) from exc


def _render_result_for_injection(result: Any) -> str:
    """Render an AgentResult for injection into a parent conversation."""
    from agent_framework.agents.result_envelope import render_subagent_envelope
    return render_subagent_envelope(
        message=getattr(result, "message", ""),
        response=getattr(result, "response", None),
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
        on_pre_agent: Sequential callbacks executed before the agent run starts.
        on_post_agent: Sequential callbacks executed after the agent run ends.
        on_pre_tool: Sequential callbacks executed before a tool call.
        on_post_tool: Sequential callbacks executed after a tool call.
        on_pre_subagent: Sequential callbacks executed before a child-agent call.
        on_post_subagent: Sequential callbacks executed after a child-agent call.
        on_pre_skill: Sequential callbacks executed before a skill invocation.
        on_post_skill: Sequential callbacks executed after a skill invocation.
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
    callback_routing_policy: CallbackRoutingPolicy = field(default_factory=CallbackRoutingPolicy)
    on_pre_agent: SequentialHook = field(default_factory=SequentialHook)
    on_post_agent: SequentialHook = field(default_factory=SequentialHook)
    on_pre_tool: SequentialHook = field(default_factory=SequentialHook)
    on_post_tool: SequentialHook = field(default_factory=SequentialHook)
    on_pre_subagent: SequentialHook = field(default_factory=SequentialHook)
    on_post_subagent: SequentialHook = field(default_factory=SequentialHook)
    on_pre_skill: SequentialHook = field(default_factory=SequentialHook)
    on_post_skill: SequentialHook = field(default_factory=SequentialHook)
    on_pre_model: SequentialHook = field(default_factory=SequentialHook)
    on_post_model: SequentialHook = field(default_factory=SequentialHook)
    behavior_ids: tuple[str, ...] = ()
    behaviors: tuple[AgentBehavior, ...] = field(default=(), repr=False)
    source_path: Path | None = None
    terminal_tools: tuple[str, ...] = ()
    planning_config: "PlanningConfig | None" = None

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
            callback_routing_policy=_parse_callback_routing_policy(runtime_metadata),
            behavior_ids=behavior_ids,
            source_path=source_path,
            terminal_tools=tuple(metadata.get("terminal_tools", []) or ()),
            planning_config=_parse_planning_config(
                runtime_metadata.get("planning") or metadata.get("planning"), source_path
            ),
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

    def _select_turn_driver(self, *, planning_override: bool | None) -> "TurnDriver":
        """Choose a TurnDriver for this invocation.

        Resolution order:
            1. planning_override=False wins unconditionally → StandardTurnDriver.
            2. planning_override=True → planning active (config defaults if no frontmatter).
            3. self.planning_config.enabled → planning active with frontmatter config.
            4. Default → StandardTurnDriver.
        """
        from .turn_driver import StandardTurnDriver  # local import avoids circular

        if planning_override is False:
            return StandardTurnDriver()

        planning_active = planning_override is True or (
            self.planning_config is not None and self.planning_config.enabled
        )
        if planning_active:
            from agent_framework.planning.turn_driver import PlanningTurnDriver
            config = self.planning_config
            if config is None:
                from agent_framework.planning.config import PlanningConfig
                config = PlanningConfig.default_enabled()
            return PlanningTurnDriver(config=config)
        return StandardTurnDriver()

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
        run_id: str | None = None,
        in_parallel_batch: bool = False,
        planning_override: bool | None = None,
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
            run_id=run_id,
            parent_run_id=parent_run_id,
            in_parallel_batch=in_parallel_batch,
            rendered_prompt_override=rendered_prompt_override,
            conversation_messages=conversation_messages,
            prompt_fragments=prompt_fragments,
        )
        register_run = getattr(host, "register_run", None)
        if callable(register_run):
            register_run(
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=caller_id,
                parent_run_id=parent_run_id,
            )
        normalize_memory_parameters = getattr(host, "normalize_memory_parameters", None)
        if callable(normalize_memory_parameters):
            normalized_seed_parameters = normalize_memory_parameters(
                agent_id=self.agent_id,
                run_id=run.run_id,
                parameters=run.seed_parameters,
            )
            if normalized_seed_parameters != run.seed_parameters:
                run.seed_parameters = normalized_seed_parameters
                if rendered_prompt_override is None:
                    run.rendered_prompt = self._render_seed_prompt(run.seed_parameters)
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
            run_id=run.run_id,
            parent_run_id=parent_run_id,
            caller_id=caller_id,
            agent_name=self.agent_id,
            system_prompt=initial_context.system_prompt,
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
            # Refresh parameter state after all pre-run hooks have had a chance
            # to inject prompt fragments (e.g. on_pre_agent hooks that add context).
            # This gives the complete bound parameter snapshot before the first model call.
            self.refresh_parameter_state(run)
            agent_events.audit_parameters_bound(
                run_id=run.run_id,
                agent_id=self.agent_id,
                bound_parameters=dict(run.parameter_values or {}),
            )
            driver = self._select_turn_driver(planning_override=planning_override)
            while self.should_continue(run):
                outcome = driver.run_turn(
                    agent=self, host=host, run=run, caller_id=caller_id,
                )
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
            usage_summary = {"usage_self": {}, "usage_inclusive": {}}
            finish_runtime_usage = getattr(host, "finish_runtime_usage", None)
            if callable(finish_runtime_usage):
                usage_summary = finish_runtime_usage(run_id=run.run_id)
            agent_events.audit_agent_call_finished(
                run_id=run.run_id,
                usage_self=usage_summary.get("usage_self"),
                usage_inclusive=usage_summary.get("usage_inclusive"),
            )

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
        resolver = getattr(host, "file_ref_resolver", None)
        if resolver is not None:
            base_dir = self.source_path.parent if self.source_path is not None else None
            system_prompt = expand_file_refs(system_prompt, resolver, base_dir=base_dir)
        prompt = _apply_runtime_placeholders(run.rendered_prompt, run.placeholder_values)
        active_workflow_step = self._active_workflow_model_step(run)
        if active_workflow_step is not None and active_workflow_step.prompt_fragment_mode == "conversation_only":
            prompt = ""
        if run.prompt_fragments:
            # System and helper augmentations stay outside the original template
            # and are appended separately from the transcript.
            prompt = f"{prompt}\n\n<augmentations>\n" + "\n".join(run.prompt_fragments) + "\n</augmentations>"
        resolve = getattr(host, "resolve_model_tool_definitions", None)
        allowed_tool_names = self._effective_allowed_tools(host)
        if callable(resolve):
            tools = resolve(
                allowed_tool_names,
                agent_id=self.agent_id,
                run_id=run.run_id,
            )
        else:
            tools = tuple(host.get_tool(name).model_definition() for name in allowed_tool_names)
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
        build_memory_prompt = getattr(host, "build_memory_prompt", None)
        memory_prompt = ""
        if callable(build_memory_prompt):
            scopes, refs, memory_prompt = build_memory_prompt(
                agent_id=self.agent_id,
                run_id=run.run_id,
                parameter_values=run.parameter_values,
                seed_parameters=run.seed_parameters,
                prompt_text=prompt,
            )
            run.visible_memory_scopes = scopes
            run.resolved_memory_refs = refs
        message_history: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if prompt.strip():
            message_history.append({"role": "user", "content": prompt})
        if skills_catalog:
            message_history.append({"role": "user", "content": skills_catalog})
        if memory_prompt:
            message_history.append({"role": "user", "content": memory_prompt})
        message_history.extend(run.conversation_messages)
        planning_active = (
            self.planning_config is not None and self.planning_config.enabled
        )
        ctx = ModelContext(
            system_prompt=system_prompt,
            user_prompt=prompt,
            messages=tuple(message_history),
            response_mode="plan_execute" if planning_active else "json_object",
            tools=tools,
            subagents=subagents,
            skills=skills,
            run_id=run.run_id,
        )
        return _merge_runtime_system_into_messages(ctx)

    def decide(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        context: ModelContext,
        planning_active: bool = False,
    ) -> AgentDecision:
        """Request and normalize the next decision from the configured model."""
        self._run_pre_model_hooks(run=run, caller_id=None, context=context)
        pre_model_hooks = getattr(host, "run_pre_model_hooks", None)
        if callable(pre_model_hooks):
            pre_model_hooks(ModelStartEvent(invocation=self._hook_invocation(run, None), context=context))
        validation_context = ModelValidationContext.from_model_context(
            agent_id=self.agent_id,
            provider_name=self.provider_name,
            model_names=self.model_names,
            context=context,
        )
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

            validated_exc = exc
            validate_model_exception = getattr(host, "validate_model_exception", None)
            if callable(validate_model_exception):
                validated_exc = validate_model_exception(
                    exc,
                    validation_context=validation_context,
                )
            sc = validated_exc.status_code if isinstance(validated_exc, ModelDriverError) else None
            ub = validated_exc.upstream_body if isinstance(validated_exc, ModelDriverError) else None
            agent_events.on_model_call_failed(
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=None,
                exc=validated_exc,
                status_code=sc,
                upstream_body=ub,
            )
            if validated_exc is exc:
                raise
            raise validated_exc from exc
        validate_model_response = getattr(host, "validate_model_response", None)
        if callable(validate_model_response):
            validate_model_response(
                response,
                validation_context=validation_context,
            )
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
        try:
            completed_step_ids: frozenset[str] | None = None
            if run.plan_state is not None:
                completed_step_ids = frozenset(run.plan_state.step_results.keys())
            return AgentDecision.from_model_response(
                response,
                planning_active=planning_active,
                completed_step_ids=completed_step_ids,
            )
        except BaseException as exc:
            validate_model_exception = getattr(host, "validate_model_exception", None)
            if not callable(validate_model_exception):
                raise
            validated_exc = validate_model_exception(
                exc,
                validation_context=validation_context,
            )
            if validated_exc is exc:
                raise
            raise validated_exc from exc

    def dispatch_decision(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        decision: AgentDecision,
        caller_id: str | None,
    ) -> AgentResult | None:
        """Dispatch a normalized decision to the appropriate handler."""
        decision = self._normalize_decision_capabilities(decision, host=host)
        from agent_framework.agent_event_publisher import agent_events

        agent_events.audit_decision(
            run_id=run.run_id,
            agent_id=self.agent_id,
            decision=decision,
            workflow_step_id=run.workflow_step_id,
            workflow_phase_id=run.workflow_phase_id,
        )
        handlers = {
            "final_message": self.handle_final_message,
            "callback": self.handle_callback,
            "callback_to_caller": self.handle_callback,
            "request_user_input": self.handle_callback,
            "request_resolution": self.handle_callback,
            "call_subagent": self.handle_subagent_call,
            "call_subagents": self.handle_subagent_calls,
            "call_tool": self.handle_tool_call,
            "invoke_skill": self.handle_skill_invocation,
        }
        handler = handlers.get(decision.kind)
        if handler is None:
            raise ValueError(f"Unsupported decision kind: {decision.kind}")
        run.transcript_entries.append(
            f"<assistant_decision>{_stringify_parameter_value(_decision_to_dict(decision))}</assistant_decision>"
        )
        active_workflow_step = self._active_workflow_model_step(run)
        if active_workflow_step is None or self._workflow_uses_legacy_decision_history(active_workflow_step):
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
            response=decision.response,
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
        """Handle callback-style interaction and resolution requests."""
        if run.in_parallel_batch:
            # Parallel children cannot block on user/caller input — save conversation
            # state so the batch orchestrator can resume after resolving the callback.
            if decision.kind == "request_user_input":
                return AgentResult(
                    status="failed",
                    message="Parallel children cannot request direct user input synchronously.",
                    decision=decision,
                    prompt=run.rendered_prompt,
                )
            save_fn = getattr(host, "save_checkpoint", None)
            if callable(save_fn):
                save_fn(run.run_id, list(run.conversation_messages))
            return AgentResult(
                status="blocked",
                message=json.dumps({
                    "kind": decision.kind,
                    "intent": decision.callback_intent or "information_request",
                    "prompt": decision.message,
                    "parameters": decision.parameters,
                }),
                decision=decision,
                prompt=run.rendered_prompt,
            )
        intent = decision.callback_intent or "information_request"
        parameter_name = str(decision.parameters.get("parameter_name", "")).strip()
        spec = self._parameter_spec_by_name().get(parameter_name) if parameter_name else None
        prompt = decision.message or (spec.description if spec is not None else "Please provide more information.")
        routes_to_caller = (
            decision.kind in {"callback", "callback_to_caller"}
            and bool(caller_id and caller_id != "host" and self.can_query_caller)
        )
        bubble_hops = _coerce_bubble_hops(decision.parameters.get("bubble_hops"))
        routing_policy = _merge_callback_routing_policy(self.callback_routing_policy, decision.parameters)
        next_bubble_hops = bubble_hops + 1
        should_passthrough = (
            routes_to_caller
            and routing_policy.passthrough_child_callbacks
            and next_bubble_hops > 0
        )
        reached_hop_limit = (
            routing_policy.max_bubble_hops is not None
            and next_bubble_hops > routing_policy.max_bubble_hops
        )
        from agent_framework.agent_event_publisher import agent_events

        agent_events.on_callback_requested(
            run_id=run.run_id,
            agent_id=self.agent_id,
            caller_id=caller_id,
            intent=intent,
            prompt=prompt,
            to_caller=routes_to_caller,
        )
        answer = ""
        if routes_to_caller and not should_passthrough and not reached_hop_limit:
            context = host.open_context(
                caller_id=self.agent_id,
                callee_id=caller_id,
                kind=f"callback:{intent}",
            )
            run.contexts.append(context)
            callback_parameters = dict(decision.parameters)
            callback_parameters["bubble_hops"] = next_bubble_hops
            answer = host.resolve_callback(
                caller_id=caller_id,
                callee=self,
                prompt=prompt,
                intent=intent,
                run_id=run.run_id,
                parent_run_id=run.parent_run_id,
                allow_user_fallback=decision.kind != "request_resolution",
                callback_parameters=callback_parameters,
            )
            context.status = "resolved"
            cb_event = {
                "type": "callback",
                "intent": intent,
                "target": f"caller:{caller_id}",
                "prompt": prompt,
                "response": answer,
            }
            agent_events.audit_callback(
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
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=caller_id,
                intent=intent,
                target=f"caller:{caller_id}",
                answer=answer,
            )
        elif routes_to_caller and (should_passthrough or reached_hop_limit):
            fallback_target = routing_policy.fallback_target
            if fallback_target == "fail" or decision.kind == "request_resolution":
                return AgentResult(
                    status="failed",
                    message=(
                        f"{self.agent_id} callback could not be resolved within caller bubble policy."
                    ),
                    decision=decision,
                    prompt=run.rendered_prompt,
                )
            if not self.can_use_host_interaction:
                raise ValueError(f"{self.agent_id} cannot request callback intent '{intent}' from host.")
            context = host.open_context(
                caller_id=self.agent_id,
                callee_id="host",
                kind=f"callback:{intent}",
            )
            run.contexts.append(context)
            answer = host.request_user_input(
                prompt,
                intent=intent,
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=caller_id,
                parent_run_id=run.parent_run_id,
                interaction_kind="callback_passthrough",
            )
            context.status = "resolved"
            cb_event = {
                "type": "callback",
                "intent": intent,
                "target": "host",
                "prompt": prompt,
                "response": answer,
            }
            agent_events.audit_callback(
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
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=caller_id,
                intent=intent,
                target="host",
                answer=answer,
            )
        elif decision.kind == "request_resolution":
            return AgentResult(
                status="failed",
                message=(
                    f"{self.agent_id} emitted request_resolution but no caller-side resolver was available."
                ),
                decision=decision,
                prompt=run.rendered_prompt,
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
            answer = host.request_user_input(
                prompt,
                intent=intent,
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=caller_id,
                parent_run_id=run.parent_run_id,
                interaction_kind="direct_user_input",
            )
            context.status = "resolved"
            cb_event = {
                "type": "callback",
                "intent": intent,
                "target": "host",
                "prompt": prompt,
                "response": answer,
            }
            agent_events.audit_callback(
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
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=caller_id,
                intent=intent,
                target="host",
                answer=answer,
            )

        if intent == "information_request" and parameter_name:
            if self._workflow_uses_prompt_fragments(run):
                run.prompt_fragments.append(f"<{parameter_name}>{answer}</{parameter_name}>")
        else:
            if self._workflow_uses_prompt_fragments(run):
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
            if self._workflow_uses_prompt_fragments(run):
                run.prompt_fragments.append(f"<subagent_error>{error_text}</subagent_error>")
            run.transcript_entries.append(f"<subagent_error>{error_text}</subagent_error>")
            run.conversation_messages.append({"role": "user", "content": error_text})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_validation_error")
            return None
        if not self._validate_subagent_permission(host, run, decision.subagent_id):
            return None
        try:
            self._execute_single_subagent_flow(
                host=host,
                run=run,
                caller_id=caller_id,
                subagent_id=decision.subagent_id,
                subagent_input=dict(decision.parameters),
                decision=decision,
            )
        except Exception:
            return None
        return None

    def handle_subagent_calls(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        decision: AgentDecision,
        caller_id: str | None,
    ) -> AgentResult | None:
        """Handle a call_subagents batch decision (parallel or sequential)."""
        from agent_framework.agent_event_publisher import agent_events

        # Validate every call entry against the allowed child agent list.
        for spec in decision.subagent_calls:
            if not self._validate_subagent_permission(
                host,
                run,
                spec.subagent_id,
                output_key=spec.output_key,
            ):
                return None

        try:
            self._execute_subagent_batch_flow(
                host=host,
                run=run,
                caller_id=caller_id,
                specs=decision.subagent_calls,
                mode=decision.batch_mode,
                timeout_seconds=decision.batch_timeout_seconds,
            )
        except Exception:
            return None
        return None

    def execute_programmatic_workflow(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        workflow: ProgrammaticWorkflow,
        initial_parameters: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Run a deterministic workflow without entering the model decision loop."""
        if initial_parameters is None:
            # Validate required parameters the same way the LLM loop does.
            self.refresh_parameter_state(run)
            if run.missing_parameters:
                raise ValueError(
                    f"Missing required parameter(s) {run.missing_parameters!r} "
                    f"for workflow agent {self.agent_id!r}."
                )
            if run.invalid_parameters:
                raise ValueError(
                    f"Invalid parameter value(s) {run.invalid_parameters!r} "
                    f"for workflow agent {self.agent_id!r}."
                )
        state = ProgrammaticWorkflowState(
            initial_parameters=dict(initial_parameters or run.parameter_values),
        )
        run.workflow_chat_history_enabled = (
            run.workflow_chat_history_enabled
            or self._workflow_uses_chat_history_context(workflow)
        )
        if run.workflow_chat_history_enabled:
            self._append_workflow_initial_prompt(host, run)
        step_id = workflow.entry_step
        steps_executed = 0

        while True:
            steps_executed += 1
            if steps_executed > workflow.max_steps:
                raise RuntimeError(
                    f"Programmatic workflow exceeded max_steps={workflow.max_steps} for {self.agent_id}."
                )
            if step_id not in workflow.steps:
                raise KeyError(f"Workflow step {step_id!r} is not defined.")
            step = workflow.steps[step_id]

            if isinstance(step, WorkflowBranchStep):
                self._emit_workflow_event(host, run, "workflow.step_started", step.step_id)
                branch_taken = bool(resolve_workflow_value(step.condition, state))
                self._emit_workflow_event(host, run, "workflow.step_completed", step.step_id, result={"branch_taken": branch_taken})
                step_id = self._resolve_workflow_next_step(
                    step.then_step if branch_taken else step.else_step,
                    state,
                    current_step_id=step.step_id,
                )
                continue

            if isinstance(step, WorkflowModelStep):
                result = self._execute_workflow_model_phase(
                    host=host,
                    run=run,
                    caller_id=caller_id,
                    state=state,
                    step=step,
                )
                state.step_results[step.step_id] = result
                state.last_step_id = step.step_id
                state.last_value = result
                state.context_entries.append(
                    f"<workflow_phase_result step_id=\"{step.step_id}\" phase_id=\"{step.phase_id}\">"
                    f"{_stringify_parameter_value({'message': result.message, 'response': result.response})}"
                    f"</workflow_phase_result>"
                )
                next_step_id = self._apply_workflow_step_end(
                    workflow=workflow,
                    step_id=step.step_id,
                    result=result,
                    state=state,
                    default_next=step.next_step,
                )
                if isinstance(next_step_id, str):
                    step_id = next_step_id
                    continue
                workflow = next_step_id
                run.workflow_chat_history_enabled = (
                    run.workflow_chat_history_enabled
                    or self._workflow_uses_chat_history_context(workflow)
                )
                step_id = workflow.entry_step
                continue

            if isinstance(step, WorkflowTransformStep):
                self._emit_workflow_event(host, run, "workflow.step_started", step.step_id)
                with self._workflow_scope(run, step_id=step.step_id, phase_id=None):
                    result = resolve_workflow_value(step.transform, state)
                state.step_results[step.step_id] = result
                state.last_step_id = step.step_id
                state.last_value = result
                state.context_entries.append(
                    f"<workflow_transform_result step_id=\"{step.step_id}\">"
                    f"{_stringify_parameter_value(result)}</workflow_transform_result>"
                )
                self._append_workflow_context(host, run, state.context_entries[-1], source="workflow_transform_result")
                self._emit_workflow_event(host, run, "workflow.step_completed", step.step_id, result=result)
                next_step_id = self._apply_workflow_step_end(
                    workflow=workflow,
                    step_id=step.step_id,
                    result=result,
                    state=state,
                    default_next=step.next_step,
                )
                if isinstance(next_step_id, str):
                    step_id = next_step_id
                    continue
                workflow = next_step_id
                run.workflow_chat_history_enabled = (
                    run.workflow_chat_history_enabled
                    or self._workflow_uses_chat_history_context(workflow)
                )
                step_id = workflow.entry_step
                continue

            if isinstance(step, WorkflowCallSubagentStep):
                self._emit_workflow_event(host, run, "workflow.step_started", step.step_id)
                resolved_subagent_id = str(resolve_workflow_value(step.subagent_id, state))
                resolved_parameters = resolve_workflow_value(step.parameters, state)
                if not isinstance(resolved_parameters, dict):
                    raise TypeError(
                        f"Workflow step {step.step_id!r} parameters must resolve to dict, "
                        f"got {type(resolved_parameters).__name__}."
                    )
                _LOGGER.debug(
                    "WorkflowCallSubagentStep resolved",
                    extra={
                        "workflow_step_id": step.step_id,
                        "agent_id": self.agent_id,
                        "subagent_id": resolved_subagent_id,
                        "parameters_json": json.dumps(
                            resolved_parameters,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                )
                with self._workflow_scope(run, step_id=step.step_id, phase_id=None):
                    result = self._execute_single_subagent_flow(
                        host=host,
                        run=run,
                        caller_id=caller_id,
                        subagent_id=resolved_subagent_id,
                        subagent_input=dict(resolved_parameters),
                        decision=None,
                    )
                state.step_results[step.step_id] = result
                state.last_step_id = step.step_id
                state.last_value = result
                self._emit_workflow_event(host, run, "workflow.step_completed", step.step_id, result=result)
                next_step_id = self._apply_workflow_step_end(
                    workflow=workflow,
                    step_id=step.step_id,
                    result=result,
                    state=state,
                    default_next=step.next_step,
                )
                if isinstance(next_step_id, str):
                    step_id = next_step_id
                    continue
                # WorkflowReplace: next_step_id is a new ProgrammaticWorkflow
                workflow = next_step_id
                run.workflow_chat_history_enabled = (
                    run.workflow_chat_history_enabled
                    or self._workflow_uses_chat_history_context(workflow)
                )
                step_id = workflow.entry_step
                continue

            if isinstance(step, WorkflowCallSubagentsStep):
                self._emit_workflow_event(host, run, "workflow.step_started", step.step_id)
                resolved_calls = resolve_workflow_value(step.calls, state)
                if not isinstance(resolved_calls, tuple):
                    raise TypeError(
                        f"Workflow step {step.step_id!r} calls must resolve to tuple[SubagentCallSpec, ...], "
                        f"got {type(resolved_calls).__name__}."
                    )
                with self._workflow_scope(run, step_id=step.step_id, phase_id=None):
                    result = self._execute_subagent_batch_flow(
                        host=host,
                        run=run,
                        caller_id=caller_id,
                        specs=resolved_calls,
                        mode=step.mode,
                        timeout_seconds=step.timeout_seconds,
                    )
                state.step_results[step.step_id] = result
                state.last_step_id = step.step_id
                state.last_value = result
                self._emit_workflow_event(host, run, "workflow.step_completed", step.step_id, result=result)
                next_step_id = self._apply_workflow_step_end(
                    workflow=workflow,
                    step_id=step.step_id,
                    result=result,
                    state=state,
                    default_next=step.next_step,
                )
                if isinstance(next_step_id, str):
                    step_id = next_step_id
                    continue
                workflow = next_step_id
                run.workflow_chat_history_enabled = (
                    run.workflow_chat_history_enabled
                    or self._workflow_uses_chat_history_context(workflow)
                )
                step_id = workflow.entry_step
                continue

            if isinstance(step, WorkflowCallToolStep):
                self._emit_workflow_event(host, run, "workflow.step_started", step.step_id)
                resolved_arguments = resolve_workflow_value(step.arguments, state)
                if not isinstance(resolved_arguments, dict):
                    raise TypeError(
                        f"Workflow step {step.step_id!r} arguments must resolve to dict, "
                        f"got {type(resolved_arguments).__name__}."
                    )
                _LOGGER.debug(
                    "WorkflowCallToolStep executing",
                    extra={
                        "workflow_step_id": step.step_id,
                        "agent_id": self.agent_id,
                        "tool_name": step.tool_name,
                    },
                )
                with self._workflow_scope(run, step_id=step.step_id, phase_id=None):
                    tc = self._execute_tool_step(
                        host=host,
                        run=run,
                        caller_id=caller_id,
                        tool_name=step.tool_name,
                        tool_input=resolved_arguments,
                    )
                if tc.early_exit is not None:
                    return coerce_workflow_result(tc.early_exit)
                if tc.error_exc is not None:
                    raise tc.error_exc
                result = tc.result
                state.step_results[step.step_id] = result
                state.last_step_id = step.step_id
                state.last_value = result
                if run.workflow_chat_history_enabled:
                    fragment = (
                        f"<workflow_tool_result step_id=\"{step.step_id}\" name=\"{step.tool_name}\">"
                        f"{_stringify_parameter_value(result)}</workflow_tool_result>"
                    )
                    run.transcript_entries.append(fragment)
                    run.conversation_messages.append({"role": "user", "content": fragment})
                    _emit_context_updated(self, host, run, run.conversation_messages[-1], "workflow_tool_result")
                self._emit_workflow_event(host, run, "workflow.step_completed", step.step_id, result=result)
                next_step_id = self._apply_workflow_step_end(
                    workflow=workflow,
                    step_id=step.step_id,
                    result=result,
                    state=state,
                    default_next=step.next_step,
                )
                if isinstance(next_step_id, str):
                    step_id = next_step_id
                    continue
                workflow = next_step_id
                run.workflow_chat_history_enabled = (
                    run.workflow_chat_history_enabled
                    or self._workflow_uses_chat_history_context(workflow)
                )
                step_id = workflow.entry_step
                continue

            if isinstance(step, WorkflowReturnStep):
                self._emit_workflow_event(host, run, "workflow.step_started", step.step_id)
                value = resolve_workflow_value(step.value, state)
                result = coerce_workflow_result(value)
                self._emit_workflow_event(host, run, "workflow.step_completed", step.step_id, result=result)
                return result

            if isinstance(step, WorkflowRaiseStep):
                self._emit_workflow_event(host, run, "workflow.step_started", step.step_id)
                error = resolve_workflow_value(step.error, state)
                if isinstance(error, BaseException):
                    raise error
                raise RuntimeError(str(error))

            raise TypeError(f"Unsupported workflow step type {type(step).__name__}.")

    def _execute_workflow_model_phase(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        state: ProgrammaticWorkflowState,
        step: WorkflowModelStep,
    ) -> AgentResult:
        """Run a phase-local model loop and return the phase final result."""
        if step.max_turns < 1:
            raise ValueError(f"Workflow model step {step.step_id!r} max_turns must be >= 1.")

        self._emit_workflow_event(host, run, "workflow.step_started", step.step_id)
        self._emit_workflow_event(host, run, "workflow.phase_started", step.step_id, phase_id=step.phase_id)
        if step.prompt_fragment_mode == "conversation_only":
            self._append_workflow_initial_prompt(host, run)
        prompt_fragment = self._resolve_workflow_phase_prompt(run, step, state)
        phase_fragment = self._render_workflow_phase_fragment(step, prompt_fragment, state)
        state.context_entries.append(phase_fragment)
        self._append_workflow_phase_context(host, run, step, phase_fragment, source="workflow_phase_prompt")

        with self._workflow_scope(run, step_id=step.step_id, phase_id=step.phase_id):
            previous_context_step = run.workflow_context_step
            previous_context_state = run.workflow_context_state
            run.workflow_context_step = step
            run.workflow_context_state = state
            try:
                for turn_index in range(1, step.max_turns + 1):
                    self.before_iteration(run)
                    decision = self.resolve_runtime_decision(run=run)
                    if decision is None:
                        context = self.build_context(host=host, run=run)
                        decision = self.decide(host=host, run=run, context=context)
                    if (
                        step.allowed_decision_kinds is not None
                        and decision.kind not in step.allowed_decision_kinds
                    ):
                        reminder = (
                            "<workflow_phase_error>"
                            f"Decision kind {decision.kind!r} is not allowed in phase {step.phase_id!r}. "
                            f"Allowed kinds: {sorted(step.allowed_decision_kinds)}."
                            "</workflow_phase_error>"
                        )
                        run.conversation_messages.append({"role": "user", "content": reminder})
                        _emit_context_updated(self, host, run, run.conversation_messages[-1], "workflow_phase_error")
                        self.after_iteration(run)
                        continue
                    if decision.kind == "final_message":
                        if step.final_response_schema is not None:
                            validate_json_schema(
                                instance=decision.response or {},
                                schema=step.final_response_schema,
                            )
                        result = AgentResult(
                            status="completed",
                            message=decision.message,
                            response=decision.response,
                            decision=decision,
                            prompt=run.rendered_prompt,
                        )
                        # Record the final decision in the shared ledger without invoking
                        # the normal final-message handler, because this is phase-local.
                        from agent_framework.agent_event_publisher import agent_events

                        agent_events.audit_decision(
                            run_id=run.run_id,
                            agent_id=self.agent_id,
                            decision=decision,
                            workflow_step_id=step.step_id,
                            workflow_phase_id=step.phase_id,
                        )
                        run.transcript_entries.append(
                            f"<workflow_phase_decision step_id=\"{step.step_id}\" phase_id=\"{step.phase_id}\">"
                            f"{_stringify_parameter_value(_decision_to_dict(decision))}"
                            f"</workflow_phase_decision>"
                        )
                        if self._workflow_uses_legacy_decision_history(step):
                            run.conversation_messages.append(
                                {
                                    "role": "assistant",
                                    "content": _stringify_parameter_value(_decision_to_dict(decision)),
                                }
                            )
                            _emit_context_updated(self, host, run, run.conversation_messages[-1], "workflow_phase_decision")
                        else:
                            self._append_workflow_history_projection(
                                host,
                                run,
                                WorkflowHistoryEvent(
                                    kind="final_message",
                                    step_id=step.step_id,
                                    phase_id=step.phase_id,
                                    decision=decision,
                                    result=result,
                                    message=decision.message,
                                    response=decision.response,
                                ),
                                role="assistant",
                                source="workflow_phase_result",
                            )
                        self._cleanup_ephemeral_workflow_phase_context(run, step, phase_fragment)
                        self.after_iteration(run)
                        self._emit_workflow_event(
                            host,
                            run,
                            "workflow.phase_completed",
                            step.step_id,
                            phase_id=step.phase_id,
                            result=result,
                        )
                        self._emit_workflow_event(host, run, "workflow.step_completed", step.step_id, result=result)
                        return result

                    outcome = self.dispatch_decision(
                        host=host,
                        run=run,
                        decision=decision,
                        caller_id=caller_id,
                    )
                    self.after_iteration(run)
                    if outcome is not None:
                        self._cleanup_ephemeral_workflow_phase_context(run, step, phase_fragment)
                        # Non-final terminal outcomes, such as callback failure or
                        # terminal tools, end the phase and are stored as its result.
                        self._emit_workflow_event(
                            host,
                            run,
                            "workflow.phase_completed",
                            step.step_id,
                            phase_id=step.phase_id,
                            result=outcome,
                        )
                        self._emit_workflow_event(host, run, "workflow.step_completed", step.step_id, result=outcome)
                        return outcome
            finally:
                run.workflow_context_step = previous_context_step
                run.workflow_context_state = previous_context_state

        message = (
            f"Workflow phase {step.phase_id!r} in step {step.step_id!r} exceeded "
            f"max_turns={step.max_turns} without a final_message."
        )
        failed = AgentResult(status="failed", message=message, prompt=run.rendered_prompt)
        self._emit_workflow_event(
            host,
            run,
            "workflow.phase_failed",
            step.step_id,
            phase_id=step.phase_id,
            result=failed,
        )
        return failed

    def _render_workflow_phase_fragment(
        self,
        step: WorkflowModelStep,
        prompt_fragment: str,
        state: ProgrammaticWorkflowState,
    ) -> str:
        state_summary = None
        if step.include_state_summary:
            state_summary = {
                "initial_parameters": state.initial_parameters,
                "step_results": {
                    key: self._workflow_jsonable_summary(value)
                    for key, value in state.step_results.items()
                },
                "last_step_id": state.last_step_id,
            }
        state_block = (
            "<workflow_state_summary>\n"
            f"{json.dumps(state_summary, ensure_ascii=False, sort_keys=True)}\n"
            "</workflow_state_summary>\n"
            if state_summary is not None
            else ""
        )
        return (
            f"<workflow_phase id=\"{step.phase_id}\" step_id=\"{step.step_id}\">\n"
            f"{prompt_fragment}\n"
            f"{state_block}"
            "</workflow_phase>"
        )

    def _resolve_workflow_phase_prompt(
        self,
        run: AgentRun,
        step: WorkflowModelStep,
        state: ProgrammaticWorkflowState,
    ) -> str:
        if step.prompt_fragment is not None:
            return str(resolve_workflow_value(step.prompt_fragment, state))
        sections = getattr(self, "workflow_prompt_sections", {}) or {}
        if step.phase_id not in sections:
            raise KeyError(
                f"Workflow model step {step.step_id!r} has no prompt_fragment and "
                f"no <{step.phase_id}> section in the workflow agent system prompt."
            )
        return _apply_runtime_placeholders(str(sections[step.phase_id]), run.placeholder_values)

    def _append_workflow_context(
        self,
        host: "AgentHostProtocol",
        run: AgentRun,
        fragment: str,
        *,
        source: str,
    ) -> None:
        if self._workflow_uses_prompt_fragments(run):
            run.prompt_fragments.append(fragment)
        run.transcript_entries.append(fragment)
        run.conversation_messages.append({"role": "user", "content": fragment})
        _emit_context_updated(self, host, run, run.conversation_messages[-1], source)

    def _append_workflow_phase_context(
        self,
        host: "AgentHostProtocol",
        run: AgentRun,
        step: WorkflowModelStep,
        fragment: str,
        *,
        source: str,
    ) -> None:
        if step.prompt_fragment_mode not in {"conversation_only", "prompt_fragment_only", "both"}:
            raise ValueError(
                f"Workflow model step {step.step_id!r} prompt_fragment_mode must be "
                "'conversation_only', 'prompt_fragment_only', or 'both'."
            )
        if step.prompt_history_policy not in {"durable", "ephemeral", "none"}:
            raise ValueError(
                f"Workflow model step {step.step_id!r} prompt_history_policy must be "
                "'durable', 'ephemeral', or 'none'."
            )
        if step.prompt_history_policy == "none":
            run.transcript_entries.append(fragment)
            return
        if step.prompt_fragment_mode in {"prompt_fragment_only", "both"}:
            run.prompt_fragments.append(fragment)
        run.transcript_entries.append(fragment)
        if step.prompt_fragment_mode in {"conversation_only", "both"}:
            run.conversation_messages.append({"role": "user", "content": fragment})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], source)

    def _cleanup_ephemeral_workflow_phase_context(
        self,
        run: AgentRun,
        step: WorkflowModelStep,
        fragment: str,
    ) -> None:
        if step.prompt_history_policy != "ephemeral":
            return
        if step.prompt_fragment_mode in {"prompt_fragment_only", "both"}:
            run.prompt_fragments = [item for item in run.prompt_fragments if item != fragment]
        if step.prompt_fragment_mode in {"conversation_only", "both"}:
            run.conversation_messages = [
                message
                for message in run.conversation_messages
                if not (message.get("role") == "user" and message.get("content") == fragment)
            ]

    def _append_workflow_initial_prompt(
        self,
        host: "AgentHostProtocol",
        run: AgentRun,
    ) -> None:
        if run.workflow_initial_prompt_appended:
            return
        initial_prompt = _apply_runtime_placeholders(run.rendered_prompt, run.placeholder_values).strip()
        if initial_prompt:
            run.conversation_messages.append({"role": "user", "content": initial_prompt})
            run.transcript_entries.append(initial_prompt)
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "workflow_initial_prompt")
        run.workflow_initial_prompt_appended = True

    def _active_workflow_model_step(self, run: AgentRun) -> WorkflowModelStep | None:
        step = run.workflow_context_step
        return step if isinstance(step, WorkflowModelStep) else None

    def _active_workflow_state(self, run: AgentRun) -> ProgrammaticWorkflowState | None:
        state = run.workflow_context_state
        return state if isinstance(state, ProgrammaticWorkflowState) else None

    def _workflow_uses_legacy_decision_history(self, step: WorkflowModelStep) -> bool:
        return (
            step.include_state_summary
            and step.prompt_fragment_mode == "both"
            and step.history_projection is None
        )

    def _workflow_uses_prompt_fragments(self, run: AgentRun) -> bool:
        step = self._active_workflow_model_step(run)
        if step is not None:
            return step.prompt_fragment_mode != "conversation_only"
        return not run.workflow_chat_history_enabled

    @staticmethod
    def _workflow_uses_chat_history_context(workflow: ProgrammaticWorkflow) -> bool:
        return any(
            isinstance(step, WorkflowModelStep)
            and step.prompt_fragment_mode == "conversation_only"
            for step in workflow.steps.values()
        )

    def _workflow_projection_for_event(
        self,
        step: WorkflowModelStep,
        event: WorkflowHistoryEvent,
        state: ProgrammaticWorkflowState,
    ) -> str | None:
        projection = step.history_projection or WorkflowHistoryProjection()
        if callable(projection) and not isinstance(projection, WorkflowHistoryProjection):
            return projection(event, state)

        selector = self._workflow_projection_selector(projection, event.kind)
        if callable(selector):
            return selector(event, state)
        if selector == "none":
            return None
        content = self._workflow_projection_content(selector, event)
        if content is None or content == "":
            return None
        if projection.wrapper_tag:
            return f"<{projection.wrapper_tag}>{content}</{projection.wrapper_tag}>"
        return content

    @staticmethod
    def _workflow_projection_selector(
        projection: WorkflowHistoryProjection,
        kind: str,
    ) -> WorkflowProjectionSelector | WorkflowHistoryProjector:
        mapping = {
            "final_message": projection.final_message,
            "callback_request": projection.callback_request,
            "callback_answer": projection.callback_answer,
            "tool_result": projection.tool_result,
            "subagent_result": projection.subagent_result,
            "subagent_batch_result": projection.subagent_batch_result,
            "skill_result": projection.skill_result,
        }
        return mapping.get(kind, "auto")

    @staticmethod
    def _workflow_projection_content(
        selector: WorkflowProjectionSelector | str,
        event: WorkflowHistoryEvent,
    ) -> str | None:
        response = event.response
        if response is None and isinstance(event.payload, dict):
            response = event.payload
        if selector == "auto":
            selector = "response" if response is not None else "message"
        if selector == "message":
            return event.message
        if selector == "response":
            if response is None:
                return None
            return json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if selector == "both":
            parts: list[str] = []
            if event.message:
                parts.append(f"<message>{event.message}</message>")
            if response is not None:
                payload = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                parts.append(f"<response>{payload}</response>")
            return "\n".join(parts)
        return None

    def _append_workflow_history_projection(
        self,
        host: "AgentHostProtocol",
        run: AgentRun,
        event: WorkflowHistoryEvent,
        *,
        role: str,
        source: str,
    ) -> None:
        step = self._active_workflow_model_step(run)
        state = self._active_workflow_state(run)
        if step is None or state is None:
            return
        projected = self._workflow_projection_for_event(step, event, state)
        if not projected:
            return
        run.conversation_messages.append({"role": role, "content": projected})
        _emit_context_updated(self, host, run, run.conversation_messages[-1], source)

    @contextmanager
    def _workflow_scope(
        self,
        run: AgentRun,
        *,
        step_id: str | None,
        phase_id: str | None,
    ):
        previous_step_id = run.workflow_step_id
        previous_phase_id = run.workflow_phase_id
        run.workflow_step_id = step_id
        run.workflow_phase_id = phase_id
        try:
            yield
        finally:
            run.workflow_step_id = previous_step_id
            run.workflow_phase_id = previous_phase_id

    def _emit_workflow_event(
        self,
        host: "AgentHostProtocol",
        run: AgentRun,
        kind: str,
        step_id: str,
        *,
        phase_id: str | None = None,
        result: Any = None,
    ) -> None:
        from agent_framework.agent_event_publisher import agent_events
        from agent_framework.tracing import TraceContext

        payload: dict[str, Any] = {
            "workflow_step_id": step_id,
            "phase_id": phase_id,
        }
        if result is not None:
            payload["result"] = self._workflow_jsonable_summary(result)
        agent_events.audit_named_event(
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={"type": kind, **payload},
        )
        publish = getattr(host, "publish_trace_event", None)
        if callable(publish):
            publish(
                kind=kind,
                title=kind.replace("_", " "),
                span_id=f"{run.run_id}:{step_id}:{phase_id or 'step'}",
                parent_span_id=run.run_id,
                payload=payload,
                context=TraceContext(run_id=run.run_id, agent_id=self.agent_id),
            )

    def _workflow_jsonable_summary(self, value: Any) -> Any:
        if isinstance(value, AgentResult):
            return {
                "status": value.status,
                "message": value.message,
                "response": value.response,
            }
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            return str(value)

    def _workflow_event_metadata(self, run: AgentRun) -> dict[str, str]:
        metadata: dict[str, str] = {}
        if run.workflow_step_id is not None:
            metadata["workflow_step_id"] = run.workflow_step_id
        if run.workflow_phase_id is not None:
            metadata["phase_id"] = run.workflow_phase_id
        return metadata

    def _resolve_workflow_next_step(
        self,
        next_step: str | Any | None,
        state: ProgrammaticWorkflowState,
        *,
        current_step_id: str,
    ) -> str:
        resolved = resolve_workflow_value(next_step, state)
        if not isinstance(resolved, str) or not resolved:
            raise ValueError(
                f"Workflow step {current_step_id!r} must resolve a non-empty next_step."
            )
        return resolved

    def _apply_workflow_step_end(
        self,
        *,
        workflow: ProgrammaticWorkflow,
        step_id: str,
        result: Any,
        state: ProgrammaticWorkflowState,
        default_next: Any,
    ) -> str | ProgrammaticWorkflow:
        """Invoke on_step_end callback and return next step_id or a replacement workflow.

        Returns a str (next step ID) or a ProgrammaticWorkflow (workflow replacement).
        Raises WorkflowAbortedError if the callback returns WorkflowAbort.
        """
        if workflow.on_step_end is not None:
            mutation = workflow.on_step_end(step_id, result, state, workflow)
            if isinstance(mutation, WorkflowGoto):
                return mutation.step_id
            if isinstance(mutation, WorkflowReplace):
                return mutation.workflow
            if isinstance(mutation, WorkflowAbort):
                raise WorkflowAbortedError(mutation.reason)
            # WorkflowContinue or None → fall through to default_next
        return self._resolve_workflow_next_step(default_next, state, current_step_id=step_id)

    def _validate_subagent_permission(
        self,
        host: "AgentHostProtocol",
        run: AgentRun,
        subagent_id: str,
        *,
        output_key: str | None = None,
    ) -> bool:
        if subagent_id in self.allowed_child_agents:
            return True
        if output_key is None:
            error_text = (
                f"{self.agent_id} is not allowed to call subagent {subagent_id}. "
                f"Legal subagent ids: {sorted(self.allowed_child_agents)}."
            )
        else:
            error_text = (
                f"{self.agent_id} is not allowed to call subagent {subagent_id!r} "
                f"(output_key={output_key!r}). "
                f"Legal subagent ids: {sorted(self.allowed_child_agents)}."
            )
        if self._workflow_uses_prompt_fragments(run):
            run.prompt_fragments.append(f"<subagent_error>{error_text}</subagent_error>")
        run.transcript_entries.append(f"<subagent_error>{error_text}</subagent_error>")
        run.conversation_messages.append({"role": "user", "content": error_text})
        _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_validation_error")
        return False

    def _execute_single_subagent_flow(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        subagent_id: str,
        subagent_input: dict[str, Any],
        decision: AgentDecision | None,
    ) -> AgentResult:
        subagent_call_id = str(uuid4())
        event = SubagentStartEvent(
            invocation=self._hook_invocation(run, caller_id),
            subagent_call_id=subagent_call_id,
            subagent_id=subagent_id,
            subagent_input=dict(subagent_input),
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

        effective_subagent_id = pre_decision.updated_subagent_id or event.subagent_id
        effective_subagent_input = pre_decision.updated_subagent_input or dict(event.subagent_input)
        if not self._validate_subagent_permission(host, run, effective_subagent_id):
            return AgentResult(status="failed", message=f"unauthorized subagent {effective_subagent_id}")
        normalize_memory_parameters = getattr(host, "normalize_memory_parameters", None)
        if callable(normalize_memory_parameters):
            effective_subagent_input = normalize_memory_parameters(
                agent_id=self.agent_id,
                run_id=run.run_id,
                parameters=effective_subagent_input,
                child_agent_id=effective_subagent_id,
            )
        from agent_framework.agent_event_publisher import agent_events

        agent_events.audit_named_event(
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "subagent_call",
                "subagent_id": effective_subagent_id,
                "parameters": dict(effective_subagent_input),
                **self._workflow_event_metadata(run),
            },
        )
        run.transcript_entries.append(
            f"<subagent_call id=\"{effective_subagent_id}\">{_stringify_parameter_value(effective_subagent_input)}</subagent_call>"
        )
        run.conversation_messages.append(
            {
                "role": "assistant",
                "content": f"Subagent call {effective_subagent_id}: {_stringify_parameter_value(effective_subagent_input)}",
            }
        )
        _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_call")
        try:
            result = host.call_subagent(
                caller=self,
                callee_id=effective_subagent_id,
                parameters=effective_subagent_input,
                parent_run_id=run.run_id,
            )
        except Exception as exc:
            agent_events.audit_named_event(
                run_id=run.run_id,
                agent_id=self.agent_id,
                event={
                    "type": "subagent_error",
                    "subagent_id": effective_subagent_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    **self._workflow_event_metadata(run),
                },
            )
            if pre_decision.system_message and self._workflow_uses_prompt_fragments(run):
                run.prompt_fragments.append(f"<system_message>{pre_decision.system_message}</system_message>")
            if self._workflow_uses_prompt_fragments(run):
                run.prompt_fragments.append(
                    f"<subagent_error id=\"{effective_subagent_id}\">{type(exc).__name__}: {exc}</subagent_error>"
                )
            run.transcript_entries.append(
                f"<subagent_error id=\"{effective_subagent_id}\">{type(exc).__name__}: {exc}</subagent_error>"
            )
            run.conversation_messages.append(
                {
                    "role": "user",
                    "content": f"Subagent error {effective_subagent_id}: {type(exc).__name__}: {exc}",
                }
            )
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_error")
            raise
        self._run_post_subagent_hooks(
            host=host,
            run=run,
            caller_id=caller_id,
            event=SubagentEndEvent(
                invocation=event.invocation,
                subagent_call_id=subagent_call_id,
                subagent_id=effective_subagent_id,
                subagent_input=effective_subagent_input,
                result=result,
            ),
        )
        if pre_decision.system_message and self._workflow_uses_prompt_fragments(run):
            run.prompt_fragments.append(f"<system_message>{pre_decision.system_message}</system_message>")
        payload = _render_result_for_injection(result)
        agent_events.audit_named_event(
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "subagent_result",
                "subagent_id": effective_subagent_id,
                "result": payload,
                "status": result.status,
                **self._workflow_event_metadata(run),
            },
        )
        run.transcript_entries.append(
            f"<subagent_result id=\"{effective_subagent_id}\">{payload}</subagent_result>"
        )
        if self._workflow_uses_prompt_fragments(run):
            run.conversation_messages.append(
                {"role": "user", "content": f"Subagent result {effective_subagent_id}: {payload}"}
            )
            run.prompt_fragments.append(
                f"<subagent_result id=\"{effective_subagent_id}\">{payload}</subagent_result>"
            )
        elif self._active_workflow_model_step(run) is not None:
            self._append_workflow_history_projection(
                host,
                run,
                WorkflowHistoryEvent(
                    kind="subagent_result",
                    step_id=run.workflow_step_id or "",
                    phase_id=run.workflow_phase_id or "",
                    result=result,
                    message=getattr(result, "message", ""),
                    response=getattr(result, "response", None),
                    payload=payload,
                    metadata={"subagent_id": effective_subagent_id},
                ),
                role="user",
                source="subagent_result",
            )
        else:
            run.conversation_messages.append(
                {"role": "user", "content": f"Subagent result {effective_subagent_id}: {payload}"}
            )
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_result")
        return result

    def _execute_subagent_batch_flow(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        specs: tuple[SubagentCallSpec, ...],
        mode: str,
        timeout_seconds: float | None,
    ) -> list[Any]:
        from agent_framework.agent_event_publisher import agent_events

        for spec in specs:
            if not self._validate_subagent_permission(
                host,
                run,
                spec.subagent_id,
                output_key=spec.output_key,
            ):
                raise RuntimeError(f"unauthorized subagent {spec.subagent_id}")

        batch_id = str(uuid4())
        agent_events.audit_named_event(
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "subagent_batch_started",
                "batch_id": batch_id,
                "mode": mode,
                "count": len(specs),
                "calls": [{"subagent_id": s.subagent_id, "output_key": s.output_key} for s in specs],
                **self._workflow_event_metadata(run),
            },
        )

        call_batch_fn = getattr(host, "call_subagent_batch", None)
        if not callable(call_batch_fn):
            error_text = "Host does not support call_subagent_batch; upgrade AgentHost."
            if self._workflow_uses_prompt_fragments(run):
                run.prompt_fragments.append(f"<subagent_error>{error_text}</subagent_error>")
            run.conversation_messages.append({"role": "user", "content": error_text})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_error")
            raise RuntimeError(error_text)

        normalize_memory_parameters = getattr(host, "normalize_memory_parameters", None)
        normalized_specs = specs
        if callable(normalize_memory_parameters):
            normalized_specs = tuple(
                SubagentCallSpec(
                    subagent_id=spec.subagent_id,
                    parameters=normalize_memory_parameters(
                        agent_id=self.agent_id,
                        run_id=run.run_id,
                        parameters=dict(spec.parameters),
                        child_agent_id=spec.subagent_id,
                    ),
                    output_key=spec.output_key,
                )
                for spec in specs
            )

        try:
            results = call_batch_fn(
                caller=self,
                specs=normalized_specs,
                mode=mode,
                timeout_seconds=timeout_seconds,
                parent_run_id=run.run_id,
            )
        except Exception as exc:
            error_text = f"call_subagent_batch failed: {type(exc).__name__}: {exc}"
            if self._workflow_uses_prompt_fragments(run):
                run.prompt_fragments.append(f"<subagent_error>{error_text}</subagent_error>")
            run.conversation_messages.append({"role": "user", "content": error_text})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_error")
            agent_events.audit_named_event(
                run_id=run.run_id,
                agent_id=self.agent_id,
                event={
                    "type": "subagent_batch_finished",
                    "batch_id": batch_id,
                    "status": "error",
                    "error": str(exc),
                    **self._workflow_event_metadata(run),
                },
            )
            raise

        statuses = [r.status for r in results]
        agent_events.audit_named_event(
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "subagent_batch_finished",
                "batch_id": batch_id,
                "status": "ok",
                "completed": statuses.count("completed"),
                "failed": statuses.count("failed"),
                "timed_out": statuses.count("timed_out"),
                "blocked": statuses.count("blocked"),
                **self._workflow_event_metadata(run),
            },
        )
        self._emit_subagent_batch_results(host, run, results)
        return results

    def _emit_subagent_batch_results(
        self,
        host: "AgentHostProtocol",
        run: AgentRun,
        results: list[Any],
    ) -> None:
        """Build the aggregated <subagent_results> fragment and add it to the run."""
        lines = ["<subagent_results>"]
        for r in results:
            attrs = f'key="{r.output_key}" agent_id="{r.subagent_id}" status="{r.status}"'
            if r.status == "blocked" and r.callback_intent:
                attrs += f' intent="{r.callback_intent}"'
            payload = _render_result_for_injection(r)
            if payload:
                lines.append(f"  <subagent_result {attrs}>{payload}</subagent_result>")
            else:
                lines.append(f"  <subagent_result {attrs}/>")
        lines.append("</subagent_results>")
        fragment = "\n".join(lines)

        run.transcript_entries.append(fragment)
        if self._workflow_uses_prompt_fragments(run):
            run.prompt_fragments.append(fragment)
            run.conversation_messages.append({"role": "user", "content": fragment})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_batch_results")
        elif self._active_workflow_model_step(run) is not None:
            payload = {
                getattr(r, "output_key", f"result_{index}"): self._workflow_jsonable_summary(r)
                for index, r in enumerate(results)
            }
            self._append_workflow_history_projection(
                host,
                run,
                WorkflowHistoryEvent(
                    kind="subagent_batch_result",
                    step_id=run.workflow_step_id or "",
                    phase_id=run.workflow_phase_id or "",
                    message=fragment,
                    response=payload,
                    payload=results,
                ),
                role="user",
                source="subagent_batch_results",
            )
        else:
            run.conversation_messages.append({"role": "user", "content": fragment})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "subagent_batch_results")

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
        allowed_tools = self._effective_allowed_tools(host)
        if not decision.tool_name:
            error_text = f"call_tool requires tool_name. Legal tool names: {sorted(allowed_tools)}."
            if self._workflow_uses_prompt_fragments(run):
                run.prompt_fragments.append(f"<tool_error>{error_text}</tool_error>")
            run.transcript_entries.append(f"<tool_error>{error_text}</tool_error>")
            run.conversation_messages.append({"role": "user", "content": error_text})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "tool_validation_error")
            return None
        if decision.tool_name not in allowed_tools:
            error_text = (
                f"{self.agent_id} is not allowed to call tool {decision.tool_name}. "
                f"Legal tool names: {sorted(allowed_tools)}."
            )
            if self._workflow_uses_prompt_fragments(run):
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

        tc = self._execute_tool_step(
            host=host,
            run=run,
            caller_id=caller_id,
            tool_name=decision.tool_name,
            tool_input=dict(decision.parameters),
            decision=decision,
        )
        if tc.early_exit is not None:
            return tc.early_exit

        run.transcript_entries.append(
            f"<tool_call name=\"{decision.tool_name}\">{_stringify_parameter_value(tc.effective_input)}</tool_call>"
        )
        run.conversation_messages.append(
            {"role": "assistant", "content": f"Tool call {decision.tool_name}: {_stringify_parameter_value(tc.effective_input)}"}
        )
        _emit_context_updated(self, host, run, run.conversation_messages[-1], "tool_call")
        if tc.error_exc is not None:
            exc = tc.error_exc
            if tc.system_message and self._workflow_uses_prompt_fragments(run):
                run.prompt_fragments.append(f"<system_message>{tc.system_message}</system_message>")
            if self._workflow_uses_prompt_fragments(run):
                run.prompt_fragments.append(
                    f"<tool_error name=\"{decision.tool_name}\">{type(exc).__name__}: {exc}</tool_error>"
                )
            run.transcript_entries.append(
                f"<tool_error name=\"{decision.tool_name}\">{type(exc).__name__}: {exc}</tool_error>"
            )
            run.conversation_messages.append(
                {"role": "user", "content": f"Tool error {decision.tool_name}: {type(exc).__name__}: {exc}"}
            )
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "tool_error")
            return None
        if tc.system_message and self._workflow_uses_prompt_fragments(run):
            run.prompt_fragments.append(f"<system_message>{tc.system_message}</system_message>")
        run.transcript_entries.append(f"<tool_result name=\"{decision.tool_name}\">{tc.result}</tool_result>")
        if self._workflow_uses_prompt_fragments(run):
            run.conversation_messages.append({"role": "user", "content": f"Tool result {decision.tool_name}: {tc.result}"})
            _emit_context_updated(self, host, run, run.conversation_messages[-1], "tool_result")
            run.prompt_fragments.append(f"<tool_result name=\"{decision.tool_name}\">{tc.result}</tool_result>")
        else:
            self._append_workflow_history_projection(
                host,
                run,
                WorkflowHistoryEvent(
                    kind="tool_result",
                    step_id=run.workflow_step_id or "",
                    phase_id=run.workflow_phase_id or "",
                    decision=decision,
                    result=tc.result,
                    message=f"Tool result {decision.tool_name}: {tc.result}",
                    payload=tc.result,
                    metadata={"tool_name": decision.tool_name},
                ),
                role="user",
                source="tool_result",
            )
        return None

    def _create_run(
        self,
        parameters: dict[str, Any],
        *,
        run_id: str | None = None,
        parent_run_id: str | None = None,
        in_parallel_batch: bool = False,
        rendered_prompt_override: str | None = None,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
        prompt_fragments: tuple[str, ...] | None = None,
    ) -> AgentRun:
        """Create runtime state for a new invocation."""
        seed_parameters = dict(parameters)
        return AgentRun(
            run_id=run_id or str(uuid4()),
            parent_run_id=parent_run_id,
            in_parallel_batch=in_parallel_batch,
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

    def _execute_tool_step(
        self,
        *,
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        tool_name: str,
        tool_input: dict[str, Any],
        decision: AgentDecision | None = None,
    ) -> "_ToolCallResult":
        """Fire pre/post hooks, emit audit events, and execute a tool.

        Handles the hook scaffolding shared by both the LLM decision loop
        (``handle_tool_call``) and ``WorkflowCallToolStep``. Does NOT inject
        results into the conversation or transcript — that is the caller's
        responsibility.
        """
        from agent_framework.agent_event_publisher import agent_events

        tool_call_id = str(uuid4())
        event = ToolStartEvent(
            invocation=self._hook_invocation(run, caller_id),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_input=tool_input,
            decision=decision,
        )
        pre_decision = self._run_pre_tool_hooks(host=host, run=run, caller_id=caller_id, event=event)
        if pre_decision.final_result is not None:
            return _ToolCallResult(effective_input=tool_input, result=None, system_message=None, error_exc=None, early_exit=pre_decision.final_result)
        if not pre_decision.continue_run:
            return _ToolCallResult(effective_input=tool_input, result=None, system_message=None, error_exc=None, early_exit=AgentResult(status="stopped", message="", prompt=run.rendered_prompt))

        effective_input = pre_decision.updated_tool_input or dict(event.tool_input)
        agent_events.audit_named_event(
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "tool_call",
                "tool_name": tool_name,
                "parameters": dict(effective_input),
                **self._workflow_event_metadata(run),
            },
        )
        try:
            result = host.execute_tool(tool_name, effective_input)
        except Exception as exc:
            agent_events.on_tool_execution_failed(run_id=run.run_id, agent_id=self.agent_id, tool_name=tool_name, exc=exc)
            agent_events.audit_named_event(
                run_id=run.run_id,
                agent_id=self.agent_id,
                event={
                    "type": "tool_error",
                    "tool_name": tool_name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    **self._workflow_event_metadata(run),
                },
            )
            return _ToolCallResult(effective_input=effective_input, result=None, system_message=pre_decision.system_message, error_exc=exc, early_exit=None)

        self._run_post_tool_hooks(
            host=host,
            run=run,
            caller_id=caller_id,
            event=ToolEndEvent(
                invocation=event.invocation,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_input=effective_input,
                result=result,
            ),
        )
        agent_events.audit_named_event(
            run_id=run.run_id,
            agent_id=self.agent_id,
            event={
                "type": "tool_result",
                "tool_name": tool_name,
                "result": result,
                **self._workflow_event_metadata(run),
            },
        )
        return _ToolCallResult(effective_input=effective_input, result=result, system_message=pre_decision.system_message, error_exc=None, early_exit=None)

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
        for callback in self.on_pre_tool:
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
        for callback in self.on_post_tool:
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
        for callback in self.on_pre_subagent:
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
        for callback in self.on_post_subagent:
            callback(event)

    def _run_pre_skill_hooks(self, *, run: AgentRun, event: SkillStartEvent) -> None:
        """Execute all subscribed pre-skill callbacks sequentially."""
        run.history.append(f"before_skill:{event.skill_name}")
        for callback in self.on_pre_skill:
            callback(event)

    def _run_post_skill_hooks(self, *, run: AgentRun, event: SkillEndEvent) -> None:
        """Execute all subscribed post-skill callbacks sequentially."""
        run.history.append(f"after_skill:{event.skill_name}")
        for callback in self.on_post_skill:
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
        for callback in self.on_pre_model:
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
        for callback in self.on_post_model:
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
            workflow_step_id=run.workflow_step_id,
            workflow_phase_id=run.workflow_phase_id,
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
        for callback in self.on_pre_agent:
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

        for callback in self.on_post_agent:
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

    def _normalize_decision_capabilities(
        self,
        decision: AgentDecision,
        *,
        host: "AgentHostProtocol",
    ) -> AgentDecision:
        """Repair tool vs subagent *slots* when they disagree with declared capabilities.

        **Intentional and confirmed:** This is not open-ended semantic inference on unknown
        ``kind`` strings. Only the branches below apply: the model used an allowed ``kind``
        but put a declared child-agent id or tool name in the wrong field, or used
        ``callback`` while filling a slot that uniquely matches a declared capability.
        Repair is keyed solely against ``allowed_child_agents`` / ``allowed_tools``.

        Both ``subagent_id`` and ``tool_name`` non-empty is always rejected (ambiguous).
        """
        allowed_tools = self._effective_allowed_tools(host)
        if decision.subagent_id is not None and decision.tool_name is not None:
            raise ValueError(
                "Invalid model decision: both subagent_id and tool_name are set; "
                "use exactly one of call_tool, call_subagent, or callback with a single target."
            )

        if decision.kind in {"callback", "callback_to_caller", "request_user_input", "request_resolution"}:
            if decision.subagent_id in self.allowed_child_agents:
                _LOGGER.warning(
                    "Agent %s: decision kind mismatch — model emitted %s but subagent_id=%r "
                    "matches a declared child agent; normalizing to call_subagent (intentional slot repair).",
                    self.agent_id,
                    decision.kind,
                    decision.subagent_id,
                )
                return AgentDecision(
                    kind="call_subagent",
                    message=decision.message,
                    parameters=dict(decision.parameters),
                    subagent_id=decision.subagent_id,
                    callback_intent=decision.callback_intent,
                )
            if decision.tool_name in allowed_tools:
                _LOGGER.warning(
                    "Agent %s: decision kind mismatch — model emitted %s but tool_name=%r "
                    "matches a declared tool; normalizing to call_tool (intentional slot repair).",
                    self.agent_id,
                    decision.kind,
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
                and decision.tool_name not in allowed_tools
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
            if decision.subagent_id is None and decision.tool_name in allowed_tools:
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
                and decision.subagent_id in allowed_tools
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

    def _effective_allowed_tools(self, host: "AgentHostProtocol") -> tuple[str, ...]:
        """Return declared tools plus host-provided default tools."""
        names = list(self.allowed_tools)
        default_tools_fn = getattr(host, "get_default_agent_tool_names", None)
        if callable(default_tools_fn):
            for name in default_tools_fn():
                if name not in names:
                    names.append(name)
        return tuple(names)

__all__ = ["Agent"]


def _parse_callback_routing_policy(runtime_metadata: dict[str, object]) -> CallbackRoutingPolicy:
    """Parse callback routing defaults from adjacent runtime metadata."""
    raw = runtime_metadata.get("callback_policy")
    if not isinstance(raw, dict):
        return CallbackRoutingPolicy()
    max_hops_raw = raw.get("max_bubble_hops")
    max_hops: int | None
    if max_hops_raw in (None, ""):
        max_hops = None
    else:
        max_hops = int(max_hops_raw)
        if max_hops < 0:
            raise ValueError("callback_policy.max_bubble_hops must be >= 0.")
    fallback_target = str(raw.get("fallback_target", "user")).strip().lower() or "user"
    if fallback_target not in {"user", "fail"}:
        raise ValueError("callback_policy.fallback_target must be 'user' or 'fail'.")
    return CallbackRoutingPolicy(
        passthrough_child_callbacks=bool(raw.get("passthrough_child_callbacks", False)),
        max_bubble_hops=max_hops,
        fallback_target=fallback_target,
    )


def _coerce_bubble_hops(raw: object) -> int:
    """Normalize decision bubble hop count metadata."""
    if raw in (None, ""):
        return 0
    value = int(raw)
    return value if value >= 0 else 0


def _merge_callback_routing_policy(
    base: CallbackRoutingPolicy,
    parameters: dict[str, Any],
) -> CallbackRoutingPolicy:
    """Merge per-decision routing overrides into the agent default policy."""
    max_hops_raw = parameters.get("max_bubble_hops")
    max_hops = base.max_bubble_hops if max_hops_raw in (None, "") else int(max_hops_raw)
    fallback_target_raw = parameters.get("fallback_target")
    fallback_target = (
        base.fallback_target
        if fallback_target_raw in (None, "")
        else str(fallback_target_raw).strip().lower()
    )
    if fallback_target not in {"user", "fail"}:
        fallback_target = base.fallback_target
    passthrough = (
        base.passthrough_child_callbacks
        if "passthrough_child_callbacks" not in parameters
        else bool(parameters.get("passthrough_child_callbacks"))
    )
    return CallbackRoutingPolicy(
        passthrough_child_callbacks=passthrough,
        max_bubble_hops=max_hops,
        fallback_target=fallback_target,
    )
