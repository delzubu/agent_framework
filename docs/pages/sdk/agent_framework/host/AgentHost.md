---
title: AgentHost
layout: default
sdk_page: true
---


# `AgentHost`

Module: [`agent_framework.host`](../host.html)

<!-- BEGIN sdk-overlay -->

# Purpose

`AgentHost` is the primary orchestration object in `agent_framework`.

It owns the runtime context for agent execution: model driver access, agent discovery, tool discovery, command dispatch, skill lookup, conversation persistence, tracing, user communication, and optional MCP bridge setup.

## Typical Lifecycle

1. Construct the host from environment configuration or explicit dependencies.
2. Start the host if the selected construction path does not already do it.
3. Run agents or direct model completions.
4. Inspect traces, results, callbacks, or conversation state.
5. Close the host when asynchronous resources such as MCP clients or async drivers need shutdown.

## Usage Guidance

Use `AgentHost` directly when building applications, tests, evaluators, or command-line tools around the framework.

For simple console projects, prefer `AgentHost.from_env_console(...)`. For embedded services, prefer `AgentHost.create(...)` with explicit dependencies so the surrounding application controls configuration and lifecycle.

## Common Mistakes

- Creating registries manually when `AgentHost` can assemble them from configuration.
- Forgetting lifecycle management when MCP or async drivers are enabled.
- Treating `complete(...)` as an agent run. `complete(...)` is a direct model call; `run_agent(...)` executes the markdown-defined agent loop.
- Hiding invalid model decisions with repair logic. `AgentHost` relies on strict decision parsing so invalid structured output is visible during development and evaluation.

<!-- END sdk-overlay -->

## API Summary

```python
class AgentHost
```

Runtime host for agents, tools, skills, and headless model invocations.

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

## Attributes

- `agent_registry`
- `command_registry`
- `config`
- `contexts`
- `conversation_store`
- `file_ref_resolver`
- `mcp_manager`
- `model_driver`
- `on_post_model`
- `on_pre_model`
- `runtime_tracer`
- `session_id`
- `skill_registry`
- `tool_registry`
- `trace_context_overlay`
- `user_comm`

## Methods

### `audit_tracer`

```python
def audit_tracer(self) -> InMemoryAuditTracer | None
```

JSONL audit store when :meth:`enable_audit_trace` is used (read-only).

### `host_receive_log_path`

```python
def host_receive_log_path(self) -> Path | None
```

Path of the unified trace JSONL file when :meth:`enable_host_receive_log` is active.

### `from_env`

```python
def from_env(cls, env_path: str | Path = '.env', *, model_driver: Any | None = None, model_override: str | tuple[str, ...] | None = None, user_comm: Any | None = None, input_reader: Any = None, output_writer: Any = None) -> 'AgentHost'
```

Construct a host from ``.env`` configuration.

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

### `from_env_console`

```python
def from_env_console(cls, env_path: str | Path = '.env', *, model_driver: Any | None = None, model_override: str | tuple[str, ...] | None = None) -> 'AgentHost'
```

Construct a console host, run discovery, and start MCP connections.

### `create`

```python
def create(cls, *, model_driver: Any, config: HostConfig | None = None, conversation_store: Any | None = None, user_comm: Any | None = None, builtin_tools: bool = True, mcp_enabled: bool = True, command_fallback: Any | None = None) -> 'AgentHost'
```

Construct a host with an explicit driver.  No ``.env`` file required.

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

### `start`

```python
async def start(self) -> None
```

Discover all registries and start MCP servers.  Idempotent.

### `aclose`

```python
async def aclose(self) -> None
```

Shut down MCP connections and close async driver if applicable.

### `execute_command`

```python
async def execute_command(self, name: str, raw_args: str = '') -> str | None
```

Render and return a command prompt, or invoke the fallback for unknown commands.

Returns the rendered prompt string when the command is found (caller
decides what to do with it, e.g. pass it to ``run_agent``).  Returns
``None`` when the command is unknown and no fallback is registered.

### `get_root_agent`

```python
def get_root_agent(self) -> Agent
```

Load and return the root agent configured in ``.env``.

### `get_model_driver`

```python
def get_model_driver(self, agent: Agent) -> ModelDriver
```

Return the model driver for use in the sync agent loop.

If the configured driver is async, it is wrapped with
``AsyncToSyncAdapter`` transparently so the existing agent loop works
without modification.

### `complete`

```python
def complete(self, *, messages: Sequence[dict[str, Any]], model_names: str | tuple[str, ...] | None = None, temperature: float = 0.2, response_format: dict[str, Any] | None = None, response_mode: str = DEFAULT_RESPONSE_MODE, tools: Sequence[ToolDefinition] | None = None, conversation_id: str | None = None) -> ModelResponse
```

Single-turn model call without loading an agent definition.

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

### `complete_async`

```python
async def complete_async(self, *, messages: Sequence[dict[str, Any]], model_names: str | tuple[str, ...] | None = None, temperature: float = 0.2, response_format: dict[str, Any] | None = None, response_mode: str = DEFAULT_RESPONSE_MODE, tools: Sequence[ToolDefinition] | None = None, conversation_id: str | None = None) -> ModelResponse
```

Async single-turn model call without loading an agent definition.

Uses the async driver directly if available, otherwise runs the sync
driver via ``asyncio.to_thread``.

See ``complete()`` for parameter documentation.

### `get_model_driver_raw`

```python
def get_model_driver_raw(self) -> Any
```

Return the raw driver (sync or async) without any adapter wrapping.

### `enable_audit_trace`

```python
def enable_audit_trace(self, *, output_dir: str | Path = 'logs') -> InMemoryAuditTracer
```

Enable immutable in-memory audit tracing plus JSONL dumping.

Subscribes :class:`AuditTraceSubscriber` to :attr:`runtime_tracer`. If the tracer
was :class:`NullRuntimeTracer`, it is replaced with a :class:`CompositeRuntimeTracer`.
LLM request/response rows are recorded from ``llm.*`` events (see :func:`wire_llm_traces_to_runtime_tracer`).

### `enable_host_receive_log`

```python
def enable_host_receive_log(self, *, output_dir: str | Path = 'logs') -> Path
```

Append every :class:`TraceEvent` the host tracer receives to a timestamped JSONL file.

File name: ``logs/agent-host-YYYYMMDD-HHMMSS.jsonl`` (under ``output_dir``).

Called automatically from :meth:`from_env` unless ``AGENT_HOST_RECEIVE_LOG`` is disabled.

If another component replaces :attr:`runtime_tracer` (e.g. the evaluator session
tracer), it must re-subscribe the same subscriber — see
``agent_framework_evaluator.runtime.session_runner``.

### `get_skill_registry`

```python
def get_skill_registry(self) -> SkillRegistry
```

Lazy-initialize and return the host-level skill registry.

### `register_tool`

```python
def register_tool(self, tool: Tool) -> None
```

Register a concrete tool instance for runtime execution.

### `get_tool`

```python
def get_tool(self, tool_name: str) -> Tool
```

Return a loaded tool by name.

### `resolve_model_tool_definitions`

```python
def resolve_model_tool_definitions(self, tool_names: tuple[str, ...], *, agent_id: str | None = None, run_id: str | None = None) -> tuple[ToolDefinition, ...]
```

Resolve agent ``allowed_tools`` into provider ``ToolDefinition`` objects.

Logs and emits ``runtime.tool_unavailable`` when a tool cannot be loaded.
With ``HostConfig.missing_tool_policy == "graceful"`` (default), missing
tools are omitted and the run continues. With ``"strict"``, the first
failure is re-raised after logging/tracing.

### `load_agent`

```python
def load_agent(self, agent_ref: str | Path) -> Agent
```

Load and cache an agent definition from Markdown.

### `get_agent`

```python
def get_agent(self, agent_id: str, *, base_dir: Path | None = None) -> Agent
```

Resolve an agent by logical id, explicit path, sibling path, or agent directory.

### `run_root`

```python
def run_root(self, initial_instruction: str | None = None, *, conversation_messages: tuple[dict[str, str], ...] | None = None, prompt_fragments: tuple[str, ...] | None = None) -> AgentResult
```

Run the configured root agent using console-sourced input.

### `publish_trace_event`

```python
def publish_trace_event(self, *, kind: str, title: str, summary: str = '', payload: dict[str, Any] | None = None, span_id: str | None = None, parent_span_id: str | None = None, context: TraceContext | None = None, channel: str = 'runtime', level: str = 'info') -> None
```

No method docstring is available yet.

### `run_agent`

```python
def run_agent(self, agent_id: str, initial_instruction: str | None = None, *, conversation_messages: tuple[dict[str, str], ...] | None = None, prompt_fragments: tuple[str, ...] | None = None) -> AgentResult
```

Run a specific agent id as a top-level invocation.

### `run_console`

```python
def run_console(self) -> AgentResult
```

Prompt for the initial instruction, run the root agent, and print the result.

### `call_subagent`

```python
def call_subagent(self, *, caller: Agent, callee_id: str, parameters: dict[str, Any], parent_run_id: str | None = None, run_id: str | None = None, in_parallel_batch: bool = False, conversation_messages: tuple[dict[str, str], ...] | None = None) -> AgentResult
```

Synchronously invoke a child agent from a caller agent.

### `call_subagent_async`

```python
def call_subagent_async(self, *, caller: Agent, callee_id: str, parameters: dict[str, Any], parent_run_id: str | None = None, run_id: str | None = None, in_parallel_batch: bool = False, conversation_messages: tuple[dict[str, str], ...] | None = None) -> Future[AgentResult]
```

Invoke a child agent on the thread pool for parallel execution.

Captures the current contextvars context so tracer scope and other
context variables propagate correctly into the worker thread.

### `save_checkpoint`

```python
def save_checkpoint(self, run_id: str, messages: list[dict]) -> None
```

Persist conversation state for a blocked parallel child.

Skipped if the run was already marked as timed-out by the parent batch
(orphaned thread finishing after parent abandoned the wait).

### `load_checkpoint`

```python
def load_checkpoint(self, run_id: str) -> list[dict] | None
```

Return saved conversation messages for a run_id, or None.

### `delete_checkpoint`

```python
def delete_checkpoint(self, run_id: str) -> None
```

Remove a checkpoint after the child has completed or failed.

### `cleanup_checkpoints`

```python
def cleanup_checkpoints(self, ttl_seconds: float = 3600.0) -> int
```

Remove checkpoints older than ttl_seconds.  Returns count removed.

Also purges the timed-out run-id tombstone set so it doesn't grow
unboundedly when many batches have been executed.

### `call_subagent_batch`

```python
def call_subagent_batch(self, *, caller: Agent, specs: tuple[SubagentCallSpec, ...], mode: str, timeout_seconds: float | None, parent_run_id: str | None = None) -> list[SubagentBatchItemResult]
```

Orchestrate a call_subagents batch with callback-resume loop.

### `execute_tool`

```python
def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> str
```

Execute a loaded tool by name.

### `resolve_callback`

```python
def resolve_callback(self, *, caller_id: str, callee: Agent, prompt: str) -> str
```

Collect a callback response from the caller side.

### `request_user_input`

```python
def request_user_input(self, prompt: str) -> str
```

Collect direct user input via ``user_comm``.

### `open_context`

```python
def open_context(self, *, caller_id: str, callee_id: str, kind: str) -> CallContext
```

Create, store, and return a new runtime call context.

### `run_pre_model_hooks`

```python
def run_pre_model_hooks(self, event: ModelStartEvent) -> None
```

Execute host-level pre-model callbacks.

### `run_post_model_hooks`

```python
def run_post_model_hooks(self, event: ModelEndEvent) -> None
```

Execute host-level post-model callbacks.

### `enable_llm_trace_logging`

```python
def enable_llm_trace_logging(self, *, target: str = 'file', output_dir: str | Path = 'logs') -> None
```

Attach shared LLM trace logging to this host.

### `resolve_world_path`

```python
def resolve_world_path(self, raw_path: object) -> Path
```

Resolve a tool path strictly inside the configured world directory.
