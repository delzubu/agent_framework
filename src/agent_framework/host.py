"""Root host for loading agents, tools, and servicing runtime interactions."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agent_framework.agent import Agent, AgentResult, CallContext, ModelEndEvent, ModelStartEvent, SequentialHook
from agent_framework.audit_trace import InMemoryAuditTracer
from agent_framework.config import HostConfig, load_host_config
from agent_framework.model import ModelDriver, OpenAiModelDriver, ProviderRequestTrace, ProviderResponseTrace
from agent_framework.tool import Tool


@dataclass(slots=True)
class AgentHost:
    """Console-oriented runtime host.

    Attributes:
        config: Typed runtime configuration loaded from `.env`.
        model_driver: Provider-backed model driver used by all agents unless
            overridden by a custom host implementation.
        input_reader: Callable used for console input.
        output_writer: Callable used for console output.
        agent_registry: Loaded agent cache keyed by id and sometimes source path.
        tool_registry: Loaded tool cache keyed by tool name.
        contexts: Call contexts opened during execution.
        _executor: Thread pool used for optional parallel subagent execution.
    """

    config: HostConfig
    model_driver: ModelDriver | None = None
    input_reader: Callable[[str], str] = input
    output_writer: Callable[[str], None] = print
    agent_registry: dict[str, Agent] = field(default_factory=dict)
    tool_registry: dict[str, Tool] = field(default_factory=dict)
    contexts: dict[str, CallContext] = field(default_factory=dict)
    onPreModel: SequentialHook = field(default_factory=SequentialHook)
    onPostModel: SequentialHook = field(default_factory=SequentialHook)
    audit_tracer: InMemoryAuditTracer | None = None
    _executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=8))

    @classmethod
    def from_env(
        cls,
        env_path: str | Path = ".env",
        *,
        model_driver: ModelDriver | None = None,
        input_reader: Callable[[str], str] = input,
        output_writer: Callable[[str], None] = print,
    ) -> "AgentHost":
        """Construct a host from `.env` configuration."""
        config = load_host_config(env_path)
        host = cls(
            config=config,
            model_driver=model_driver or OpenAiModelDriver(api_key=config.openai_api_key),
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
        model_driver: ModelDriver | None = None,
    ) -> "AgentHost":
        """Construct a host wired to real console input and output."""
        return cls.from_env(
            env_path,
            model_driver=model_driver,
            input_reader=input,
            output_writer=print,
        )

    def get_root_agent(self) -> Agent:
        """Load and return the root agent configured in `.env`."""
        return self.get_agent(self.config.root_agent_id)

    def get_model_driver(self, agent: Agent) -> ModelDriver:
        """Return the shared model driver used for this runtime."""
        if self.model_driver is None:
            raise ValueError("AgentHost requires a model driver.")
        return self.model_driver

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

__all__ = ["AgentHost"]
