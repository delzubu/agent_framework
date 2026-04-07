"""Root host for loading agents, tools, and servicing runtime interactions."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from uuid import uuid4

from agent_framework.agent import Agent, AgentResult, CallContext, ModelEndEvent, ModelStartEvent, SequentialHook
from agent_framework.audit_trace import InMemoryAuditTracer
from agent_framework.config import HostConfig, load_host_config
from agent_framework.model import (
    AsyncToSyncAdapter,
    ModelContext,
    ModelDriver,
    ModelResponse,
    OpenAiModelDriver,
    ProviderRequestTrace,
    ProviderResponseTrace,
)
from agent_framework.skill import SkillRegistry
from agent_framework.tool import Tool, ToolDefinition


@dataclass(slots=True)
class AgentHost:
    """Runtime host for agents, tools, skills, and headless model invocations.

    Attributes:
        config: Typed runtime configuration loaded from ``.env``.
        model_driver: Provider-backed model driver used by all agents.  May be
            a sync ``ModelDriver`` or an async ``AsyncModelDriver`` — the host
            bridges between the two transparently.
        input_reader: Callable used for console input.
        output_writer: Callable used for console output.
        agent_registry: Loaded agent cache keyed by id and sometimes source path.
        tool_registry: Loaded tool cache keyed by tool name.
        contexts: Call contexts opened during execution.
        conversation_store: Optional conversation store for multi-turn sessions.
            When set, ``complete()`` / ``complete_async()`` can load and persist
            message history by ``conversation_id``.
        _executor: Thread pool used for optional parallel subagent execution.
    """

    config: HostConfig
    model_driver: Any | None = None  # ModelDriver | AsyncModelDriver | None
    input_reader: Callable[[str], str] = input
    output_writer: Callable[[str], None] = print
    agent_registry: dict[str, Agent] = field(default_factory=dict)
    tool_registry: dict[str, Tool] = field(default_factory=dict)
    contexts: dict[str, CallContext] = field(default_factory=dict)
    onPreModel: SequentialHook = field(default_factory=SequentialHook)
    onPostModel: SequentialHook = field(default_factory=SequentialHook)
    audit_tracer: InMemoryAuditTracer | None = None
    skill_registry: SkillRegistry | None = None
    conversation_store: Any | None = None  # ConversationStore | AsyncConversationStore | None
    _executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=8))

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        env_path: str | Path = ".env",
        *,
        model_driver: Any | None = None,
        input_reader: Callable[[str], str] = input,
        output_writer: Callable[[str], None] = print,
    ) -> "AgentHost":
        """Construct a host from ``.env`` configuration.

        Auto-detects the driver type from ``DEFAULT_PROVIDER``:
        - ``dial``: constructs a ``DialChatCompletionsDriver`` (requires
          ``agent_framework[dial]`` to be installed).
        - ``openai`` (default): constructs an ``OpenAiModelDriver``.
        """
        config = load_host_config(env_path)
        if model_driver is None:
            if config.default_provider == "dial" and config.dial_base_url:
                from agent_framework.drivers.dial import DialChatCompletionsDriver

                model_driver = DialChatCompletionsDriver(
                    base_url=config.dial_base_url,
                    deployment=config.dial_deployment,
                    api_version=config.dial_api_version,
                    api_key=config.dial_api_key,
                )
            else:
                model_driver = OpenAiModelDriver(api_key=config.openai_api_key)
        host = cls(
            config=config,
            model_driver=model_driver,
            input_reader=input_reader,
            output_writer=output_writer,
        )
        host.enable_audit_trace(output_dir="logs")
        return host

    @classmethod
    def from_env_console(
        cls,
        env_path: str | Path = ".env",
        *,
        model_driver: Any | None = None,
    ) -> "AgentHost":
        """Construct a host wired to real console input and output."""
        return cls.from_env(
            env_path,
            model_driver=model_driver,
            input_reader=input,
            output_writer=print,
        )

    @classmethod
    def create(
        cls,
        *,
        model_driver: Any,
        config: HostConfig | None = None,
        conversation_store: Any | None = None,
        input_reader: Callable[[str], str] = input,
        output_writer: Callable[[str], None] = print,
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
            input_reader: Console input callable (rarely needed in headless use).
            output_writer: Console output callable (rarely needed in headless use).
        """
        if config is None:
            config = HostConfig()
        host = cls(
            config=config,
            model_driver=model_driver,
            input_reader=input_reader,
            output_writer=output_writer,
            conversation_store=conversation_store,
        )
        return host

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
        model_name: str | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
        response_mode: str = "text",
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
            model_name: Model to use, defaults to ``config.default_model``.
            temperature: Sampling temperature.
            response_format: Provider-native response format (forwarded to
                drivers that support it, e.g. ``{"type": "json_object"}``).
            response_mode: ``"text"`` or ``"json_object"``.
            tools: Tool definitions to expose to the model.
            conversation_id: If provided and a ``conversation_store`` is
                attached, prior messages are prepended and the response is
                appended to the store.

        Returns:
            ``ModelResponse`` with the model's reply.
        """
        all_messages = self._load_conversation(conversation_id, messages)
        run_id = str(uuid4())
        context = ModelContext(
            system_prompt="",
            user_prompt="",
            messages=tuple(all_messages),
            response_mode=response_mode,
            response_format=response_format,
            tools=tuple(tools or []),
            run_id=run_id,
        )
        driver = self.get_model_driver_raw()
        if asyncio.iscoroutinefunction(getattr(driver, "decide", None)):
            driver = AsyncToSyncAdapter(driver)
        response = driver.decide(
            agent_id=None,
            provider_name=self.config.default_provider,
            model_name=model_name or self.config.default_model,
            temperature=temperature,
            context=context,
        )
        self._persist_response(conversation_id, all_messages, messages, response)
        return response

    async def complete_async(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        model_name: str | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
        response_mode: str = "text",
        tools: Sequence[ToolDefinition] | None = None,
        conversation_id: str | None = None,
    ) -> ModelResponse:
        """Async single-turn model call without loading an agent definition.

        Uses the async driver directly if available, otherwise runs the sync
        driver via ``asyncio.to_thread``.

        See ``complete()`` for parameter documentation.
        """
        all_messages = await self._load_conversation_async(conversation_id, messages)
        run_id = str(uuid4())
        context = ModelContext(
            system_prompt="",
            user_prompt="",
            messages=tuple(all_messages),
            response_mode=response_mode,
            response_format=response_format,
            tools=tuple(tools or []),
            run_id=run_id,
        )
        driver = self.get_model_driver_raw()
        if asyncio.iscoroutinefunction(getattr(driver, "decide", None)):
            response = await driver.decide(
                agent_id=None,
                provider_name=self.config.default_provider,
                model_name=model_name or self.config.default_model,
                temperature=temperature,
                context=context,
            )
        else:
            response = await asyncio.to_thread(
                driver.decide,
                agent_id=None,
                provider_name=self.config.default_provider,
                model_name=model_name or self.config.default_model,
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
        """Enable immutable in-memory audit tracing plus JSONL dumping."""
        tracer = InMemoryAuditTracer(Path(output_dir))
        self.audit_tracer = tracer
        if self.model_driver is None:
            return tracer
        driver = self.model_driver
        if hasattr(driver, "set_trace_callbacks"):
            existing_request = getattr(driver, "on_request_trace", None)
            existing_response = getattr(driver, "on_response_trace", None)

            def on_request(event: ProviderRequestTrace) -> None:
                if callable(existing_request):
                    existing_request(event)
                if tracer is not None and event.run_id is not None:
                    tracer.record_llm_request(run_id=event.run_id, payload=event.input_payload)

            def on_response(event: ProviderResponseTrace) -> None:
                if callable(existing_response):
                    existing_response(event)
                if tracer is not None and event.run_id is not None:
                    parsed_payload = None if event.parsed_payload is None else dict(event.parsed_payload)
                    tracer.record_llm_response(run_id=event.run_id, raw_text=event.raw_text, parsed_payload=parsed_payload)

            driver.set_trace_callbacks(on_request=on_request, on_response=on_response)
        return tracer

    def get_skill_registry(self) -> SkillRegistry:
        """Lazy-initialize and return the host-level skill registry."""
        if self.skill_registry is None:
            self.skill_registry = SkillRegistry.from_config(self.config)
            self.skill_registry.discover()
        return self.skill_registry

    def register_tool(self, tool: Tool) -> None:
        """Register a concrete tool instance for runtime execution."""
        self.tool_registry[tool.name] = tool

    def get_tool(self, tool_name: str) -> Tool:
        """Return a loaded tool, creating it from the configured tool directory on demand."""
        if tool_name in self.tool_registry:
            return self.tool_registry[tool_name]
        tool = Tool.from_name(tool_name, self.config.tools_directory)
        self.tool_registry[tool.name] = tool
        return tool

    def load_agent(self, agent_ref: str | Path) -> Agent:
        """Load and cache an agent definition from Markdown."""
        source_path = self._resolve_agent_markdown_path(agent_ref)
        agent = Agent.from_markdown(
            source_path,
            default_provider=self.config.default_provider,
            default_model=self.config.default_model,
        )
        if source_path.stem in self.config.agent_models:
            agent.model_name = self.config.agent_models[source_path.stem]
        if agent.agent_id in self.config.agent_models:
            agent.model_name = self.config.agent_models[agent.agent_id]
        self.agent_registry[agent.agent_id] = agent
        if agent.source_path is not None:
            self.agent_registry[str(agent.source_path)] = agent
        return agent

    def get_agent(self, agent_id: str, *, base_dir: Path | None = None) -> Agent:
        """Resolve an agent by logical id, explicit path, sibling path, or agent directory."""
        if agent_id in self.agent_registry:
            return self.agent_registry[agent_id]
        path_candidate = Path(agent_id)
        if path_candidate.exists():
            return self.load_agent(path_candidate)
        if base_dir is not None:
            sibling_candidate = (base_dir / f"{agent_id}.md").resolve()
            if sibling_candidate.exists():
                return self.load_agent(sibling_candidate)
        default_dir_candidate = (self.config.agent_directory / f"{agent_id}.md").resolve()
        if default_dir_candidate.exists():
            return self.load_agent(default_dir_candidate)
        raise KeyError(f"Unknown agent: {agent_id}")

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

    def run_agent(
        self,
        agent_id: str,
        initial_instruction: str | None = None,
        *,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
        prompt_fragments: tuple[str, ...] | None = None,
    ) -> AgentResult:
        """Run a specific agent id as a top-level invocation."""
        agent = self.get_agent(agent_id)
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
        initial_instruction = self.input_reader("Instruction: ")
        result = self.run_root(initial_instruction=initial_instruction)
        if result.message:
            self.output_writer(result.message)
        return result

    def call_subagent(self, *, caller: Agent, callee_id: str, parameters: dict[str, Any]) -> AgentResult:
        """Synchronously invoke a child agent from a caller agent."""
        base_dir = caller.source_path.parent if caller.source_path is not None else None
        callee = self.get_agent(callee_id, base_dir=base_dir)
        return callee.run(host=self, parameters=parameters, caller_id=caller.agent_id)

    def call_subagent_async(
        self,
        *,
        caller: Agent,
        callee_id: str,
        parameters: dict[str, Any],
    ) -> Future[AgentResult]:
        """Invoke a child agent on the thread pool for parallel execution."""
        return self._executor.submit(self.call_subagent, caller=caller, callee_id=callee_id, parameters=parameters)

    def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> str:
        """Execute a loaded tool by name."""
        return self.get_tool(tool_name).invoke(parameters, self)

    def resolve_callback(self, *, caller_id: str, callee: Agent, prompt: str) -> str:
        """Collect a callback response from the caller side through console I/O."""
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
        message = f"{callee.agent_id} asks {caller_id}: {prompt}\nResponse: "
        return self.input_reader(message)

    def request_user_input(self, prompt: str) -> str:
        """Collect direct user input requested by an agent."""
        return self.input_reader(f"{prompt}\n> ")

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
    model_name: str | None = None,
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
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
        model_name: Passed to ``complete_async()``.
        temperature: Passed to ``complete_async()``.
        response_format: Passed to ``complete_async()``.

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
            model_name=model_name,
            temperature=temperature,
            response_format=response_format,
            response_mode="text",
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
            except _json.JSONDecodeError:
                args = {}

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


__all__ = ["AgentHost", "run_tool_loop"]
