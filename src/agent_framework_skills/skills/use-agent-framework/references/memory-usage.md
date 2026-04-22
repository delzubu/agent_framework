# agent_framework — Memory Usage Reference

Use this reference when an agent needs to work with `mem://...` identifiers, large structured parameters, or shared session-scoped artifacts.

---

## What memory is for

The memory subsystem is a shared resource layer owned by the host.

Use memory for:

- large structured parameters that should not be copied through every subagent call
- artifacts shared across sibling subagents in one request
- host-managed context that should be listed, queried, or injected deterministically

Do not confuse memory with:

- `ConversationStore` — message history persistence across roundtrips
- file reference injection (`@file`) — prompt-time expansion of local files

---

## When to use memory vs direct parameters

| Situation | Recommended path |
|---|---|
| Small scalar or compact JSON needed by one agent only | Pass as normal parameter |
| Large JSON/object reused by subagents | Store or auto-store in memory and pass a `mem://...` ref |
| Local file content that should be expanded directly into the prompt | Use `@file` / `@"path with spaces"` |
| Shared runtime artifact that agents may list or query later | Put it in memory |

Rule of thumb:

- parameters are the contract
- memory is the transport/store for large or shared payloads
- prompts are for instructions, not bulk state transport

---

## URI model

Memory entries are addressed by URIs:

```text
mem://<scope-kind>/<scope-key>/<path>
```

Examples:

```text
mem://session/abc123/deck/full
mem://session/abc123/runs/run-1/agents/reviewer/parameters/deck_json
mem://global/default/style-guides/deck-review
```

Current public types live in `src/agent_framework/memory.py`:

- `MemoryScope`
- `MemoryRef`
- `MemoryEntry`
- `MemoryQueryHit`
- `MemoryBackend`
- `MemoryQueryProvider`
- `MemoryProjector`
- `MemoryScopeResolver`

---

## Automatic runtime behavior

### Auto-storage

Oversized parameters are automatically stored in memory and replaced with a `mem://...` ref.

This applies to:

- root `seed_parameters`
- subagent `parameters`

This does not apply to:

- system prompt text
- user prompt text
- prompt fragments / augmentations

Threshold control:

```env
MEMORY_AUTO_STORE_THRESHOLD_BYTES=32768
```

Code default:

- `DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES = 32768`

### Prompt projection

When memory is visible to the current run, the host can inject deterministic XML:

```xml
<available_memory>
  <memory_ref id="mem://session/abc123/deck/full" summary="Normalized deck JSON" />
</available_memory>

<memory id="mem://session/abc123/deck/full">
  ...content...
</memory>
```

The current default projection mode is controlled by:

```env
MEMORY_DEFAULT_PROJECTION_MODE=catalog_and_selected_content
```

---

## Tools agents can use

Read-side memory tools are available to agents by default when memory is enabled:

- `memory_get`
- `memory_list`
- `memory_query`

Write-side tools exist but are not default-exposed:

- `memory_put`
- `memory_update`

### `memory_get`

Input:

```json
{"uri": "mem://session/abc123/deck/full"}
```

Use when the agent already has an exact id and needs the rendered contents.

### `memory_list`

Input:

```json
{"scope_kind": "session", "scope_key": "abc123", "limit": 20}
```

Use when the agent needs discovery by scope, not semantic search.

### `memory_query`

Input:

```json
{"query": "deck layout guidance", "limit": 10}
```

Use when the agent knows the topic but not the exact `mem://...` id.

Current behavior is catalog-style search over id/title/summary/metadata. It is intentionally pluggable so a semantic provider can replace it later.

---

## Scope model

The runtime is already designed for multiple scopes even though the common current case is session memory.

Supported scope kinds:

- `session`
- `global`
- `group`
- `use_case`
- `agent`

Relevant config:

```env
MEMORY_GLOBAL_SCOPES=
MEMORY_GROUP_SCOPES=
MEMORY_USE_CASE_SCOPES=
MEMORY_ENABLE_AGENT_SCOPE=false
```

Visible-scope policy is resolved through the host's `MemoryScopeResolver`. Do not hardcode new scope logic in agent prompts or ad hoc runtime branches.

---

## Agent design guidance

Prefer these patterns:

- declare `*_ref` parameters for large shared artifacts
- pass memory refs to subagents instead of copying expanded content
- call `memory_list` or `memory_query` when the agent needs discovery
- call `memory_get` only when the exact contents are required

Avoid these patterns:

- putting prompt instructions into memory just to move them around
- inventing fake `mem://...` ids in prompts or tests
- using conversation history as a substitute for shared memory
- copying a full 60k-120k JSON payload into every child call after a ref already exists

---

## Common patterns

### Pass a ref to a subagent

```json
{
  "kind": "call_subagent",
  "subagent_id": "slide_layout_reviewer",
  "parameters": {
    "deck_ref": "mem://session/abc123/deck/full"
  }
}
```

### Discover related memory first

```json
{
  "kind": "call_tool",
  "tool_name": "memory_query",
  "parameters": {
    "query": "style guide for slide decks",
    "limit": 5
  }
}
```

### Deterministically preload memory in Python

Good fit for host setup or a behavior:

- parse incoming payload
- store a normalized form in memory
- replace the large parameter with a `*_ref`
- optionally add a short prompt fragment describing what was loaded

---

## Extension seams

Use these host factory methods instead of patching internals directly:

- `create_memory_backend()`
- `create_memory_query_provider()`
- `create_memory_projector()`
- `create_memory_scope_resolver()`

Current defaults:

- backend: in-memory
- query: catalog
- projector: XML
- scope resolver: configured visibility policy

Deferred, not part of normal small fixes:

- persistent backends
- semantic retrieval
- automatic summarization/consolidation
- production non-session sharing policy

---

## File map

Core implementation:

- `src/agent_framework/memory.py`
- `src/agent_framework/memory_tools.py`
- `src/agent_framework/host.py`
- `src/agent_framework/agents/agent.py`
- `src/agent_framework/config.py`
- `src/agent_framework/audit_trace.py`

Tests:

- `tests/test_memory.py`

Human-facing docs:

- `docs/guides/using-memory.md`
- `docs/architecture/memory-system.md`
