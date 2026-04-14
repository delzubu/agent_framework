"""Root host for loading agents, tools, and servicing runtime interactions."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from uuid import uuid4

from agent_framework.agent import Agent, AgentResult, CallContext, ModelEndEvent, ModelStartEvent, SequentialHook
from agent_framework.agent_registry import AgentRegistry
from agent_framework.agent_event_publisher import agent_events
from agent_framework.audit_trace import AuditTraceSubscriber, InMemoryAuditTracer
from agent_framework.command import CommandDefinition, CommandRegistry, render as render_command
from agent_framework.config import HostConfig, load_host_config
from agent_framework.model import (
    AsyncToSyncAdapter,
    DEFAULT_RESPONSE_MODE,
    ModelContext,
    ModelDriver,
    ModelResponse,
    OpenAiModelDriver,
    merge_runtime_system_into_messages,
)
from agent_framework.skill import SkillRegistry
from agent_framework.tool import Tool, ToolDefinition
from agent_framework.tool_registry import ToolRegistry
from agent_framework.llm_trace_logging import wire_llm_traces_to_runtime_tracer
from agent_framework.tracing import (
    CompositeRuntimeTracer,
    NullRuntimeTracer,
    RuntimeTracer,
    TraceContext,
    make_trace_event,
)
from agent_framework.tracing_bridge import active_tracer_scope
from agent_framework.user_communication import NullUserCommunication, UserCommunication

_LOGGER = logging.getLogger(__name__)


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
    model_driver: Any | None = None  # ModelDriver | AsyncModelDriver | None
    tool_registry: ToolRegistry = field(default_factory=lambda: ToolRegistry(directories=()))
    agent_registry: AgentRegistry = field(default_factory=lambda: AgentRegistry(directories=(), config=None))
    command_registry: CommandRegistry = field(default_factory=lambda: CommandRegistry(directories=()))
    user_comm: Any = None   # UserCommunication | None
    mcp_manager: Any = None  # McpManager | None
    contexts: dict[str, CallContext] = field(default_factory=dict)
    onPreModel: SequentialHook = field(default_factory=SequentialHook)
    onPostModel: SequentialHook = field(default_factory=SequentialHook)
    runtime_tracer: RuntimeTracer = field(default_factory=NullRuntimeTracer)
    trace_context_overlay: TraceContext | None = None
    _audit_jsonl: InMemoryAuditTracer | None = field(default=None, repr=False)
    _audit_trace_subscriber: AuditTraceSubscriber | None = field(default=None, repr=False)
    _llm_traces_wired: bool = field(default=False, repr=False)
    skill_registry: SkillRegistry | None = None
    conversation_store: Any | None = None  # ConversationStore | AsyncConversationStore | None
    _executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=8))
    _command_fallback: Any = None  # Callable[[str, str], Awaitable[str | None]] | None
    _started: bool = False
    _host_receive_log_subscriber: Any | None = field(default=None, repr=False)
    _host_receive_log_path: Path | None = field(default=None, repr=False)

    @property
    def audit_tracer(self) -> InMemoryAuditTracer | None:
        """JSONL audit store when :meth:`enable_audit_trace` is used (read-only)."""
        return self._audit_jsonl

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
        return host

    @classmethod
    def from_env_console(
        cls,
        env_path: str | Path = ".env",
        *,
        model_driver: Any | None = None,
        model_override: str | tuple[str, ...] | None = None,
    ) -> "AgentHost":
        """Construct a console host, run discovery, and start MCP connections."""
        from agent_framework.console_communication import ConsoleUserCommunication

        host = cls.from_env(
            env_path,
            model_driver=model_driver,
            model_override=model_override,
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
        command_registry = CommandRegistry.from_config(config)

        if builtin_tools:
            from agent_framework.builtin_tools import register_builtin_tools
            register_builtin_tools(tool_registry)

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
        self.tool_registry.discover()
        self.agent_registry.discover()
        self.command_registry.discover()
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
        # If this was the first turn, store the initial messages too
        prior_len = len(all_messages) - len(new_messages)
        if prior_len == 0:
            store.append(conversation_id, list(new_messages) + [assistant_msg])
        else:
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
        return self.agent_registry._load_and_cache(source_path)

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
            onPreAgent=_copy_hook(agent.onPreAgent),
            onPostAgent=_copy_hook(agent.onPostAgent),
            onPreTool=_copy_hook(agent.onPreTool),
            onPostTool=_copy_hook(agent.onPostTool),
            onPreSubagent=_copy_hook(agent.onPreSubagent),
            onPostSubagent=_copy_hook(agent.onPostSubagent),
            onPreSkill=_copy_hook(agent.onPreSkill),
            onPostSkill=_copy_hook(agent.onPostSkill),
            onPreModel=_copy_hook(agent.onPreModel),
            onPostModel=_copy_hook(agent.onPostModel),
        )
        rt_behavior = RuntimeTraceBehavior()
        wired = replace(cloned, behaviors=cloned.behaviors + (rt_behavior,))
        rt_behavior.attach(wired)
        return wired

    def run_agent(
        self,
        agent_id: str,
        initial_instruction: str | None = None,
        *,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
        prompt_fragments: tuple[str, ...] | None = None,
    ) -> AgentResult:
        """Run a specific agent id as a top-level invocation."""
        agent = self._agent_with_runtime_tracing(self.get_agent(agent_id))
        with active_tracer_scope(self.runtime_tracer, self.trace_context_overlay):
            return agent.run(
                host=self,
                parameters={},
                caller_id="host",
                rendered_prompt_override=initial_instruction or "",
                conversation_messages=conversation_messages,
                prompt_fragments=prompt_fragments,
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
    ) -> AgentResult:
        """Synchronously invoke a child agent from a caller agent."""
        base_dir = caller.source_path.parent if caller.source_path is not None else None
        callee = self._agent_with_runtime_tracing(self.get_agent(callee_id, base_dir=base_dir))
        with active_tracer_scope(self.runtime_tracer, self.trace_context_overlay):
            return callee.run(
                host=self,
                parameters=parameters,
                caller_id=caller.agent_id,
                parent_run_id=parent_run_id,
            )

    def call_subagent_async(
        self,
        *,
        caller: Agent,
        callee_id: str,
        parameters: dict[str, Any],
        parent_run_id: str | None = None,
    ) -> Future[AgentResult]:
        """Invoke a child agent on the thread pool for parallel execution."""
        return self._executor.submit(
            self.call_subagent,
            caller=caller,
            callee_id=callee_id,
            parameters=parameters,
            parent_run_id=parent_run_id,
        )

    def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> str:
        """Execute a loaded tool by name."""
        return self.get_tool(tool_name).invoke(parameters, self)

    def resolve_callback(self, *, caller_id: str, callee: Agent, prompt: str) -> str:
        """Collect a callback response from the caller side."""
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
                    parameters={},
                    caller_id="host",
                    rendered_prompt_override=prompt,
                )
                if result.message:
                    return result.message
        # Fall back to user_comm
        if self.user_comm is None:
            return ""
        message = f"{callee.agent_id} asks {caller_id}: {prompt}\nResponse: "
        result = self._run_user_comm_coro(self.user_comm.read_user_input(message))
        return result or ""

    def request_user_input(self, prompt: str) -> str:
        """Collect direct user input via ``user_comm``."""
        if self.user_comm is None:
            raise RuntimeError(
                "No UserCommunication configured. Use AgentHost.create() with user_comm=."
            )
        result = self._run_user_comm_coro(self.user_comm.read_user_input(f"{prompt}\n> "))
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
        for callback in self.onPreModel:
            callback(event)

    def run_post_model_hooks(self, event: ModelEndEvent) -> None:
        """Execute host-level post-model callbacks."""
        for callback in self.onPostModel:
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
        messages: Initial message list.  Modified in-place as turns progress.
        tools: Tool definitions exposed to the model.
        tool_executor: Async callable ``(tool_name, arguments) -> result_str``.
            When ``None``, tool calls are recorded but not executed.
        terminal_tools: Tool names that cause an immediate loop exit when called
            by the model.  The tool is not executed; its arguments are returned.
        max_iterations: Maximum number of model calls before raising
            ``RuntimeError``.
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

    current_messages = list(messages)

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

        tool_calls = response.tool_calls or ()

        # No tool calls — model is done
        if not tool_calls:
            return response

        # Check for terminal tool
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            if name in terminal_tools:
                raw_args = fn.get("arguments", "{}")
                return ModelResponse(
                    payload={},
                    raw_text=raw_args,
                    tool_calls=response.tool_calls,
                    finish_reason="terminal_tool",
                    usage=response.usage,
                )

        # Append assistant message with tool calls
        current_messages.append({
            "role": "assistant",
            "content": response.raw_text or None,
            "tool_calls": list(tool_calls),
        })

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


class _TracingUserCommunication:
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
