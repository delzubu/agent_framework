# Full Interface Specifications

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Agent Runtime](./agent-runtime.md) · [Model Abstraction](./model-abstraction.md) · [Host & Orchestration](./host-orchestration.md) · [Extension Points](./extension-points.md) · [Tracing & Evaluation](./tracing-evaluation.md)

This document is the complete reference for every public protocol, class, and method in the framework. Each entry includes field types, method signatures, and semantic notes.

---

## 1. Protocols

### `ModelDriver` (`model.py`)

The provider abstraction protocol. Any object with matching method signatures satisfies it — no inheritance required.

```python
class ModelDriver(Protocol):
    def decide(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_name: str,
        temperature: float,
        context: ModelContext,
    ) -> ModelResponse: ...

    def set_trace_callbacks(
        self,
        *,
        on_request: Callable[[ProviderRequestTrace], None] | None = None,
        on_response: Callable[[ProviderResponseTrace], None] | None = None,
    ) -> None: ...
```

| Method | Semantics |
|--------|-----------|
| `decide(...)` | Synchronous model call. Receives `ModelContext` whose `messages` are already merged (`merge_runtime_system_into_messages`) when coming from `Agent.build_context` or `AgentHost.complete`; returns parsed response. Must handle `context.exact_input_payload` bypass and all three `response_mode` values. |
| `set_trace_callbacks(...)` | Called once at host setup. Implementors store and fire callbacks from `decide()`. Both callbacks are optional; treat `None` as no-op. |

---

### `AgentHostProtocol` (`agents/agent_host_protocol.py`)

The host contract exposed to agents. `Agent.run()` receives this type — not `AgentHost` directly.

```python
class AgentHostProtocol(Protocol):
    def get_model_driver(self, agent: "Agent") -> ModelDriver: ...
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

| Method | Semantics |
|--------|-----------|
| `get_model_driver(agent)` | Returns the model driver for the given agent. Currently returns the host's single shared driver. |
| `get_agent(agent_id, *, base_dir)` | Loads and caches an agent. `base_dir` enables sibling-relative discovery. |
| `request_user_input(prompt)` | Presents prompt to the user and returns the response. |
| `call_subagent(*, caller, callee_id, parameters)` | Synchronously invokes a child agent. `caller.agent_id` becomes the child's `caller_id`. |
| `execute_tool(tool_name, parameters)` | Loads and invokes a tool; returns result as string. |
| `get_tool(tool_name)` | Returns the tool object (used by agents to get `model_definition()`). |
| `resolve_callback(*, caller_id, callee, prompt)` | Attempts to resolve a callback through the caller's behavior chain, then the caller agent, then console. |
| `open_context(*, caller_id, callee_id, kind)` | Creates and stores a `CallContext` for a call edge. |
| `run_pre_model_hooks(event)` | Fires host-level `onPreModel` hooks. |
| `run_post_model_hooks(event)` | Fires host-level `onPostModel` hooks. |

---

### `ResultJudge` (`evaluator.py`)

Protocol for LLM-based evaluation scoring.

```python
class ResultJudge(Protocol):
    def score(
        self,
        *,
        evaluator_prompt: str,
        prompt: str,
        expected: str,
        result: str,
        interactions: list[RecordedInteraction],
    ) -> JudgeResult: ...
```

---

## 2. Core Agent Runtime Classes

### `Agent` (`agents/agent.py`)

```python
@dataclass(slots=True)
class Agent:
    # Identity & Definition
    agent_id: str
    role: str
    description: str
    system_prompt: str
    user_prompt_template: str
    parameters: tuple[AgentParameter, ...]
    provider_name: str
    model_name: str
    temperature: float                      # default: 0.2
    allowed_tools: tuple[str, ...]          # default: ()
    allowed_child_agents: tuple[str, ...]   # default: ()
    allowed_skills: tuple[str, ...]         # default: ()
    can_query_caller: bool                  # default: True
    can_use_host_interaction: bool          # default: True
    behavior_ids: tuple[str, ...]           # default: ()
    behaviors: tuple[AgentBehavior, ...]    # default: (), repr=False
    source_path: Path | None               # default: None

    # Lifecycle Hooks (SequentialHook instances)
    onPreAgent: SequentialHook
    onPostAgent: SequentialHook
    onPreTool: SequentialHook
    onPostTool: SequentialHook
    onPreSubagent: SequentialHook
    onPostSubagent: SequentialHook
    onPreModel: SequentialHook
    onPostModel: SequentialHook
```

**Class Methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `from_markdown` | `(path: Path, *, default_provider: str, default_model: str, model_override: str \| None = None) -> Agent` | Load agent from Markdown file. Validates template contract, loads behaviors. |

**Public Instance Methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `run` | `(*, host, parameters=None, caller_id=None, rendered_prompt_override=None, conversation_messages=None, prompt_fragments=None) -> AgentResult` | Execute the agent's decision loop. Final method — do not override. |
| `get_parameter_spec` | `() -> tuple[AgentParameter, ...]` | Returns declared parameters. |
| `validate_parameters` | `(parameters: dict) -> dict` | Validates values against spec; applies defaults; raises on unknown/missing required. |
| `render_user_prompt` | `(parameters: dict) -> str` | Renders `user_prompt_template` with `{{ name }}` substitution. |
| `try_parse_prompt_input` | `(prompt: str) -> dict \| None` | Recovers parameter values from a pre-composed prompt using XML tag extraction. |
| `respond_to_callback` | `(host, *, callee_id: str, prompt: str) -> str \| None` | Delegates to behaviors; returns first non-None response. |
| `refresh_parameter_state` | `(run: AgentRun) -> None` | Re-extracts parameter values from prompt + fragments. Updates `run.parameter_values`, `run.missing_parameters`, `run.invalid_parameters`. |

**Overridable Step Methods (Template Method):**

| Method | Default | Override Purpose |
|--------|---------|-----------------|
| `should_continue(run)` | `True` | Iteration limits, convergence guards |
| `before_iteration(run)` | History + refresh | Per-iteration setup |
| `after_iteration(run)` | History | Per-iteration teardown |
| `resolve_runtime_decision(run)` | `None` | Inject synthetic decisions |
| `build_context(host, run)` | Full assembly | Custom prompt construction |
| `decide(host, run, context)` | Model call + normalize | Override model interaction |
| `dispatch_decision(host, run, decision, caller_id)` | Route by kind | Cross-cutting decision processing |
| `complete_without_result(run)` | `AgentResult(status="stopped")` | Custom fallback result |

---

### `AgentRun` (`agents/agent_run.py`)

Per-invocation mutable state. Created by `_create_run()` at the start of each `run()` call.

```python
@dataclass(slots=True)
class AgentRun:
    run_id: str                             # UUID4 string
    rendered_prompt: str                    # rendered user prompt (from template or override)
    seed_parameters: dict[str, Any]         # original invocation parameters
    parameter_values: dict[str, Any]        # extracted + coerced parameter values
    placeholder_values: dict[str, str]      # {name} replacement values for system prompt
    missing_parameters: list[str]           # required params not found
    invalid_parameters: dict[str, str]      # param_name → error_message
    prompt_fragments: list[str]             # accumulated XML-tagged augmentation strings
    transcript_entries: list[str]           # human-readable transcript
    conversation_messages: list[dict]       # role/content dicts for multi-turn history
    contexts: list[CallContext]             # tracked call edges from this run
    history: list[str]                      # lifecycle audit trail ("before_iteration:id", etc.)
```

---

### `AgentDecision` (`agents/agent_decision.py`)

Normalized model decision. Immutable.

```python
@dataclass(frozen=True, slots=True)
class AgentDecision:
    kind: str                       # "final_message" | "callback" | "call_subagent" | "call_tool"
    message: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    subagent_id: str | None = None
    tool_name: str | None = None
    callback_intent: str | None = None
    # callback_intent values: "information_request" | "proposal_review" |
    #   "execution_recovery" | "delegation_return" | "policy_or_approval" | "guardrail_trip"
    # All six intent strings normalize to kind="callback"
```

**Class Method:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `from_model_response` | `(response: ModelResponse) -> AgentDecision` | Normalizes raw model output. Handles missing `kind`, legacy kinds, and callback intent kinds. |

---

### `AgentResult` (`agents/agent_result.py`)

Run outcome. Immutable.

```python
@dataclass(frozen=True, slots=True)
class AgentResult:
    status: str                     # "completed" | "stopped" | custom
    message: str = ""               # agent's final response text
    decision: AgentDecision | None = None   # the decision that produced this result
    prompt: str = ""                # run.rendered_prompt at time of completion
    context: CallContext | None = None      # call edge context (for callbacks)
```

---

### `AgentParameter` (`agents/agent_parameter.py`)

Declared invocation parameter. Immutable.

```python
@dataclass(frozen=True, slots=True)
class AgentParameter:
    name: str
    description: str
    required: bool = True
    value_type: str = "string"      # "string" | "integer" | "number" | "boolean" | "object" | "array"
    default: Any = None
    schema_path: Path | None = None  # optional JSON Schema file for validation
```

---

### `AgentInvocation` (`agents/agent_invocation.py`)

Shared context payload carried by all lifecycle events. Immutable.

```python
@dataclass(frozen=True, slots=True)
class AgentInvocation:
    run_id: str
    agent_id: str
    caller_id: str | None
    parameters: dict[str, Any]
    rendered_prompt: str
```

---

### `CallContext` (`agents/call_context.py`)

Tracks a single call edge between two agents. Mutable — `status` is updated to `"resolved"` after completion.

```python
@dataclass(slots=True)
class CallContext:
    context_id: str
    caller_id: str
    callee_id: str
    kind: str                       # e.g., "callback:information_request"
    status: str = "open"            # "open" | "resolved"
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
```

---

### `AgentBehavior` (`agents/agent_behavior.py`)

Extension base class. All methods return `None` by default.

```python
class AgentBehavior:
    def attach(self, agent: Agent) -> None: raise NotImplementedError
    def before_run(self, agent, host, *, run: AgentRun, caller_id) -> AgentHookDecision | None: return None
    def respond_to_callback(self, agent, host, *, callee_id: str, prompt: str) -> str | None: return None
    def after_run(self, agent, host, *, run: AgentRun, caller_id, result: AgentResult) -> AgentEndHookDecision | AgentResult | None: return None
```

---

### `SequentialHook` (`agents/sequential_hook.py`)

Ordered callback collection.

```python
class SequentialHook:
    def __iadd__(self, callback: Callable) -> "SequentialHook"  # subscribe
    def __isub__(self, callback: Callable) -> "SequentialHook"  # unsubscribe (by identity)
    def __iter__(self) -> Iterator[Callable]
```

---

## 3. Model Layer

### `ModelContext` (`model.py`)

Complete prompt payload for one model call. Immutable.

```python
@dataclass(frozen=True, slots=True)
class ModelContext:
    system_prompt: str
    user_prompt: str
    messages: tuple[dict[str, Any], ...]
    response_mode: str                      # "decision" | "text" | "json_object"
    exact_input_payload: Any | None         # bypass prompt assembly; send directly
    tools: tuple[ToolDefinition, ...]
    subagents: tuple[CapabilityDefinition, ...]
    skills: tuple[CapabilityDefinition, ...]
    run_id: str | None
```

---

### `ModelResponse` (`model.py`)

Output from a model driver call. Immutable.

```python
@dataclass(frozen=True, slots=True)
class ModelResponse:
    payload: dict[str, object]  # parsed JSON (empty dict for text mode)
    raw_text: str               # verbatim model output
```

---

### `CapabilityDefinition` (`model.py`)

Describes a tool, subagent, or skill to the model. Immutable.

```python
@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    capability_id: str
    description: str
    parameters: tuple[CapabilityParameter, ...]
    priority: int = 0

    def to_model_payload(self) -> dict: ...
```

---

### `CapabilityParameter` (`model.py`)

Parameter within a `CapabilityDefinition`. Immutable.

```python
@dataclass(frozen=True, slots=True)
class CapabilityParameter:
    name: str
    description: str
    required: bool = True
    value_type: str = "string"

    def to_model_payload(self) -> dict: ...
```

---

### `ProviderRequestTrace` / `ProviderResponseTrace` (`model.py`)

Provider-level trace records. Immutable. Passed to trace callbacks set via `ModelDriver.set_trace_callbacks()`.

```python
@dataclass(frozen=True, slots=True)
class ProviderRequestTrace:
    agent_id: str | None
    provider_name: str
    model_name: str
    input_payload: Any      # exact payload sent to provider API
    temperature: float
    run_id: str | None

@dataclass(frozen=True, slots=True)
class ProviderResponseTrace:
    agent_id: str | None
    provider_name: str
    model_name: str
    raw_text: str           # verbatim model output
    parsed_payload: Any     # parsed JSON or None
    run_id: str | None
```

---

### `OpenAiModelDriver` (`model.py`)

Reference `ModelDriver` implementation using the OpenAI SDK.

```python
@dataclass(slots=True)
class OpenAiModelDriver:
    api_key: str
    on_request_trace: Callable[[ProviderRequestTrace], None] | None = None
    on_response_trace: Callable[[ProviderResponseTrace], None] | None = None

    def decide(self, *, agent_id, provider_name, model_name, temperature, context) -> ModelResponse: ...
    def set_trace_callbacks(self, *, on_request=None, on_response=None) -> None: ...
```

---

### Module-Level Functions (`model.py`)

| Function | Signature | Description |
|----------|-----------|-------------|
| `assemble_system_prompt` | `(context: ModelContext) -> str` | Combines agent `context.system_prompt` with `ModelDriverBase._runtime_prompt` (shared `system.md` + mode template). |
| `merge_runtime_system_into_messages` | `(context: ModelContext) -> ModelContext` | Merges runtime templates into the first `system` message; used by `Agent.build_context` and `AgentHost.complete` so all drivers receive the same `ModelContext.messages`. |
| `ModelDriverBase` | class | Shared capability metadata and runtime prompt assembly; `OpenAiModelDriver` and `DialChatCompletionsDriver` inherit it. |
| `runtime_prompt_source_paths` | `(response_mode: str) -> list[Path]` | Returns template file paths for a given mode. Used by audit tracer. |
| `build_skills_catalog` | `(skills: tuple[CapabilityDefinition, ...], max_tokens: int = 2000) -> str` | Builds a formatted skills catalog with priority-based truncation for conversation injection. Returns empty string if no skills. |

---

## 4. Tool Layer

### `Tool` (`tool.py`)

Base class for tool implementations.

```python
@dataclass(slots=True)
class Tool:
    definition: ToolDefinition
    source_path: Path | None

    @property
    def name(self) -> str: ...          # returns definition.tool_id
    @property
    def description(self) -> str: ...

    def model_definition(self) -> ToolDefinition: ...

    def invoke(self, arguments: dict, host) -> str:
        raise NotImplementedError       # subclasses must override

    @classmethod
    def from_name(cls, name: str, tools_directory: Path) -> "Tool": ...
        # Loads {name}.md for definition + {name}.py for implementation
        # Python module must export: build_tool(definition: ToolDefinition) -> Tool
```

---

### `ToolDefinition` (`tool.py`)

Tool capability contract. Immutable.

```python
@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tool_id: str
    description: str
    parameters: tuple[ToolParameter, ...]
    source_path: Path | None
    documentation: str      # markdown body text

    def to_model_payload(self) -> dict: ...
    # Returns OpenAI function call schema:
    # {"name": tool_id, "description": ..., "parameters": {"type": "object", "properties": {...}, "required": [...]}}
```

---

### `ToolParameter` (`tool.py`)

```python
@dataclass(frozen=True, slots=True)
class ToolParameter:
    name: str
    description: str
    required: bool = True
    value_type: str = "string"
    default: Any = None
```

---

## 5. Host & Configuration

### `AgentHost` (`host.py`)

```python
@dataclass(slots=True)
class AgentHost:
    config: HostConfig
    model_driver: ModelDriver | AsyncModelDriver | None
    tool_registry: ToolRegistry
    agent_registry: AgentRegistry
    command_registry: CommandRegistry
    user_comm: UserCommunication | None
    mcp_manager: McpManager | None
    contexts: dict[str, CallContext]
    onPreModel: SequentialHook
    onPostModel: SequentialHook
    audit_tracer: InMemoryAuditTracer | None
    runtime_tracer: RuntimeTracer              # default NullRuntimeTracer
    trace_context_overlay: TraceContext | None
    skill_registry: SkillRegistry | None
    conversation_store: ConversationStore | AsyncConversationStore | None
    _executor: ThreadPoolExecutor
```

**Factory Methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `create` | `(*, model_driver, config=None, user_comm=None, ...) -> AgentHost` | Programmatic construction; optional registries and MCP. |
| `from_env` | `(env_path, *, model_driver=None, user_comm=None, input_reader=None, output_writer=None) -> AgentHost` | Load config and driver; **`input_reader`/`output_writer` ignored** (deprecated). |
| `from_env_console` | `(env_path, *, model_driver=None) -> AgentHost` | `from_env` + **`ConsoleUserCommunication`**, then **`start()`** synchronously. |

**Tracing-related instance methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `publish_trace_event` | `(*, kind, title, summary="", payload=None, span_id=None, parent_span_id=None, context=None, channel="runtime", level="info") -> None` | No-op if **`runtime_tracer`** is **`NullRuntimeTracer`**; merges **`trace_context_overlay`**. |
| `run_agent` | `(agent_id, initial_instruction, *, conversation_messages=None, prompt_fragments=None) -> AgentResult` | Clones hooks, attaches **`RuntimeTraceBehavior`** when tracer active; **`active_tracer_scope`** for **`run`**. |
| `call_subagent` | `(*, caller, callee_id, parameters) -> AgentResult` | Same runtime-tracing wrapper for the callee agent. |

**Other instance methods** (unchanged in spirit from earlier docs): **`get_agent`**, **`run_root`**, **`run_console`**, **`execute_tool`**, **`request_user_input`** (via **`user_comm`**), **`resolve_callback`**, **`open_context`**, **`resolve_world_path`**, **`run_pre_model_hooks`**, **`run_post_model_hooks`**, **`enable_audit_trace`**, **`enable_llm_trace_logging`**, **`complete`**, **`complete_async`**, **`run_tool_loop`**, etc. See [Host & Orchestration](./host-orchestration.md).

---

### `HostConfig` (`config.py`)

```python
@dataclass(frozen=True, slots=True)
class HostConfig:
    openai_api_key: str
    default_provider: str
    default_model: str
    agent_directory: Path
    tools_directory: Path
    world_directory: Path
    root_agent_id: str
    agent_models: dict[str, tuple[str, ...]]    # agent_id → fallback model list

    def model_for(self, agent_id: str, fallback: tuple[str, ...] | None = None) -> tuple[str, ...]: ...
    # Returns agent_models[agent_id] or fallback or default_model
```

**Module-Level Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `load_host_config` | `(env_path: str \| Path = ".env") -> HostConfig` | Parses `.env` file; resolves paths relative to env file's parent. |

---

## 6. Events

All events are `@dataclass(frozen=True, slots=True)`. All carry `invocation: AgentInvocation` as their first field.

| Class | File | Additional Fields |
|-------|------|------------------|
| `AgentStartEvent` | `agents/agent_start_event.py` | — |
| `AgentEndEvent` | `agents/agent_end_event.py` | `result: AgentResult` |
| `ModelStartEvent` | `agents/model_start_event.py` | `context: ModelContext` |
| `ModelEndEvent` | `agents/model_end_event.py` | `context: ModelContext`, `response: ModelResponse` |
| `ToolStartEvent` | `agents/tool_start_event.py` | `tool_call_id: str`, `tool_name: str`, `tool_input: dict[str, Any]`, `decision: AgentDecision` |
| `ToolEndEvent` | `agents/tool_end_event.py` | `tool_call_id: str`, `tool_name: str`, `tool_input: dict[str, Any]`, `result: str` |
| `SubagentStartEvent` | `agents/subagent_start_event.py` | `subagent_call_id: str`, `subagent_id: str`, `subagent_input: dict[str, Any]`, `decision: AgentDecision` |
| `SubagentEndEvent` | `agents/subagent_end_event.py` | `subagent_call_id: str`, `subagent_id: str`, `subagent_input: dict[str, Any]`, `result: AgentResult` |

---

## 7. Hook Decisions

All hook decisions are `@dataclass(frozen=True, slots=True)`.

### `AgentHookDecision` (`agents/agent_hook_decision.py`)

Returned by `onPreAgent` callbacks and `AgentBehavior.before_run()`.

```python
@dataclass(frozen=True, slots=True)
class AgentHookDecision:
    continue_run: bool = True
    system_message: str | None = None       # appended as <system_message> fragment
    final_result: AgentResult | None = None  # if set: short-circuit with this result
```

---

### `AgentEndHookDecision` (`agents/agent_end_hook_decision.py`)

Returned by `onPostAgent` callbacks and `AgentBehavior.after_run()`.

```python
@dataclass(frozen=True, slots=True)
class AgentEndHookDecision:
    continue_run: bool = False
    prompt_fragments: tuple[str, ...] = ()          # upsert (replace-by-tag-name)
    append_prompt_fragments: tuple[str, ...] = ()   # always append
    final_result: AgentResult | None = None
```

---

### `ToolHookDecision` (`agents/tool_hook_decision.py`)

Returned by `onPreTool` callbacks.

```python
@dataclass(frozen=True, slots=True)
class ToolHookDecision:
    continue_run: bool = True
    updated_tool_input: dict | None = None          # replace tool arguments
    system_message: str | None = None               # appended as <system_message> fragment
    final_result: AgentResult | None = None
```

---

### `SubagentHookDecision` (`agents/subagent_hook_decision.py`)

Returned by `onPreSubagent` callbacks.

```python
@dataclass(frozen=True, slots=True)
class SubagentHookDecision:
    continue_run: bool = True
    updated_subagent_id: str | None = None          # redirect to different agent
    updated_subagent_input: dict | None = None      # replace subagent parameters
    system_message: str | None = None
    final_result: AgentResult | None = None
```

---

## 8. Audit Tracing

### `InMemoryAuditTracer` (`audit_trace.py`)

```python
@dataclass(slots=True)
class InMemoryAuditTracer:
    output_dir: Path
    active_records: dict[str, AgentCallAuditRecord]
    output_path: Path           # trace-YYMMDD_HHMMSS.jsonl
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `start_agent_call` | `(run_id, caller_id, agent_name, system_prompt, system_prompt_sources, user_prompt, user_prompt_sources)` | Create initial record in `active_records`. |
| `record_llm_request` | `(*, run_id, payload)` | `replace()` record with LLM request payload. |
| `record_llm_response` | `(*, run_id, raw_text, parsed_payload)` | `replace()` record with response. |
| `record_decision` | `(*, run_id, decision: AgentDecision)` | `replace()` record with serialized decision. |
| `record_callback` | `(*, run_id, intent, prompt, target, response)` | Append `CallbackAuditRecord` to record. |
| `record_event` | `(*, run_id, event: dict)` | Append event dict to record. |
| `finish_agent_call` | `(*, run_id)` | Pop record, append JSONL line to `output_path`. |

---

### `AgentCallAuditRecord` (`audit_trace.py`)

```python
@dataclass(frozen=True, slots=True)
class AgentCallAuditRecord:
    timestamp: str
    run_id: str
    caller_id: str | None
    agent_name: str
    system_prompt: str
    system_prompt_sources: tuple[str, ...]
    user_prompt: str
    user_prompt_sources: tuple[str, ...]
    llm_message_sent: Any
    llm_message_received: Any
    model_response: Any
    agent_decision: dict | None
    callbacks: tuple[CallbackAuditRecord, ...]
    events: tuple[dict, ...]

    def to_jsonable(self) -> dict: ...
```

---

### `CallbackAuditRecord` (`audit_trace.py`)

```python
@dataclass(frozen=True, slots=True)
class CallbackAuditRecord:
    timestamp: str
    intent: str
    prompt: str
    target: str
    response: str | None = None
```

---

## 9. Evaluation

### `AgentPromptEvaluator` (`evaluator.py`)

```python
class AgentPromptEvaluator:
    def evaluate_file(self, path: Path, *, agent_id: str | None = None) -> EvaluationSummary: ...
    @staticmethod
    def parse_input_file(path: Path) -> EvaluationInput: ...
```

---

### `OpenAiConversationEvaluator` (`evaluator.py`)

```python
class OpenAiConversationEvaluator:
    def evaluate_file(self, path: Path) -> EvaluationSummary: ...
    @staticmethod
    def parse_input_file(path: Path) -> OpenAiEvaluationInput: ...
```

---

### `RecordingAgentHost` (`evaluator.py`)

```python
class RecordingAgentHost(AgentHost):
    interactions: list[RecordedInteraction]
    auto_input_response: str | None

    @classmethod
    def from_host(cls, host: AgentHost) -> "RecordingAgentHost": ...
```

Used by **`AgentPromptEvaluator`** / **`OpenAiConversationEvaluator`** to capture tool/subagent/callback/user-input edges. It is **orthogonal** to the unified **`TraceEvent`** pipeline: regression evaluation does not require replacing **`RecordingAgentHost`** with runtime tracing.

---

### Evaluation Data Types

```python
@dataclass(frozen=True)
class EvaluationScene:
    prompt: str
    expected: str

@dataclass(frozen=True)
class EvaluationInput:
    scenes: tuple[EvaluationScene, ...]
    evaluator_prompt: str
    schema: dict | None

@dataclass(frozen=True)
class RecordedInteraction:
    kind: str               # "subagent_call" | "tool_call" | "callback" | "user_input"
    caller_id: str | None
    callee_id: str | None
    payload: dict
    def to_dict(self) -> dict: ...

@dataclass(frozen=True)
class JudgeResult:
    score: float            # 1.0–10.0
    output_text: str
    payload: dict

@dataclass(frozen=True)
class PromptScore:
    prompt: str
    expected: str
    agent_output: str
    llm_evaluator_output: str
    llm_evaluator_payload: dict
    schema_evaluator_output: str
    interactions: tuple[RecordedInteraction, ...]
    llm_score: float | None
    schema_score: float | None
    overall_score: float

@dataclass(frozen=True)
class EvaluationSummary:
    prompt_scores: tuple[PromptScore, ...]
    overall_score: float
    def to_json(self) -> str: ...
    def to_markdown_table(self) -> str: ...

@dataclass(frozen=True)
class FormatEvaluationResult:
    score: float            # 0 or 10
    output_text: str
    normalized_output: str
```

---

## 10. Helper Utilities (`agents/helpers.py`)

Module-level functions used throughout the framework:

| Function | Signature | Description |
|----------|-----------|-------------|
| `split_markdown_sections` | `(raw_text: str) -> tuple[str, str, str]` | Split on `^---\s*$` into (frontmatter, system_prompt, user_prompt_template). |
| `apply_runtime_placeholders` | `(template: str, values: dict) -> str` | Replace `{name}` single-brace patterns. |
| `coerce_parameter_value` | `(spec: AgentParameter, raw_value: str) -> Any` | Type-coerce string to declared type. |
| `extract_prompt_value` | `(spec: AgentParameter, prompt: str) -> Any \| None` | Extract value from `<name>...</name>` XML tag in prompt text. |
| `stringify_parameter_value` | `(value: Any) -> str` | JSON-serialize dicts/lists; `str()` otherwise. |
| `resolve_schema_path` | `(source_path: Path, raw_path: str) -> Path \| None` | Resolve schema path relative to `source_path.parent.parent`. |
| `load_runtime_metadata` | `(source_path: Path) -> dict` | Load sidecar `.json` file. Returns `{}` if not found. |
| `decision_to_dict` | `(decision: AgentDecision) -> dict` | Serialize `AgentDecision` to plain dict. |
| `parse_behavior_ids` | `(runtime_metadata: dict) -> tuple[str, ...]` | Extract behavior IDs from sidecar JSON. |
| `parse_allowed_tool_names` | `(raw_tools: Any) -> tuple[str, ...]` | Normalize list/dict tool name formats. |
| `agent_to_capability_definition` | `(agent: Agent) -> CapabilityDefinition` | Convert agent to model-facing capability metadata. |

**Constants:**

| Name | Value | Usage |
|------|-------|-------|
| `PLACEHOLDER_PATTERN` | `re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")` | Matches `{{ name }}` in templates |
| `SECTION_PATTERN` | `re.compile(r"^---\s*$", re.MULTILINE)` | Splits markdown into sections |
