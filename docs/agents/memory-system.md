# Memory System Reference for Coding Agents

This file is intended for general coding agents such as Cursor, Claude Code, or Codex that need a concise but implementation-accurate reference to the framework's memory subsystem.

Use this when you need to:

- add or modify agent code that uses memory refs
- understand how large parameters are rewritten
- know which memory tools are available to agents by default
- understand how memory differs from conversation persistence
- extend storage, query, projection, or scope policy

---

## 1. Core intent

The framework memory subsystem is a **shared resource layer**, not chat history.

It exists to move large or shared artifacts out of the transcript and into a scoped store addressed by `mem://...` URIs.

Canonical use cases:

- large deck JSON shared across subagents
- normalized document payloads
- scoped runtime artifacts that should be queried or projected into prompts

Do not confuse this with:

- `ConversationStore`, which persists message history across client roundtrips

---

## 2. Public runtime model

Main public types live in:

- `src/agent_framework/memory.py`

Important symbols:

- `MemoryScope`
- `MemoryRef`
- `MemoryEntry`
- `MemoryQueryHit`
- `MemoryBackend`
- `MemoryQueryProvider`
- `MemoryProjector`
- `MemoryScopeResolver`
- `ConfiguredMemoryScopeResolver`
- `InMemoryMemoryBackend`
- `CatalogMemoryQueryProvider`
- `XmlMemoryProjector`
- `build_memory_uri`
- `parse_memory_uri`
- `find_memory_uris`
- `is_memory_uri`
- `next_memory_version`

URI format:

```text
mem://<scope-kind>/<scope-key>/<path>
```

Examples:

```text
mem://session/abc123/deck/full
mem://session/abc123/runs/run-1/agents/reviewer/parameters/deck_json
mem://global/default/style-guide/deck-review
```

---

## 3. Current host integration

The host owns the subsystem.

Relevant host fields:

- `memory_backend`
- `memory_query_provider`
- `memory_projector`
- `memory_scope_resolver`

Relevant host methods:

- `create_memory_backend()`
- `get_memory_backend()`
- `create_memory_query_provider()`
- `get_memory_query_provider()`
- `create_memory_projector()`
- `get_memory_projector()`
- `create_memory_scope_resolver()`
- `get_memory_scope_resolver()`
- `get_visible_memory_scopes(agent_id, run_id)`
- `store_memory(...)`
- `create_memory(...)`
- `get_memory(uri)`
- `update_memory(...)`
- `render_memory_entry(uri)`
- `list_memory_refs(...)`
- `query_memory(...)`
- `build_memory_prompt(...)`
- `normalize_memory_parameters(...)`

Primary implementation file:

- `src/agent_framework/host.py`

---

## 4. What happens automatically

### 4.1 Auto-storage

Oversized **parameters** are automatically stored in session memory and replaced with a `mem://...` ref.

This applies to:

- root `seed_parameters`
- subagent `parameters`

This does **not** apply to:

- prompt text
- prompt fragments

Threshold config:

- `MEMORY_AUTO_STORE_THRESHOLD_BYTES`

Code default:

- `32768`

### 4.2 Default tools

When memory is enabled, agents implicitly receive these read tools:

- `memory_get`
- `memory_list`
- `memory_query`

Write tools are registered but not default-exposed:

- `memory_put`
- `memory_update`

### 4.3 Prompt projection

If memory is visible or explicitly referenced, `Agent.build_context()` injects:

- `<available_memory>`
- `<memory>`

Projection is deterministic and currently XML-based.

---

## 5. Scope behavior

Current default visible scope:

- `session:<host.session_id>`

The runtime also supports configured additional scopes through the default resolver:

- `global`
- `group`
- `use_case`
- `agent`

Relevant config keys:

- `MEMORY_GLOBAL_SCOPES`
- `MEMORY_GROUP_SCOPES`
- `MEMORY_USE_CASE_SCOPES`
- `MEMORY_ENABLE_AGENT_SCOPE`

The correct extension seam for visibility policy is:

- `AgentHost.create_memory_scope_resolver()`

Do not hardcode new scope logic directly into random call sites if it belongs in the resolver.

---

## 6. Tool contracts

### `memory_get`

Input:

- `uri`

Behavior:

- resolves one exact memory entry
- returns XML rendering of that entry

### `memory_list`

Input:

- `scope_kind` optional
- `scope_key` optional
- `limit` optional

Behavior:

- lists visible refs without full content

### `memory_query`

Input:

- `query`
- `scope_kind` optional
- `scope_key` optional
- `limit` optional

Behavior:

- searches visible refs by URI/title/summary/metadata via the active query provider

### `memory_put` and `memory_update`

They exist and are implemented, but are **not** part of the implicit default agent tool set.

---

## 7. Guidance when editing agents or runtime code

Prefer these rules:

- pass `mem://...` refs to subagents instead of large inline payloads
- do not auto-store or rewrite prompt text
- do not invent fake memory ids in agent prompts or tests
- keep `ConversationStore` and memory responsibilities separate
- extend memory through the documented host factories rather than bypassing them

When defining agent parameter contracts for multi-agent workflows, prefer explicit `*_ref` parameters over very large `object` parameters.

---

## 8. Current extension seams

Use these extension seams instead of modifying core call paths directly:

- storage backend: `create_memory_backend()`
- query provider: `create_memory_query_provider()`
- projector: `create_memory_projector()`
- scope policy: `create_memory_scope_resolver()`

These seams are intentionally documented in code for SDK generation and for agent-assisted maintenance.

---

## 9. Traceability

Memory operations currently emit runtime/audit events for:

- `runtime.memory_put`
- `runtime.memory_update`
- `runtime.memory_autostore`

If you are debugging unexpected parameter rewriting or missing refs, inspect the audit trace first.

---

## 10. Current non-goals / deferred areas

These are not the right things to implement accidentally as part of a small fix:

- persistent backends
- semantic retrieval
- automatic summary generation
- production-grade non-session sharing policy

Those belong as explicit follow-up work, not silent scope creep inside the current runtime path.

---

## 11. File map

Core implementation:

- `src/agent_framework/memory.py`
- `src/agent_framework/memory_tools.py`
- `src/agent_framework/host.py`
- `src/agent_framework/agents/agent.py`
- `src/agent_framework/agents/agent_run.py`
- `src/agent_framework/audit_trace.py`
- `src/agent_framework/config.py`

Primary tests:

- `tests/test_memory.py`

Human-facing docs:

- `docs/guides/using-memory.md`
- `docs/architecture/memory-system.md`

Design spec:

- `docs/superpowers/specs/2026-04-21-memory-system-design.md`
