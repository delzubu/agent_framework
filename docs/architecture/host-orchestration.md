# Host & Orchestration

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Agent Runtime](./agent-runtime.md) · [Model Abstraction](./model-abstraction.md) · [Extension Points](./extension-points.md) · [Interface Specifications](./interfaces.md)

---

## 1. Overview

`AgentHost` (`src/agent_framework/host.py`) is the central orchestration runtime for the framework. It owns:
- **Agent registry** — loads, caches, and resolves `Agent` instances from Markdown files
- **Tool registry** — loads, caches, and executes `Tool` instances
- **Model driver** — the `ModelDriver` implementation used for all LLM calls
- **Call contexts** — `CallContext` objects tracking active call edges between agents
- **Audit tracer** — optional `InMemoryAuditTracer` for immutable JSONL audit output
- **I/O callables** — pluggable `input_reader` and `output_writer` for console interaction
- **Thread pool** — `ThreadPoolExecutor` for async subagent parallelism
- **Host-level hooks** — `onPreModel` and `onPostModel` for cross-cutting model interception

`AgentHost` implements `AgentHostProtocol` — agents access it only through that protocol interface, enabling test doubles and alternative implementations.

---

## 2. `AgentHost` Structure

```python
@dataclass(slots=True)
class AgentHost:
    config: HostConfig
    model_driver: ModelDriver | None
    input_reader: Callable[[str], str]           # default: input
    output_writer: Callable[[str], None]          # default: print
    agent_registry: dict[str, Agent]
    tool_registry: dict[str, Tool]
    contexts: dict[str, CallContext]
    onPreModel: SequentialHook
    onPostModel: SequentialHook
    audit_tracer: InMemoryAuditTracer | None
    _executor: ThreadPoolExecutor                 # max_workers=8
```

### Factory Methods

**`AgentHost.from_env(env_path, *, model_driver, input_reader, output_writer) -> AgentHost`**

Loads `HostConfig` from the `.env` file, constructs an `OpenAiModelDriver` if `model_driver` is not provided, creates the `InMemoryAuditTracer`, and wires model driver trace callbacks to the tracer.

**`AgentHost.from_env_console(env_path, *, model_driver) -> AgentHost`**

Shorthand: `from_env` wired to real `input` and `print` callables. Used by the CLI.

---

## 3. Configuration (`HostConfig`)

```python
@dataclass(frozen=True, slots=True)
class HostConfig:
    openai_api_key: str
    default_provider: str           # "openai"
    default_model: str              # "gpt-4o-mini"
    agent_directory: Path
    tools_directory: Path
    world_directory: Path
    root_agent_id: str              # "root"
    agent_models: dict[str, str]    # per-agent model overrides
```

**`.env` Keys:**

| Key | Description | Default |
|-----|-------------|---------|
| `OPENAI_API_KEY` | API key for OpenAI (required if using OpenAI) | — |
| `DEFAULT_PROVIDER` | Provider name | `openai` |
| `DEFAULT_MODEL` | Default model ID | `gpt-4o-mini` |
| `AGENT_DIRECTORY` | Directory containing agent `.md` files | `agents` |
| `TOOLS_DIRECTORY` | Directory containing tool `.md` + `.py` pairs | `tools` |
| `WORLD_DIRECTORY` | Sandboxed root for tool file access | `world` |
| `ROOT_AGENT` | Agent ID to run as the root agent | `root` |
| `AGENT_MODELS` | Per-agent model overrides: `agent_id:model,...` | — |

**`HostConfig.model_for(agent_id, fallback=None) -> str`**

Returns `agent_models[agent_id]` if present, else `fallback` if provided, else `default_model`.

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

```python
self.output_writer(prompt)
return self.input_reader("> ")
```

Uses the injected `output_writer` and `input_reader` callables. In the standard console configuration these are `print` and `input`. In tests they can be replaced with mock callables.

---

## 8. Model Driver Integration

### 8.1 Driver Access

**`get_model_driver(agent: Agent) -> ModelDriver`**

Returns `self.model_driver`. Currently all agents share the same driver instance. (Per-agent driver dispatch is a planned extension for multi-provider configurations.)

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

## 10. CLI Entry Point

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
