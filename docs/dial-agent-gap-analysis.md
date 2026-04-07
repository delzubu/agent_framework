# Gap analysis: agent_framework support for dial-agent

**Branch:** `feature/dial-agent-support`
**Date:** 2026-04-07
**Method:** Direct code review of both codebases.
**Sources reviewed:**
- agent_framework: `model.py`, `host.py`, `config.py`, `skill.py`, `agent.py`, `__init__.py`
- dial-agent: `llm/provider.py`, `llm/providers/dial.py`, `llm/types.py`, `llm/conversation.py`, `llm/factory.py`, `llm/result.py`, `orchestrator/agent_orchestrator.py`, `orchestrator/context_assembler.py`, `orchestrator/response_formatter.py`, `config/settings.py`

---

## 1. What "support dial-agent" means

dial-agent is a **FastAPI service** with a three-phase LLM pipeline (discovery → phase-1 → phase-2) over DIAL (OpenAI-compatible chat completions). It has its own `LLMProvider` protocol, `ConversationStore`, `ContextAssembler`, `SkillRouter`, and `ResponseFormatter`.

The migration goal is to let dial-agent **import and delegate to agent_framework** for model I/O and (optionally) orchestration, removing its in-house `DialProvider` and related plumbing.

This analysis identifies what agent_framework must gain or change before that is possible. It does **not** prescribe architecture or implementation — that follows once the list is confirmed.

---

## 2. Gap inventory

### G-01 — Framework is not installable as a dependency

**Severity: Blocker**

`pyproject.toml` exists but agent_framework has no published release and no stable public API surface (`__init__.py` does not export the key types). dial-agent cannot `pip install` it or declare a reliable `[tool.poetry.dependencies]` / editable path dependency today without risk of breakage on any internal rename.

**What is missing:** Version-pinnable distribution (editable install at minimum), stable `__all__` exports from the public package `__init__.py`.

---

### G-02 — ModelDriver is synchronous; dial-agent is fully async

**Severity: Blocker**

`ModelDriver.decide()` is a synchronous `Protocol` method. `OpenAiModelDriver.decide()` blocks the calling thread via the synchronous `openai.OpenAI` client and `client.responses.create(...)`.

All of dial-agent runs on an `asyncio` event loop (FastAPI + `httpx.AsyncClient`). Wrapping `decide()` in `asyncio.to_thread` for every LLM call is costly, adds latency under concurrent requests, and makes testing harder.

**What is missing:** An `AsyncModelDriver` protocol with `async def decide_async(...)`, and at minimum a reference async implementation for the DIAL driver (see G-03).

---

### G-03 — No DIAL / OpenAI-compatible chat-completions driver

**Severity: Blocker**

`OpenAiModelDriver` calls the **OpenAI Responses API** (`client.responses.create`). DIAL uses the **OpenAI-compatible chat completions** endpoint:

```
POST {base_url}/openai/deployments/{deployment}/chat/completions?api-version={version}
Headers: Api-Key: {secret}
Body: ChatCompletionRequest (messages, tools, response_format, custom_fields, …)
```

This is a different wire format, different auth scheme, different SDK, and a different Python library (`httpx` + `aidial_sdk` in dial-agent vs `openai` client in agent_framework). No amount of configuration makes `OpenAiModelDriver` speak this protocol.

**What is missing:** A `DialChatCompletionsDriver` (or generic `OpenAICompatibleChatDriver`) that handles the DIAL endpoint, `Api-Key` header, `api-version` query param, and `aidial_sdk`-shaped request/response bodies.

---

### G-04 — ModelContext has no typed multimodal message model

**Severity: High**

`ModelContext.messages` is `tuple[dict[str, Any], ...]` — untyped dicts. dial-agent sends multimodal messages containing `ContentPart` objects with `type="image_url"` and base64-encoded slide images:

```python
ChatMessage(role="user", content=[
    ContentPart(type="text", text="Slide image:"),
    ContentPart(type="image_url", image_url=ImageUrl(url="data:image/png;base64,…")),
])
```

There is no equivalent typed model in agent_framework, no validation, and the Responses API path in `OpenAiModelDriver` does not handle multipart content construction. Any DIAL driver must accept and forward these structures.

**What is missing:** A typed multimodal message model aligned with the OpenAI chat completions spec (or clear documentation that callers must pre-serialize into raw dicts and pass via `exact_input_payload`).

---

### G-05 — response_format is not surfaced to ModelDriver at all

**Severity: High**

`ModelDriver.decide()` and `ModelContext` have no `response_format` field. dial-agent always sends `{"type": "json_object"}` (or a full JSON schema) as `response_format` in the DIAL request body to constrain output. The framework's `response_mode` field is a runtime routing hint for prompt assembly — it is not forwarded to the provider as a structured output constraint.

**What is missing:** `response_format` (either as a `ModelContext` field or a driver-level parameter) that gets forwarded verbatim to the provider.

---

### G-06 — No response_format retry policy

**Severity: High**

When DIAL returns HTTP 400 because a given deployment does not support `response_format`, `DialProvider._post_chat()` retries the request once without `response_format`. This is a DIAL-specific behavior that agent_framework has no mechanism to express.

**What is missing:** A pluggable retry policy at the driver level: "on HTTP 400 when request included `response_format`, retry once without it."

---

### G-07 — No structured LLM error type with HTTP status

**Severity: High**

dial-agent raises `LLMProviderError(message, status_code=..., upstream_body=...)` which propagates through `AgentOrchestrator` and maps to an HTTP 502 in the API layer. agent_framework raises `ValueError` from the driver. There is no exception type carrying HTTP status or upstream response body.

**What is missing:** A structured `ModelDriverError` (or equivalent) exception type with at minimum `status_code: int | None` and `upstream_body: str | None`, plus clear guidance on how HTTP-serving callers should map it.

---

### G-08 — No programmatic ("headless") model invocation API

**Severity: High**

dial-agent builds message lists entirely in Python via `ContextAssembler` — no markdown agent files. `AgentHost.run_agent()` requires loading an `Agent` from a `.md` file with YAML frontmatter before any model call can happen.

There is no public API to say: "given this list of chat messages and these parameters, call the model and return a response" without creating a markdown agent file first. `exact_input_payload` bypass exists on `ModelContext` but requires constructing the full `ModelContext` and calling `driver.decide()` directly, bypassing all host-level lifecycle (audit trace, hooks, error handling).

**What is missing:** A lightweight `complete(messages, ...)` or `run_turn(context, ...)` entry point on `AgentHost` (or as a standalone function) that accepts pre-assembled messages, applies driver/trace lifecycle, and returns a response — without requiring an `.md` agent definition.

---

### G-09 — No resumable conversation model

**Severity: High**

This is an architectural execution model mismatch, not just a missing utility class.

agent_framework's execution model is **single-run**: an agent starts, runs its decision loop to completion, and returns a result. All conversation state lives on the call stack for the duration of that run. Once the run returns, the state is gone. There is no concept of pausing, handing off, and resuming a conversation from an external trigger.

dial-agent's model is **resumable multi-turn**: a conversation is created with an identity, passed through one or more processing steps that may be separated in time and initiated by different callers, and each step appends to the same message history. The discovery phase, for example, creates a conversation, runs one model turn inside it, and the result is surfaced to a caller; that same conversation may be resumed later with new input appended. The conversation outlives any single processing call.

These are fundamentally different models. agent_framework cannot currently support the resumable pattern at all — not because a class is missing, but because the decision loop has no way to yield, persist state, and be re-entered.

**What is needed** is a **Conversation API as an optional add-on** that does not change how agent_framework works today:

- A `Conversation` abstraction representing an ordered, appendable message history with an identity and optional metadata.
- A `ConversationStore` **protocol** (not a concrete class) that defines the operations: create, load by id, append, snapshot, delete. Concrete implementations — in-memory, Redis, database-backed — satisfy the protocol without any change to the framework core.
- The existing single-run agent loop continues to work unchanged when no `ConversationStore` is configured. The add-on only activates when a caller explicitly creates or loads a conversation and passes its snapshot as the message history for a model call.
- The framework should provide at least one reference implementation (in-memory with TTL) for use in tests and simple deployments.

The protocol boundary must be storage-agnostic: the framework defines what operations are needed, not how messages are persisted. Out-of-process stores (database, cache) are first-class citizens of the design, not afterthoughts.

---

### G-10 — No clarification pseudo-tool / terminal tool exit

**Severity: Medium**

dial-agent's discovery loop recognizes a special tool `ask_for_clarification` (value of `CLARIFICATION_TOOL_NAME`). When the model calls it, `DialProvider.run()` immediately exits with `finish_reason="clarification"` without executing any server-side tool — bypassing the normal tool loop.

agent_framework's tool loop executes every tool the model calls. There is no mechanism to declare a tool as "terminal" (exit loop and return its arguments as the result) rather than "execute and continue."

**What is missing:** A configurable terminal-tool pattern: a named tool that, when invoked by the model, stops the loop and returns its arguments as a special result kind.

---

### G-11 — No async JSON-validation retry helper

**Severity: Medium**

`ResponseFormatter` implements an async pattern: parse and Pydantic-validate model output → on failure, append an error message to the conversation → retry `llm_provider.complete()` → validate again → raise if still invalid. This is an important production reliability pattern (it handles malformed JSON from the model).

agent_framework has `_normalize_json_text` (strips fences) but no retry-with-correction loop and no async path for it.

**What is missing:** An async `parse_and_validate(content, model_type, retry_fn)` utility or documented pattern for callers that want this behavior with a framework driver.

---

### G-12 — HostConfig and driver construction are OpenAI-only

**Severity: Medium**

`HostConfig` has `openai_api_key: str` as the only credential field. `AgentHost.from_env()` constructs `OpenAiModelDriver(api_key=config.openai_api_key)` unconditionally. There is no config path for DIAL credentials (`dial_base_url`, `dial_deployment`, `dial_api_version`, `dial_api_key`).

dial-agent cannot construct an `AgentHost` with a DIAL driver by reading its own `Settings` — it would need to bypass `from_env()` and pass a driver directly, which works but leaves `HostConfig.openai_api_key` as a required field with no applicable value.

**What is missing:** Either a driver-agnostic `HostConfig` (credentials optional/per-driver), or a clearly supported path to construct `AgentHost` with an externally constructed driver without needing an `openai_api_key`.

---

### G-13 — No aidial-sdk dependency (and no decision on it)

**Severity: Low–Medium**

dial-agent uses `aidial_sdk.chat_completion.request` types (`ChatCompletionRequest`, `Message`, `Tool`, `ToolCall`, `ResponseFormat`, `ImageURL`, etc.) to build well-typed DIAL request bodies. If agent_framework adds a DIAL driver, it must decide: take `aidial-sdk` as an optional dependency, replicate the relevant types internally, or use raw dicts.

**What is missing:** A decision on the `aidial-sdk` dependency and its placement (required, optional extra, or absent with raw-dict approach).

---

### G-15 — No connector capability contract

**Severity: High**

The individual symptoms of this gap appear elsewhere in the analysis (G-02 async, G-04 multimodal, G-05 response_format, G-06 retry policy, G-10 terminal tool), but there is an underlying architectural concern they share: **different connectors support different capabilities, and the framework has no way to express, declare, or route on those differences**.

Today the framework assumes a single driver model — synchronous, Responses API, no response_format, no multimodal. When a DIAL driver is introduced, it will differ on every one of those axes. Callers currently have no way to know what a given driver supports, and the framework has no mechanism to adapt behavior based on driver capabilities (e.g., skip response_format if the driver doesn't support it, use async paths when available, represent tool results differently per connector).

Without a capability contract, two failure modes emerge:
1. Callers must check the concrete driver type to decide how to invoke it — tight coupling that defeats the `ModelDriver` protocol abstraction.
2. The framework silently ignores capabilities it doesn't know about (e.g., multimodal content passed as dicts that a driver drops without error).

**What is missing:** A connector capability declaration mechanism — a way for a driver to advertise what it supports (async, multimodal content, `response_format`, structured outputs, streaming, etc.) and for the framework or callers to query those capabilities before constructing a `ModelContext` or invoking `decide`. This does not need to be a runtime type system; a simple set of declared flags or a capabilities dataclass on the driver is sufficient. The key property is that it is explicit and inspectable, not inferred from duck-typing.

---

### G-14 — Logging/trace wiring is not compatible with dial-agent's trace hooks

**Severity: Low**

dial-agent has `log_llm_request` / `log_llm_response_text` / `log_llm_transport_error` hooks called at the HTTP layer inside `DialProvider`. agent_framework has `ProviderRequestTrace` / `ProviderResponseTrace` callbacks on `ModelDriver.set_trace_callbacks()` plus `InMemoryAuditTracer`.

The trace callback shapes are not identical (e.g., `ProviderRequestTrace` includes `agent_id`, `run_id`; dial-agent logs raw endpoint + body dict). Integration is possible but requires adapter code.

**What is missing:** Documented mapping between agent_framework's trace callbacks and dial-agent's logging hooks, or a trace adapter in the framework.

---

## 3. Gap summary table

| ID | Title | Severity | Affects |
|----|-------|----------|---------|
| G-01 | Not installable as a dependency | Blocker | All integration |
| G-02 | Sync ModelDriver; async dial-agent | Blocker | All model calls |
| G-03 | No DIAL chat-completions driver | Blocker | All model calls |
| G-04 | No typed multimodal message model | High | Slide image turns |
| G-05 | response_format not surfaced | High | All JSON outputs |
| G-06 | No response_format retry policy | High | All JSON outputs |
| G-07 | No structured LLM error type | High | Error propagation |
| G-08 | No headless model invocation API | High | Non-agentic phases |
| G-09 | No resumable conversation model | High | Multi-turn flows |
| G-10 | No clarification/terminal tool | Medium | Discovery phase |
| G-11 | No async JSON validation retry | Medium | All phases |
| G-12 | HostConfig is OpenAI-only | Medium | Driver construction |
| G-13 | aidial-sdk dependency undecided | Low–Medium | DIAL driver |
| G-14 | Trace/logging wiring incompatible | Low | Observability |
| G-15 | No connector capability contract | High | All driver integrations |

---

## 4. What is explicitly out of scope

The following are **not** gaps in agent_framework — they are dial-agent domain concerns that will stay in dial-agent regardless:

- `ContextAssembler` (XML wrapping, placeholder rendering, slide/shape formatting)
- `SkillRouter` and the Git-sync skill inventory
- `AgentOrchestrator` phases (discovery/phase1/phase2 state machine)
- `ResponseFormatter` Pydantic result models (`DeckReviewReport`, `SlideReviewReport`, etc.)
- FastAPI routes, auth middleware, `JobStore`, `TemplateStore`
- PPTX execution (`pptx_executor`)

---

## 5. Out-of-scope for this analysis (open questions, not gaps)

1. Should dial-agent phases eventually be expressed as markdown agent files (framework-native), or remain programmatic-assembly forever? (Affects G-08 scope.)
2. Is DIAL the only production provider for the foreseeable future, or should the async driver and capability contract (G-15) be designed for multi-provider from day one?
3. What is the right scope of the `ConversationStore` protocol (G-09): framework-owned with multiple reference implementations, or defined in framework but shipped/implemented in dial-agent?
4. Should `aidial-sdk` (G-13) be a hard dependency of a new `agent_framework[dial]` extra, or should the DIAL driver use raw dicts to avoid it?

---

*Ready for review. No implementation work has been started.*
