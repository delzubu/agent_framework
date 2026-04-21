# Memory System Design

**Date:** 2026-04-21
**Status:** Proposed — ready for implementation
**Target area:** `src/agent_framework/`

---

## Overview

This document specifies a first-class memory system for the agent framework.

The immediate driver is a multi-agent slide-deck review workflow where the root agent must hand large deck payloads to subagents. The payloads are too large to treat as ordinary chat content:

- deck JSON may be 60k-120k+ characters
- repeating the full payload across agent turns is expensive
- subagent calls may crop or fail because of model output and context limits
- the existing `conversation_store` is scoped to conversation transcript persistence, not shared runtime resources

The memory system solves this by moving large or shared payloads out of the chat transcript and into a scoped resource store. Agents pass stable memory references between each other, while the runtime can inject selected memory content into the prompt deterministically when needed.

This system must support two access paths from day one:

1. Tool-driven access, for explicit reads and later writes
2. Deterministic runtime projection, where memory is injected into the model context before inference

It must also be extensible in three directions:

- scopes beyond `session`
- query and discovery beyond exact identifier lookup
- storage beyond in-memory runtime objects

This is a new subsystem. It is not an extension of `conversation_store`.

### Key design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Canonical identity | URI-based `mem://...` references | Stable handles are easier to pass between agents, tools, and future backends |
| Storage abstraction | Separate `MemoryBackend` protocol | Avoid coupling to in-memory implementation; enable Redis/DB/file later |
| Retrieval abstraction | Separate `MemoryQueryProvider` protocol | Query/list can evolve from simple catalog search to semantic retrieval without changing callers |
| Model integration | Deterministic memory projection in `build_context()` | Avoids extra LLM decision cost for routine retrieval |
| Agent transport | Pass references, not expanded payloads | Prevents subagent fan-out from duplicating large blobs |
| Current default scope | `session` | Matches the current use case while leaving scope extensibility explicit |
| Query result shape | identifier + title + summary + metadata | Supports both pre-injected catalogs and tool results |
| Initial write support | Store API defined now, writes deferred in tools | Avoids redesign when agents later need to update memory |

---

## Goals

- Add a first-class memory resource layer distinct from conversation history.
- Support session-scoped shared memory across a root agent and all subagents in a single request flow.
- Support deterministic prompt injection of selected memory content using XML.
- Support explicit tool access for `get`, `list`, and `query`.
- Support identifier-based and summary-based discovery of available memory.
- Ensure parent and child agents can pass references instead of re-emitting large payloads.
- Design scoping so `global`, `group`, `agent`, and `use_case` scopes can be added without redesign.
- Design storage so in-memory, Redis, database, and file-backed implementations can all satisfy the same protocol.
- Keep the initial implementation strict and deterministic; do not add fuzzy repair or heuristic reinterpretation.

## Non-goals

- No semantic vector retrieval in the first implementation
- No automatic summarization model in the first implementation
- No persistence backend in the first implementation
- No background indexing pipeline in the first implementation
- No ACL or auth model beyond scope-based visibility rules
- No UI work in the first implementation

---

## Terminology

### Memory entry

A stored memory item with canonical content, metadata, scope, and a stable URI.

### Memory reference

A lightweight handle pointing to a memory entry, used in prompts, tools, and subagent parameters.

### Memory scope

A namespace that determines where a memory entry is visible. `session` is the initial scope, but the model supports others.

### Memory query

A discovery operation returning candidate memory references with summaries, rather than full content.

### Memory projection

Deterministic runtime rendering of memory into model-visible XML.

### Memory view

A derived representation of a memory entry, such as a deck summary, a single slide, or extracted speaker notes.

---

## Research Basis

This design follows common patterns used by agentic frameworks:

- LangGraph separates checkpointed thread state from a shared store and supports persistent or in-memory stores.
- AutoGen exposes memory as a protocol with `query`, `add`, and `update_context`, explicitly allowing memory to mutate the model context before inference.
- LlamaIndex distinguishes workflow runtime context from memory.
- OpenAI Agents SDK exposes deterministic session input shaping before model invocation.
- MCP resources use URI-addressable resources and metadata for application-driven inclusion.

The framework-specific conclusion is:

- canonical memory should live outside the transcript
- retrieval should be application-controlled and deterministic when possible
- resources should be URI-addressable
- query and storage should be pluggable

Sources:

- https://docs.langchain.com/oss/python/langgraph/persistence
- https://microsoft.github.io/autogen/stable/reference/python/autogen_core.memory.html
- https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/memory.html
- https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/
- https://openai.github.io/openai-agents-js/guides/sessions/
- https://modelcontextprotocol.io/specification/draft/server/resources

---

## User-Facing Behavior

### Root-agent behavior

When a root agent receives a large payload such as a slide-deck JSON:

1. The runtime stores the payload in memory as a session-scoped entry.
2. The runtime replaces the large payload in canonical runtime state with a `MemoryRef`.
3. The runtime may inject the full content, a catalog entry, or a derived view into the model prompt, depending on policy.

### Subagent behavior

When a parent agent calls a subagent:

- the parent may pass a `MemoryRef` directly
- the runtime must normalize oversized payloads back to `MemoryRef` form before the child call executes
- the child may receive projected memory in its prompt and/or use tools to retrieve memory explicitly

### Discovery behavior

Agents must be able to answer questions like:

- "what memory is available in this session?"
- "what do you know about this topic?"
- "find the deck memory item"

The initial answer path is catalog-style discovery using title, summary, and metadata. Later this may be upgraded to semantic retrieval without changing the public API.

---

## Architecture & Component Map

### New components

```
src/agent_framework/memory.py
  MemoryScope
  MemoryRef
  MemoryEntry
  MemoryQueryHit
  MemoryBackend
  MemoryQueryProvider
  MemoryProjector
  MemoryResolutionPolicy
  InMemoryMemoryBackend
  CatalogMemoryQueryProvider
  XmlMemoryProjector

src/agent_framework/memory_tools.py
  build_memory_get_tool()
  build_memory_list_tool()
  build_memory_query_tool()

src/agent_framework/agents/memory_start_event.py
  MemoryStartEvent

src/agent_framework/agents/memory_end_event.py
  MemoryEndEvent
```

### Modified components

```
src/agent_framework/host.py
  AgentHost
    add memory_backend
    add memory_query_provider
    add memory_projector
    add get_memory_backend()
    add get_memory_query_provider()
    add get_memory_projector()
    add memory scope visibility helpers

src/agent_framework/agents/agent_behavior.py
  AgentBehavior
    add memory-oriented pre-run hook guidance

src/agent_framework/agents/agent_run.py
  AgentRun
    add visible_memory_scopes
    add resolved_memory_refs
    add memory_projection_requests

src/agent_framework/agents/agent.py
  Agent.build_context()
    inject memory XML
  Agent.handle_subagent_call()
    normalize memory refs in child parameters
  Agent.handle_subagent_calls()
    normalize each batch item

src/agent_framework/tool_registry.py
  register memory tools as builtins or host-managed tools

src/agent_framework/config.py
  HostConfig
    add memory backend/query/projector configuration

src/agent_framework/audit_trace.py
  add memory read / query / projection records

docs/architecture/*.md
  update host orchestration, extension points, and runtime assembly docs
```

---

## Data Model

### `MemoryScope`

```python
@dataclass(frozen=True, slots=True)
class MemoryScope:
    kind: str      # "session" | "global" | "group" | "agent" | "use_case"
    key: str       # e.g. "abc123", "default", "deck-reviewers"
```

Rules:

- `kind` is required and case-sensitive
- `key` is required and non-empty
- unknown scope kinds are allowed by the type system but may not be supported by the active visibility policy
- the initial implementation must support at least `session`

### `MemoryRef`

```python
@dataclass(frozen=True, slots=True)
class MemoryRef:
    uri: str
    scope: MemoryScope
    mime_type: str
    title: str | None = None
    summary: str | None = None
    size_bytes: int = 0
    version: str = "1"
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

Rules:

- `uri` must use the `mem://` scheme
- `uri` must be stable for the lifetime of the entry version
- `summary` is optional in the type, but strongly recommended for query/list output
- `metadata` may include domain-specific attributes such as `topic`, `deck_id`, `slide_count`, `source`, or `created_at`

### URI format

The runtime must use a predictable URI layout:

```text
mem://<scope-kind>/<scope-key>/<path>
```

Examples:

- `mem://session/abc123/deck/full`
- `mem://session/abc123/deck/slide/17`
- `mem://global/default/style-guide/deck-review`
- `mem://group/deck-reviewers/rubric/default`

Rules:

- path segments are slash-separated
- the final path naming is application-defined but should be stable and human-readable
- URI layout must not assume a filesystem

### `MemoryEntry`

```python
@dataclass(frozen=True, slots=True)
class MemoryEntry:
    ref: MemoryRef
    content_text: str | None = None
    content_bytes: bytes | None = None
    content_json: Any | None = None
```

Rules:

- exactly one of `content_text`, `content_bytes`, or `content_json` should be populated in normal usage
- `mime_type` determines how projection and tools render the entry
- binary content is supported structurally but is not a priority in the first use case

### `MemoryQueryHit`

```python
@dataclass(frozen=True, slots=True)
class MemoryQueryHit:
    ref: MemoryRef
    score: float | None = None
    match_reason: str | None = None
```

The first implementation may leave `score` as `None` and use exact or substring matching. The shape exists now so semantic retrieval can be added later without API churn.

---

## Core Protocols

### `MemoryBackend`

```python
class MemoryBackend(Protocol):
    def put(self, entry: MemoryEntry) -> MemoryRef: ...
    def get(self, uri: str) -> MemoryEntry: ...
    def update(self, uri: str, *, content: Any, mime_type: str | None = None, summary: str | None = None,
               metadata: Mapping[str, Any] | None = None) -> MemoryRef: ...
    def delete(self, uri: str) -> None: ...
    def list(self, scopes: Sequence[MemoryScope], *, prefix: str | None = None,
             mime_type: str | None = None, limit: int = 50) -> tuple[MemoryRef, ...]: ...
```

Requirements:

- `get()` raises `KeyError` for unknown URIs
- `put()` stores a full entry and returns its `MemoryRef`
- `update()` is part of the contract even if not exposed by tools yet
- `list()` returns refs only, never full content
- backend implementations must not assume session-only scoping

### `MemoryQueryProvider`

```python
class MemoryQueryProvider(Protocol):
    def list(self, scopes: Sequence[MemoryScope], *, limit: int = 20) -> tuple[MemoryQueryHit, ...]: ...
    def query(self, text: str, scopes: Sequence[MemoryScope], *, limit: int = 10) -> tuple[MemoryQueryHit, ...]: ...
```

Requirements:

- `list()` is discovery without a query string
- `query()` returns candidate refs plus summaries
- both methods operate over visible scopes
- the initial implementation may use a catalog search over URI, title, summary, and selected metadata

### `MemoryProjector`

```python
class MemoryProjector(Protocol):
    def render_catalog(self, hits: Sequence[MemoryQueryHit]) -> str: ...
    def render_entries(self, entries: Sequence[MemoryEntry]) -> str: ...
```

Requirements:

- output must be deterministic
- XML is the required initial format
- projection must not mutate underlying memory

### `MemoryResolutionPolicy`

```python
class MemoryResolutionPolicy(Protocol):
    def should_store_parameter(self, *, name: str, value: Any) -> bool: ...
    def should_project_full_entry(self, *, agent_id: str, ref: MemoryRef) -> bool: ...
    def should_project_catalog(self, *, agent_id: str) -> bool: ...
    def derive_child_parameters(self, *, parent_agent_id: str, child_agent_id: str,
                                parameters: Mapping[str, Any]) -> dict[str, Any]: ...
```

This policy is where "store now, inject later, pass refs to children" becomes enforceable runtime behavior rather than prompt advice.

---

## Initial Implementations

### `InMemoryMemoryBackend`

The first implementation is an in-memory backend.

Suggested shape:

```python
@dataclass(slots=True)
class InMemoryMemoryBackend:
    _entries: dict[str, MemoryEntry] = field(default_factory=dict)
```

Requirements:

- fast exact lookup by URI
- no persistence across process restarts
- sufficient for evaluator runs, local development, and the first deck-review workflow

### `CatalogMemoryQueryProvider`

The first query provider behaves like a tool registry lookup:

- `list()` returns recent or sorted refs in visible scopes
- `query(text)` searches across:
  - URI
  - title
  - summary
  - selected metadata string values

Matching rules for v1:

- case-insensitive
- exact substring search is sufficient
- ranking may be:
  1. URI exact contains
  2. title exact contains
  3. summary exact contains
  4. metadata contains

This provider must be replaceable later by a semantic provider that uses embeddings or another retrieval engine.

### `XmlMemoryProjector`

The first projector renders two XML blocks.

Catalog:

```xml
<available_memory>
  <memory_ref id="mem://session/abc123/deck/full"
              scope="session:abc123"
              mime="application/json"
              title="Slide deck JSON"
              summary="Normalized deck payload for the current review session" />
</available_memory>
```

Content:

```xml
<memory id="mem://session/abc123/deck/full"
        scope="session:abc123"
        mime="application/json">
{...}
</memory>
```

Rules:

- escape XML attributes correctly
- preserve text and JSON content faithfully
- JSON content must be rendered as pretty-printed JSON text
- do not wrap XML in Markdown fences

---

## Host Integration

### `AgentHost` fields

Add the following fields to `AgentHost`:

```python
memory_backend: MemoryBackend | None = None
memory_query_provider: MemoryQueryProvider | None = None
memory_projector: MemoryProjector | None = None
memory_resolution_policy: MemoryResolutionPolicy | None = None
```

### `AgentHost` methods

```python
def get_memory_backend(self) -> MemoryBackend: ...
def get_memory_query_provider(self) -> MemoryQueryProvider: ...
def get_memory_projector(self) -> MemoryProjector: ...
def get_memory_resolution_policy(self) -> MemoryResolutionPolicy: ...
def get_visible_memory_scopes(self, *, agent_id: str, run_id: str) -> tuple[MemoryScope, ...]: ...
```

Default behavior:

- if unset, `get_memory_backend()` returns `InMemoryMemoryBackend`
- if unset, `get_memory_query_provider()` returns `CatalogMemoryQueryProvider`
- if unset, `get_memory_projector()` returns `XmlMemoryProjector`
- if unset, `get_memory_resolution_policy()` returns a simple default policy described below

### Default visible scopes

The initial host visibility rule is:

- current session scope only

Specifically:

```python
MemoryScope(kind="session", key=self.session_id)
```

The method exists so future implementations can append:

- `global:default`
- `group:<name>`
- `agent:<agent_id>`
- `use_case:<id>`

without changing callers.

---

## Agent Run State

Extend `AgentRun` with the following fields:

```python
visible_memory_scopes: tuple[MemoryScope, ...] = ()
resolved_memory_refs: tuple[MemoryRef, ...] = ()
memory_projection_requests: tuple[str, ...] = ()
```

Intent:

- `visible_memory_scopes` is computed once per run
- `resolved_memory_refs` tracks the refs known to the run
- `memory_projection_requests` is reserved for explicit behavior-driven projection requests

The first implementation may populate only `visible_memory_scopes`, but the other fields should be added now to avoid later dataclass churn.

---

## Prompt Assembly

### Injection point

Memory projection must happen in `Agent.build_context()` alongside other runtime prompt assembly, not by mutating the stored conversation log.

Relevant current seam:

- `src/agent_framework/agents/agent.py` `build_context()`

### Required prompt structure

When memory is visible to an agent, the prompt must include:

1. an `<available_memory>` catalog block
2. zero or more `<memory>` blocks for fully projected entries

The catalog is important even if full memory content is injected, because agents need a stable identifier they can pass onward.

### Example

```xml
<available_memory>
  <memory_ref id="mem://session/abc123/deck/full"
              scope="session:abc123"
              mime="application/json"
              title="Slide deck JSON"
              summary="Normalized deck payload for this session" />
</available_memory>

<memory id="mem://session/abc123/deck/full"
        scope="session:abc123"
        mime="application/json">
{
  "slides": [...]
}
</memory>
```

### Prompt instruction updates

The runtime prompt templates should be updated so agents are told:

- memory items are available by `mem://` reference
- memory refs should be passed to subagents instead of copying large content
- if a memory ref is already available, do not duplicate the full payload in tool or subagent arguments unless explicitly required

This is prompt guidance only. Runtime normalization remains authoritative.

---

## Ingress Capture and Canonicalization

### Problem

If the root input includes a huge payload, storing it in memory is not sufficient unless the runtime also replaces the canonical in-run representation. Otherwise the model and child agents will keep copying the raw blob.

### Required behavior

At or before `before_run()`, large or marked payload parameters must be moved into memory and replaced with a `MemoryRef` representation in the run parameters.

Example:

Input:

```json
{
  "deck_json": { "... very large ..." }
}
```

Canonicalized runtime parameters:

```json
{
  "deck_ref": "mem://session/abc123/deck/full"
}
```

Optionally, for compatibility, the runtime may also preserve:

```json
{
  "deck_ref": "mem://session/abc123/deck/full",
  "deck_json": "@memory-ref:mem://session/abc123/deck/full"
}
```

but the preferred design is to bind downstream prompts and schemas to explicit `*_ref` parameters rather than fake inline placeholders.

### Initial storage trigger

The default resolution policy should store a parameter when:

- the serialized value exceeds a configured size threshold, or
- the parameter name matches an explicit list such as `deck`, `deck_json`, `document`, `payload`, or `content_blob`

The threshold is required. Name-based matching is optional but useful for early adoption.

---

## Subagent Handoff Normalization

### Problem

Even if the root agent receives memory by ref, the model may still try to call subagents with huge inline payloads copied from projected memory.

### Required runtime rule

Before `host.call_subagent()` and `host.call_subagent_batch()` execute:

- inspect child parameters
- if a parameter value is already known to correspond to a memory entry, replace it with its `MemoryRef`
- if a parameter value is oversized and not yet stored, store it and replace it with a `MemoryRef`

This rewrite must happen in runtime code, not only through prompt instructions.

### Integration points

- `Agent.handle_subagent_call()`
- `Agent.handle_subagent_calls()`

### Child parameter representation

The normalized representation should be plain JSON-serializable and obvious:

```json
{
  "deck_ref": "mem://session/abc123/deck/full"
}
```

Do not invent a separate ephemeral token format if a URI string is sufficient.

---

## Tool Interface

The initial implementation must expose three read-side tools.

### `memory_get`

Purpose:

- retrieve full content for an exact memory URI

Parameters:

- `uri` string, required

Returns:

- full memory entry rendered as text or JSON
- if desired, may wrap in XML or JSON envelope, but must include the full content

Errors:

- unknown URI -> explicit error message

### `memory_list`

Purpose:

- list visible memory entries without full content

Parameters:

- `scope_kind` optional
- `scope_key` optional
- `limit` optional, default 20

Returns:

- identifier
- title
- summary
- mime type
- scope

### `memory_query`

Purpose:

- search visible memory entries by text

Parameters:

- `query` string, required
- `limit` optional, default 10

Returns:

- list of matching refs with title, summary, scope, and optional score

### Tool registration

These tools may be registered:

- as builtins on host startup, or
- lazily when the host enables memory support

They must operate against the same backend/query provider used by runtime projection.

---

## Query and Discovery Semantics

### Initial implementation

The first implementation is intentionally simple:

- list is catalog enumeration
- query is catalog search
- summaries are provided by the writer or creator of the memory entry

This means the system behaves similarly to a tool registry or file inventory at first.

### Future implementation

The public contract must also support:

- summary generation by a memory-specific model or summarizer
- semantic retrieval
- hybrid retrieval combining explicit metadata filters with embedding search

This is why query is its own protocol rather than a method on the backend alone.

### Contract stability

Future semantic retrieval must not require changes to:

- `memory_query` tool signature
- `MemoryQueryHit`
- memory XML catalog format
- subagent parameter conventions

---

## Scope Model

### Supported shape

The design must support at least these scope kinds:

- `session`
- `global`
- `group`
- `agent`
- `use_case`

### Visibility model

The runtime computes `visible_memory_scopes` for each run.

The first implementation should include:

- `session:<host.session_id>`

The design must allow later extension so a run can see:

- the current session scope
- one or more global scopes
- one or more group scopes
- an agent-specific scope
- a use-case scope

### Precedence

If multiple entries conceptually describe the same thing across scopes, selection is application-specific. The memory system itself does not merge or override entries automatically.

### Security note

This design addresses scope visibility, not authentication. Production auth rules for group or global scopes are deferred.

---

## Storage Backends

### Initial backend

- `InMemoryMemoryBackend`

### Required future backend compatibility

The protocol must support future implementations backed by:

- Redis
- PostgreSQL or another database
- file-based storage, including `.md` or `.json` files
- external service adapters

### Backend requirements

All backends must preserve:

- stable URI lookup
- scope-aware listing
- update semantics
- query-provider compatibility

### File-backed storage note

File-backed memory is expected to store metadata and content separately or infer metadata from frontmatter/file naming. The protocol should not assume everything is JSON.

---

## Events and Tracing

Add first-class tracing for memory operations.

### New event types

```python
@dataclass(frozen=True, slots=True)
class MemoryStartEvent:
    invocation: AgentInvocation
    operation: str      # "store" | "get" | "list" | "query" | "project"
    target: str | None

@dataclass(frozen=True, slots=True)
class MemoryEndEvent:
    invocation: AgentInvocation
    operation: str
    target: str | None
    status: str
```

### Audit records

Add records for:

- memory store
- memory get
- memory list
- memory query
- memory projection
- subagent parameter normalization to memory refs

Suggested shape:

```python
@dataclass(frozen=True, slots=True)
class MemoryOperationRecord:
    timestamp: str
    operation: str
    uri: str | None = None
    scope: str | None = None
    query: str | None = None
    result_count: int | None = None
    projected: bool = False
```

This tracing is required so future debugging can answer:

- why was a memory item projected?
- why did a child receive a ref instead of raw content?
- what memory was visible to an agent?

---

## Configuration

Add the following `HostConfig` fields:

```python
memory_enabled: bool = True
memory_auto_store_threshold_bytes: int = 32768
memory_builtin_tools_enabled: bool = True
memory_default_projection_mode: str = "catalog_and_selected_content"
memory_backend_kind: str = "memory"
memory_query_provider_kind: str = "catalog"
memory_projector_kind: str = "xml"
```

Suggested `.env` keys:

```ini
MEMORY_ENABLED=true
MEMORY_AUTO_STORE_THRESHOLD_BYTES=32768
MEMORY_BUILTIN_TOOLS_ENABLED=true
MEMORY_DEFAULT_PROJECTION_MODE=catalog_and_selected_content
MEMORY_BACKEND=memory
MEMORY_QUERY_PROVIDER=catalog
MEMORY_PROJECTOR=xml
```

Interpretation:

- `memory` backend kind means in-memory
- `catalog` query provider means simple exact/substring provider
- `xml` projector means the XML blocks described in this spec

The string-based config exists so future backends/providers/projectors can be selected without changing config shape.

---

## Error Handling

Rules:

- unknown memory URI -> clear error, never silent fallback
- invalid `mem://` URI -> clear validation error
- inaccessible scope -> clear error or empty result, depending on operation
- failed projection -> fail the projection step clearly; do not silently drop memory if the caller expected it
- invalid memory tool arguments -> fail with explicit validation message

Do not:

- guess alternate URIs
- silently coerce malformed refs
- rewrite arbitrary strings into valid refs through heuristics

This follows the repository’s strict contract-validation approach.

---

## Testing Requirements

### Unit tests

Add tests for:

- `InMemoryMemoryBackend.put/get/list/update`
- `CatalogMemoryQueryProvider.list/query`
- `XmlMemoryProjector.render_catalog/render_entries`
- URI validation and scope parsing
- auto-store threshold behavior
- subagent parameter normalization

### Integration tests

Add tests covering:

- root agent receives large payload -> runtime stores it and passes ref onward
- projected `<available_memory>` and `<memory>` blocks appear in built context
- `memory_list` and `memory_query` reflect visible scopes only
- child agent receives `deck_ref` instead of full deck JSON
- batch subagent calls normalize memory refs for each item

### Non-regression tests

Add tests ensuring:

- `conversation_store` behavior is unchanged
- normal small parameters are not moved to memory unnecessarily
- agents without memory usage still run exactly as before

---

## Phased Implementation Plan

### Phase 1: Core memory resource layer

Deliver:

- `MemoryScope`, `MemoryRef`, `MemoryEntry`, `MemoryQueryHit`
- `MemoryBackend`, `MemoryQueryProvider`, `MemoryProjector`
- `InMemoryMemoryBackend`
- `CatalogMemoryQueryProvider`
- `XmlMemoryProjector`
- `AgentHost` integration

### Phase 2: Tool and prompt integration

Deliver:

- `memory_get`
- `memory_list`
- `memory_query`
- prompt injection in `build_context()`
- config wiring

### Phase 3: Canonicalization and subagent handoff

Deliver:

- auto-store for large payloads
- run-parameter canonicalization
- subagent parameter normalization
- tracing for normalization decisions

### Phase 4: Future extensions

Deferred:

- persistent backends
- semantic query provider
- write/update tools
- memory-specific summarization pipeline

---

## Acceptance Criteria

This feature is complete when all of the following are true:

1. A host can store a session-scoped memory entry and retrieve it by `mem://` URI.
2. An agent run can expose a visible memory catalog in prompt XML.
3. An agent can retrieve full memory content by tool using an identifier.
4. An agent can list and query visible memory entries by tool without retrieving full content.
5. A large root payload can be canonicalized into a memory ref before subagent fan-out.
6. Subagent calls pass refs instead of re-emitting oversized payloads.
7. The design supports future scopes beyond `session` without changing the main interfaces.
8. The design supports future backends beyond in-memory without changing the main interfaces.
9. The design supports future semantic query without changing tool signatures or query result types.

---

## Implementation Notes for the First Use Case

For the slide-deck review workflow, the expected first concrete memory items are:

- `mem://session/<id>/deck/full`
- `mem://session/<id>/deck/text`
- `mem://session/<id>/deck/slide/<n>`

Only `deck/full` is mandatory in the first implementation. The others are recommended follow-ups once the base system is stable.

Derived views should be modeled as independent memory entries, not ad hoc prompt fragments. That keeps the system composable and queryable.

---

## Documentation Updates Required

Update these architecture docs after implementation:

- `docs/architecture/overview.md`
- `docs/architecture/host-orchestration.md`
- `docs/architecture/extension-points.md`
- `docs/architecture/agent-runtime.md`
- `docs/architecture/conversation-model.md`

The conversation model document must explicitly distinguish:

- conversation transcript persistence
- memory resource storage

---

## Open Questions

These are not blockers for the initial implementation, but they should remain explicit:

1. Should memory summaries be required at `put()` time, or may they be absent until a summarizer is introduced?
2. Should a projected `<memory>` block be truncated by policy when too large for a given model, or should only refs/catalog be shown?
3. Should future write/update tools be generic (`memory_put`, `memory_update`) or domain-specific wrappers around the backend?
4. Should `agent` and `use_case` scopes be enabled by config or by host code only?

The first implementation may choose pragmatic defaults as long as the public interfaces in this spec remain intact.
