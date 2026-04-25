# Using Memory

This guide explains how to use the framework's memory system in real agent workflows.

The memory subsystem is meant for **shared runtime resources**, not for normal conversation history. Use it when an agent or a group of subagents need access to the same large or structured payload and copying that payload through prompts or subagent parameters would be expensive or unreliable.

Typical examples:

- a slide deck JSON that multiple review subagents need to inspect
- a normalized document payload that should be referenced across several tool and agent calls
- scoped knowledge or session artifacts that should be discoverable through `memory_list` or `memory_query`

If all you need is multi-turn chat history between separate client roundtrips, use the conversation store instead. See [Conversation Model](../architecture/conversation-model.md).

---

## 1. What memory is for

The memory runtime gives the host a place to store content under stable `mem://...` identifiers and then expose that content to agents in two ways:

1. **Deterministic projection into the prompt**
2. **Explicit read-side memory tools**

The important design rule is that memory references become the canonical transport format between agents. The runtime may project the full content for the current model call, but subagent handoff should prefer the ref, not the expanded payload.

---

## 2. Current behavior

### 2.1 Session-scoped by default

Today, memory is enabled by default and every host session has at least one visible scope:

- `session:<host.session_id>`

The runtime already supports extension points for broader visibility such as `global`, `group`, `agent`, and `use_case`, but the first implementation is centered on the current session.

### 2.2 Oversized parameters are auto-stored

Large **parameters** are automatically moved into memory and replaced with a `mem://...` reference when they exceed the configured threshold.

Important boundary:

- **parameters** may be auto-stored
- **prompt text and prompt fragments are never auto-stored**

This means:

- root seed parameters can be rewritten to refs
- subagent call parameters can be rewritten to refs
- the user prompt template itself is not treated as memory input

### 2.3 Read tools are available by default

When memory is enabled, every agent receives these read-side tools implicitly:

- `memory_get`
- `memory_list`
- `memory_query`

Write-side tools exist in the registry:

- `memory_put`
- `memory_update`

but they are **not** part of the default per-agent tool set. If you want an agent to mutate memory intentionally, allow those tools explicitly.

---

## 3. Memory references and XML projection

The runtime uses URI-shaped identifiers:

```text
mem://<scope-kind>/<scope-key>/<path>
```

Examples:

```text
mem://session/abc123/deck/full
mem://session/abc123/runs/xyz/agents/reviewer/parameters/deck_json
mem://global/default/style-guide
```

When the current run references memory, `Agent.build_context()` injects:

- an `<available_memory>` catalog
- zero or more `<memory>` blocks for fully resolved entries

Example:

```xml
<available_memory>
  <memory_ref id="mem://session/abc123/deck/full"
              scope="session:abc123"
              mime="application/json"
              title="Deck JSON"
              summary="Normalized deck payload for this review session" />
</available_memory>

<memory id="mem://session/abc123/deck/full"
        scope="session:abc123"
        mime="application/json">
{
  "slides": [
    {"title": "Overview"}
  ]
}
</memory>
```

The catalog matters even when the full content is projected, because the model then has a stable reference it can pass to tools or subagents.

---

## 4. Agent authoring guidance

### 4.1 When to rely on memory

Use memory when:

- the same payload is needed by multiple subagents
- a parameter is large enough to risk prompt bloat or callback/output truncation
- the agent should discover stored resources by title or summary before reading full content

Do not use memory for:

- simple short scalar parameters
- normal prompt text
- conversation transcript persistence

### 4.2 What to tell the model

The default system prompt now includes memory handling guidance, but custom agents should still be written with these expectations in mind:

- use `memory_get`, `memory_list`, and `memory_query` for reading and discovery
- do not invent `mem://...` ids
- if a memory ref is already available, pass the ref to subagents instead of copying the full content

### 4.3 Parameter contract design

When possible, define child-agent contracts around refs explicitly rather than around huge blobs.

Less robust:

```yaml
parameters:
  deck_json:
    description: Full deck payload
    required: true
    type: object
```

Preferred:

```yaml
parameters:
  deck_ref:
    description: Memory ref for the normalized deck payload
    required: true
    type: string
```

The runtime can still auto-store oversized parameters for compatibility, but explicit ref-shaped contracts make multi-agent orchestration much clearer.

---

## 5. Host APIs for application developers

The host owns memory storage, lookup, query, projection, and scope visibility.

Key methods:

```python
host.get_memory_backend()
host.get_memory_query_provider()
host.get_memory_projector()
host.get_memory_scope_resolver()
host.get_visible_memory_scopes(agent_id="reviewer", run_id="run-123")
host.store_memory(path="deck/full", content=payload, mime_type="application/json")
host.create_memory(path="notes/topic-a", content={"answer": 42})
host.get_memory(uri)
host.update_memory(uri=uri, content={"answer": 43})
host.render_memory_entry(uri)
host.list_memory_refs()
host.query_memory("deck review")
host.build_memory_prompt(...)
host.normalize_memory_parameters(...)
```

### 5.1 Creating memory explicitly

```python
from agent_framework import AgentHost

host = AgentHost.from_env(".env")
ref = host.create_memory(
    path="deck/full",
    content={"slides": [{"title": "Overview"}]},
    mime_type="application/json",
    title="Deck JSON",
    summary="Normalized deck payload for this session",
)

print(ref.uri)
# mem://session/<session-id>/deck/full
```

### 5.2 Querying visible memory

```python
hits = host.query_memory("deck", limit=5)
for hit in hits:
    print(hit.ref.uri, hit.ref.summary)
```

### 5.3 Rendering one entry

```python
xml = host.render_memory_entry(ref.uri)
print(xml)
```

---

## 6. Configuration

Current memory settings live on `HostConfig` and can be loaded from `.env`.

```ini
MEMORY_ENABLED=true
MEMORY_AUTO_STORE_THRESHOLD_BYTES=32768
MEMORY_BUILTIN_TOOLS_ENABLED=true
MEMORY_DEFAULT_PROJECTION_MODE=catalog_and_selected_content
MEMORY_BACKEND=memory
MEMORY_QUERY_PROVIDER=catalog
MEMORY_PROJECTOR=xml
MEMORY_GLOBAL_SCOPES=default
MEMORY_GROUP_SCOPES=deck-reviewers
MEMORY_USE_CASE_SCOPES=slide-review
MEMORY_ENABLE_AGENT_SCOPE=false
```

Meaning of the important ones:

- `MEMORY_ENABLED`: turn the subsystem on or off
- `MEMORY_AUTO_STORE_THRESHOLD_BYTES`: parameter size threshold for auto-storage
- `MEMORY_BUILTIN_TOOLS_ENABLED`: controls whether read tools are added to every agent by default
- `MEMORY_GLOBAL_SCOPES`, `MEMORY_GROUP_SCOPES`, `MEMORY_USE_CASE_SCOPES`: extra visible scopes configured at the host level
- `MEMORY_ENABLE_AGENT_SCOPE`: whether `agent:<agent_id>` becomes visible to runs

---

## 7. Scopes and visibility

The first implementation is session-first, but the runtime now has an explicit scope-resolution seam.

Public types:

- `MemoryScope`
- `MemoryScopeResolver`
- `ConfiguredMemoryScopeResolver`

Default behavior:

- always include `session:<host.session_id>`
- optionally append configured `global`, `group`, `use_case`, and `agent` scopes

This is enough to support future shared-memory policies without redesigning the storage and projection APIs.

---

## 8. Extension points

The memory subsystem is intentionally pluggable.

### 8.1 Storage backend

Override `AgentHost.create_memory_backend()` to replace the in-memory store.

Current default:

- `InMemoryMemoryBackend`

Future-compatible targets:

- Redis
- database-backed persistence
- file-backed memory stores

### 8.2 Query provider

Override `AgentHost.create_memory_query_provider()`.

Current default:

- `CatalogMemoryQueryProvider`

Behavior today:

- exact and substring matching over URI, title, summary, and metadata

The API is stable enough to support semantic retrieval later without changing callers.

### 8.3 Prompt projector

Override `AgentHost.create_memory_projector()`.

Current default:

- `XmlMemoryProjector`

### 8.4 Scope resolver

Override `AgentHost.create_memory_scope_resolver()`.

Current default:

- `ConfiguredMemoryScopeResolver`

Use this when memory visibility should depend on the caller, the agent, or application-level policy.

---

## 9. Tracing and debugging

Memory operations are traced into the runtime audit log.

Current traced operations include:

- `runtime.memory_put`
- `runtime.memory_update`
- `runtime.memory_autostore`

The JSONL audit trace records these as `type: "memory_operation"`.

Use this when you need to answer:

- why a large parameter was rewritten to a ref
- which memory URI was created or updated
- which run produced an auto-stored session artifact

---

## 10. Current limits

The current implementation is intentionally conservative.

Included now:

- in-memory backend
- catalog query provider
- XML prompt projection
- configurable scope resolution
- read tools by default
- write tools available but not default-exposed
- automatic parameter-only auto-storage

Not included yet:

- persistent backends
- semantic retrieval
- automatic summary generation
- full non-session memory policy in a production workflow

See also:

- [Memory System](../architecture/memory-system.md)
- [Extension Points](../architecture/extension-points.md)
- [Conversation Model](../architecture/conversation-model.md)
