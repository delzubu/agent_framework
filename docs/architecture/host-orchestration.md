# Host & Orchestration

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Agent Runtime](./agent-runtime.md) · [Model Abstraction](./model-abstraction.md) · [Drivers](./drivers.md) · [Conversation Model](./conversation-model.md) · [Extension Points](./extension-points.md) · [Tracing & Evaluation](./tracing-evaluation.md) · [Interface Specifications](./interfaces.md) · User guide: [Using the agent evaluator](../guides/using-agent-evaluator.md)

---

## 1. Overview

`AgentHost` (`src/agent_framework/host.py`) is the central orchestration runtime for the framework. It owns:
- **Tool registry** (`ToolRegistry`) — discovers, caches, and executes `Tool` instances; accepts programmatic registration for built-in and MCP-bridged tools
- **Agent registry** (`AgentRegistry`) — discovers, caches, and resolves `Agent` instances from Markdown files
- **Command registry** (`CommandRegistry`) — discovers parametrized markdown prompt commands
- **Model driver** — the `ModelDriver` implementation used for all LLM calls
- **User communication** (`UserCommunication`) — async I/O abstraction replacing `input_reader`/`output_writer`; default `NullUserCommunication` for headless use
- **MCP manager** (`McpManager`) — optional client-side MCP integration; bridges MCP tools into `ToolRegistry`
- **Call contexts** — `CallContext` objects tracking active call edges between agents
- **Audit tracer** — optional `InMemoryAuditTracer` for immutable JSONL audit output
- **Runtime tracer** — `runtime_tracer` (`RuntimeTracer`, default `NullRuntimeTracer`) for structured `TraceEvent` fan-out; LLM provider hooks also publish `llm.request` / `llm.response` when trace logging is enabled
- **Thread pool** — `ThreadPoolExecutor` for async subagent parallelism
- **Host-level hooks** — `onPreModel` and `onPostModel` for cross-cutting model interception

`AgentHost` implements `AgentHostProtocol` — agents access it only through that protocol interface, enabling test doubles and alternative implementations.

---

## 2. `AgentHost` Structure

```python
@dataclass(slots=True)
class AgentHost:
    config: HostConfig
    model_driver: ModelDriver | AsyncModelDriver | None
    tool_registry: ToolRegistry              # eager catalog / lazy load; programmatic registration
    agent_registry: AgentRegistry            # eager catalog / lazy load
    command_registry: CommandRegistry        # fully loaded at discovery time
    user_comm: UserCommunication | None      # default: NullUserCommunication
    mcp_manager: McpManager | None           # None when MCP disabled or no servers configured
    contexts: dict[str, CallContext]
    onPreModel: SequentialHook
    onPostModel: SequentialHook
    audit_tracer: InMemoryAuditTracer | None
    runtime_tracer: RuntimeTracer              # default NullRuntimeTracer
    trace_context_overlay: TraceContext | None  # merged into published TraceEvent context (e.g. evaluator session_id)
    skill_registry: SkillRegistry | None
    conversation_store: ConversationStore | AsyncConversationStore | None
    _executor: ThreadPoolExecutor            # max_workers=8
    _command_fallback: Callable | None       # (name, raw_args) -> Awaitable[str | None]
    _started: bool
```

### Factory Methods

**`AgentHost.create(*, model_driver, config=None, conversation_store=None, user_comm=None, builtin_tools=True, mcp_enabled=True, command_fallback=None) -> AgentHost`**

Preferred programmatic entry point — no `.env` file required. Constructs formal registries from config directories, registers built-in tools when `builtin_tools=True`, loads MCP configs (if available and `mcp_enabled=True`) but does **not** start connections. Returns an unstarted host.

**`create_web_host(...)`** (`web_host.py`) — builds an `AgentHost` via `AgentHost.create(...)` and assigns a supplied `CompositeRuntimeTracer` (or `NullRuntimeTracer`) to `runtime_tracer`, pairing it with a `WebUserCommunication` instance for browser-driven runs.

```python
from agent_framework import AgentHost
from agent_framework.drivers.dial import DialChatCompletionsDriver

driver = DialChatCompletionsDriver(base_url="https://...", api_key="...")
host = AgentHost.create(model_driver=driver)
await host.start()
```

**`AgentHost.from_env(env_path, *, model_driver=None, model_override=None, user_comm=None) -> AgentHost`**

Loads `HostConfig` from the `.env` file, auto-detects the driver, creates the `InMemoryAuditTracer`, and runs synchronous registry discovery. Does **not** call `start()` — MCP startup still requires `await host.start()`. The deprecated `input_reader`/`output_writer` kwargs are silently ignored for backward compatibility.

**`AgentHost.from_env_console(env_path, *, model_driver=None, model_override=None) -> AgentHost`**

Calls `from_env` with `ConsoleUserCommunication` wired, then runs `await host.start()` synchronously via a thread pool executor. Used by the CLI. Returns a fully started host.

### Lifecycle

**`await host.start() -> None`**

Idempotent. Discovers registries (tool, agent, command, skill), starts MCP connections (`McpManager.start_all()`), bridges MCP tools into `tool_registry`, and wraps `user_comm` with `_TracingUserCommunication` when `audit_tracer` is set. Safe to call multiple times.

**`await host.aclose() -> None`**

Stops MCP connections (`McpManager.stop_all()`) and calls `driver.aclose()` if the driver exposes it (e.g., `DialChatCompletionsDriver`).

---

## 3. Configuration (`HostConfig`)

```python
@dataclass(frozen=True, slots=True)
class HostConfig:
    openai_api_key: str = ""
    default_provider: str = "openai"
    default_model: tuple[str, ...] = ("gpt-4o-mini",)   # first = highest priority
    agent_directory: Path = Path("agents")
    tools_directory: Path = Path("tools")
    world_directory: Path = Path("world")
    root_agent_id: str = "root"
    agent_models: dict[str, tuple[str, ...]] = field(default_factory=dict)
    skills_directories: tuple[Path, ...] = ()
    skills_catalog_max_tokens: int = 2000
    # DIAL provider
    dial_base_url: str = ""
    dial_api_version: str = "2024-10-21"
    dial_api_key: str = ""
    # Commands
    commands_directories: tuple[Path, ...] = ()
    # MCP
    mcp_config_path: Path | None = None
    mcp_enabled: bool = True
```

All fields have defaults — `HostConfig()` with no arguments is valid.

**`.env` Keys:**

| Key | Description | Default |
|-----|-------------|---------|
| `OPENAI_API_KEY` | API key for OpenAI | `""` |
| `DEFAULT_PROVIDER` | Provider name (`openai` or `dial`) | `openai` |
| `DEFAULT_MODEL` | Comma-separated model list (first = highest priority): `gpt-4o,gpt-4o-mini` | `gpt-4o-mini` |
| `AGENT_DIRECTORY` | Directory containing agent `.md` files | `agents` |
| `TOOLS_DIRECTORY` | Directory containing tool `.md` + `.py` pairs | `tools` |
| `WORLD_DIRECTORY` | Sandboxed root for tool file access | `world` |
| `ROOT_AGENT` | Agent ID to run as the root agent | `root` |
| `AGENT_MODELS` | Per-agent model overrides: `agent1=m1,m2\|agent2=m3` (`\|` separates agents, `,` separates models) | — |
| `SKILLS_DIRECTORY` | Single skills directory path | — |
| `SKILLS_DIRECTORIES` | Multiple skills directories (comma-separated) | — |
| `SKILLS_CATALOG_MAX_TOKENS` | Max tokens for skills catalog | `2000` |
| `DIAL_BASE_URL` | DIAL API base URL | `""` |
| `DIAL_API_VERSION` | DIAL API version query param | `2024-10-21` |
| `DIAL_API_KEY` | DIAL API key | `""` |
| `COMMANDS_DIRECTORY` | Single commands directory | — |
| `COMMANDS_DIRECTORIES` | Multiple commands directories (comma-separated) | — |
| `MCP_CONFIG_PATH` | Explicit `.mcp.json` path (overrides auto-discovery) | — |
| `MCP_ENABLED` | Set `false` to disable MCP entirely | `true` |
| `MISSING_TOOL_POLICY` | `graceful` (default) — omit unloadable tools from the model API and continue; `strict` — fail the run when a listed tool cannot be loaded | `graceful` |

Directory paths in `.env` are relative to the env file's parent directory and are resolved to absolute `Path` objects by `load_host_config()`.

---

## 4. Agent Lifecycle Management

### 4.1 Agent Loading

**`AgentHost.load_agent(agent_ref: str | Path) -> Agent`**

Loads an agent from its Markdown file. Applies the per-agent model override from `config.agent_models`.

```python
agent = Agent.from_markdown(
    path,
    default_provider=self.config.default_provider,
    default_model=self.config.default_model,
    model_override=self.config.model_for(agent_id),
)
```

After loading, the agent is cached in `agent_registry` by both its `agent_id` and its `source_path`.

### 4.2 Agent Resolution Chain

**`AgentHost.get_agent(agent_id: str, *, base_dir: Path | None = None) -> Agent`**

Resolution order (first match wins):

| Priority | Source | Condition |
|----------|--------|-----------|
| 1 | `agent_registry[agent_id]` | Already loaded by ID |
| 2 | `agent_registry[source_path]` | Already loaded by path |
| 3 | `agent_id` as a literal file path | If it resolves to an existing `.md` file |
| 4 | `base_dir / agent_id` | If `base_dir` provided and `{agent_id}.md` exists there |
| 5 | `base_dir / f"{agent_id}.md"` | Explicit `.md` suffix |
| 6 | `config.agent_directory / agent_id` | Main agent directory |
| 7 | `config.agent_directory / f"{agent_id}.md"` | Main directory with `.md` suffix |

This enables relative agent discovery: a parent agent stored at `agents/orchestrator.md` can reference `child_agent` and the host will find `agents/child_agent.md` via `base_dir = source_path.parent`.

### 4.3 Root Agent Convenience Methods

**`get_root_agent() -> Agent`** — loads `config.root_agent_id`

**`run_root(initial_instruction, *, conversation_messages, prompt_fragments) -> AgentResult`** — runs the root agent with the given instruction as the first parameter.

**`run_agent(agent_id, initial_instruction, *, conversation_messages, prompt_fragments) -> AgentResult`** — loads the agent and calls `agent.run(host=self, parameters={"instruction": initial_instruction}, ...)`.

**`run_console()`** — prompts the user for an instruction, runs the root agent, and prints the result.

### 4.4 Runtime tracing around `run_agent` and subagents

When `runtime_tracer` is not a **`NullRuntimeTracer`**:

- **`_agent_with_runtime_tracing`** clones the resolved agent’s **`SequentialHook`** instances (so the cached registry agent is not mutated), appends **`RuntimeTraceBehavior`**, and calls **`attach()`** so `runtime.*` events are published from the decision loop (tools, subagents, skills, model end / decision, callbacks where wired).
- **`run_agent`** wraps execution in **`active_tracer_scope(runtime_tracer, trace_context_overlay)`** (`tracing_bridge.py`). That scope lets **`ConsoleUserCommunication`**, **`WebUserCommunication`**, and **`TraceLoggingBehavior`** publish **`user.*`** and mirrored **`system.log`** lines into the same tracer without passing tracers through every constructor.
- **`trace_context_overlay`** (optional **`TraceContext`**) is merged into each published event’s context via **`publish_trace_event`** and subscriber fan-out. The evaluator’s **`SessionRunner`** sets **`host.trace_context_overlay = TraceContext(session_id=...)`** around **`run_agent`** so browser traces can be keyed by session.
- **`publish_trace_event(...)`** builds a **`TraceEvent`** and calls **`runtime_tracer.publish`**, no-op when the tracer is null. Used from **`Agent`** callback paths and anywhere else outside behaviors that need a single emission point.
- **`call_subagent`** also routes through **`_agent_with_runtime_tracing`** so nested runs emit **`runtime.*`** for the callee.

For JSONL or debugger sinks, assign **`host.runtime_tracer = CompositeRuntimeTracer([...])`** before **`run_agent`** (see **`tracing-evaluation.md`** and the main CLI **`--runtime-trace-jsonl`** flag).

---

## 5. Tool Execution

### 5.1 Tool Registration and Loading

**`register_tool(tool: Tool) -> None`** — directly registers a pre-built tool instance into `tool_registry`.

**`get_tool(tool_name: str) -> Tool`** — lazy-loads from `config.tools_directory` if not in registry:
```python
tool = Tool.from_name(tool_name, self.config.tools_directory)
self.tool_registry[tool_name] = tool
return tool
```

**`Tool.from_name(name, tools_directory)`** (in `tool.py`):
1. Loads `{name}.md` → YAML frontmatter → `ToolDefinition`
2. Loads sibling `{name}.py` → imports module → calls `module.build_tool(definition) -> Tool`

The `build_tool()` factory function is the required export from a tool implementation module.

### 5.2 Tool Execution

**`execute_tool(tool_name: str, parameters: dict) -> str`**

```python
tool = self.get_tool(tool_name)
return tool.invoke(parameters, host=self)
```

Tool implementations receive the `AgentHost` as the `host` argument, enabling tools to access `resolve_world_path()`, call other agents, or read configuration.

### 5.3 World Path Sandboxing

**`resolve_world_path(raw_path: str) -> Path`**

Sandboxes all file access inside `config.world_directory`. Security rules:
1. Rejects absolute paths (raises `ValueError`)
2. Strips any leading `world/` or `world\` prefix (common model hallucination)
3. Resolves the cleaned path relative to `world_directory`
4. Validates the resolved path does not escape `world_directory` via `..` traversal (raises `ValueError` if outside)

Tools that access the filesystem should always call `host.resolve_world_path(path)` before any file I/O.

---

## 6. Multi-Agent Orchestration

### 6.1 Synchronous Subagent Calls

**`call_subagent(*, caller: Agent, callee_id: str, parameters: dict) -> AgentResult`**

```python
callee = self.get_agent(callee_id, base_dir=caller.source_path.parent if caller.source_path else None)
return callee.run(
    host=self,
    parameters=parameters,
    caller_id=caller.agent_id,
    rendered_prompt_override=parameters.get("rendered_prompt"),
    conversation_messages=parameters.get("conversation_messages"),
    prompt_fragments=parameters.get("prompt_fragments"),
)
```

The `base_dir` is the calling agent's directory, enabling relative agent discovery (agents can reference siblings by name).

### 6.2 Asynchronous Subagent Calls

**`call_subagent_async(*, caller: Agent, callee_id: str, parameters: dict) -> Future[AgentResult]`**

Submits the `call_subagent` work to the shared `ThreadPoolExecutor` (max 8 workers). Returns a `Future[AgentResult]` that the caller can await.

```python
return self._executor.submit(
    self.call_subagent, caller=caller, callee_id=callee_id, parameters=parameters
)
```

Used by agents that want to invoke multiple subagents in parallel. Callers aggregate results via `future.result()`.

### 6.3 Call Context Tracking

**`open_context(*, caller_id: str, callee_id: str, kind: str) -> CallContext`**

Creates and stores a `CallContext` for each call edge:

```python
ctx = CallContext(
    context_id=str(uuid4()),
    caller_id=caller_id,
    callee_id=callee_id,
    kind=kind,          # e.g., "callback:information_request", "subagent:call"
    status="open",
    correlation_id=str(uuid4()),
)
self.contexts[ctx.context_id] = ctx
return ctx
```

Contexts remain in `self.contexts` with `status="resolved"` after completion. The full context dict provides a trace of all active and completed call edges.

---

## 7. Callback Resolution

### 7.1 The `resolve_callback()` Chain

**`resolve_callback(*, caller_id: str, callee: Agent, prompt: str) -> str`**

Called when an agent emits a `callback` decision and has a `caller_id`. Implements a three-level resolution chain:

**Level 1 — Caller's behavior (`respond_to_callback`)**

```python
caller_agent = self.get_agent(caller_id)
response = caller_agent.respond_to_callback(host=self, callee_id=callee.agent_id, prompt=prompt)
if response is not None:
    return response
```

`respond_to_callback()` on `Agent` delegates to each attached `AgentBehavior.respond_to_callback()`. The first non-None response wins. This allows behaviors to intercept callbacks without running the full agent loop.

**Level 2 — Run caller agent**

```python
result = self.run_agent(caller_id, prompt)
return result.message
```

The caller agent is run with the callback prompt as its instruction. Its response becomes the callback resolution. This enables parent agents to handle subagent questions by running themselves.

**Level 3 — Console fallback**

If neither level 1 nor level 2 is available, falls back to `request_user_input(prompt)`.

### 7.2 Direct User Input

**`request_user_input(prompt: str) -> str`**

Delegates to **`user_comm.read_user_input(...)`** via the host’s async bridge (same path as the rest of **`UserCommunication`**). If **`user_comm`** is **`None`**, raises **`RuntimeError`** — use **`AgentHost.create(..., user_comm=...)`** or **`from_env_console`** (which wires **`ConsoleUserCommunication`**).

Legacy **`input_reader`** / **`output_writer`** arguments on **`from_env`** are deprecated and ignored.

---

## 8. Model Driver Integration

### 8.1 Driver Access

**`get_model_driver(agent: Agent) -> ModelDriver`**

Returns the driver suitable for use in the sync agent loop. If the configured driver is an `AsyncModelDriver` (detected via `asyncio.iscoroutinefunction(driver.decide)`), it is automatically wrapped with `AsyncToSyncAdapter` before returning. This means the existing sync agent loop works unchanged with async drivers (e.g., `DialChatCompletionsDriver`).

**`get_model_driver_raw() -> Any`**

Returns the raw driver without adapter wrapping. Used by `complete_async()` and `run_tool_loop()` which want to call the async driver directly.

### 8.2 Host-Level Model Hooks

The host maintains two `SequentialHook` instances for cross-cutting model interception:

**`run_pre_model_hooks(event: ModelStartEvent) -> None`** — fires `onPreModel` callbacks with the event.

**`run_post_model_hooks(event: ModelEndEvent) -> None`** — fires `onPostModel` callbacks with the event.

These are called by `Agent.decide()` after the agent-level model hooks, providing a host-wide interception point for logging, rate limiting, caching, or response modification.

### 8.3 Audit Trace Wiring

**`enable_audit_trace(*, output_dir: Path) -> None`**

Creates an `InMemoryAuditTracer(output_dir)` and wires model driver trace callbacks:

```python
self.audit_tracer = InMemoryAuditTracer(output_dir)
self.model_driver.set_trace_callbacks(
    on_request=lambda trace: self.audit_tracer.record_llm_request(
        run_id=trace.run_id, payload=trace.input_payload
    ),
    on_response=lambda trace: self.audit_tracer.record_llm_response(
        run_id=trace.run_id, raw_text=trace.raw_text, parsed_payload=trace.parsed_payload
    ),
)
```

### 8.4 LLM Trace Logging

**`enable_llm_trace_logging(*, target: str, output_dir: Path) -> None`**

Lazy-imports `llm_trace_logging` and calls `attach_to_host(self, target=target, output_dir=output_dir)`. This chains a `LlmTraceLogger` onto the existing model driver trace callbacks (both audit tracer and LLM logger can be active simultaneously).

`target` is one of:
- `"console"` — ANSI-colored output to stdout
- `"file"` — writes per-agent `.log` files and `llm-trace.log` to `output_dir`
- `"both"` — both simultaneously

---

## 9. `AgentHostProtocol` — The Host Contract

`AgentHostProtocol` (in `src/agent_framework/agents/agent_host_protocol.py`) is a `typing.Protocol` that defines exactly the interface `Agent` uses from its host:

```python
class AgentHostProtocol(Protocol):
    def get_model_driver(self, agent: "Agent") -> "ModelDriver": ...
    def get_agent(self, agent_id: str, *, base_dir: Path | None = None) -> "Agent": ...
    def request_user_input(self, prompt: str) -> str: ...
    def call_subagent(self, *, caller: "Agent", callee_id: str, parameters: dict) -> "AgentResult": ...
    def execute_tool(self, tool_name: str, parameters: dict) -> str: ...
    def get_tool(self, tool_name: str): ...
    def resolve_callback(self, *, caller_id: str, callee: "Agent", prompt: str) -> str: ...
    def open_context(self, *, caller_id: str, callee_id: str, kind: str) -> "CallContext": ...
    def run_pre_model_hooks(self, event: "ModelStartEvent") -> None: ...
    def run_post_model_hooks(self, event: "ModelEndEvent") -> None: ...
```

Agents receive a reference typed as `AgentHostProtocol`, not `AgentHost`. This enables:
- **Test doubles:** `FakeAgentHost` in tests without importing the full host.
- **Alternative implementations:** A `RemoteAgentHost` dispatching to a remote orchestrator.
- **Decorator pattern:** Wrapping `AgentHost` to intercept specific methods (as `RecordingAgentHost` does for evaluation).

---

## 10. Headless Model Invocation (v0.2)

For services that need direct model access without running a markdown agent — e.g., multi-phase LLM orchestration, chat completion services, or tool-calling loops — the host exposes `complete()`, `complete_async()`, and `run_tool_loop()`.

### 10.1 `complete()` — Sync Single-Turn Invocation

```python
result: ModelResponse = host.complete(
    messages=[{"role": "user", "content": "What is 2+2?"}],
    model_name="gpt-4o",          # optional, overrides config.default_model
    temperature=0.2,
    response_format=None,         # {"type": "json_object"} or json_schema dict
    response_mode="json_object",  # default — parse response as JSON
    tools=None,
    conversation_id=None,         # if set and conversation_store configured, loads/saves history
)
```

`response_mode` defaults to `"json_object"` — the driver parses the model output as JSON and populates `ModelResponse.payload`. Pass `"text"` when you expect plain-text output and want `raw_text` only.

Fires trace callbacks if the host has `audit_tracer` or LLM trace logging configured. If `conversation_id` is provided and a `conversation_store` is attached, the method: (1) loads existing messages from the store, (2) prepends them to the provided messages, (3) appends the assistant response to the store after completion.

### 10.2 `complete_async()` — Async Single-Turn Invocation

```python
result = await host.complete_async(
    messages=[{"role": "user", "content": "Summarize this."}],
    **kwargs,  # same as complete()
)
```

Uses the raw async driver if available (via `get_model_driver_raw()`), otherwise wraps a sync driver with `SyncToAsyncAdapter`. Preferred for async service contexts.

### 10.3 `run_tool_loop()` — Async Multi-Turn Tool Loop

```python
from agent_framework.host import run_tool_loop

result = await run_tool_loop(
    host,
    messages=[{"role": "user", "content": "Find and summarize."}],
    tools=[...],                  # ToolDefinition list passed to model
    tool_executor=my_executor,    # async callable(name, args) -> str
    terminal_tools=["clarify"],   # tool names that exit the loop immediately
    max_iterations=10,
    conversation_id=None,
    response_mode="json_object",  # default — parse each turn as JSON
)
```

Loops until:
- `finish_reason == "stop"` (no tool calls requested)
- A terminal tool is called (returns immediately with `finish_reason="terminal_tool"` and args as `raw_text`)
- `max_iterations` is reached (raises `RuntimeError("max_iterations")`)

When a non-terminal tool is called: the `tool_executor` coroutine is called with `(tool_name, arguments_dict)`, its string result is appended as a `tool` role message, and the loop continues.

**Terminal tool pattern:** Useful for implementing clarification requests. The orchestrator calls `run_tool_loop()` with `terminal_tools=["request_clarification"]`. When the model calls `request_clarification`, the loop exits immediately with the clarification arguments — the caller interprets them and decides whether to re-enter the loop or escalate.

---

## 11. Conversation Store (v0.2)

`AgentHost` accepts an optional `conversation_store` (any object satisfying `ConversationStore` or `AsyncConversationStore` protocol). When set, it integrates with `complete()` and `complete_async()` for automatic multi-turn history management.

```python
from agent_framework.conversation import InMemoryConversationStore

store = InMemoryConversationStore(ttl_seconds=3600)
host = AgentHost.create(model_driver=driver, conversation_store=store)

# Create a conversation
cid = store.create([{"role": "system", "content": "You are helpful."}])

# Each complete() call with conversation_id loads and saves history automatically
result = host.complete(messages=[{"role": "user", "content": "hi"}], conversation_id=cid)
result = host.complete(messages=[{"role": "user", "content": "follow-up"}], conversation_id=cid)

# History is accumulated in the store
msgs = store.get_messages(cid)  # system + user + assistant + user + assistant
```

See [Conversation Model](./conversation-model.md) for the full protocol reference.

---

## 12. Terminal Tools in the Agent Loop (v0.2)

Markdown agents support a `terminal_tools` list in frontmatter:

```yaml
---
id: orchestrator
tools:
  - request_clarification
terminal_tools:
  - request_clarification
---
```

When the model calls a terminal tool, `handle_tool_call()` returns immediately with `AgentResult(status="completed", message=json.dumps(args))` — the tool implementation is **not** executed. This enables a clean exit point for clarification or escalation workflows without requiring a separate callback mechanism.

`terminal_tools` defaults to `()` — agents without this key behave identically to before.

---

## 13. CLI Entry Point  *(was §10)*

`__main__.py` provides the command-line interface:

```
python -m agent_framework [options]
```

| Flag | Description |
|------|-------------|
| `--console` | Interactive console mode — prompts for instruction, runs root agent, prints result |
| `--env PATH` | Path to `.env` file (default: `.env` in current directory) |
| `--instruction TEXT` | One-shot instruction. Prefix with `@` to load from file: `--instruction @task.txt` |
| `--evaluate PATH` | Run XML-based evaluation (`AgentPromptEvaluator`) |
| `--evaluate-openai PATH` | Run JSON-based evaluation (`OpenAiConversationEvaluator`, requires `--agent`) |
| `--agent ID` | Specific agent ID for `--instruction` or `--evaluate-openai` |
| `--llm-trace MODE` | Enable LLM trace logging: `console`, `file`, or `both` |
| `--llm-trace-dir DIR` | Directory for trace files (default: `logs`) |

The `main(argv, *, host_factory)` function accepts an injectable `host_factory` for testing. The default factory is `AgentHost.from_env_console`.
