# Driver Ecosystem

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Model Abstraction](./model-abstraction.md) · [Host & Orchestration](./host-orchestration.md) · [Extension Points](./extension-points.md)

---

## 1. Driver Selection Model

The driver used for LLM calls is configured at `AgentHost` construction time. There are three ways to select a driver:

### 1.1 Auto-detection via `from_env()`

`AgentHost.from_env()` reads `DEFAULT_PROVIDER` from `.env` and constructs the appropriate driver automatically:

```
DEFAULT_PROVIDER=openai              → OpenAiModelDriver(api_key=OPENAI_API_KEY)
DEFAULT_PROVIDER=dial + DIAL_BASE_URL → DialChatCompletionsDriver(base_url=..., ...)
```

### 1.2 Explicit injection via `from_env()`

Pass `model_driver=...` to override auto-detection:

```python
from agent_framework.drivers.dial import DialChatCompletionsDriver

driver = DialChatCompletionsDriver(base_url="https://...", deployment="gpt-4o", api_key="...")
host = AgentHost.from_env(".env", model_driver=driver)
```

### 1.3 Programmatic construction via `AgentHost.create()`

No `.env` file required:

```python
from agent_framework import AgentHost, HostConfig
from agent_framework.drivers.dial import DialChatCompletionsDriver

driver = DialChatCompletionsDriver(base_url="https://...", deployment="gpt-4o", api_key="...")
host = AgentHost.create(
    model_driver=driver,
    config=HostConfig(default_model="gpt-4o"),
)
```

---

## 2. `ModelDriver` vs `AsyncModelDriver`

| Aspect | `ModelDriver` | `AsyncModelDriver` |
|--------|--------------|-------------------|
| `decide()` | `def decide(...)` — synchronous | `async def decide(...)` — coroutine |
| Agent loop | Used directly by the sync agent loop | Wrapped in `AsyncToSyncAdapter` by `get_model_driver()` |
| `complete()` | Used directly | Wrapped in `SyncToAsyncAdapter` for `complete_async()` |
| Reference impl | `OpenAiModelDriver` | `DialChatCompletionsDriver` |
| Capabilities flag | `is_async=False` | `is_async=True` |

The sync agent loop (`Agent.run()`) always receives a sync-compatible driver from `get_model_driver()`. When an `AsyncModelDriver` is configured, it is transparently wrapped with `AsyncToSyncAdapter`. This means **all existing agent definitions work unchanged** regardless of which driver is configured.

---

## 3. `DriverCapabilities` Contract

Drivers declare capabilities via a `ClassVar[DriverCapabilities]` attribute:

```python
@dataclass(frozen=True, slots=True)
class DriverCapabilities:
    is_async: bool = False
    supports_multimodal: bool = False
    supports_response_format: bool = False
    supports_tools: bool = False
    supports_streaming: bool = False
```

Query at runtime with `get_driver_capabilities(driver) -> DriverCapabilities`.

| Capability | When to check | Example use |
|------------|--------------|-------------|
| `is_async` | Before calling `decide()` directly | Determines adapter wrapping |
| `supports_multimodal` | Before sending image_url content parts | Skip multimodal content for text-only drivers |
| `supports_response_format` | Before passing `response_format` to `ModelContext` | Conditional structured output |
| `supports_tools` | Before passing tools to `ModelContext` | Conditional tool injection |
| `supports_streaming` | Before enabling streaming | Not yet used by the framework |

---

## 4. `OpenAiModelDriver` — Synchronous Responses API Driver

```python
@dataclass(slots=True)
class OpenAiModelDriver:
    api_key: str
    on_request_trace: Callable | None = None
    on_response_trace: Callable | None = None

    capabilities: ClassVar[DriverCapabilities] = DriverCapabilities()  # all False
```

**Key behaviors:**

- Synchronous — `decide()` blocks the calling thread
- Uses OpenAI Responses API (`client.responses.create()`)
- Handles `exact_input_payload` bypass for evaluation harness
- Normalizes model output: strips markdown fences before JSON parsing
- Fires `ProviderRequestTrace` / `ProviderResponseTrace` callbacks

**When to use:** Local development, single-agent workflows, environments where the OpenAI Responses API is preferred.

---

## 5. `DialChatCompletionsDriver` — Async DIAL Chat Completions Driver

```python
@dataclass(slots=True)
class DialChatCompletionsDriver:
    base_url: str
    deployment: str
    api_version: str = "2024-10-21"
    api_key: str = ""
    custom_fields: dict[str, Any] | None = None
    retry_without_response_format: bool = True
    timeout: float = 120.0
    on_request_trace: Any | None = None
    on_response_trace: Any | None = None

    capabilities: ClassVar[DriverCapabilities] = DriverCapabilities(
        is_async=True,
        supports_multimodal=True,
        supports_response_format=True,
        supports_tools=True,
    )
```

**Install:** `pip install agent_framework[dial]` (adds `httpx` and `aidial-sdk` dependencies).

**Key behaviors:**

- Asynchronous — `decide()` is a coroutine, uses `httpx.AsyncClient`
- DIAL endpoint: `POST {base_url}/openai/deployments/{deployment}/chat/completions?api-version={api_version}`
- Auth: `Api-Key` header
- Uses `aidial-sdk` types (`ChatCompletionRequest`, `Message`, `Tool`, `ResponseFormat`) for well-typed request construction and validated serialization
- Multimodal: converts `image_url` content parts to `MessageContentImagePart`
- Tool calls: converts `ToolDefinition` → `aidial_sdk.Tool` with JSON Schema parameters
- Response format: converts `response_format` dict → `ResponseFormatJsonObject` or `ResponseFormatJsonSchema`
- **HTTP 400 retry:** When the server returns 400 and `response_format` was in the request body, retries once without `response_format` (configurable via `retry_without_response_format=False`)
- Raises `ModelDriverError(status_code=..., upstream_body=...)` on HTTP errors
- Raises `ModelDriverError(status_code=502)` on transport errors
- Fires `ProviderRequestTrace` / `ProviderResponseTrace` — same mechanism as `OpenAiModelDriver`
- `custom_fields`: merged into request body as DIAL-specific `custom_fields.configuration` (for rate limiting, model routing, etc.)

**Endpoint format:**

```
POST https://{base_url}/openai/deployments/{deployment}/chat/completions?api-version=2024-10-21
Headers:
  Api-Key: {api_key}
  Content-Type: application/json
```

**When to use:** EPAM AI DIAL deployments, services using OpenAI-compatible chat completions with DIAL-specific extensions, async multi-turn orchestration.

**Resource lifecycle:**

```python
driver = DialChatCompletionsDriver(base_url="https://...", deployment="gpt-4o")
try:
    result = await driver.decide(...)
finally:
    await driver.aclose()  # releases the httpx.AsyncClient
```

---

## 6. Implementing a Custom Driver

### 6.1 Sync Driver

```python
from dataclasses import dataclass, field
from typing import Any, ClassVar, Callable
from agent_framework.model import (
    ModelContext, ModelResponse, DriverCapabilities,
    ProviderRequestTrace, ProviderResponseTrace,
    assemble_system_prompt,
)

@dataclass(slots=True)
class MyDriver:
    api_key: str
    on_request_trace: Callable | None = field(default=None, repr=False)
    on_response_trace: Callable | None = field(default=None, repr=False)

    capabilities: ClassVar[DriverCapabilities] = DriverCapabilities(
        supports_tools=True,
    )

    def set_trace_callbacks(self, *, on_request=None, on_response=None) -> None:
        self.on_request_trace = on_request
        self.on_response_trace = on_response

    def decide(self, *, agent_id, provider_name, model_name, temperature, context: ModelContext) -> ModelResponse:
        # 1. Assemble prompt
        system_prompt = assemble_system_prompt(context)

        # 2. Fire request trace
        if self.on_request_trace:
            self.on_request_trace(ProviderRequestTrace(
                agent_id=agent_id, provider_name=provider_name,
                model_name=model_name, input_payload={},
                temperature=temperature, run_id=context.run_id,
            ))

        # 3. Call your provider API
        raw_text = call_my_api(system_prompt, context.user_prompt, ...)

        # 4. Parse based on response_mode
        import json
        from agent_framework.validation import _normalize_json_text
        if context.response_mode in ("decision", "json_object"):
            payload = json.loads(_normalize_json_text(raw_text))
        else:
            payload = {}

        # 5. Fire response trace
        if self.on_response_trace:
            self.on_response_trace(ProviderResponseTrace(
                agent_id=agent_id, provider_name=provider_name,
                model_name=model_name, raw_text=raw_text,
                parsed_payload=payload if payload else None,
                run_id=context.run_id,
            ))

        return ModelResponse(payload=payload, raw_text=raw_text)
```

### 6.2 Async Driver

```python
from agent_framework.model import DriverCapabilities

@dataclass(slots=True)
class MyAsyncDriver:
    capabilities: ClassVar[DriverCapabilities] = DriverCapabilities(
        is_async=True,
        supports_tools=True,
    )

    # ... (set_trace_callbacks same as sync)

    async def decide(self, *, agent_id, provider_name, model_name, temperature, context) -> ModelResponse:
        raw_text = await call_my_api_async(...)
        # ... same parsing logic
        return ModelResponse(payload=payload, raw_text=raw_text)
```

### 6.3 Capability Declaration Checklist

| You support | Set this flag |
|-------------|---------------|
| `async def decide()` | `is_async=True` |
| `image_url` content parts | `supports_multimodal=True` |
| `context.response_format` | `supports_response_format=True` |
| OpenAI-format tool definitions | `supports_tools=True` |

### 6.4 Trace Callbacks

Always implement `set_trace_callbacks()` and call the stored callbacks in `decide()`. Even if your driver doesn't need tracing, the host will call `set_trace_callbacks()` when `enable_audit_trace()` or `enable_llm_trace_logging()` is invoked — not implementing it will cause an `AttributeError`.

---

## 7. Error Handling

Drivers should raise `ModelDriverError` for recoverable, structured errors:

```python
from agent_framework.errors import ModelDriverError

raise ModelDriverError(
    "HTTP 429: rate limited",
    status_code=429,
    upstream_body=response.text[:2000],
)
```

`ModelDriverError` carries:
- `status_code: int | None` — HTTP status if applicable
- `upstream_body: str | None` — raw provider error response (truncated)

Transport failures (connection refused, timeout) should use `status_code=502` by convention.
