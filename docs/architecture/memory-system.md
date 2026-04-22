# Memory System

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Host & Orchestration](./host-orchestration.md) · [Extension Points](./extension-points.md) · [Conversation Model](./conversation-model.md) · User guide: [Using Memory](../guides/using-memory.md)

---

## 1. Purpose

The memory subsystem provides a **shared resource layer** for agent runs.

It exists to solve a different problem from the conversation store:

- **Conversation store** persists message history across client roundtrips.
- **Memory system** stores large or shared runtime artifacts under stable refs so several agents can work on the same content without repeatedly copying it through prompts or subagent payloads.

In practice, memory is the right abstraction when an invocation contains a payload such as a large deck JSON, normalized document body, or scoped runtime artifact that should be:

- stored once
- addressed by URI
- projected into prompts deterministically when needed
- discoverable through list/query interfaces

---

## 2. Core model

The memory subsystem is built around URI-addressed entries.

### 2.1 `MemoryScope`

```python
MemoryScope(kind: str, key: str)
```

Examples:

- `session:abc123`
- `global:default`
- `group:deck-reviewers`
- `agent:slide-reviewer`
- `use_case:deck-review`

### 2.2 `MemoryRef`

`MemoryRef` is the canonical handle passed between runtime components.

Important fields:

- `uri`
- `scope`
- `mime_type`
- `title`
- `summary`
- `size_bytes`
- `version`
- `metadata`

### 2.3 `MemoryEntry`

The stored value plus metadata.

Exactly one content representation is populated:

- `content_text`
- `content_bytes`
- `content_json`

### 2.4 URI shape

```text
mem://<scope-kind>/<scope-key>/<path>
```

Examples:

```text
mem://session/abc123/deck/full
mem://session/abc123/runs/run-42/agents/reviewer/parameters/deck_json
mem://global/default/style-guide/deck-review
```

---

## 3. Runtime components

### 3.1 Backend

`MemoryBackend` owns canonical storage and exact lookup.

Current default:

- `InMemoryMemoryBackend`

Responsibilities:

- `put(entry)`
- `get(uri)`
- `update(uri, ...)`
- `delete(uri)`
- `list(scopes, ...)`

### 3.2 Query provider

`MemoryQueryProvider` is responsible for discovery, not storage.

Current default:

- `CatalogMemoryQueryProvider`

Responsibilities:

- `list(scopes, limit=...)`
- `query(text, scopes, limit=...)`

The current provider uses simple catalog matching over URI, title, summary, and metadata.

### 3.3 Projector

`MemoryProjector` turns refs and entries into deterministic prompt text.

Current default:

- `XmlMemoryProjector`

Responsibilities:

- `render_catalog(hits)`
- `render_entries(entries)`

### 3.4 Scope resolver

`MemoryScopeResolver` determines which scopes are visible to a given run.

Current default:

- `ConfiguredMemoryScopeResolver`

This is the key architectural seam that allows the runtime to grow beyond session-only visibility without redesigning the backend and query contracts.

---

## 4. Host integration

`AgentHost` owns the memory subsystem.

Relevant fields:

```python
memory_backend: MemoryBackend | None
memory_query_provider: MemoryQueryProvider | None
memory_projector: MemoryProjector | None
memory_scope_resolver: MemoryScopeResolver | None
```

Relevant factories:

```python
create_memory_backend()
create_memory_query_provider()
create_memory_projector()
create_memory_scope_resolver()
```

Relevant lazy getters:

```python
get_memory_backend()
get_memory_query_provider()
get_memory_projector()
get_memory_scope_resolver()
```

High-level operational methods:

```python
store_memory(...)
create_memory(...)
get_memory(uri)
update_memory(...)
render_memory_entry(uri)
list_memory_refs(...)
query_memory(...)
build_memory_prompt(...)
normalize_memory_parameters(...)
```

---

## 5. Prompt assembly

Memory prompt assembly happens in `Agent.build_context()`.

That method calls `host.build_memory_prompt(...)`, which:

1. computes visible scopes for the run
2. queries a catalog of visible refs
3. resolves any explicit `mem://...` references found in the run parameters
4. renders catalog and entries through the projector

The result is injected into the model-visible message list as deterministic XML.

### 5.1 Catalog block

```xml
<available_memory>
  <memory_ref id="mem://session/abc123/deck/full"
              scope="session:abc123"
              mime="application/json"
              title="Deck JSON"
              summary="Normalized deck payload for this review session" />
</available_memory>
```

### 5.2 Entry block

```xml
<memory id="mem://session/abc123/deck/full"
        scope="session:abc123"
        mime="application/json">
{ ... }
</memory>
```

This means the model sees both:

- what memory exists
- which specific refs already resolve to full content in the current call

---

## 6. Parameter normalization

One of the most important behaviors in the current implementation is **automatic parameter canonicalization**.

The runtime replaces oversized parameter payloads with a stored memory ref before the call proceeds.

This currently happens for:

- root `seed_parameters`
- subagent `parameters`

It does **not** happen for:

- prompt text
- prompt fragments

The threshold is controlled by:

```ini
MEMORY_AUTO_STORE_THRESHOLD_BYTES=32768
```

The goal is to keep large payloads out of repeated model calls and out of subagent fan-out, while still letting the current agent see the full content via memory projection if appropriate.

---

## 7. Tool surface

The memory tool layer is host-managed.

Registered tools:

- `memory_get`
- `memory_list`
- `memory_query`
- `memory_put`
- `memory_update`

Default agent exposure:

- `memory_get`
- `memory_list`
- `memory_query`

This split is deliberate:

- read-side capabilities are broadly useful and safe
- write-side mutation should remain opt-in

---

## 8. Scope model

The current implementation is session-first but not session-only at the type level.

Visible scopes are computed through the scope resolver. The default resolver:

1. always includes the current session scope
2. may append configured `global`, `group`, and `use_case` scopes
3. may append `agent:<agent_id>` when enabled

Config keys:

- `MEMORY_GLOBAL_SCOPES`
- `MEMORY_GROUP_SCOPES`
- `MEMORY_USE_CASE_SCOPES`
- `MEMORY_ENABLE_AGENT_SCOPE`

This gives the architecture an explicit answer to the question “what memory may this run see?” without coupling visibility rules to storage or prompt rendering.

---

## 9. Relationship to conversation state

The memory system and conversation store should not be conflated.

### Conversation store

Purpose:

- persist message history between calls
- restore prior conversation turns by `conversation_id`

Shape:

- ordered chat messages

### Memory system

Purpose:

- persist or share large runtime resources by stable identifier
- support catalog/query/projection behavior outside the transcript

Shape:

- scoped, URI-addressed entries

The host may use both simultaneously, but they solve different runtime problems.

---

## 10. Tracing

Memory create, update, and auto-store events are published into runtime tracing and audit output.

Current operation kinds:

- `runtime.memory_put`
- `runtime.memory_update`
- `runtime.memory_autostore`

These are also materialized into audit JSONL as `memory_operation` records.

This makes it possible to diagnose:

- when a parameter was rewritten to a memory ref
- which URI was created or updated
- which run and agent produced the operation

---

## 11. Extension points

The subsystem was intentionally split so each concern can evolve independently.

### Storage

Replace `create_memory_backend()` for:

- Redis
- database persistence
- file-backed content

### Discovery

Replace `create_memory_query_provider()` for:

- semantic retrieval
- hybrid ranking
- external search adapters

### Projection

Replace `create_memory_projector()` for:

- alternative prompt serialization formats
- provider-specific packing

### Visibility

Replace `create_memory_scope_resolver()` for:

- caller-aware visibility
- product-specific scoping rules
- use-case policy

---

## 12. Current boundaries

Included now:

- scoped refs and entries
- in-memory storage
- catalog discovery
- XML prompt projection
- parameter auto-storage
- read tools by default
- documented extension seams

Deferred:

- persistent backends
- semantic query providers
- broader production scope policy
- memory-specific summarization pipeline

See also:

- [Using Memory](../guides/using-memory.md)
- [Extension Points](./extension-points.md)
