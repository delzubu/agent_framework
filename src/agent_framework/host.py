"""Root host for loading agents, tools, and servicing runtime interactions."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait as _futures_wait
from datetime import datetime, timezone
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from uuid import uuid4

from agent_framework.agent import Agent, AgentResult, CallContext, ModelEndEvent, ModelStartEvent, SequentialHook
from agent_framework.agents.agent_decision import SubagentCallSpec
from agent_framework.agent_registry import AgentRegistry
from agent_framework.agent_event_publisher import agent_events
from agent_framework.audit_trace import AuditTraceSubscriber, InMemoryAuditTracer
from agent_framework.command import CommandRegistry, render as render_command
from agent_framework.config import (
    DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES,
    HostConfig,
    load_host_config,
)
from agent_framework.drivers import OpenAiModelDriver
from agent_framework.model import (
    AsyncModelDriver,
    AsyncToSyncAdapter,
    CapabilityDefinition,
    DEFAULT_RESPONSE_MODE,
    ModelContext,
    ModelDriver,
    ModelResponse,
    merge_runtime_system_into_messages,
)
from agent_framework.model_overrides import normalize_model_override_names
from agent_framework.model_validation import ModelValidationChain, ModelValidationContext
from agent_framework.file_reference import (
    DefaultFileReferenceResolver,
    FileReferenceResolver,
    expand_file_refs,
    replace_file_blocks,
)
from agent_framework.skill import SkillRegistry
from agent_framework.tool import Tool, ToolDefinition
from agent_framework.tool_registry import ToolRegistry
from agent_framework.llm_trace_logging import wire_llm_traces_to_runtime_tracer
from agent_framework.interaction import PendingInteraction
from agent_framework.memory import (
    CatalogMemoryQueryProvider,
    ConfiguredMemoryScopeResolver,
    InMemoryMemoryBackend,
    MemoryBackend,
    MemoryEntry,
    MemoryProjector,
    MemoryQueryHit,
    MemoryQueryProvider,
    MemoryRef,
    MemoryScope,
    MemoryScopeResolver,
    XmlMemoryProjector,
    _entry_from_content,
    _size_bytes_for_content,
    build_memory_uri,
    find_memory_uris,
    next_memory_version,
    parse_memory_uri,
)
from agent_framework.tracing import (
    CompositeRuntimeTracer,
    NullRuntimeTracer,
    RuntimeTracer,
    TraceContext,
    TraceSubscriber,
    make_trace_event,
)
from agent_framework.tracing_bridge import active_tracer_scope
from agent_framework.user_communication import NullUserCommunication, UserCommunication
from agent_framework.usage_tracking import RuntimeUsageTracker

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubagentBatchItemResult:
    """Result for one entry in a call_subagents batch."""

    output_key: str
    subagent_id: str
    run_id: str
    status: str  # "completed" | "failed" | "timed_out" | "blocked"
    message: str = ""
    parameters: dict[str, Any] | None = None
    parameters_injection: str = "override"
    callback_intent: str | None = None
    callback_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class RunRegistration:
    """Minimal lineage record for one active or recently active run."""

    run_id: str
    agent_id: str
    caller_id: str | None
    parent_run_id: str | None


def _agent_host_receive_log_enabled_from_env() -> bool:
    """Default on; set ``AGENT_HOST_RECEIVE_LOG=0`` / ``false`` / ``no`` / ``off`` to disable."""
    raw = os.environ.get("AGENT_HOST_RECEIVE_LOG")
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(slots=True)
class AgentHost:
    """Runtime host for agents, tools, skills, and headless model invocations.

    Attributes:
        config: Typed runtime configuration loaded from ``.env``.
        model_driver: Provider-backed model driver used by all agents.  May be
            a sync ``ModelDriver`` or an async ``AsyncModelDriver`` — the host
            bridges between the two transparently.
        agent_registry: Formal AgentRegistry with discover/cache semantics.
        tool_registry: Formal ToolRegistry with discover/cache semantics.
        command_registry: Formal CommandRegistry for slash-commands.
        user_comm: UserCommunication implementation (console, web, null, etc).
        mcp_manager: Optional MCP manager for bridging MCP tools.
        contexts: Call contexts opened during execution.
        conversation_store: Optional conversation store for multi-turn sessions.
            When set, ``complete()`` / ``complete_async()`` can load and persist
            message history by ``conversation_id``.
        _executor: Thread pool used for optional parallel subagent execution.
    """

    config: HostConfig
    model_driver: ModelDriver | AsyncModelDriver | None = None
    tool_registry: ToolRegistry = field(default_factory=lambda: ToolRegistry(directories=()))
    agent_registry: AgentRegistry = field(default_factory=lambda: AgentRegistry(directories=(), config=None))
    command_registry: CommandRegistry = field(default_factory=lambda: CommandRegistry(directories=()))
    user_comm: UserCommunication | None = None
    mcp_manager: Any = None  # McpManager | None (optional dependency)
    contexts: dict[str, CallContext] = field(default_factory=dict)
    on_pre_model: SequentialHook = field(default_factory=SequentialHook)
    on_post_model: SequentialHook = field(default_factory=SequentialHook)
    runtime_tracer: RuntimeTracer = field(default_factory=NullRuntimeTracer)
    trace_context_overlay: TraceContext | None = None
    _audit_jsonl: InMemoryAuditTracer | None = field(default=None, repr=False)
    _audit_trace_subscriber: AuditTraceSubscriber | None = field(default=None, repr=False)
    _llm_traces_wired: bool = field(default=False, repr=False)
    skill_registry: SkillRegistry | None = None
    memory_backend: MemoryBackend | None = None
    memory_query_provider: MemoryQueryProvider | None = None
    memory_projector: MemoryProjector | None = None
    memory_scope_resolver: MemoryScopeResolver | None = None
    conversation_store: Any | None = None  # ConversationStore | AsyncConversationStore | None
    file_ref_resolver: FileReferenceResolver | None = field(
        default_factory=DefaultFileReferenceResolver, repr=False
    )
    _executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=8))
    _command_fallback: Callable[[str, str], Awaitable[str | None]] | None = None
    _started: bool = False
    _registries_discovered: bool = field(default=False, repr=False)
    _host_receive_log_subscriber: TraceSubscriber | None = field(default=None, repr=False)
    _host_receive_log_path: Path | None = field(default=None, repr=False)
    # Hierarchical run ID — stable for the lifetime of this host instance.
    session_id: str = field(default_factory=lambda: str(uuid4())[:12], repr=False)
    _prompt_counter: int = field(default=0, repr=False)
    _prompt_counter_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # Checkpoint storage for blocked parallel children (run_id → (messages, timestamp)).
    _checkpoints: dict[str, tuple[list, float]] = field(default_factory=dict, repr=False)
    _checkpoint_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _timed_out_run_ids: set[str] = field(default_factory=set, repr=False)
    _timed_out_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    pending_interactions: dict[str, PendingInteraction] = field(default_factory=dict, repr=False)
    _pending_interactions_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _run_registry: dict[str, RunRegistration] = field(default_factory=dict, repr=False)
    _run_registry_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    runtime_usage_tracker: RuntimeUsageTracker = field(default_factory=RuntimeUsageTracker, repr=False)
    model_validation_chain: ModelValidationChain = field(
        default_factory=ModelValidationChain.with_defaults,
        repr=False,
    )

    @property
    def audit_tracer(self) -> InMemoryAuditTracer | None:
        """JSONL audit store when :meth:`enable_audit_trace` is used (read-only)."""
        return self._audit_jsonl

    def register_model_exception_validator(self, validator: Any) -> None:
        """Append one model-call exception validator to the runtime chain."""
        self.model_validation_chain.register_exception_validator(validator)

    def register_model_response_validator(self, validator: Any) -> None:
        """Append one parsed-response validator to the runtime chain."""
        self.model_validation_chain.register_response_validator(validator)

    def validate_model_exception(
        self,
        exc: BaseException,
        *,
        validation_context: ModelValidationContext,
    ) -> BaseException:
        """Run registered exception validators and return the final exception."""
        return self.model_validation_chain.validate_exception(
            exc,
            context=validation_context,
        )

    def validate_model_response(
        self,
        response: ModelResponse,
        *,
        validation_context: ModelValidationContext,
    ) -> None:
        """Run registered parsed-response validators."""
        self.model_validation_chain.validate_response(
            response,
            context=validation_context,
        )

    @property
    def host_receive_log_path(self) -> Path | None:
        """Path of the unified trace JSONL file when :meth:`enable_host_receive_log` is active."""
        return self._host_receive_log_path

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        env_path: str | Path = ".env",
        *,
        model_driver: Any | None = None,
        model_override: str | tuple[str, ...] | None = None,
        all_agents_model_override: str | tuple[str, ...] | None = None,
        user_comm: Any | None = None,
        input_reader: Any = None,  # deprecated; ignored (kept for test compat)
        output_writer: Any = None,  # deprecated; ignored (kept for test compat)
    ) -> "AgentHost":
        """Construct a host from ``.env`` configuration.

        Auto-detects the driver type from ``DEFAULT_PROVIDER``:
        - ``dial``: constructs a ``DialChatCompletionsDriver`` (requires
          ``agent_framework[dial]`` to be installed).
        - ``openai`` (default): constructs an ``OpenAiModelDriver``.

        Note: Does NOT call ``start()`` — callers must await ``host.start()``
        (or use ``from_env_console`` which does it synchronously) to run
        registry discovery and MCP startup.

        Args:
            model_override: When provided, overrides ``DEFAULT_MODEL`` from the
                ``.env`` file.  Accepts a comma-separated string or a tuple of
                model names (first = highest priority).  This is the programmatic
                mechanism for runtime model selection; no default behaviour is
                added here.
            all_agents_model_override: When provided, forces every agent loaded
                by this host to use the given model tuple, overriding agent-side
                runtime metadata and ``AGENT_MODELS`` for this host instance.
            user_comm: Optional ``UserCommunication`` implementation.  Defaults
                to ``NullUserCommunication`` inside ``create()``.
        """
        from dataclasses import replace as _replace

        config = load_host_config(env_path)
        if model_override is not None:
            if isinstance(model_override, str):
                model_override = tuple(m.strip() for m in model_override.split(",") if m.strip())
            config = _replace(config, default_model=model_override)
        if model_driver is None:
            if config.default_provider == "dial" and config.dial_base_url:
                from agent_framework.drivers.dial import DialChatCompletionsDriver

                model_driver = DialChatCompletionsDriver(
                    base_url=config.dial_base_url,
                    api_version=config.dial_api_version,
                    api_key=config.dial_api_key,
                )
            else:
                model_driver = OpenAiModelDriver(api_key=config.openai_api_key)
        host = cls.create(
            model_driver=model_driver,
            config=config,
            user_comm=user_comm,
            all_agents_model_override=all_agents_model_override,
        )
        host.enable_audit_trace(output_dir="logs")
        if _agent_host_receive_log_enabled_from_env():
            try:
                host.enable_host_receive_log(output_dir="logs")
            except OSError as exc:
                _LOGGER.warning("host receive log not started (%s)", exc)
        # Eagerly run synchronous registry discovery so disk-backed tools/agents
        # are resolvable without requiring the caller to await start().  MCP
        # startup still only happens in start().
        host.tool_registry.discover()
        host.agent_registry.discover()
        host.command_registry.discover()
        host._registries_discovered = True
        return host

    @classmethod
    def from_env_console(
        cls,
        env_path: str | Path = ".env",
        *,
        model_driver: Any | None = None,
        model_override: str | tuple[str, ...] | None = None,
        all_agents_model_override: str | tuple[str, ...] | None = None,
    ) -> "AgentHost":
        """Construct a console host, run discovery, and start MCP connections."""
        from agent_framework.console_communication import ConsoleUserCommunication

        host = cls.from_env(
            env_path,
            model_driver=model_driver,
            model_override=model_override,
            all_agents_model_override=all_agents_model_override,
            user_comm=ConsoleUserCommunication(),
        )
        # Run start() synchronously to discover registries and start MCP
        import concurrent.futures

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            asyncio.run(host.start())
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(asyncio.run, host.start()).result()
        return host

    @classmethod
    def create(
        cls,
        *,
        model_driver: Any,
        config: HostConfig | None = None,
        conversation_store: Any | None = None,
        user_comm: Any | None = None,
        builtin_tools: bool = True,
        mcp_enabled: bool = True,
        command_fallback: Any | None = None,
        all_agents_model_override: str | tuple[str, ...] | None = None,
    ) -> "AgentHost":
        """Construct a host with an explicit driver.  No ``.env`` file required.

        This is the preferred entry point for programmatic use (e.g. from
        dial-agent or other FastAPI services) where configuration comes from
        the application's own settings rather than a ``.env`` file.

        Args:
            model_driver: A sync ``ModelDriver`` or async ``AsyncModelDriver``.
            config: Optional ``HostConfig``.  Defaults to a minimal config with
                all paths set to sensible defaults.
            conversation_store: Optional ``ConversationStore`` or
                ``AsyncConversationStore`` for multi-turn sessions.
            user_comm: Optional ``UserCommunication``.  Defaults to
                ``NullUserCommunication`` when not provided.
            builtin_tools: When True (default), registers all built-in tools
                into the tool registry.
            mcp_enabled: When True (default), attempts to load MCP configs and
                construct an ``McpManager``.  Actual connections happen in
                ``start()``.
            command_fallback: Optional async callable
                ``(name, raw_args) -> str | None`` invoked by
                ``execute_command`` when the command registry has no match.
        """
        if config is None:
            config = HostConfig()

        tool_registry = ToolRegistry.from_config(config)
        agent_registry = AgentRegistry.from_config(config)
        agent_registry.runtime_model_override = normalize_model_override_names(
            all_agents_model_override
        )
        command_registry = CommandRegistry.from_config(config)

        if builtin_tools:
            from agent_framework.builtin_tools import register_builtin_tools
            register_builtin_tools(tool_registry)
            if getattr(config, "memory_enabled", True) and getattr(config, "memory_builtin_tools_enabled", True):
                from agent_framework.memory_tools import register_memory_tools

                register_memory_tools(tool_registry)

        mcp_manager: Any = None
        if mcp_enabled and getattr(config, "mcp_enabled", True):
            try:
                from agent_framework.mcp import McpManager, load_mcp_configs
                mcp_configs = load_mcp_configs(
                    explicit_path=getattr(config, "mcp_config_path", None),
                )
                if mcp_configs:
                    mcp_manager = McpManager(configs=mcp_configs)
            except ImportError:
                pass

        if user_comm is None:
            user_comm = NullUserCommunication()

        host = cls(
            config=config,
            model_driver=model_driver,
            tool_registry=tool_registry,
            agent_registry=agent_registry,
            command_registry=command_registry,
            user_comm=user_comm,
            mcp_manager=mcp_manager,
            conversation_store=conversation_store,
            _command_fallback=command_fallback,
        )
        return host

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Discover all registries and start MCP servers.  Idempotent."""
        if self._started:
            _LOGGER.debug("start() skipped: host already started")
            return
        if not self._registries_discovered:
            self.tool_registry.discover()
            self.agent_registry.discover()
            self.command_registry.discover()
            self._registries_discovered = True
        # Ensure skill registry is initialized
        self.get_skill_registry()
        # Start MCP if configured
        if self.mcp_manager is not None:
            errors = await self.mcp_manager.start_all()
            for name, err in errors.items():
                if err is not None:
                    _LOGGER.warning("MCP server %r failed to connect: %s", name, err)
            # Bridge MCP tools into tool registry
            from agent_framework.mcp.tools import bridge_mcp_tools
            bridge_mcp_tools(self.mcp_manager, self.tool_registry, self._run_user_comm_coro)
        # Wrap user_comm with tracing decorator when JSONL audit is enabled
        if self._audit_jsonl is not None and self.user_comm is not None:
            self.user_comm = _TracingUserCommunication(self.user_comm, self._audit_jsonl)
        if not isinstance(self.runtime_tracer, NullRuntimeTracer):
            wire_llm_traces_to_runtime_tracer(self)
            agent_events.attach_log_sources()
        self._started = True
        _LOGGER.info(
            "host started (tools=%s, agents=%s, commands=%s, mcp=%s)",
            len(self.tool_registry.list_names()),
            len(self.agent_registry.list_names()),
            len(self.command_registry.get_all()),
            self.mcp_manager is not None,
        )

    async def aclose(self) -> None:
        """Shut down MCP connections and close async driver if applicable."""
        _LOGGER.debug("aclose: shutting down MCP and model driver")
        if self.mcp_manager is not None:
            await self.mcp_manager.stop_all()
        driver = self.model_driver
        if driver is not None and hasattr(driver, "aclose"):
            await driver.aclose()

    def _run_user_comm_coro(self, coro: "Awaitable[Any]") -> Any:
        """Run an async coroutine synchronously (bridges sync tool invoke → async user_comm).

        Uses ``asyncio.run()`` when no loop is running, or a thread-pool
        executor trick to avoid nested-loop errors (same pattern as
        ``AsyncToSyncAdapter``).
        """
        import concurrent.futures

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            return asyncio.run(coro)
        # Running inside an event loop — run in a separate thread with its own loop
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            fut = executor.submit(asyncio.run, coro)
            return fut.result()

    async def execute_command(self, name: str, raw_args: str = "") -> str | None:
        """Render and return a command prompt, or invoke the fallback for unknown commands.

        Returns the rendered prompt string when the command is found (caller
        decides what to do with it, e.g. pass it to ``run_agent``).  Returns
        ``None`` when the command is unknown and no fallback is registered.
        """
        try:
            cmd = self.command_registry.get(name)
            return render_command(cmd, raw_args)
        except KeyError:
            if self._command_fallback is not None:
                return await self._command_fallback(name, raw_args)
            return None

    # ------------------------------------------------------------------
    # Driver access
    # ------------------------------------------------------------------

    def get_root_agent(self) -> Agent:
        """Load and return the root agent configured in ``.env``."""
        return self.get_agent(self.config.root_agent_id)

    def get_model_driver(self, agent: Agent) -> ModelDriver:
        """Return the model driver for use in the sync agent loop.

        If the configured driver is async, it is wrapped with
        ``AsyncToSyncAdapter`` transparently so the existing agent loop works
        without modification.
        """
        if self.model_driver is None:
            raise ValueError("AgentHost requires a model driver.")
        if asyncio.iscoroutinefunction(getattr(self.model_driver, "decide", None)):
            return AsyncToSyncAdapter(self.model_driver)
        return self.model_driver

    # ------------------------------------------------------------------
    # Headless model invocation (G-08)
    # ------------------------------------------------------------------

    def complete(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        model_names: str | tuple[str, ...] | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
        response_mode: str = DEFAULT_RESPONSE_MODE,
        tools: Sequence[ToolDefinition] | None = None,
        skills: Sequence[CapabilityDefinition] | None = None,
        subagents: Sequence[CapabilityDefinition] | None = None,
        conversation_id: str | None = None,
    ) -> ModelResponse:
        """Single-turn model call without loading an agent definition.

        Applies the full host-level lifecycle: trace callbacks and audit
        recording.  When ``conversation_id`` and a ``conversation_store`` are
        configured, loads prior messages from the store, appends the new
        messages, and persists the assistant response back.

        Args:
            messages: Chat messages to send.  May include history.
            model_names: Model(s) to use.  Accepts a comma-separated string,
                a tuple of model names, or ``None`` to use
                ``config.default_model``.  When multiple models are given the
                driver tries them in order (first = highest priority).
            temperature: Sampling temperature.
            response_format: Provider-native response format (forwarded to
                drivers that support it, e.g. ``{"type": "json_object"}``).
            response_mode: ``"json_object"`` (default) or ``"text"``.  Controls
                how the driver parses the model output.  Use ``"text"`` for
                plain-text responses or tool-calling loops where JSON parsing
                of the assistant turn is not needed.
            tools: Tool definitions to expose to the model.
            skills: Optional skill capabilities (``CapabilityDefinition``) for the
                runtime envelope (e.g. ``invoke_skill`` metadata). Headless
                ``complete`` does not execute skill or subagent callbacks—callers
                that emit those decisions need a multi-step loop (see
                :meth:`run_agent`).
            subagents: Optional child-agent capabilities for the runtime envelope.
            conversation_id: If provided and a ``conversation_store`` is
                attached, prior messages are prepended and the response is
                appended to the store.

        Returns:
            ``ModelResponse`` with the model's reply.
        """
        resolved_model_names = _normalize_model_names(model_names, self.config.default_model)
        all_messages = self._load_conversation(conversation_id, messages)
        run_id = str(uuid4())
        context = merge_runtime_system_into_messages(
            ModelContext(
                system_prompt="",
                user_prompt="",
                messages=tuple(all_messages),
                response_mode=response_mode,
                response_format=response_format,
                tools=tuple(tools or []),
                skills=tuple(skills or ()),
                subagents=tuple(subagents or ()),
                run_id=run_id,
            )
        )
        driver = self.get_model_driver_raw()
        if asyncio.iscoroutinefunction(getattr(driver, "decide", None)):
            driver = AsyncToSyncAdapter(driver)
        response = driver.decide(
            agent_id=None,
            provider_name=self.config.default_provider,
            model_names=resolved_model_names,
            temperature=temperature,
            context=context,
        )
        self._persist_response(conversation_id, all_messages, messages, response)
        return response

    async def complete_async(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        model_names: str | tuple[str, ...] | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
        response_mode: str = DEFAULT_RESPONSE_MODE,
        tools: Sequence[ToolDefinition] | None = None,
        skills: Sequence[CapabilityDefinition] | None = None,
        subagents: Sequence[CapabilityDefinition] | None = None,
        conversation_id: str | None = None,
    ) -> ModelResponse:
        """Async single-turn model call without loading an agent definition.

        Uses the async driver directly if available, otherwise runs the sync
        driver via ``asyncio.to_thread``.

        See ``complete()`` for parameter documentation.
        """
        resolved_model_names = _normalize_model_names(model_names, self.config.default_model)
        all_messages = await self._load_conversation_async(conversation_id, messages)
        run_id = str(uuid4())
        context = merge_runtime_system_into_messages(
            ModelContext(
                system_prompt="",
                user_prompt="",
                messages=tuple(all_messages),
                response_mode=response_mode,
                response_format=response_format,
                tools=tuple(tools or []),
                skills=tuple(skills or ()),
                subagents=tuple(subagents or ()),
                run_id=run_id,
            )
        )
        driver = self.get_model_driver_raw()
        if asyncio.iscoroutinefunction(getattr(driver, "decide", None)):
            response = await driver.decide(
                agent_id=None,
                provider_name=self.config.default_provider,
                model_names=resolved_model_names,
                temperature=temperature,
                context=context,
            )
        else:
            response = await asyncio.to_thread(
                driver.decide,
                agent_id=None,
                provider_name=self.config.default_provider,
                model_names=resolved_model_names,
                temperature=temperature,
                context=context,
            )
        await self._persist_response_async(conversation_id, all_messages, messages, response)
        return response

    def get_model_driver_raw(self) -> Any:
        """Return the raw driver (sync or async) without any adapter wrapping."""
        if self.model_driver is None:
            raise ValueError("AgentHost requires a model driver.")
        return self.model_driver

    # ------------------------------------------------------------------
    # Conversation store helpers
    # ------------------------------------------------------------------

    def _load_conversation(
        self,
        conversation_id: str | None,
        new_messages: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        store = self.conversation_store
        if conversation_id is None or store is None:
            return list(new_messages)
        prior = store.get_messages(conversation_id)
        return prior + list(new_messages)

    async def _load_conversation_async(
        self,
        conversation_id: str | None,
        new_messages: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        store = self.conversation_store
        if conversation_id is None or store is None:
            return list(new_messages)
        if asyncio.iscoroutinefunction(getattr(store, "get_messages", None)):
            prior = await store.get_messages(conversation_id)
        else:
            prior = store.get_messages(conversation_id)
        return prior + list(new_messages)

    def _persist_response(
        self,
        conversation_id: str | None,
        all_messages: list[dict[str, Any]],
        new_messages: Sequence[dict[str, Any]],
        response: ModelResponse,
    ) -> None:
        store = self.conversation_store
        if conversation_id is None or store is None:
            return
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": response.raw_text}
        store.append(conversation_id, list(new_messages) + [assistant_msg])

    async def _persist_response_async(
        self,
        conversation_id: str | None,
        all_messages: list[dict[str, Any]],
        new_messages: Sequence[dict[str, Any]],
        response: ModelResponse,
    ) -> None:
        store = self.conversation_store
        if conversation_id is None or store is None:
            return
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": response.raw_text}
        msgs_to_append = list(new_messages) + [assistant_msg]
        if asyncio.iscoroutinefunction(getattr(store, "append", None)):
            await store.append(conversation_id, msgs_to_append)
        else:
            store.append(conversation_id, msgs_to_append)

    # ------------------------------------------------------------------
    # Audit trace
    # ------------------------------------------------------------------

    def enable_audit_trace(self, *, output_dir: str | Path = "logs") -> InMemoryAuditTracer:
        """Enable immutable in-memory audit tracing plus JSONL dumping.

        Subscribes :class:`AuditTraceSubscriber` to :attr:`runtime_tracer`. If the tracer
        was :class:`NullRuntimeTracer`, it is replaced with a :class:`CompositeRuntimeTracer`.
        LLM request/response rows are recorded from ``llm.*`` events (see :func:`wire_llm_traces_to_runtime_tracer`).
        """
        store = InMemoryAuditTracer(Path(output_dir))
        self._audit_jsonl = store
        _LOGGER.info("audit trace enabled (JSONL under %s)", output_dir)
        subscriber = AuditTraceSubscriber(store)
        if self._audit_trace_subscriber is not None:
            self.runtime_tracer.unsubscribe(self._audit_trace_subscriber)
        self._audit_trace_subscriber = subscriber
        if isinstance(self.runtime_tracer, NullRuntimeTracer):
            self.runtime_tracer = CompositeRuntimeTracer()
        self.runtime_tracer.subscribe(subscriber)
        self._llm_traces_wired = False
        return store

    def enable_host_receive_log(self, *, output_dir: str | Path = "logs") -> Path:
        """Append every :class:`TraceEvent` the host tracer receives to a timestamped JSONL file.

        File name: ``logs/agent-host-YYYYMMDD-HHMMSS.jsonl`` (under ``output_dir``).

        Called automatically from :meth:`from_env` unless ``AGENT_HOST_RECEIVE_LOG`` is disabled.

        If another component replaces :attr:`runtime_tracer` (e.g. the evaluator session
        tracer), it must re-subscribe the same subscriber — see
        ``agent_framework_evaluator.runtime.session_runner``.
        """
        from agent_framework.tracing_subscribers.jsonl_subscriber import JsonlTraceSubscriber

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"agent-host-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"

        prev = self._host_receive_log_subscriber
        if prev is not None:
            try:
                self.runtime_tracer.unsubscribe(prev)
            except Exception:
                pass

        subscriber = JsonlTraceSubscriber(path)
        self._host_receive_log_subscriber = subscriber
        self._host_receive_log_path = path

        if isinstance(self.runtime_tracer, NullRuntimeTracer):
            self.runtime_tracer = CompositeRuntimeTracer()
        self.runtime_tracer.subscribe(subscriber)
        self._llm_traces_wired = False
        _LOGGER.info("host receive log: %s", path.resolve())
        return path

    def get_skill_registry(self) -> SkillRegistry:
        """Lazy-initialize and return the host-level skill registry."""
        if self.skill_registry is None:
            self.skill_registry = SkillRegistry.from_config(self.config)
            self.skill_registry.discover()
        return self.skill_registry

    def create_memory_backend(self) -> MemoryBackend:
        """Construct the default memory backend for this host.

        Override in subclasses to provide persistent or remote-backed memory.
        The default implementation returns :class:`InMemoryMemoryBackend`.
        """
        return InMemoryMemoryBackend()

    def get_memory_backend(self) -> MemoryBackend:
        """Lazy-initialize and return the host-level memory backend.

        The backend owns canonical storage and exact lookup for ``mem://``
        entries.
        """
        if self.memory_backend is None:
            self.memory_backend = self.create_memory_backend()
        return self.memory_backend

    def create_memory_query_provider(self) -> MemoryQueryProvider:
        """Construct the default memory query provider for this host.

        Override in subclasses to plug in semantic retrieval or hybrid catalog
        implementations. The default implementation returns
        :class:`CatalogMemoryQueryProvider`.
        """
        return CatalogMemoryQueryProvider(self.get_memory_backend())

    def get_memory_query_provider(self) -> MemoryQueryProvider:
        """Lazy-initialize and return the host-level memory query provider.

        Query providers handle discovery and ranking of memory refs within the
        visible scopes of a run.
        """
        if self.memory_query_provider is None:
            self.memory_query_provider = self.create_memory_query_provider()
        return self.memory_query_provider

    def create_memory_projector(self) -> MemoryProjector:
        """Construct the default memory projector for this host.

        Override in subclasses to emit alternative prompt formats. The default
        implementation returns :class:`XmlMemoryProjector`.
        """
        return XmlMemoryProjector()

    def get_memory_projector(self) -> MemoryProjector:
        """Lazy-initialize and return the host-level memory projector.

        Projectors render already-resolved memory metadata and content into the
        deterministic prompt format used by model calls.
        """
        if self.memory_projector is None:
            self.memory_projector = self.create_memory_projector()
        return self.memory_projector

    def create_memory_scope_resolver(self) -> MemoryScopeResolver:
        """Construct the default visibility policy for scoped memory.

        Override in subclasses to enforce caller-aware or product-specific
        scope rules. The default implementation returns a
        :class:`ConfiguredMemoryScopeResolver` seeded from :class:`HostConfig`.
        """
        return ConfiguredMemoryScopeResolver(
            global_scope_keys=tuple(getattr(self.config, "memory_global_scopes", ())),
            group_scope_keys=tuple(getattr(self.config, "memory_group_scopes", ())),
            use_case_scope_keys=tuple(getattr(self.config, "memory_use_case_scopes", ())),
            enable_agent_scope=bool(getattr(self.config, "memory_enable_agent_scope", False)),
        )

    def get_memory_scope_resolver(self) -> MemoryScopeResolver:
        """Lazy-initialize and return the host-level memory scope resolver.

        The resolver decides which scoped memory namespaces are visible to a
        host operation or agent run before any query or projection occurs.
        """
        if self.memory_scope_resolver is None:
            self.memory_scope_resolver = self.create_memory_scope_resolver()
        return self.memory_scope_resolver

    def get_visible_memory_scopes(self, *, agent_id: str, run_id: str) -> tuple[MemoryScope, ...]:
        """Return the memory scopes visible to a host operation or agent run."""
        if not getattr(self.config, "memory_enabled", True):
            return ()
        return self.get_memory_scope_resolver().visible_scopes(
            session_id=self.session_id,
            agent_id=agent_id,
            run_id=run_id,
        )

    def get_default_agent_tool_names(self) -> tuple[str, ...]:
        """Return tools implicitly available to every agent.

        Memory read tools are injected here rather than in agent frontmatter so
        they remain available across the framework without granting write access
        by default.
        """
        if not getattr(self.config, "memory_enabled", True):
            return ()
        if not getattr(self.config, "memory_builtin_tools_enabled", True):
            return ()
        return ("memory_get", "memory_list", "memory_query")

    def get_memory_auto_store_threshold_bytes(self) -> int:
        """Return the byte threshold above which payloads are auto-stored in memory."""
        configured = int(
            getattr(self.config, "memory_auto_store_threshold_bytes", DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES)
        )
        return configured if configured > 0 else DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES

    def store_memory(
        self,
        *,
        path: str,
        content: Any,
        mime_type: str,
        scope: MemoryScope | None = None,
        title: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRef:
        """Store a memory entry and return its stable ref."""
        scope_value = scope or MemoryScope(kind="session", key=self.session_id)
        uri = build_memory_uri(scope_value, path)
        ref = MemoryRef(
            uri=uri,
            scope=scope_value,
            mime_type=mime_type,
            title=title,
            summary=summary,
            size_bytes=_size_bytes_for_content(content),
            metadata=dict(metadata or {}),
        )
        entry = _entry_from_content(ref, content)
        stored_ref = self.get_memory_backend().put(entry)
        self.publish_trace_event(
            kind="runtime.memory_put",
            title=f"Memory stored: {path}",
            summary=f"Stored memory entry {stored_ref.uri}.",
            payload={
                "memory_uri": stored_ref.uri,
                "scope": stored_ref.scope.as_text(),
                "mime_type": stored_ref.mime_type,
                "title": stored_ref.title,
                "summary": stored_ref.summary,
                "size_bytes": stored_ref.size_bytes,
                "version": stored_ref.version,
                "metadata": dict(stored_ref.metadata),
            },
            context=TraceContext(session_id=self.session_id),
        )
        return stored_ref

    def create_memory(
        self,
        *,
        path: str,
        content: Any,
        mime_type: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        scope: MemoryScope | None = None,
    ) -> MemoryRef:
        """Create a new memory entry using inferred defaults where possible.

        Args:
            path: Relative path under the target scope.
            content: Content to store. Strings become text; objects become JSON.
            mime_type: Optional MIME type override. Inferred when omitted.
            title: Optional short human-readable label.
            summary: Optional short discovery summary for list/query output.
            metadata: Optional arbitrary metadata persisted on the ref.
            scope: Optional explicit scope override. Defaults to the current
                host session scope.
        """
        inferred_mime = mime_type or self._infer_memory_mime_type(content)
        return self.store_memory(
            path=path,
            content=content,
            mime_type=inferred_mime,
            scope=scope,
            title=title,
            summary=summary,
            metadata=metadata,
        )

    def get_memory(self, uri: str) -> MemoryEntry:
        """Return one memory entry by URI."""
        return self.get_memory_backend().get(uri)

    def update_memory(
        self,
        *,
        uri: str,
        content: Any,
        mime_type: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRef:
        """Update an existing memory entry and bump its version."""
        current = self.get_memory(uri)
        updated_ref = MemoryRef(
            uri=current.ref.uri,
            scope=current.ref.scope,
            mime_type=mime_type or current.ref.mime_type,
            title=title if title is not None else current.ref.title,
            summary=summary if summary is not None else current.ref.summary,
            size_bytes=_size_bytes_for_content(content),
            version=next_memory_version(current.ref.version),
            metadata=dict(current.ref.metadata) | dict(metadata or {}),
        )
        entry = _entry_from_content(updated_ref, content)
        stored_ref = self.get_memory_backend().put(entry)
        self.publish_trace_event(
            kind="runtime.memory_update",
            title=f"Memory updated: {uri}",
            summary=f"Updated memory entry {stored_ref.uri} to version {stored_ref.version}.",
            payload={
                "memory_uri": stored_ref.uri,
                "scope": stored_ref.scope.as_text(),
                "mime_type": stored_ref.mime_type,
                "title": stored_ref.title,
                "summary": stored_ref.summary,
                "size_bytes": stored_ref.size_bytes,
                "version": stored_ref.version,
                "metadata": dict(stored_ref.metadata),
            },
            context=TraceContext(session_id=self.session_id),
        )
        return stored_ref

    def render_memory_entry(self, uri: str) -> str:
        """Render one memory entry as XML."""
        entry = self.get_memory(uri)
        return self.get_memory_projector().render_entries((entry,))

    def list_memory_refs(
        self,
        *,
        scope_kind: str | None = None,
        scope_key: str | None = None,
        limit: int = 20,
    ) -> tuple[MemoryRef, ...]:
        """List visible memory refs, optionally filtered to a single visible scope.

        This method returns lightweight refs only and never materialises full
        content.
        """
        scopes = self._filter_visible_memory_scopes(
            scope_kind=scope_kind,
            scope_key=scope_key,
            agent_id="",
            run_id="",
        )
        hits = self.get_memory_query_provider().list(scopes, limit=limit)
        return tuple(hit.ref for hit in hits)

    def query_memory(
        self,
        text: str,
        *,
        scope_kind: str | None = None,
        scope_key: str | None = None,
        limit: int = 10,
    ) -> tuple[MemoryQueryHit, ...]:
        """Query visible memory by text.

        Query semantics depend on the active :class:`MemoryQueryProvider`.
        """
        scopes = self._filter_visible_memory_scopes(
            scope_kind=scope_kind,
            scope_key=scope_key,
            agent_id="",
            run_id="",
        )
        return self.get_memory_query_provider().query(text, scopes, limit=limit)

    def build_memory_prompt(
        self,
        *,
        agent_id: str,
        run_id: str,
        parameter_values: dict[str, Any],
        seed_parameters: dict[str, Any],
        prompt_text: str = "",
    ) -> tuple[tuple[MemoryScope, ...], tuple[MemoryRef, ...], str]:
        """Build deterministic memory XML for one model call."""
        scopes = self.get_visible_memory_scopes(agent_id=agent_id, run_id=run_id)
        if not scopes:
            return scopes, (), ""

        query_provider = self.get_memory_query_provider()
        projector = self.get_memory_projector()
        catalog_hits = query_provider.list(scopes, limit=20)
        catalog_xml = projector.render_catalog(catalog_hits)

        explicit_uris = (
            find_memory_uris(parameter_values)
            + find_memory_uris(seed_parameters)
            + find_memory_uris(prompt_text)
        )
        seen: set[str] = set()
        entries: list[MemoryEntry] = []
        refs: list[MemoryRef] = []
        visible_scope_pairs = {(scope.kind, scope.key) for scope in scopes}
        for uri in explicit_uris:
            if uri in seen:
                continue
            seen.add(uri)
            scope_kind, scope_key, _ = parse_memory_uri(uri)
            if visible_scope_pairs and (scope_kind, scope_key) not in visible_scope_pairs:
                continue
            entry = self.get_memory_backend().get(uri)
            entries.append(entry)
            refs.append(entry.ref)

        content_xml = projector.render_entries(entries)
        parts = [part for part in (catalog_xml, content_xml) if part]
        return scopes, tuple(refs), "\n\n".join(parts)

    def normalize_memory_parameters(
        self,
        *,
        agent_id: str,
        run_id: str,
        parameters: dict[str, Any],
        child_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace oversized parameter payloads with session-scoped memory refs."""
        if not getattr(self.config, "memory_enabled", True):
            return dict(parameters)

        normalized: dict[str, Any] = {}
        for name, value in parameters.items():
            normalized[name] = self._normalize_memory_parameter_value(
                name=name,
                value=value,
                agent_id=agent_id,
                run_id=run_id,
                child_agent_id=child_agent_id,
            )
        return normalized

    def _normalize_memory_parameter_value(
        self,
        *,
        name: str,
        value: Any,
        agent_id: str,
        run_id: str,
        child_agent_id: str | None = None,
    ) -> Any:
        if isinstance(value, str) and value.startswith("mem://"):
            return value
        try:
            size_bytes = _size_bytes_for_content(value)
        except TypeError:
            return value
        if size_bytes < self.get_memory_auto_store_threshold_bytes():
            return value

        scope = MemoryScope(kind="session", key=self.session_id)
        target = child_agent_id or agent_id
        path = f"runs/{run_id}/agents/{target}/parameters/{name}"
        ref = self.store_memory(
            path=path,
            content=value,
            mime_type=self._infer_memory_mime_type(value),
            scope=scope,
            title=f"Auto-stored parameter {name}",
            summary=(
                f"Auto-stored parameter {name!r} for agent {target!r} because it exceeded "
                f"{self.get_memory_auto_store_threshold_bytes()} bytes."
            ),
            metadata={
                "source_agent_id": agent_id,
                "target_agent_id": target,
                "parameter_name": name,
                "run_id": run_id,
                "size_bytes": size_bytes,
                "auto_stored": True,
            },
        )
        self.publish_trace_event(
            kind="runtime.memory_autostore",
            title=f"Auto-stored parameter: {name}",
            summary=f"Stored oversized parameter {name!r} as {ref.uri}.",
            payload={
                "agent_id": agent_id,
                "child_agent_id": child_agent_id,
                "run_id": run_id,
                "parameter_name": name,
                "memory_uri": ref.uri,
                "size_bytes": size_bytes,
                "threshold_bytes": self.get_memory_auto_store_threshold_bytes(),
            },
            context=TraceContext(run_id=run_id, agent_id=agent_id),
        )
        return ref.uri

    def _filter_visible_memory_scopes(
        self,
        *,
        scope_kind: str | None,
        scope_key: str | None,
        agent_id: str,
        run_id: str,
    ) -> tuple[MemoryScope, ...]:
        """Return visible scopes, optionally narrowed to one explicit scope."""
        scopes = self.get_visible_memory_scopes(agent_id=agent_id, run_id=run_id)
        if scope_kind or scope_key:
            if not (scope_kind and scope_key):
                raise ValueError("scope_kind and scope_key must be supplied together.")
            scopes = tuple(
                scope for scope in scopes if scope.kind == scope_kind and scope.key == scope_key
            )
        return scopes

    @staticmethod
    def _infer_memory_mime_type(value: Any) -> str:
        if isinstance(value, str):
            return "text/plain"
        if isinstance(value, bytes):
            return "application/octet-stream"
        return "application/json"

    @staticmethod
    def _sanitize_memory_path_segment(value: str | None, *, fallback: str) -> str:
        if not value:
            return fallback
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
        return cleaned or fallback

    def _lift_prompt_file_blocks_to_memory(
        self,
        *,
        agent_id: str,
        run_id: str,
        prompt: str,
    ) -> str:
        """Store expanded ``<file>`` prompt blocks in memory and replace them with refs.

        Only host-generated file/RAG inclusions are lifted from the prompt. Freeform
        user prose is intentionally left inline.
        """
        if not prompt or "<file" not in prompt or not getattr(self.config, "memory_enabled", True):
            return prompt

        scope = MemoryScope(kind="session", key=self.session_id)

        def _replace(block: str, attrs: dict[str, str], index: int) -> str:
            file_name = attrs.get("name")
            safe_name = self._sanitize_memory_path_segment(file_name, fallback=f"file-{index}")
            path = f"runs/{run_id}/agents/{agent_id}/prompt-files/{index:03d}-{safe_name}"
            ref = self.store_memory(
                path=path,
                content=block,
                mime_type="text/xml",
                scope=scope,
                title=f"Prompt file {file_name or index}",
                summary=(
                    f"Expanded file content lifted from the root prompt for agent {agent_id!r}."
                ),
                metadata={
                    "source_agent_id": agent_id,
                    "run_id": run_id,
                    "file_name": file_name,
                    "encoding": attrs.get("encoding"),
                    "source": "prompt_file_block",
                    "auto_lifted": True,
                },
            )
            self.publish_trace_event(
                kind="runtime.memory_prompt_lift",
                title=f"Lifted prompt file block: {file_name or index}",
                summary=f"Stored root prompt file block as {ref.uri}.",
                payload={
                    "agent_id": agent_id,
                    "run_id": run_id,
                    "memory_uri": ref.uri,
                    "file_name": file_name,
                    "encoding": attrs.get("encoding"),
                },
                context=TraceContext(run_id=run_id, agent_id=agent_id),
            )
            return f'<memory id="{ref.uri}" />'

        return replace_file_blocks(prompt, _replace)

    def _bootstrap_root_prompt_inputs(
        self,
        *,
        agent: Agent,
        run_id: str,
        initial_instruction: str,
    ) -> tuple[dict[str, Any], str | None]:
        """Prepare root-run parameters and prompt text before the first model call.

        The bootstrap path is intentionally conservative:

        - when the supplied prompt cleanly matches the agent's template contract,
          recover structured parameters and let the normal parameter/memory flow run
        - otherwise, keep the prompt freeform but lift expanded ``<file>`` blocks
          into memory so large RAG payloads are no longer carried inline
        """
        if not initial_instruction:
            return {}, ""

        parsed_parameters = agent.try_parse_prompt_input(initial_instruction)
        if parsed_parameters is not None:
            try:
                rendered_from_parameters = agent.render_user_prompt(parsed_parameters)
            except ValueError:
                rendered_from_parameters = None
            if rendered_from_parameters is not None and rendered_from_parameters.strip() == initial_instruction.strip():
                return parsed_parameters, None

        return {}, self._lift_prompt_file_blocks_to_memory(
            agent_id=agent.agent_id,
            run_id=run_id,
            prompt=initial_instruction,
        )

    def register_tool(self, tool: Tool) -> None:
        """Register a concrete tool instance for runtime execution."""
        self.tool_registry.register(tool)

    def get_tool(self, tool_name: str) -> Tool:
        """Return a loaded tool by name."""
        return self.tool_registry.get(tool_name)

    def resolve_model_tool_definitions(
        self,
        tool_names: tuple[str, ...],
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[ToolDefinition, ...]:
        """Resolve agent ``allowed_tools`` into provider ``ToolDefinition`` objects.

        Logs and emits ``runtime.tool_unavailable`` when a tool cannot be loaded.
        With ``HostConfig.missing_tool_policy == \"graceful\"`` (default), missing
        tools are omitted and the run continues. With ``\"strict\"``, the first
        failure is re-raised after logging/tracing.
        """
        definitions: list[ToolDefinition] = []
        policy = self.config.missing_tool_policy
        for name in tool_names:
            try:
                definitions.append(self.get_tool(name).model_definition())
            except Exception as exc:
                _LOGGER.error(
                    "Tool %r could not be loaded for agent %r: %s",
                    name,
                    agent_id,
                    exc,
                    exc_info=True,
                )
                trace_ctx = TraceContext(run_id=run_id, agent_id=agent_id, tool_name=name)
                self.publish_trace_event(
                    kind="runtime.tool_unavailable",
                    title=f"Tool not loaded: {name}",
                    summary=str(exc),
                    payload={
                        "tool_name": name,
                        "agent_id": agent_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "missing_tool_policy": policy,
                    },
                    context=trace_ctx,
                    level="error",
                )
                if policy == "strict":
                    raise
        return tuple(definitions)

    def load_agent(self, agent_ref: str | Path) -> Agent:
        """Load and cache an agent definition from Markdown."""
        source_path = self._resolve_agent_markdown_path(agent_ref)
        return self.agent_registry.load_from_path(source_path)

    def get_agent(self, agent_id: str, *, base_dir: Path | None = None) -> Agent:
        """Resolve an agent by logical id, explicit path, sibling path, or agent directory."""
        return self.agent_registry.get(agent_id, base_dir=base_dir)

    def run_root(
        self,
        initial_instruction: str | None = None,
        *,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
        prompt_fragments: tuple[str, ...] | None = None,
    ) -> AgentResult:
        """Run the configured root agent using console-sourced input."""
        return self.run_agent(
            self.config.root_agent_id,
            initial_instruction=initial_instruction,
            conversation_messages=conversation_messages,
            prompt_fragments=prompt_fragments,
        )

    def _effective_trace_context(self, extra: TraceContext | None) -> TraceContext:
        overlay = self.trace_context_overlay
        if overlay is None:
            return extra or TraceContext()
        if extra is None:
            return overlay
        updates = {f.name: getattr(extra, f.name) for f in fields(extra) if getattr(extra, f.name) is not None}
        return overlay.merged(**updates)

    def publish_trace_event(
        self,
        *,
        kind: str,
        title: str,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        context: TraceContext | None = None,
        channel: str = "runtime",
        level: str = "info",
    ) -> None:
        if isinstance(self.runtime_tracer, NullRuntimeTracer):
            return
        merged_ctx = self._effective_trace_context(context)
        event = make_trace_event(
            kind=kind,
            title=title,
            summary=summary,
            channel=channel,  # type: ignore[arg-type]
            level=level,  # type: ignore[arg-type]
            span_id=span_id,
            parent_span_id=parent_span_id,
            context=merged_ctx,
            payload=payload or {},
        )
        self.runtime_tracer.publish(event)

    def _agent_with_runtime_tracing(self, agent: Agent) -> Agent:
        if isinstance(self.runtime_tracer, NullRuntimeTracer):
            return agent
        from agent_framework.runtime_trace_behavior import RuntimeTraceBehavior

        def _copy_hook(hook: SequentialHook) -> SequentialHook:
            nh = SequentialHook()
            for cb in hook:
                nh += cb
            return nh

        cloned = replace(
            agent,
            on_pre_agent=_copy_hook(agent.on_pre_agent),
            on_post_agent=_copy_hook(agent.on_post_agent),
            on_pre_tool=_copy_hook(agent.on_pre_tool),
            on_post_tool=_copy_hook(agent.on_post_tool),
            on_pre_subagent=_copy_hook(agent.on_pre_subagent),
            on_post_subagent=_copy_hook(agent.on_post_subagent),
            on_pre_skill=_copy_hook(agent.on_pre_skill),
            on_post_skill=_copy_hook(agent.on_post_skill),
            on_pre_model=_copy_hook(agent.on_pre_model),
            on_post_model=_copy_hook(agent.on_post_model),
        )
        rt_behavior = RuntimeTraceBehavior()
        wired = replace(cloned, behaviors=cloned.behaviors + (rt_behavior,))
        rt_behavior.attach(wired)
        return wired

    def _next_prompt_counter(self) -> int:
        """Thread-safely increment and return the prompt counter."""
        with self._prompt_counter_lock:
            self._prompt_counter += 1
            return self._prompt_counter

    def open_interaction(
        self,
        *,
        prompt: str,
        intent: str,
        run_id: str,
        agent_id: str,
        caller_id: str | None,
        parent_run_id: str | None,
        interaction_kind: str,
        blocking: bool = True,
    ) -> PendingInteraction:
        """Register a pending interactive prompt and emit a trace event."""
        interaction = PendingInteraction(
            prompt_id=str(uuid4()),
            session_id=self.session_id,
            prompt=prompt,
            intent=intent,
            run_id=run_id,
            agent_id=agent_id,
            caller_id=caller_id,
            parent_run_id=parent_run_id,
            interaction_kind=interaction_kind,
            blocking=blocking,
            created_at=datetime.now(timezone.utc),
        )
        with self._pending_interactions_lock:
            self.pending_interactions[interaction.prompt_id] = interaction
        self.publish_trace_event(
            kind="runtime.interaction_opened",
            title="Interaction opened",
            payload=interaction.metadata(),
            context=TraceContext(
                run_id=run_id,
                agent_id=agent_id,
                caller_id=caller_id,
            ),
        )
        return interaction

    def close_interaction(self, prompt_id: str, *, answer: str | None = None, cancelled: bool = False) -> None:
        """Remove a pending interaction and emit the matching trace event."""
        with self._pending_interactions_lock:
            interaction = self.pending_interactions.pop(prompt_id, None)
        if interaction is None:
            return
        kind = "runtime.interaction_cancelled" if cancelled else "runtime.interaction_answered"
        title = "Interaction cancelled" if cancelled else "Interaction answered"
        payload = interaction.metadata()
        payload["answer"] = answer
        self.publish_trace_event(
            kind=kind,
            title=title,
            payload=payload,
            context=TraceContext(
                run_id=interaction.run_id,
                agent_id=interaction.agent_id,
                caller_id=interaction.caller_id,
            ),
        )

    def get_pending_interaction(self, prompt_id: str) -> PendingInteraction | None:
        """Return a pending interaction by prompt id."""
        with self._pending_interactions_lock:
            return self.pending_interactions.get(prompt_id)

    def register_run(
        self,
        *,
        run_id: str,
        agent_id: str,
        caller_id: str | None,
        parent_run_id: str | None,
    ) -> None:
        """Record minimal lineage for a run so callback bubbling can skip layers."""
        with self._run_registry_lock:
            self._run_registry[run_id] = RunRegistration(
                run_id=run_id,
                agent_id=agent_id,
                caller_id=caller_id,
                parent_run_id=parent_run_id,
            )
        self.runtime_usage_tracker.record_run_started(
            run_id=run_id,
            agent_id=agent_id,
            parent_run_id=parent_run_id,
        )

    def record_runtime_llm_usage(self, *, run_id: str | None, usage: Any) -> None:
        """Feed normalized LLM usage into the per-session runtime tracker."""
        if not run_id:
            return
        self.runtime_usage_tracker.record_llm_usage(run_id=run_id, usage=usage)

    def finish_runtime_usage(self, *, run_id: str | None) -> dict[str, dict[str, int]]:
        """Return canonical self and inclusive usage totals for a completed run."""
        if not run_id:
            empty = {
                "input_tokens": 0,
                "input_cached_tokens": 0,
                "output_tokens": 0,
                "output_cached_tokens": 0,
                "total_tokens": 0,
            }
            return {"usage_self": dict(empty), "usage_inclusive": dict(empty)}
        return self.runtime_usage_tracker.finish_run(run_id=run_id)

    def session_usage_totals(self) -> dict[str, int]:
        """Return session-wide normalized usage totals."""
        return self.runtime_usage_tracker.session_totals()

    def get_run_registration(self, run_id: str | None) -> RunRegistration | None:
        """Return the stored run lineage record, if any."""
        if not run_id:
            return None
        with self._run_registry_lock:
            return self._run_registry.get(run_id)

    def run_agent(
        self,
        agent_id: str,
        initial_instruction: str | None = None,
        *,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
        prompt_fragments: tuple[str, ...] | None = None,
        model_override: str | tuple[str, ...] | None = None,
        planning_override: bool | None = None,
    ) -> AgentResult:
        """Run a specific agent id as a top-level invocation."""
        if initial_instruction and self.file_ref_resolver is not None:
            initial_instruction = expand_file_refs(initial_instruction, self.file_ref_resolver)
        agent = self.get_agent(agent_id)
        root_model_override = normalize_model_override_names(model_override)
        if root_model_override is not None:
            agent = replace(agent, model_names=root_model_override)
        agent = self._agent_with_runtime_tracing(agent)
        prompt_num = self._next_prompt_counter()
        root_run_id = f"{self.session_id}.p{prompt_num}.{agent_id}"
        parameters, rendered_prompt_override = self._bootstrap_root_prompt_inputs(
            agent=agent,
            run_id=root_run_id,
            initial_instruction=initial_instruction or "",
        )
        with active_tracer_scope(self.runtime_tracer, self.trace_context_overlay):
            return agent.run(
                host=self,
                parameters=parameters,
                caller_id="host",
                rendered_prompt_override=rendered_prompt_override,
                conversation_messages=conversation_messages,
                prompt_fragments=prompt_fragments,
                run_id=root_run_id,
                planning_override=planning_override,
            )

    def run_console(self) -> AgentResult:
        """Prompt for the initial instruction, run the root agent, and print the result."""
        if self.user_comm is not None:
            initial_instruction = self._run_user_comm_coro(
                self.user_comm.read_user_input("Instruction: ")
            ) or ""
        else:
            initial_instruction = input("Instruction: ")
        result = self.run_root(initial_instruction=initial_instruction)
        if result.message:
            if self.user_comm is not None:
                self._run_user_comm_coro(self.user_comm.send_message(result.message))
            else:
                print(result.message)
        return result

    def call_subagent(
        self,
        *,
        caller: Agent,
        callee_id: str,
        parameters: dict[str, Any],
        parent_run_id: str | None = None,
        run_id: str | None = None,
        in_parallel_batch: bool = False,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
    ) -> AgentResult:
        """Synchronously invoke a child agent from a caller agent."""
        base_dir = caller.source_path.parent if caller.source_path is not None else None
        callee = self._agent_with_runtime_tracing(self.get_agent(callee_id, base_dir=base_dir))
        normalized_parameters = self.normalize_memory_parameters(
            agent_id=caller.agent_id,
            run_id=run_id or (f"{parent_run_id}.{callee_id}" if parent_run_id else f"{callee_id}.{self.session_id}"),
            parameters=parameters,
            child_agent_id=callee_id,
        )
        child_run_id = run_id or (f"{parent_run_id}.{callee_id}" if parent_run_id else None)
        with active_tracer_scope(self.runtime_tracer, self.trace_context_overlay):
            return callee.run(
                host=self,
                parameters=normalized_parameters,
                caller_id=caller.agent_id,
                parent_run_id=parent_run_id,
                run_id=child_run_id,
                in_parallel_batch=in_parallel_batch,
                conversation_messages=conversation_messages,
            )

    def call_subagent_async(
        self,
        *,
        caller: Agent,
        callee_id: str,
        parameters: dict[str, Any],
        parent_run_id: str | None = None,
        run_id: str | None = None,
        in_parallel_batch: bool = False,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
    ) -> Future[AgentResult]:
        """Invoke a child agent on the thread pool for parallel execution.

        Captures the current contextvars context so tracer scope and other
        context variables propagate correctly into the worker thread.
        """
        ctx = contextvars.copy_context()
        return self._executor.submit(
            ctx.run,
            self.call_subagent,
            caller=caller,
            callee_id=callee_id,
            parameters=parameters,
            parent_run_id=parent_run_id,
            run_id=run_id,
            in_parallel_batch=in_parallel_batch,
            conversation_messages=conversation_messages,
        )

    # ------------------------------------------------------------------
    # Checkpoint storage for parallel-batch callback resume
    # ------------------------------------------------------------------

    def save_checkpoint(self, run_id: str, messages: list[dict]) -> None:
        """Persist conversation state for a blocked parallel child.

        Skipped if the run was already marked as timed-out by the parent batch
        (orphaned thread finishing after parent abandoned the wait).
        """
        with self._timed_out_lock:
            if run_id in self._timed_out_run_ids:
                return
        with self._checkpoint_lock:
            self._checkpoints[run_id] = (list(messages), time.monotonic())

    def load_checkpoint(self, run_id: str) -> list[dict] | None:
        """Return saved conversation messages for a run_id, or None."""
        with self._checkpoint_lock:
            entry = self._checkpoints.get(run_id)
        return list(entry[0]) if entry is not None else None

    def delete_checkpoint(self, run_id: str) -> None:
        """Remove a checkpoint after the child has completed or failed."""
        with self._checkpoint_lock:
            self._checkpoints.pop(run_id, None)

    def cleanup_checkpoints(self, ttl_seconds: float = 3600.0) -> int:
        """Remove checkpoints older than ttl_seconds.  Returns count removed.

        Also purges the timed-out run-id tombstone set so it doesn't grow
        unboundedly when many batches have been executed.
        """
        cutoff = time.monotonic() - ttl_seconds
        with self._checkpoint_lock:
            expired = [k for k, (_, ts) in self._checkpoints.items() if ts < cutoff]
            for k in expired:
                del self._checkpoints[k]
        # Best-effort tombstone GC: drop IDs that are also gone from checkpoints.
        with self._timed_out_lock:
            with self._checkpoint_lock:
                self._timed_out_run_ids -= self._timed_out_run_ids - self._checkpoints.keys()
        return len(expired)

    # ------------------------------------------------------------------
    # Parallel / sequential batch orchestration
    # ------------------------------------------------------------------

    def call_subagent_batch(
        self,
        *,
        caller: Agent,
        specs: tuple[SubagentCallSpec, ...],
        mode: str,
        timeout_seconds: float | None,
        parent_run_id: str | None = None,
    ) -> list[SubagentBatchItemResult]:
        """Orchestrate a call_subagents batch with callback-resume loop."""
        if mode not in ("parallel", "sequential"):
            raise ValueError(
                f"call_subagent_batch: unknown mode {mode!r}. Must be 'parallel' or 'sequential'."
            )
        if mode == "parallel":
            max_parallelism = int(os.environ.get("SUBAGENT_MAX_PARALLELISM", "8"))
            if len(specs) > max_parallelism:
                raise ValueError(
                    f"call_subagents parallel batch size {len(specs)} exceeds "
                    f"SUBAGENT_MAX_PARALLELISM={max_parallelism}."
                )
        timeout = timeout_seconds if timeout_seconds is not None else float(
            os.environ.get("SUBAGENT_BATCH_TIMEOUT_SECONDS", "300")
        )
        max_rounds = int(os.environ.get("SUBAGENT_BATCH_MAX_CALLBACK_ROUNDS", "5"))

        # pending_specs is a list of (SubagentCallSpec, child_run_id, conversation_messages)
        pending: list[tuple[SubagentCallSpec, str, tuple[dict, ...] | None]] = [
            (s, f"{parent_run_id}.{s.output_key}" if parent_run_id else s.output_key, None)
            for s in specs
        ]
        final_results: list[SubagentBatchItemResult] = []

        for round_num in range(max_rounds + 1):
            if not pending:
                break

            if mode == "parallel":
                round_results = self._run_parallel_round(caller, pending, timeout, parent_run_id)
            elif mode == "sequential":
                round_results = self._run_sequential_round(caller, pending, timeout, parent_run_id)
            else:
                raise ValueError(f"call_subagent_batch: unknown mode {mode!r}.")

            blocked = [r for r in round_results if r.status == "blocked"]
            final_results.extend(r for r in round_results if r.status != "blocked")

            if not blocked:
                break

            if round_num == max_rounds:
                for r in blocked:
                    self.delete_checkpoint(r.run_id)
                    final_results.append(replace(r, status="failed", message="max callback rounds exceeded"))
                break

            # Resolve callbacks and build resume specs.
            # Note: in sequential mode children never return status="blocked"
            # because in_parallel_batch=False lets callbacks block synchronously
            # via the normal handle_callback → resolve_callback path. The resume
            # loop below is therefore only exercised in parallel mode.
            pending = []
            for br in blocked:
                saved = self.load_checkpoint(br.run_id)
                if saved is None:
                    final_results.append(replace(br, status="failed", message="checkpoint not found after block"))
                    continue
                try:
                    callee = self.get_agent(br.subagent_id)
                    answer = self.resolve_callback(
                        caller_id=caller.agent_id,
                        callee=callee,
                        prompt=br.callback_prompt or "",
                        intent=br.callback_intent or "information_request",
                        run_id=br.run_id,
                        parent_run_id=parent_run_id,
                    )
                except Exception as exc:
                    self.delete_checkpoint(br.run_id)
                    final_results.append(replace(br, status="failed", message=f"callback resolution error: {exc}"))
                    continue
                resumed_messages = tuple(saved + [{"role": "user", "content": answer}])
                # Find original spec to get parameters.
                orig_spec = next((s for s in specs if s.output_key == br.output_key), None)
                if orig_spec is None:
                    orig_spec = SubagentCallSpec(subagent_id=br.subagent_id, output_key=br.output_key)
                pending.append((orig_spec, br.run_id, resumed_messages))

        # Clean up any remaining checkpoints for non-blocked results.
        for r in final_results:
            if r.status != "blocked":
                self.delete_checkpoint(r.run_id)

        return final_results

    def _run_parallel_round(
        self,
        caller: Agent,
        pending: list[tuple[SubagentCallSpec, str, tuple[dict, ...] | None]],
        timeout: float,
        parent_run_id: str | None,
    ) -> list[SubagentBatchItemResult]:
        future_to_key: dict[Future[AgentResult], tuple[SubagentCallSpec, str]] = {}
        for spec, child_run_id, convo in pending:
            # Each future needs its own context copy — a single Context object
            # cannot be entered concurrently by multiple threads.
            ctx = contextvars.copy_context()
            fut = self._executor.submit(
                ctx.run,
                self.call_subagent,
                caller=caller,
                callee_id=spec.subagent_id,
                parameters=spec.parameters,
                parent_run_id=parent_run_id,
                run_id=child_run_id,
                in_parallel_batch=True,
                conversation_messages=convo,
            )
            future_to_key[fut] = (spec, child_run_id)

        done, not_done = _futures_wait(list(future_to_key), timeout=timeout)

        # Register timed-out run IDs as tombstones so that any orphaned thread
        # that completes after the parent abandons the wait cannot write a stale
        # checkpoint entry via save_checkpoint.
        if not_done:
            timed_out_ids = {
                child_run_id
                for fut, (_, child_run_id) in future_to_key.items()
                if fut in not_done
            }
            with self._timed_out_lock:
                self._timed_out_run_ids |= timed_out_ids

        results: list[SubagentBatchItemResult] = []

        for fut, (spec, child_run_id) in future_to_key.items():
            if fut in not_done:
                results.append(SubagentBatchItemResult(
                    output_key=spec.output_key,
                    subagent_id=spec.subagent_id,
                    run_id=child_run_id,
                    status="timed_out",
                ))
                continue
            try:
                agent_result = fut.result()
            except Exception as exc:
                results.append(SubagentBatchItemResult(
                    output_key=spec.output_key,
                    subagent_id=spec.subagent_id,
                    run_id=child_run_id,
                    status="failed",
                    message=f"{type(exc).__name__}: {exc}",
                ))
                continue
            results.append(self._agent_result_to_batch_item(spec, child_run_id, agent_result))

        return results

    def _run_sequential_round(
        self,
        caller: Agent,
        pending: list[tuple[SubagentCallSpec, str, tuple[dict, ...] | None]],
        timeout: float,
        parent_run_id: str | None,
    ) -> list[SubagentBatchItemResult]:
        deadline = time.monotonic() + timeout
        results: list[SubagentBatchItemResult] = []
        for spec, child_run_id, convo in pending:
            if time.monotonic() >= deadline:
                results.append(SubagentBatchItemResult(
                    output_key=spec.output_key,
                    subagent_id=spec.subagent_id,
                    run_id=child_run_id,
                    status="timed_out",
                ))
                continue
            try:
                agent_result = self.call_subagent(
                    caller=caller,
                    callee_id=spec.subagent_id,
                    parameters=spec.parameters,
                    parent_run_id=parent_run_id,
                    run_id=child_run_id,
                    in_parallel_batch=False,
                    conversation_messages=convo,
                )
            except Exception as exc:
                results.append(SubagentBatchItemResult(
                    output_key=spec.output_key,
                    subagent_id=spec.subagent_id,
                    run_id=child_run_id,
                    status="failed",
                    message=f"{type(exc).__name__}: {exc}",
                ))
                continue
            results.append(self._agent_result_to_batch_item(spec, child_run_id, agent_result))
        return results

    @staticmethod
    def _agent_result_to_batch_item(
        spec: SubagentCallSpec,
        child_run_id: str,
        result: AgentResult,
    ) -> SubagentBatchItemResult:
        if result.status == "blocked":
            try:
                import json as _json
                payload = _json.loads(result.message) if result.message else {}
            except Exception:
                payload = {}
            return SubagentBatchItemResult(
                output_key=spec.output_key,
                subagent_id=spec.subagent_id,
                run_id=child_run_id,
                status="blocked",
                message=result.message,
                callback_intent=payload.get("intent"),
                callback_prompt=payload.get("prompt", ""),
            )
        return SubagentBatchItemResult(
            output_key=spec.output_key,
            subagent_id=spec.subagent_id,
            run_id=child_run_id,
            status=result.status,
            message=result.message,
            parameters=result.parameters,
            parameters_injection=result.parameters_injection,
        )

    def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> str:
        """Execute a loaded tool by name."""
        return self.get_tool(tool_name).invoke(parameters, self)

    def resolve_callback(
        self,
        *,
        caller_id: str,
        callee: Agent,
        prompt: str,
        intent: str = "information_request",
        run_id: str = "",
        parent_run_id: str | None = None,
        allow_user_fallback: bool = True,
        callback_parameters: dict[str, Any] | None = None,
    ) -> str:
        """Collect a callback response from the caller side."""
        params = dict(callback_parameters or {})
        current_hops = int(params.get("bubble_hops", 0) or 0)
        passthrough_agents = {
            str(item).strip()
            for item in (params.get("passthrough_agents") or [])
            if str(item).strip()
        }
        resolvable_by = {
            str(item).strip()
            for item in (params.get("resolvable_by") or [])
            if str(item).strip()
        }
        should_passthrough = caller_id in passthrough_agents
        not_resolvable_here = bool(resolvable_by) and caller_id not in resolvable_by and caller_id != "host"
        if caller_id != "host" and (should_passthrough or not_resolvable_here):
            caller_run = self.get_run_registration(parent_run_id)
            if caller_run is not None and caller_run.caller_id is not None:
                params["bubble_hops"] = current_hops + 1
                return self.resolve_callback(
                    caller_id=caller_run.caller_id,
                    callee=callee,
                    prompt=prompt,
                    intent=intent,
                    run_id=run_id,
                    parent_run_id=caller_run.parent_run_id,
                    allow_user_fallback=allow_user_fallback,
                    callback_parameters=params,
                )
            if not allow_user_fallback:
                raise RuntimeError(
                    f"Callback for {callee.agent_id!r} could not be resolved without host/user interaction."
                )
            caller_id = "host"
        if caller_id != "host":
            try:
                caller_agent = self.get_agent(caller_id)
            except KeyError:
                caller_agent = None
            if caller_agent is not None:
                response = caller_agent.respond_to_callback(self, callee_id=callee.agent_id, prompt=prompt)
                if response is not None:
                    return response
                result = caller_agent.run(
                    host=self,
                    parameters=dict(callback_parameters or {}),
                    caller_id="host",
                    rendered_prompt_override=prompt,
                )
                if result.message:
                    return result.message
                if not allow_user_fallback:
                    raise RuntimeError(
                        f"Caller agent {caller_id!r} did not resolve callback for {callee.agent_id!r}."
                    )
        # Fall back to user_comm
        if not allow_user_fallback:
            raise RuntimeError(
                f"Callback for {callee.agent_id!r} could not be resolved without host/user interaction."
            )
        if self.user_comm is None:
            return ""
        message = f"{callee.agent_id} asks {caller_id}: {prompt}\nResponse: "
        interaction = self.open_interaction(
            prompt=message,
            intent=intent,
            run_id=run_id or f"{callee.agent_id}.{self.session_id}",
            agent_id=callee.agent_id,
            caller_id=caller_id,
            parent_run_id=parent_run_id,
            interaction_kind="callback_to_caller",
            blocking=True,
        )
        result = self._run_user_comm_coro(
            self.user_comm.read_user_input(
                message,
                prompt_id=interaction.prompt_id,
                metadata=interaction.metadata(),
            )
        )
        self.close_interaction(interaction.prompt_id, answer=result, cancelled=result is None)
        return result or ""

    def request_user_input(
        self,
        prompt: str,
        *,
        intent: str = "information_request",
        run_id: str = "",
        agent_id: str = "",
        caller_id: str | None = None,
        parent_run_id: str | None = None,
        interaction_kind: str = "direct_user_input",
    ) -> str:
        """Collect direct user input via ``user_comm``."""
        if self.user_comm is None:
            raise RuntimeError(
                "No UserCommunication configured. Use AgentHost.create() with user_comm=."
            )
        interaction = self.open_interaction(
            prompt=prompt,
            intent=intent,
            run_id=run_id or f"{agent_id or 'agent'}.{self.session_id}",
            agent_id=agent_id or "agent",
            caller_id=caller_id,
            parent_run_id=parent_run_id,
            interaction_kind=interaction_kind,
            blocking=True,
        )
        result = self._run_user_comm_coro(
            self.user_comm.read_user_input(
                f"{prompt}\n> ",
                prompt_id=interaction.prompt_id,
                metadata=interaction.metadata(),
            )
        )
        self.close_interaction(interaction.prompt_id, answer=result, cancelled=result is None)
        return result or ""

    def open_context(self, *, caller_id: str, callee_id: str, kind: str) -> CallContext:
        """Create, store, and return a new runtime call context."""
        context = CallContext(
            context_id=str(uuid4()),
            caller_id=caller_id,
            callee_id=callee_id,
            kind=kind,
        )
        self.contexts[context.context_id] = context
        return context

    def run_pre_model_hooks(self, event: ModelStartEvent) -> None:
        """Execute host-level pre-model callbacks."""
        for callback in self.on_pre_model:
            callback(event)

    def run_post_model_hooks(self, event: ModelEndEvent) -> None:
        """Execute host-level post-model callbacks."""
        for callback in self.on_post_model:
            callback(event)

    def enable_llm_trace_logging(self, *, target: str = "file", output_dir: str | Path = "logs") -> None:
        """Attach shared LLM trace logging to this host."""
        from agent_framework.llm_trace_logging import attach_to_host

        attach_to_host(self, target=target, output_dir=output_dir)

    def resolve_world_path(self, raw_path: object) -> Path:
        """Resolve a tool path strictly inside the configured world directory."""
        if raw_path in (None, ""):
            raise ValueError("A non-empty relative path is required.")

        root = self.config.world_directory.resolve()
        candidate = Path(str(raw_path))
        if candidate.is_absolute():
            raise ValueError("World file tools require relative paths.")

        # Agents may redundantly include the configured world directory name in
        # a tool path. Treat that as the world root rather than nesting it.
        candidate_parts = candidate.parts
        if candidate_parts and candidate_parts[0].lower() == root.name.lower():
            candidate = Path(*candidate_parts[1:]) if len(candidate_parts) > 1 else Path(".")

        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("World file tools may not escape the configured world directory.") from exc
        return resolved

    def _resolve_agent_markdown_path(self, agent_ref: str | Path) -> Path:
        """Resolve an agent reference to its Markdown file path."""
        candidate = Path(agent_ref)
        if candidate.suffix:
            return candidate.resolve()
        return (self.config.agent_directory / f"{candidate.name}.md").resolve()


# ---------------------------------------------------------------------------
# Tool loop helper (G-08, G-10)
# ---------------------------------------------------------------------------


async def run_tool_loop(
    host: AgentHost,
    *,
    messages: list[dict[str, Any]],
    tools: Sequence[ToolDefinition],
    tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None,
    terminal_tools: Sequence[str] = (),
    max_iterations: int = 10,
    return_on_max_iterations: bool = False,
    conversation_id: str | None = None,
    model_names: str | tuple[str, ...] | None = None,
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
    response_mode: str = DEFAULT_RESPONSE_MODE,
) -> ModelResponse:
    """Run a multi-turn tool-calling loop using ``complete_async()``.

    Loops until one of:
    - The model returns ``finish_reason="stop"`` (or no tool calls).
    - A terminal tool is called — returns immediately with ``finish_reason=
      "terminal_tool"`` and the tool call arguments as ``raw_text``.
    - ``max_iterations`` is reached.

    This gives callers the equivalent of dial-agent's ``DialProvider.run()``
    with clarification/terminal tool support, without requiring markdown agent
    definitions.

    Args:
        host: The ``AgentHost`` to use for model calls.
        messages: Mutable message list **mutated in place** (not copied). After a plain
            ``stop`` (no tool calls), an ``assistant`` row is appended. After tool calls,
            an ``assistant`` row with ``tool_calls`` is appended **before** terminal-tool
            handling so transcripts match clarification-style flows.
        tools: Tool definitions exposed to the model.
        tool_executor: Async callable ``(tool_name, arguments) -> result_str``.
            When ``None``, tool calls are recorded but not executed.
        terminal_tools: Tool names that cause an immediate loop exit when called
            by the model.  The tool is not executed; its arguments are returned.
        max_iterations: Maximum number of model calls before raising
            ``RuntimeError`` (unless ``return_on_max_iterations`` is true).
        return_on_max_iterations: When true, return a ``ModelResponse`` with
            ``finish_reason="max_iterations"`` instead of raising when the loop
            exhausts iterations without a stop or terminal tool.
        conversation_id: Passed through to ``complete_async()`` for store
            integration.
        model_names: Model(s) to use.  Accepts a comma-separated string, a
            tuple, or ``None`` to use ``host.config.default_model``.
        temperature: Passed to ``complete_async()``.
        response_format: Passed to ``complete_async()``.
        response_mode: ``"json_object"`` (default) or ``"text"``.  Controls
            how the driver parses the assistant turn.  Use ``"text"`` when the
            loop is purely tool-driven and the final assistant message is plain
            text.

    Returns:
        The final ``ModelResponse``.  ``finish_reason`` is ``"terminal_tool"``
        when a terminal tool triggered the exit.

    Raises:
        RuntimeError: When ``max_iterations`` is reached without a stop
            condition.
    """
    import json as _json

    from agent_framework.model import ModelResponse

    # Mutate the caller's ``messages`` list in place (do not copy), so store-backed
    # wrappers can diff-append new rows to a ``ConversationStore``.
    current_messages = messages
    terminal_set = frozenset(terminal_tools)
    last_response: ModelResponse | None = None

    for _ in range(max_iterations):
        response = await host.complete_async(
            messages=current_messages,
            model_names=model_names,
            temperature=temperature,
            response_format=response_format,
            response_mode=response_mode,
            tools=tools,
            conversation_id=conversation_id,
        )
        last_response = response

        tool_calls = response.tool_calls or ()

        # No tool calls — model is done (record final assistant in transcript)
        if not tool_calls:
            current_messages.append(
                {
                    "role": "assistant",
                    "content": response.raw_text or None,
                }
            )
            return response

        # Record assistant turn (including tool_calls) before terminal handling so
        # callers persisting ``messages`` match clarification-style transcripts.
        current_messages.append(
            {
                "role": "assistant",
                "content": response.raw_text or None,
                "tool_calls": list(tool_calls),
            }
        )

        # Terminal tool — do not execute tools; return tool arguments as raw_text
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            if name in terminal_set:
                raw_args = fn.get("arguments", "{}")
                return ModelResponse(
                    payload={},
                    raw_text=raw_args,
                    tool_calls=response.tool_calls,
                    finish_reason="terminal_tool",
                    usage=response.usage,
                )

        # Execute tools and collect results
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                args = _json.loads(raw_args) if raw_args else {}
            except _json.JSONDecodeError as exc:
                _LOGGER.error(
                    "run_tool_loop: tool %r arguments are not valid JSON (fragment=%r): %s",
                    name,
                    raw_args if len(str(raw_args)) <= 500 else str(raw_args)[:500] + "…",
                    exc,
                )
                raise ValueError(
                    f"Tool {name!r} arguments must be valid JSON; decode failed: {exc}"
                ) from exc

            if tool_executor is not None:
                result = await tool_executor(name, args)
            else:
                result = f"Tool '{name}' called but no executor provided."

            current_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result,
            })

    if return_on_max_iterations and last_response is not None:
        return ModelResponse(
            payload={},
            raw_text=last_response.raw_text or "",
            finish_reason="max_iterations",
            usage=last_response.usage,
        )

    raise RuntimeError(
        f"run_tool_loop reached max_iterations={max_iterations} without a stop condition."
    )


def _normalize_model_names(
    value: str | tuple[str, ...] | None,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    """Normalize a model specification to a non-empty tuple of model names.

    Accepts a comma-separated string, an existing tuple, or ``None`` (use
    ``default``).
    """
    if value is None:
        return default
    if isinstance(value, str):
        parsed = tuple(m.strip() for m in value.split(",") if m.strip())
        return parsed if parsed else default
    return value if value else default


class _TracingUserCommunication(UserCommunication):
    """Decorator that adds audit tracing to any UserCommunication implementation."""

    def __init__(self, wrapped: Any, tracer: "InMemoryAuditTracer") -> None:
        self._wrapped = wrapped
        self._tracer = tracer

    async def send_message(self, text: str, *, role: str = "assistant") -> None:
        self._tracer.record_user_output(role=role, text=text)
        await self._wrapped.send_message(text, role=role)

    async def ask_question(self, prompt: str, *, options: Any = None, allow_freetext: bool = True) -> str:
        response = await self._wrapped.ask_question(prompt, options=options, allow_freetext=allow_freetext)
        self._tracer.record_user_input(prompt=prompt, response=response)
        return response

    async def ask_confirmation(self, prompt: str, *, default: bool = False) -> bool:
        result = await self._wrapped.ask_confirmation(prompt, default=default)
        self._tracer.record_user_input(prompt=prompt, response=str(result))
        return result

    async def request_permission(self, request: Any) -> Any:
        decision = await self._wrapped.request_permission(request)
        self._tracer.record_permission(
            tool_name=request.tool_name,
            action=request.action,
            resource=request.resource,
            summary=request.summary,
            allowed=decision.allowed,
            remember_for_session=decision.remember_for_session,
        )
        return decision

    async def read_user_input(self, prompt: str = "") -> Any:
        response = await self._wrapped.read_user_input(prompt)
        if response is not None:
            self._tracer.record_user_input(prompt=prompt, response=response)
        return response

    async def stream_text(self, chunks: Any) -> None:
        await self._wrapped.stream_text(chunks)


__all__ = ["AgentHost", "run_tool_loop"]
