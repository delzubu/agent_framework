# Model Driver Abstraction Layer

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Agent Runtime](./agent-runtime.md) · [Host & Orchestration](./host-orchestration.md) · [Drivers](./drivers.md) · [Extension Points](./extension-points.md) · [Interface Specifications](./interfaces.md)

---

## 1. Design Goal

The framework must run the same agent definitions against any LLM provider without modifying agents. The `ModelDriver` protocol (in `src/agent_framework/model.py`) defines the exact interface that separates the agent runtime from the underlying LLM API. The `OpenAiModelDriver` is the reference implementation. Anthropic Claude API and custom provider drivers are first-class extension targets.

The abstraction boundary is intentionally narrow: the driver receives a fully-assembled `ModelContext` (system prompt, user prompt, history, mode, tools, subagents) and returns a `ModelResponse` (parsed payload + raw text). Provider-specific details — authentication, SDK calls, retry logic, streaming — are entirely contained within the driver implementation.

---

## 2. The `ModelDriver` Protocol

```python
# src/agent_framework/model.py

class ModelDriver(Protocol):
    def decide(
        self,
        *,
        agent_id: str,
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

### `decide()` Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent_id` | `str` | Calling agent's identifier. Used for trace correlation — appears in `ProviderRequestTrace` and `ProviderResponseTrace`. |
| `provider_name` | `str` | Provider name string (e.g., `"openai"`, `"anthropic"`). Passed through to trace records; not used for routing — the driver already knows its own provider. |
| `model_name` | `str` | Specific model to invoke (e.g., `"gpt-4o-mini"`, `"claude-opus-4-5"`). Comes from `HostConfig.model_for(agent_id)` with fallback to `config.default_model`. |
| `temperature` | `float` | Sampling temperature, 0.0–2.0. Comes from the agent definition (default `0.2`). |
| `context` | `ModelContext` | Complete prompt payload — system prompt, user prompt, message history, response mode, tools, subagents, skills, run ID. See Section 4. |

### `decide()` Return Value

Returns `ModelResponse(payload: dict, raw_text: str)`. Both fields are **always required**:
- For `"decision"` and `"json_object"` modes: `payload` is the parsed JSON dict; `raw_text` is the original string.
- For `"text"` mode: `payload` is `{}` (empty dict); `raw_text` is the model's plain text output.

### `set_trace_callbacks()`

Called once by `AgentHost.enable_audit_trace()` and/or `AgentHost.enable_llm_trace_logging()` to wire provider-level observability. Implementors store the callbacks and invoke them from `decide()`:
- `on_request(ProviderRequestTrace)` — called immediately before the API call
- `on_response(ProviderResponseTrace)` — called immediately after parsing the response

Both callbacks are optional (`None` means no-op). The driver must not raise if they are not set.

---

## 2b. `AsyncModelDriver` — The Async Protocol

Added in v0.2 to support async providers (DIAL, custom async HTTP clients):

```python
class AsyncModelDriver(Protocol):
    async def decide(
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

`AsyncModelDriver` is structurally identical to `ModelDriver` except `decide()` is an `async def` coroutine. The sync agent loop cannot call it directly — see the adapter classes below.

### Sync/Async Adapters

Two adapter classes bridge the sync and async worlds:

**`SyncToAsyncAdapter`** — wraps a sync `ModelDriver` for async callers:
```python
adapter = SyncToAsyncAdapter(my_sync_driver)
result = await adapter.decide(...)  # runs decide() in asyncio.to_thread
```

**`AsyncToSyncAdapter`** — wraps an `AsyncModelDriver` for sync callers:
```python
adapter = AsyncToSyncAdapter(my_async_driver)
result = adapter.decide(...)  # runs the coroutine to completion
```

`AsyncToSyncAdapter` handles both cases: when called outside an event loop it uses `asyncio.run()`, and when called from within a running loop (e.g., from a `ThreadPoolExecutor` worker) it creates a new event loop in the current thread.

`AgentHost.get_model_driver(agent)` automatically wraps async drivers with `AsyncToSyncAdapter`, so the existing sync agent loop works unchanged when an `AsyncModelDriver` is configured.

---

## 2c. `DriverCapabilities` — Runtime Feature Declaration

Drivers declare what they support via a `ClassVar[DriverCapabilities]`:

```python
@dataclass(frozen=True, slots=True)
class DriverCapabilities:
    is_async: bool = False
    supports_multimodal: bool = False
    supports_response_format: bool = False
    supports_tools: bool = False
    supports_streaming: bool = False
```

| Flag | Meaning |
|------|---------|
| `is_async` | `decide()` is a coroutine (implements `AsyncModelDriver`) |
| `supports_multimodal` | Accepts `image_url` content parts in messages |
| `supports_response_format` | Accepts `context.response_format` hint |
| `supports_tools` | Accepts OpenAI-format tool definitions in the request |
| `supports_streaming` | Supports streaming responses (not yet used by the framework) |

**Querying capabilities:**

```python
from agent_framework.model import get_driver_capabilities

caps = get_driver_capabilities(driver)
if caps.supports_response_format:
    context = ModelContext(..., response_format={"type": "json_object"})
```

`get_driver_capabilities(driver)` returns `driver.capabilities` if the attribute exists (class or instance), otherwise returns `DriverCapabilities()` (all False) for backward compatibility with legacy drivers.

**Declaring capabilities on a custom driver:**

```python
from typing import ClassVar
from agent_framework.model import DriverCapabilities

@dataclass(slots=True)
class MyDriver:
    capabilities: ClassVar[DriverCapabilities] = DriverCapabilities(
        is_async=True,
        supports_tools=True,
    )
    ...
```

`ClassVar` is important — it makes `capabilities` a class-level attribute, not a constructor argument.

---

## 3. `ModelContext` — The Complete Prompt Payload

```python
@dataclass(frozen=True, slots=True)
class ModelContext:
    system_prompt: str
    user_prompt: str
    messages: tuple[dict[str, Any], ...]
    response_mode: str
    exact_input_payload: Any | None
    tools: tuple[ToolDefinition, ...]
    subagents: tuple[CapabilityDefinition, ...]
    skills: tuple[CapabilityDefinition, ...]
    run_id: str | None
    response_format: dict[str, Any] | None = None  # added in v0.2
```

| Field | Description |
|-------|-------------|
| `system_prompt` | Fully assembled system prompt. Constructed by `assemble_system_prompt(context)` = agent's own system prompt + capability metadata injected into `system.md` placeholders + response mode template. See Section 6. |
| `user_prompt` | Rendered user prompt. Agent's `user_prompt_template` with `{{ param }}` placeholders replaced + `<augmentations>` block containing accumulated prompt fragments. |
| `messages` | Conversation history as role/content dicts (`[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`). Passed directly to the provider's messages array for multi-turn context. |
| `response_mode` | One of `"decision"`, `"text"`, `"json_object"`. Controls which mode template is appended to the system prompt and how the model output is parsed. See Section 5. |
| `exact_input_payload` | When set (non-None), the driver bypasses normal prompt assembly and sends this payload directly to the provider API. Used by `OpenAiConversationEvaluator` to send raw provider input payloads. |
| `tools` | Tool capability definitions for injection into the system prompt as JSON. Each `ToolDefinition` exposes `to_model_payload()` → OpenAI function call format. |
| `subagents` | Subagent capability definitions (converted from `Agent` via `agent_to_capability_definition()`). Injected into the system prompt as JSON. |
| `skills` | Skill stub definitions (same structure as subagents, for future use). |
| `run_id` | Correlation ID for the current run. Passed through to trace records. |
| `response_format` | Optional structured output format hint. Dict with `{"type": "json_object"}` or `{"type": "json_schema", "json_schema": {...}}`. Forwarded verbatim to drivers that support it (`DriverCapabilities.supports_response_format = True`). Ignored by drivers that don't. |

---

## 4. `ModelResponse` — The Output Contract

```python
@dataclass(frozen=True, slots=True)
class ModelResponse:
    payload: dict[str, object]
    raw_text: str
    tool_calls: tuple[dict[str, Any], ...] | None = None   # added in v0.2
    finish_reason: str | None = None                        # added in v0.2
    usage: dict[str, int] | None = None                    # added in v0.2
```

| Field | Description |
|-------|-------------|
| `payload` | Parsed JSON dict, or `{}` for text mode. Populated from `raw_text` by the driver. |
| `raw_text` | Verbatim string returned by the model (before JSON parsing). Always populated. |
| `tool_calls` | Tuple of raw tool call dicts from the provider response (OpenAI format: `[{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}]`). Set by async drivers when the model requests tool use. `None` for sync agent loop responses that use the decision envelope pattern. |
| `finish_reason` | Provider finish reason string (e.g., `"stop"`, `"tool_calls"`, `"length"`). Set by async drivers. |
| `usage` | Token usage dict from the provider (e.g., `{"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}`). Set by async drivers when available. |

The `raw_text` field is **always** the verbatim string returned by the model (before any JSON parsing). The `payload` is the parsed dict (or `{}` for text mode). Both are preserved in `AgentCallAuditRecord` via the audit tracer.

`AgentDecision.from_model_response(response)` is called on the returned `ModelResponse` to normalize it into a structured `AgentDecision`. See the [Agent Runtime](./agent-runtime.md#7-decision-normalization) documentation.

**Backward compatibility:** The new fields default to `None` — existing code constructing `ModelResponse(payload=..., raw_text=...)` continues to work without modification.

---

## 5. Response Modes

The `response_mode` field on `ModelContext` controls three distinct behavioral contracts:

### 5.1 `"decision"` Mode (default)

**Purpose:** Action selection in the agent decision loop.

**System prompt template appended:** `system.decision.md`
```
You are currently participating in a runtime decision loop.
Rules:
1. Return exactly one JSON object.
2. Do not answer in prose.
3. Use the action/callback structure required by the current agent system prompt.
4. Use declared tool names, subagent ids, and parameter names exactly as provided.
```

**Expected model output:**
```json
{
  "kind": "final_message | callback | call_subagent | call_tool",
  "intent": "information_request | ...",
  "message": "string",
  "subagent_id": "string",
  "tool_name": "string",
  "parameters": {}
}
```

**Parser behavior:** Output is JSON-parsed into `payload`; `raw_text` is preserved. `AgentDecision.from_model_response()` normalizes the payload into a structured decision.

**Used by:** Most agents performing iterative action selection.

### 5.2 `"text"` Mode

**Purpose:** Plain text output — narrative responses, summaries, or content generation.

**System prompt template appended:** `system.text.md`
```
You are currently producing a plain-text response.
Rules:
1. Return plain text, not the runtime decision envelope.
2. Do not wrap the response in JSON unless the agent system prompt explicitly requires JSON content.
3. Keep the response faithful to the agent system prompt and the available context.
```

**Expected model output:** Any plain text.

**Parser behavior:** `raw_text` is the model output. `payload = {}`. `AgentDecision.from_model_response()` treats the absent `kind` field as `final_message` and returns `AgentDecision(kind="final_message", message=raw_text)`.

**Used by:** Agents producing narrative or document output rather than selecting actions.

### 5.3 `"json_object"` Mode

**Purpose:** Structured JSON output with full callback semantics. The most detailed mode.

**System prompt template appended:** `system.json_object.md`
```
You are currently producing a final JSON object as content.
Rules:
1. Return exactly one JSON object.
2. Do not answer in prose outside the JSON object.
3. Follow the agent system prompt for the object shape and field semantics.
4. If the current task is a runtime action selection task, use the structured action object.
5. If information is missing, do not ask in plain text. Emit the structured callback object.
6. If a declared tool or subagent can make progress, prefer using it over a callback.
7. Use declared tool names, subagent ids, and parameter names exactly as provided.
```

Also defines all six callback intents with structured parameter requirements (see Section 5.4).

**Expected model output:** An arbitrary JSON object (agent-defined shape), OR the structured action/callback envelope if doing action selection.

**Parser behavior:** JSON-parsed into `payload`; `raw_text` preserved.

**Used by:** Agents producing structured data output (e.g., evaluation results, plans, configurations) or agents needing full callback semantics with structured parameters.

### 5.4 Callback Intents (from `system.json_object.md`)

Defined for both `"decision"` and `"json_object"` modes via the `system.md` base template's callback handling section:

| Intent | `kind` | Usage | Required `parameters` |
|--------|--------|-------|----------------------|
| `information_request` | `callback` | Required info missing after exhausting local retrieval | Missing field names, attempted retrieval steps, partial info |
| `proposal_review` | `callback` | Proposed answer needs caller review before continuing | `proposal`, review criteria |
| `execution_recovery` | `callback` | Error or partial failure needs caller decision | Error description, attempted actions, partial results |
| `delegation_return` | `callback` | Delegated work complete/blocked — caller decides next steps | `status` (completed/partial/blocked/not_applicable), work product |
| `policy_or_approval` | `callback` | Sensitive/irreversible action needs caller approval | Proposed action, reason, consequences |
| `guardrail_trip` | `callback` | Policy violation or unsafe request detected | Violated rule, triggering input, safe alternative |

Note: `delegation_return` normalizes to `kind="callback"` like all other intents. The caller's resolution chain (behavior → parent agent → console) interprets it as a signal that delegated work has concluded.

---

## 6. System Prompt Assembly

The complete system prompt sent to the model is assembled by `assemble_system_prompt(context: ModelContext) -> str` in `model.py`:

```
assembled_system_prompt =
    context.system_prompt                   (agent's own instructions, from .md file)
    + "\n\n"
    + _runtime_prompt(context)              (capability metadata + mode template)
```

`_runtime_prompt(context)` is:
```
formatted_system_md                         (system.md with {tools_json} and {subagents_json} filled in)
+ "\n\n"
+ mode_template                             (system.decision.md OR system.text.md OR system.json_object.md)
```

### Capability Metadata Injection

`_capability_metadata(tools, subagents)` produces JSON strings for the system prompt placeholders:
- `{tools_json}` → JSON array of `tool.to_model_payload()` results (OpenAI function call format with `name`, `description`, `parameters` object with JSON Schema properties)
- `{subagents_json}` → JSON array of `CapabilityDefinition.to_model_payload()` results (with `capability_id`, `description`, `parameters` array)

`_capability_metadata()` does **not** return a `skills_section` key. The skills catalog is handled separately by `build_context()` in `agent.py`: when skills are available, `build_skills_catalog(skills, max_tokens=...)` in `model.py` builds the catalog text, and `build_context()` injects it as a `{"role": "user"}` message at index 2 in the conversation — after the system prompt and initial user prompt, but before `run.conversation_messages`. This keeps the skills catalog in the conversation history rather than in the system prompt.

The `system.md` base template injects `{tools_json}` and `{subagents_json}` under `<allowed_tools>` and `<allowed_agents>` XML tags, and provides the Information Retrieval workflow (6-step escalation policy) and callback handling instructions.

### Template File Locations

All four template files are loaded from the `agents/` directory relative to `model.py` at module import time:

```
src/agent_framework/agents/system.md
src/agent_framework/agents/system.decision.md
src/agent_framework/agents/system.text.md
src/agent_framework/agents/system.json_object.md
```

`runtime_prompt_source_paths(response_mode) -> list[Path]` returns the source file paths for a given mode — used by the audit tracer to record which templates contributed to a system prompt.

---

## 7. Capability Definitions

Tools, subagents, and skills are described to the model as typed capability structures:

```python
@dataclass(frozen=True, slots=True)
class CapabilityParameter:
    name: str
    description: str
    required: bool = True
    value_type: str = "string"      # string, integer, number, boolean, object, array

    def to_model_payload(self) -> dict: ...

@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    capability_id: str
    description: str
    parameters: tuple[CapabilityParameter, ...]

    def to_model_payload(self) -> dict: ...
```

**Tools** are described via `ToolDefinition.to_model_payload()` which produces the OpenAI function call schema format (with `properties` and `required` arrays).

**Subagents** are converted from `Agent` instances via `agent_to_capability_definition(agent)` in `helpers.py`, which maps `agent.agent_id` → `capability_id`, `agent.description` → `description`, and `agent.parameters` → `CapabilityParameter` tuples.

**Skills** are directory-discovered markdown-defined instruction sets. `SkillRegistry` discovers skills from configured `skills_directories` and exposes them as `CapabilityDefinition` tuples. Each `CapabilityDefinition` for a skill carries a `priority: int = 0` field read from the SKILL.md frontmatter; `build_skills_catalog()` uses this to drop lowest-priority skills first when the catalog must be truncated to fit within `max_tokens`. The catalog (names + descriptions) is built by `build_skills_catalog(skills, max_tokens=...)` and injected by `build_context()` as a first-turn `{"role": "user"}` conversation message (at index 2, before `run.conversation_messages`) when skills are available — it is not part of the system prompt. Full skill content is loaded on demand by `SkillLoader` only when the model emits an `invoke_skill` decision.

---

## 8. `OpenAiModelDriver` — Reference Implementation

```python
@dataclass(slots=True)
class OpenAiModelDriver:
    api_key: str
    on_request_trace: Callable[[ProviderRequestTrace], None] | None = None
    on_response_trace: Callable[[ProviderResponseTrace], None] | None = None
```

### Implementation Details

1. **Per-call client creation:** A new `openai.OpenAI(api_key=self.api_key)` client is created on each `decide()` call. No shared connection state.

2. **`exact_input_payload` bypass:** If `context.exact_input_payload is not None`, the driver sends it directly to `client.responses.create(**context.exact_input_payload)` without any prompt assembly.

3. **Normal assembly path:** Calls `assemble_system_prompt(context)` for the system prompt, uses `context.user_prompt` as the user turn, and `context.messages` for history.

4. **JSON normalization:** `_normalize_json_text(raw_text)` strips markdown code fences (` ```json ... ``` `) from model output before JSON parsing — a common model output artifact.

5. **Response parsing by mode:**
   - `"decision"` / `"json_object"`: `json.loads(_normalize_json_text(raw_text))` → `payload`
   - `"text"`: `payload = {}`, `raw_text` returned as-is

6. **Trace callback sequence:**
   ```python
   # Before API call:
   if self.on_request_trace:
       self.on_request_trace(ProviderRequestTrace(agent_id, provider_name, model_name, input_payload, temperature, run_id))

   # After API call:
   if self.on_response_trace:
       self.on_response_trace(ProviderResponseTrace(agent_id, provider_name, model_name, raw_text, parsed_payload, run_id))
   ```

---

## 9. Provider Trace Contracts

```python
@dataclass(frozen=True, slots=True)
class ProviderRequestTrace:
    agent_id: str
    provider_name: str
    model_name: str
    input_payload: Any          # the exact payload sent to the provider API
    temperature: float
    run_id: str | None

@dataclass(frozen=True, slots=True)
class ProviderResponseTrace:
    agent_id: str
    provider_name: str
    model_name: str
    raw_text: str               # verbatim model output
    parsed_payload: Any         # parsed JSON or None for text mode
    run_id: str | None
```

These trace records flow to:
- `InMemoryAuditTracer.record_llm_request/response()` — wired by `AgentHost.enable_audit_trace()`
- `LlmTraceLogger.log_provider_request/response()` — wired by `AgentHost.enable_llm_trace_logging()`

Both can be active simultaneously. The callbacks are stored as `on_request_trace` / `on_response_trace` fields on the driver and chained when multiple consumers exist.

---

## 10. Implementing a New Provider Driver

To add support for Anthropic Claude API (or any custom provider), implement a class satisfying the `ModelDriver` protocol. No inheritance required.

### Minimal Implementation Skeleton

```python
from dataclasses import dataclass, field
from typing import Callable
from agent_framework.model import (
    ModelContext, ModelResponse, ModelDriver,
    ProviderRequestTrace, ProviderResponseTrace,
    assemble_system_prompt, _normalize_json_text,
)

@dataclass(slots=True)
class AnthropicClaudeDriver:
    api_key: str
    on_request_trace: Callable | None = field(default=None, repr=False)
    on_response_trace: Callable | None = field(default=None, repr=False)

    def set_trace_callbacks(self, *, on_request=None, on_response=None) -> None:
        self.on_request_trace = on_request
        self.on_response_trace = on_response

    def decide(self, *, agent_id, provider_name, model_name, temperature, context: ModelContext) -> ModelResponse:
        import anthropic

        # 1. Handle exact_input_payload bypass
        if context.exact_input_payload is not None:
            # Send directly to provider
            ...

        # 2. Assemble prompts using framework utilities
        system_prompt = assemble_system_prompt(context)
        user_prompt = context.user_prompt

        # 3. Build message history (role/content format)
        messages = list(context.messages) + [{"role": "user", "content": user_prompt}]

        # 4. Fire request trace
        if self.on_request_trace:
            self.on_request_trace(ProviderRequestTrace(
                agent_id=agent_id, provider_name=provider_name,
                model_name=model_name, input_payload={...},
                temperature=temperature, run_id=context.run_id,
            ))

        # 5. Call provider API
        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=model_name,
            system=system_prompt,
            messages=messages,
            temperature=temperature,
            max_tokens=4096,
        )
        raw_text = response.content[0].text

        # 6. Parse response based on response_mode
        if context.response_mode in ("decision", "json_object"):
            import json
            payload = json.loads(_normalize_json_text(raw_text))
        else:  # "text"
            payload = {}

        # 7. Fire response trace
        if self.on_response_trace:
            self.on_response_trace(ProviderResponseTrace(
                agent_id=agent_id, provider_name=provider_name,
                model_name=model_name, raw_text=raw_text,
                parsed_payload=payload, run_id=context.run_id,
            ))

        return ModelResponse(payload=payload, raw_text=raw_text)
```

### Registering the Driver with AgentHost

```python
from agent_framework.host import AgentHost

driver = AnthropicClaudeDriver(api_key="sk-ant-...")
host = AgentHost.from_env(".env", model_driver=driver)
```

The `from_env()` factory accepts a `model_driver` argument that overrides the default `OpenAiModelDriver` construction. The driver will receive trace callbacks if `host.enable_audit_trace()` or `host.enable_llm_trace_logging()` is called.

### Per-Agent Model Overrides

Agents can use different models/providers via the `AGENT_MODELS` configuration key in `.env`:

```
AGENT_MODELS=search_agent:gpt-4o,summarizer:claude-opus-4-5
```

`HostConfig.model_for(agent_id, fallback=None) -> str` returns the per-agent override if present, or falls back to `fallback` or `default_model`. The `AgentHost` calls this when constructing the model parameters passed to `driver.decide()`.

Note: The current `AGENT_MODELS` config only overrides model names, not providers. A multi-provider `AgentHost` would require extending the config and host to dispatch to different drivers per agent — a planned extension.
