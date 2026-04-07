# Using DIAL with agent_framework

This guide covers integrating `agent_framework` with EPAM AI DIAL — an OpenAI-compatible LLM gateway with enterprise features (multi-model routing, rate limiting, audit logging).

---

## Prerequisites

```bash
pip install "agent_framework[dial]"
```

This adds `httpx` and `aidial-sdk` as dependencies alongside the base package.

---

## 1. Configuration

### Via `.env` file

```env
DEFAULT_PROVIDER=dial
DIAL_BASE_URL=https://your-dial-instance.example.com
DIAL_DEPLOYMENT=gpt-4o
DIAL_API_VERSION=2024-10-21
DIAL_API_KEY=your-api-key
DEFAULT_MODEL=gpt-4o
```

```python
from agent_framework import AgentHost

host = AgentHost.from_env(".env")
# → auto-constructs DialChatCompletionsDriver from DIAL_* env vars
```

### Programmatic construction (no `.env` required)

```python
from agent_framework import AgentHost, HostConfig
from agent_framework.drivers.dial import DialChatCompletionsDriver

driver = DialChatCompletionsDriver(
    base_url="https://your-dial-instance.example.com",
    deployment="gpt-4o",
    api_version="2024-10-21",
    api_key="your-api-key",
)
host = AgentHost.create(
    model_driver=driver,
    config=HostConfig(default_model="gpt-4o"),
)
```

---

## 2. Single-Turn Calls

Use `complete_async()` for single-turn model invocations without a markdown agent:

```python
# Default: response_mode="json_object" — result.payload is a parsed dict
result = await host.complete_async(
    messages=[{"role": "user", "content": "Return a JSON object with 'name' and 'score'."}],
)
data = result.payload  # already parsed dict
print(data["name"])
```

For plain-text output, pass `response_mode="text"` explicitly:

```python
result = await host.complete_async(
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Summarize this in one sentence: ..."},
    ],
    response_mode="text",
)
print(result.raw_text)
```

With structured output hint (for models that support `response_format`):

```python
from agent_framework.model import ModelContext

result = await host.complete_async(
    messages=[...],
    response_mode="json_object",
    response_format={"type": "json_object"},
)
```

---

## 3. Multi-Turn Conversations

Pair `complete_async()` with a `ConversationStore` for automatic history management:

```python
from agent_framework import AgentHost, HostConfig
from agent_framework.conversation import InMemoryConversationStore
from agent_framework.drivers.dial import DialChatCompletionsDriver

store = InMemoryConversationStore(ttl_seconds=3600)
driver = DialChatCompletionsDriver(base_url="...", deployment="gpt-4o", api_key="...")
host = AgentHost.create(model_driver=driver, conversation_store=store)

# Create a conversation with an initial system message
cid = store.create([{"role": "system", "content": "You are a helpful assistant."}])

# Turn 1
r1 = await host.complete_async(
    messages=[{"role": "user", "content": "What is the capital of France?"}],
    conversation_id=cid,
    response_mode="text",
)
print(r1.raw_text)  # "Paris"

# Turn 2 — history is loaded automatically
r2 = await host.complete_async(
    messages=[{"role": "user", "content": "And Germany?"}],
    conversation_id=cid,
    response_mode="text",
)
print(r2.raw_text)  # "Berlin"
```

The store accumulates the full history. Each `complete_async()` with `conversation_id`:
1. Loads existing messages from store
2. Appends the new user message(s)
3. Calls the model
4. Saves the assistant response back to the store

---

## 4. Tool-Calling Loop

Use `run_tool_loop()` for multi-turn tool-use orchestration:

```python
import json
from agent_framework.host import run_tool_loop
from agent_framework.tool import ToolDefinition, ToolParameter

# Define a tool
search_tool = ToolDefinition(
    tool_id="search",
    description="Search the web for information.",
    parameters=(ToolParameter(name="query", description="Search query", required=True),),
)

async def tool_executor(name: str, args: dict) -> str:
    if name == "search":
        return f"Search results for: {args['query']}"
    raise ValueError(f"Unknown tool: {name}")

result = await run_tool_loop(
    host,
    messages=[{"role": "user", "content": "Who invented Python?"}],
    tools=[search_tool],
    tool_executor=tool_executor,
    max_iterations=5,
)
print(result.raw_text)
```

The loop continues until the model stops calling tools or `max_iterations` is reached (raises `RuntimeError`).

---

## 5. Terminal Tools (Clarification Pattern)

Terminal tools let the model exit the loop by "calling" a special tool — useful for requesting clarification before proceeding:

```python
from agent_framework.tool import ToolDefinition, ToolParameter

clarify_tool = ToolDefinition(
    tool_id="request_clarification",
    description="Ask the user a clarifying question.",
    parameters=(
        ToolParameter(name="question", description="The clarifying question", required=True),
    ),
)

result = await run_tool_loop(
    host,
    messages=[{"role": "user", "content": "Book me a flight."}],
    tools=[search_tool, clarify_tool],
    tool_executor=tool_executor,
    terminal_tools=["request_clarification"],
    max_iterations=10,
)

if result.finish_reason == "terminal_tool":
    # Model asked a clarifying question — parse the arguments
    args = json.loads(result.raw_text)
    user_answer = await ask_user(args["question"])
    # Re-enter the loop with the answer
    result = await run_tool_loop(
        host,
        messages=[
            {"role": "user", "content": "Book me a flight."},
            {"role": "user", "content": f"Clarification: {user_answer}"},
        ],
        ...
    )
```

---

## 6. Multimodal Messages

DIAL supports image inputs. Use `ChatMessage` with `ContentPart` for type-safe construction:

```python
from agent_framework.messages import ChatMessage, ContentPart, ImageUrl

msg = ChatMessage(
    role="user",
    content=(
        ContentPart(type="text", text="Describe what you see:"),
        ContentPart(
            type="image_url",
            image_url=ImageUrl(url="data:image/png;base64,iVBORw0KGgo...", detail="high"),
        ),
    ),
)

result = await host.complete_async(
    messages=[msg.to_dict()],
    response_mode="text",
)
```

The `DialChatCompletionsDriver` automatically converts `image_url` content parts to `aidial_sdk.MessageContentImagePart` objects.

---

## 7. JSON Validation with Retry

Use `validate_and_retry()` when you need typed, validated JSON output from the model:

```python
from pydantic import BaseModel, ValidationError
from agent_framework.validation import validate_and_retry

class AnalysisResult(BaseModel):
    summary: str
    score: float
    tags: list[str]

result = await host.complete_async(
    messages=[{"role": "user", "content": "Analyze this text: ..."}],
    response_mode="json_object",
)

async def retry_fn(error_description: str) -> str:
    """Re-ask the model when validation fails."""
    retry_result = await host.complete_async(
        messages=[
            {"role": "user", "content": "Analyze this text: ..."},
            {"role": "assistant", "content": result.raw_text},
            {"role": "user", "content": f"Your response was invalid: {error_description}. Please fix it."},
        ],
        response_mode="json_object",
    )
    return retry_result.raw_text

analysis = await validate_and_retry(
    result.raw_text,
    validator=lambda d: AnalysisResult(**d),
    retry_fn=retry_fn,
)
print(analysis.score)
```

`validate_and_retry()`:
1. Parses the JSON (stripping markdown fences if present)
2. Calls `validator(parsed_dict)` — if it raises, calls `retry_fn(error_description)`
3. Tries the validator one more time on the retry output
4. If retry also fails, re-raises the validator exception

---

## 8. Error Handling

```python
from agent_framework.errors import ModelDriverError

try:
    result = await host.complete_async(messages=[...])
except ModelDriverError as e:
    if e.status_code == 429:
        # Rate limited — implement backoff
        await asyncio.sleep(retry_after)
    elif e.status_code == 400:
        # Bad request — check e.upstream_body for DIAL error details
        print(f"DIAL error: {e.upstream_body}")
    elif e.status_code == 502:
        # Transport error — network/VPN issue; str(e) contains the target URL
        # and a connectivity hint for operators
        raise
    else:
        raise
```

`ModelDriverError` fields:
- `str(e)` — human-readable message
- `e.status_code: int | None` — HTTP status (502 for transport errors, None for protocol errors)
- `e.upstream_body: str | None` — raw provider error response (up to 2000 chars)

---

## 9. Tracing

The DIAL driver fires the same `ProviderRequestTrace` / `ProviderResponseTrace` callbacks as `OpenAiModelDriver`. Wire them via the standard host methods:

```python
# Audit trace — immutable JSONL log of all LLM calls
from pathlib import Path
host.enable_audit_trace(output_dir=Path("logs"))

# Console trace — colored request/response output
host.enable_llm_trace_logging(target="console", output_dir=Path("logs"))

# File trace
host.enable_llm_trace_logging(target="file", output_dir=Path("logs"))
```

Or attach custom trace callbacks directly:

```python
driver = DialChatCompletionsDriver(...)
driver.set_trace_callbacks(
    on_request=lambda t: print(f"→ DIAL [{t.model_name}] {len(str(t.input_payload))} chars"),
    on_response=lambda t: print(f"← DIAL [{t.model_name}] {t.raw_text[:80]}"),
)
```

---

## 10. DIAL-Specific Configuration

### Custom Fields

DIAL supports `custom_fields.configuration` for per-request metadata (used for rate limiting, model routing, etc.):

```python
driver = DialChatCompletionsDriver(
    base_url="...",
    deployment="gpt-4o",
    api_key="...",
    custom_fields={
        "project": "my-project",
        "user_tier": "premium",
    },
)
```

### Disabling Response Format Retry

By default, if DIAL returns HTTP 400 when `response_format` is included in the request, the driver retries once without it. Disable this if you want strict validation:

```python
driver = DialChatCompletionsDriver(
    ...,
    retry_without_response_format=False,
)
```

### Timeout

```python
driver = DialChatCompletionsDriver(
    ...,
    timeout=300.0,  # seconds (default: 120)
)
```

---

## 11. Resource Cleanup

Always close the driver when done to release the `httpx.AsyncClient`:

```python
driver = DialChatCompletionsDriver(...)
try:
    # ... use host ...
finally:
    await driver.aclose()
```

Or use as an async context manager pattern:

```python
async with contextlib.asynccontextmanager(lambda: driver):
    ...
```

The `httpx.AsyncClient` is created lazily on first use and reused across calls.
