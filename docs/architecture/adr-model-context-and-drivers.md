# ADR: Model context assembly and LLM driver layering

## Status

Accepted (implemented incrementally; Phases 3–4 may extend behavior).

## Context

The runtime must present a **consistent** instruction and capability surface to every provider (DIAL, OpenAI Responses, future drivers) without duplicating merge logic. Some APIs support **typed** parameters (`tools`, `response_format`); others rely on **message** content. Agent code should remain provider-agnostic.

## Decisions

### Stable vs dynamic content (Anthropic-aligned default)

- **Default shaping:** Keep **stable** instructions in the system channel (or equivalent). Place **volatile** context (tool results, skill bodies, caller context) in **user/assistant** turns when that improves clarity and caching.
- **Not exclusive:** When the API exposes typed slots (`tools`, `response_format`, …), the **driver layer** maps `ModelContext` fields to those parameters. Prompt injection is a **fallback** when there is no typed slot or `DriverCapabilities` indicates prompt-only embedding.

### Capability-driven assembly

- `DriverCapabilities` describes what the **derived** driver can forward natively.
- **Merge** of agent system text with shared runtime templates (`system.md`, response-mode templates) is centralized in **`ModelDriverBase`** (see `assemble_system_prompt`, `merge_runtime_system_into_messages`) so derived drivers do not duplicate it.

### Responsibility split

| Layer | Role |
|-------|------|
| **Agent** | Decision loop: build abstract `ModelContext`, call driver, parse decisions, tools/subagents/skills, validation, callbacks. |
| **ModelDriverBase** | Map agent-agnostic context to provider-ready inputs where shared; merge runtime instructions into messages; trace hooks; extension point for future store/tool bridging. |
| **Derived driver** | Transport: HTTP/async, auth, endpoint-specific request/response mapping. |
| **Model** | Emit outputs; structured JSON per `response_mode` for agent steps. |

### Defaults: system vs user messages

1. **System:** Abstract model may allow multiple system parts; chat APIs with a single system string **merge** into one in the assembly layer (order documented in code).
2. **User turns:** Default is **multiple** messages — full conversation history in `ModelContext.messages`.
3. **Tools, skills, subagents, MCP, commands:** Represented on `ModelContext`; mapping to typed API parameters vs message injection is **per capability** and **per provider**, handled in derived drivers with guidance from `DriverCapabilities`.

### Conversation store (Phase 3)

- **Implemented scope:** **full history** is passed on `ModelContext.messages` from the Agent. **`AgentHost.complete`** / **`complete_async`** load prior turns and persist the assistant reply via `conversation_id` + **`ConversationStore`** at the **host** layer (unchanged).
- **Future:** optional hooks on `ModelDriverBase` or `ModelContext` if a provider requires the driver to own store I/O; not required for current OpenAI/DIAL drivers.

### Native tool execution (Phase 4)

- **Current behavior:** providers that return **`tool_calls`** on the assistant message are handled by the **Agent** loop (unchanged).
- **Future:** if a provider executes tools inside its SDK with streaming/callbacks, introduce an injectable **`ToolExecutionBridge`** (or equivalent protocol on the host) so pre/post hooks and audit run without fabricating tool messages. No default implementation is wired yet; derived drivers opt in when a provider requires it.

## Consequences

- Callers must run **`merge_runtime_system_into_messages`** (or obtain context from `Agent.build_context` / merged `complete()`) before `decide` so all drivers see the same merged system message.
- `exact_input_payload` on `ModelContext` remains a **bypass** for evaluators and must skip normal assembly in drivers.
