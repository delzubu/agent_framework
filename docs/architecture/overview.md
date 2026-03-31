# Architecture Overview

> This document is part of the `agent_framework` architecture reference.
> See also: [Agent Runtime](./agent-runtime.md) · [Model Abstraction](./model-abstraction.md) · [Host & Orchestration](./host-orchestration.md) · [Extension Points](./extension-points.md) · [Tracing & Evaluation](./tracing-evaluation.md) · [Interface Specifications](./interfaces.md)

---

## 1. Purpose and Scope

`agent_framework` is a generic, **LLM-agnostic**, markdown-defined agent runtime, orchestration host, tracing, and evaluation toolkit. Its purpose is to decouple agent behavior definitions from provider-specific LLM SDKs, enabling the same agent definitions to run against OpenAI, Anthropic Claude, or any custom provider through a thin `ModelDriver` protocol.

The framework currently ships with an `OpenAiModelDriver` implementation. Anthropic Claude API and custom provider drivers are first-class extension targets — the architecture is explicitly designed to accommodate them without modifying agent definitions.

**What it is:**
- A runtime for executing markdown-defined agents with a structured decision loop
- An orchestration host managing agent registries, tool execution, and multi-agent call hierarchies
- A tracing and audit system for recording LLM calls, decisions, and agent interactions
- An evaluation framework for regression testing agent behavior

**What it is not:**
- An application or domain-specific agent
- Tied to any particular LLM provider or API shape
- A framework for training or fine-tuning models

---

## 2. Design Principles

### 2.1 Markdown-as-Contract

Agents and tools are defined in Markdown files with YAML frontmatter. The LLM-facing contract — system prompts, user prompt templates, parameter declarations, capability allowlists — lives in `.md` files, not Python classes. This separates behavior specification from runtime implementation: prompts can be edited, versioned, and reviewed without touching Python code, and parameter specs are colocated with the prompts that use them.

### 2.2 Protocol-Based Abstraction

`ModelDriver` and `AgentHostProtocol` are `typing.Protocol` classes, not abstract base classes. Any object satisfying the structural method signatures works — enabling dependency injection, test fakes, and provider swapping without inheritance hierarchies. The `OpenAiModelDriver` and the `FakeModelDriver` in tests both satisfy `ModelDriver` without sharing a base class.

### 2.3 Immutable Event and State Dataclasses

All hook events, decisions, and results are `@dataclass(frozen=True, slots=True)`. Per-invocation mutable state is isolated in `AgentRun` objects (one per agent run). Immutability makes the system easier to reason about across threads and hook chains, prevents accidental mutation of event data after firing, and enables `dataclasses.replace()` for audit record construction.

### 2.4 Template Method Pattern for the Run Loop

`Agent.run()` is a `final` orchestration method that calls a sequence of overridable step methods (`build_context`, `decide`, `dispatch_decision`, `should_continue`, `before_iteration`, `after_iteration`, `resolve_runtime_decision`, `complete_without_result`). Subclasses extend behavior at specific steps without re-implementing the full loop. This is the classic Template Method pattern applied to an agent decision loop.

### 2.5 Hook-Driven Extensibility

Eight `SequentialHook` instances on `Agent` (pre/post for agent, tool, subagent, model) plus `AgentBehavior` classes provide non-invasive extension without modifying core loop logic. Behaviors are loaded dynamically from Python modules at agent load time. Hook callbacks receive typed event objects and can return decision objects that control execution flow — short-circuiting, injecting messages, modifying inputs, or requesting loop continuation.

### 2.6 Hierarchical Agent Orchestration

Agents invoke other agents as subagents via the host. The call tree is explicit, not emergent: each agent has a declared `allowed_child_agents` allowlist, and `CallContext` objects track each call edge with correlation IDs. Subagents can be invoked synchronously (`call_subagent`) or asynchronously via a shared thread pool (`call_subagent_async`).

### 2.7 Unified Callback Protocol

All escalations from an agent to its caller flow through a single `callback` decision kind with six typed intents: `information_request`, `proposal_review`, `execution_recovery`, `delegation_return`, `policy_or_approval`, `guardrail_trip`. This creates a clean, uniform boundary between agent execution and human/caller interaction. All six intent strings normalize to `kind="callback"`; the caller resolves each based on the intent value.

### 2.8 Prompt Augmentation via Fragments

Rather than rebuilding the full user prompt each iteration, tool results, subagent results, and callback responses are accumulated as typed XML fragments in `AgentRun.prompt_fragments` and wrapped in `<augmentations>` before each model call. `_upsert_prompt_fragment()` applies replace-by-tag-name semantics, so the latest tool result replaces the previous one for the same tool — avoiding context bloat. Behaviors can replace specific fragment tags or append new ones via `AgentEndHookDecision`.

### 2.9 Immutable Audit Records via Replace

`InMemoryAuditTracer` uses `dataclasses.replace()` to progressively build up frozen `AgentCallAuditRecord` objects across LLM requests, responses, decisions, callbacks, and events. Records are never directly mutated. When a run completes, the final record is serialized as a JSONL line and appended to the output file — an append-only, immutable audit trail.

### 2.10 Lazy Loading with Registry Caching

Agents and tools are loaded from the filesystem on first access and cached by ID and source path. Agent resolution follows a priority chain: registry → explicit path → sibling path → configured agent directory. This enables relative agent discovery (an agent can reference another by a path relative to itself) while maintaining a central cache.

---

## 3. Key Design Decisions

### Why Markdown Definitions Instead of Python Classes for Agents

Separating the LLM-facing contract from runtime code has several benefits: prompts and parameter specs can be edited without modifying Python; multiple agent variants can share the same runtime logic with different prompts; the contract is readable and reviewable as text; and non-programmers can author or modify agents. The sidecar `.json` file handles runtime configuration (model, temperature, behaviors) that doesn't belong in the prompt contract.

### Why `typing.Protocol` Over `ABC`

Structural subtyping enables test fakes and alternative implementations without inheritance. Any object with the right method signatures satisfies the protocol — a `FakeModelDriver` in tests doesn't need to import or inherit from a framework class. This also prevents the framework from leaking into provider implementations and avoids the diamond inheritance problems that ABC hierarchies accumulate.

### Why Frozen Dataclasses for Events and Decisions

Events fired into hook callbacks must not be mutated after firing — a hook that modifies an event would create non-obvious side effects on other hooks. `frozen=True` makes this a runtime error rather than a silent bug. `slots=True` reduces memory overhead for the many small dataclasses created per agent run. Hashability (from frozen) is a bonus for caching and set operations.

### Why Sequential Hooks Instead of Pub/Sub

Ordered, synchronous callback lists are simple to reason about. Pre-hooks that return `final_result` short-circuit cleanly — the runtime inspects the return value and stops processing. Pub/sub would add indirection and subscription management without benefit given the synchronous, single-threaded execution model of each agent run. The `SequentialHook` implementation is intentionally minimal: `+=`, `-=`, and iteration.

### Why Sidecar JSON for Runtime Metadata

YAML frontmatter holds the agent contract — parameter declarations, tool allowlists, subagent allowlists — things callers need to know before invocation. The sidecar `.json` holds runtime configuration — provider, model, temperature, behaviors — implementation details that callers don't need. This keeps the public contract clean and allows runtime configuration to change without modifying the prompt definition.

### Why `dataclasses.replace()` for Audit Records

Audit records must be trustworthy: a record must not be silently modified after the fact. Building them immutably via `replace()` — adding LLM request, then response, then decision, then callbacks — gives the appearance of progressive construction without allowing direct mutation. The JSONL output is append-only and never modified after writing.

### Why `CallContext` Tracking

Explicit call edges with correlation IDs enable tracing of complex multi-agent conversations where the same agent might be called from multiple callers simultaneously. Status transitions (`open` → `resolved`) make it clear when a callback or subagent call completes. The context stack in `AgentRun.contexts` provides a complete record of all call edges from a single run.

### Why `_normalize_decision_capabilities()`

LLM models frequently confuse tool names and subagent IDs, placing one in the wrong slot. Rather than failing with an unhelpful error, the runtime applies six heuristic repair cases to recover gracefully. This is a pragmatic acknowledgment that LLM outputs are probabilistic — the framework prefers resilience over strictness at the decision-parsing boundary.

---

## 4. System Context

```
┌─────────────────────────────────────────────────────────────────────┐
│                         agent_framework                              │
│                                                                      │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │  config  │  │     host     │  │    agents/   │  │   model    │ │
│  │HostConfig│  │  AgentHost   │  │    Agent     │  │ ModelDriver│ │
│  └──────────┘  └──────────────┘  └──────────────┘  └────────────┘ │
│                                                                      │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │   tool   │  │ audit_trace  │  │  evaluator   │  │  __main__  │ │
│  │   Tool   │  │ AuditTracer  │  │  Evaluators  │  │    CLI     │ │
│  └──────────┘  └──────────────┘  └──────────────┘  └────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
          │                │                │               │
          ▼                ▼                ▼               ▼
   LLM Providers      Filesystem       Console I/O     Eval Harness
   (OpenAI, Claude,   (.md, .json,     (input_reader,  (RecordingHost)
    custom)           .env, .jsonl)    output_writer)
```

**LLM Providers (external):**
- OpenAI API — implemented via `OpenAiModelDriver`, calls `client.responses.create()`
- Anthropic Claude API — planned, same `ModelDriver` protocol
- Custom APIs — extension point, any implementation of `ModelDriver`

**Filesystem (local):**
- Agent `.md` definitions (YAML frontmatter + system prompt + user prompt template)
- Optional sidecar `.json` files (runtime metadata per agent)
- Tool `.md` + `.py` pairs (contract + implementation)
- Optional `behaviors/` directory (Python modules for `AgentBehavior` implementations)
- `.env` configuration file (API keys, directories, model settings)
- `logs/` directory (JSONL audit trace output, LLM request/response logs)
- Evaluation XML/JSON input files and JSON output artifacts

**Console I/O:**
- `input_reader: Callable[[str], str]` — defaults to Python `input()`, configurable
- `output_writer: Callable[[str], None]` — defaults to Python `print()`, configurable
- Both are injected into `AgentHost` at construction time

**Python Runtime:**
- Dynamic module loading for tool implementations (`importlib.util.spec_from_file_location`)
- Dynamic module loading for behavior extensions (same mechanism)

---

## 5. Package Structure

The framework has a two-layer structure:

### Top-Level Package (`src/agent_framework/`)

Infrastructure layer — entry points, orchestration, and cross-cutting concerns:

| Module | Role |
|--------|------|
| `__init__.py` | Public API surface — re-exports key classes |
| `__main__.py` | CLI entry point — argument parsing, run/evaluate dispatch |
| `agent.py` | Compatibility facade — re-exports everything from `agents/` |
| `config.py` | Configuration loading — `HostConfig` dataclass, `.env` parser |
| `host.py` | `AgentHost` — central orchestrator |
| `model.py` | `ModelDriver` protocol, `ModelContext`, `OpenAiModelDriver`, system prompt templates |
| `tool.py` | `Tool` base class, `ToolDefinition`, markdown-based tool loading |
| `audit_trace.py` | `InMemoryAuditTracer`, `AgentCallAuditRecord` — immutable JSONL audit trail |
| `evaluator.py` | `AgentPromptEvaluator`, `OpenAiConversationEvaluator`, `RecordingAgentHost` |
| `llm_trace_logging.py` | `LlmTraceLogger` — provider-level request/response logging |
| `trace_logging.py` | `TraceLoggingBehavior` — console lifecycle tracing as an `AgentBehavior` |

### Agents Subpackage (`src/agent_framework/agents/`)

Agent runtime layer — one class per file discipline. Each file exports exactly one public class:

**Core classes:**
- `agent.py` — `Agent` (1289 lines — the complete decision loop)
- `agent_run.py` — `AgentRun` (per-invocation mutable state)
- `agent_decision.py` — `AgentDecision` (normalized model decision)
- `agent_result.py` — `AgentResult` (run outcome)
- `agent_behavior.py` — `AgentBehavior` (behavior extension base)
- `sequential_hook.py` — `SequentialHook` (callback collection)
- `call_context.py` — `CallContext` (call edge tracking)
- `helpers.py` — shared utility functions (not a class)

**Contract types:**
- `agent_parameter.py` — `AgentParameter`
- `agent_invocation.py` — `AgentInvocation`
- `agent_host_protocol.py` — `AgentHostProtocol` (Protocol)

**Events (8):** `AgentStartEvent`, `AgentEndEvent`, `ModelStartEvent`, `ModelEndEvent`, `ToolStartEvent`, `ToolEndEvent`, `SubagentStartEvent`, `SubagentEndEvent`

**Hook decisions (4):** `AgentHookDecision`, `AgentEndHookDecision`, `ToolHookDecision`, `SubagentHookDecision`

**System prompt templates (4 `.md` files):** `system.md`, `system.decision.md`, `system.text.md`, `system.json_object.md`

---

## 6. Dependency Structure

The package has a strict, acyclic dependency structure:

```
__main__  ──────────────────────────────────┐
    │                                        │
    ▼                                        ▼
  host  ──────────────────────────────► evaluator
    │                                        │
    ├──► config (no internal deps)           │
    │                                        │
    ├──► model (no internal deps)  ◄─────────┤
    │        ▲                               │
    ├──► agents/ ─────────────────────────────┘
    │        │
    ├──► tool ─► model (ToolDefinition in payloads)
    │
    ├──► audit_trace ──► agents/ (records AgentDecision, events)
    │
    ├──► llm_trace_logging ──► model (ProviderRequestTrace/Response)
    │
    └──► trace_logging ──► agents/ (AgentBehavior, events)
```

No circular dependencies. The `model` and `config` modules are leaves — they depend on nothing else in the package. The `agents/` subpackage depends only on `model` (for `ModelContext`, `ModelResponse`, `CapabilityDefinition`). The `host` aggregates everything.

---

## 7. Diagrams

Architecture diagrams are in `docs/architecture/diagrams/`:

| Diagram | Description |
|---------|-------------|
| [`system-context.drawio`](./diagrams/system-context.drawio) | System boundary and external interactions |
| [`component-overview.drawio`](./diagrams/component-overview.drawio) | Package-level components and dependencies |
| [`agent-decision-loop.drawio`](./diagrams/agent-decision-loop.drawio) | Complete agent run loop flowchart |
| [`class-relationships.drawio`](./diagrams/class-relationships.drawio) | Class relationships and key fields |
| [`decision-dispatch.drawio`](./diagrams/decision-dispatch.drawio) | Decision kind routing to handlers |
| [`callback-flow.drawio`](./diagrams/callback-flow.drawio) | Callback intent and resolution chain |
| [`hook-event-lifecycle.drawio`](./diagrams/hook-event-lifecycle.drawio) | Hook firing order across the agent lifecycle |
| [`prompt-assembly.drawio`](./diagrams/prompt-assembly.drawio) | System and user prompt assembly pipeline |
| [`multi-agent-orchestration.drawio`](./diagrams/multi-agent-orchestration.drawio) | Hierarchical agent call flow |
| [`model-driver-abstraction.drawio`](./diagrams/model-driver-abstraction.drawio) | ModelDriver protocol and implementations |
