# Subagent User Interaction Design

**Date:** 2026-04-22
**Status:** Proposed — ready for implementation
**Target area:** `src/agent_framework/`

---

## Overview

This document specifies a framework-level redesign of how agents and sub-agents request user input.

The current runtime treats callback-style interaction as a mostly caller-mediated flow. In practice, the implementation already allows some direct child-to-user interaction for sequential sub-agents, while parallel sub-agents are blocked and resumed later. This partial behavior is useful, but it is not modeled explicitly enough in the host, tracing, or transport layers.

The immediate driver is a multi-agent deck-review workflow where a specialist intake sub-agent should be able to own its own clarification loop. In the current design, that is only partially supported and is difficult to reason about when the host is not a simple single-root console session.

The core design decision is:

- sequential sub-agents may directly request user input as a first-class runtime behavior
- parallel sub-agents may not synchronously block on user input
- user-input routing must be based on an explicit interaction identifier, not on guessing the active agent for a session

This change is primarily about runtime interaction semantics and transport identity. It is not a change to model-output contract leniency. Decision JSON must remain strict.

---

## Problem Statement

### Current behavior is inconsistent

The current codebase already behaves differently depending on execution mode:

- In sequential child execution, a sub-agent can fall through to host user interaction.
- In parallel child execution, a sub-agent cannot synchronously block and instead returns a blocked result so the batch orchestrator can resume it later.

This is implemented today, but the behavior is not explicit in the host API or the transport model.

### Host routing is under-specified

The console host effectively assumes a single active blocking prompt. That is manageable.

The web/evaluator stack is already better: `WebUserCommunication` assigns each pending prompt a `prompt_id`, and the evaluator returns user input by `prompt_id`. However, the host does not expose enough provenance for the UI to know:

- which agent asked
- which run is blocked
- whether the prompt is from the root agent, a sequential child, or a caller-mediated escalation

### Caller escalation and direct user interaction are conflated

Today, `callback` covers at least two distinct behaviors:

1. Ask the caller agent to resolve something
2. Ask the host/user directly for more information

Those have different routing, tracing, UI, and retry semantics. They should not share only one implicit transport path.

---

## Goals

- Make direct user interaction by sequential sub-agents a supported, explicit runtime feature.
- Preserve the current rule that parallel batch children cannot synchronously block on user input.
- Introduce a host-level interaction model with stable identifiers and agent/run provenance.
- Route all interactive replies by `prompt_id` rather than by implicit session ownership.
- Preserve existing console usability while making web/HTTP routing correct and auditable.
- Keep callback escalation to caller available when that is the right semantic behavior.
- Produce a design that another agentic system can implement without relying on this conversation.

## Non-goals

- No heuristic repair of invalid model JSON
- No support in this scope for multiple simultaneously active blocking prompts in a single console session
- No support in this scope for fully concurrent direct user interaction from parallel children
- No redesign of the evaluator UI beyond the metadata required to display and route prompts correctly
- No changes to provider drivers beyond trace/provenance fields needed for observability

---

## Current Relevant Code

This section describes the current implementation points that the new design must modify.

### `Agent.handle_callback()`

File: `src/agent_framework/agents/agent.py`

Current behavior:

- If `run.in_parallel_batch` is true:
  - the child cannot block synchronously
  - the host checkpoint is saved
  - the result becomes `AgentResult(status="blocked", ...)`
- Otherwise:
  - if there is a non-host caller and `can_query_caller` is true, callback is routed to `host.resolve_callback(...)`
  - else, if `can_use_host_interaction` is true, the child directly calls `host.request_user_input(...)`

Implication:

- direct child-to-user interaction already exists in the sequential case
- parallel children already require resume-based orchestration

### `AgentHost.call_subagent_batch()`

File: `src/agent_framework/host.py`

Current behavior:

- blocked batch children are resumed by a callback-resolution loop
- in sequential batch mode, children do not return `blocked`; they resolve callbacks synchronously

Implication:

- the batch orchestration logic already encodes a distinction between sequential and parallel callback behavior

### `AgentHost.resolve_callback()` and `AgentHost.request_user_input()`

File: `src/agent_framework/host.py`

Current behavior:

- `resolve_callback()` tries to resolve a callback via caller agent behavior or a caller agent run, then falls back to `user_comm.read_user_input(...)`
- `request_user_input()` directly proxies to `user_comm.read_user_input(...)`

Problem:

- neither method creates a first-class interaction object
- routing/provenance is implicit and partly encoded in prompt text

### `WebUserCommunication`

File: `src/agent_framework/web_communication.py`

Current behavior:

- every wait gets a `prompt_id`
- `submit_user_input(text, prompt_id=...)` resolves only the matching wait

Implication:

- the low-level transport identity primitive already exists
- the host should build on it rather than inventing a separate routing mechanism

---

## Design Summary

Introduce a first-class host interaction model and split interactive semantics into two runtime concepts:

1. **Caller callback**
   - the callee wants its caller to resolve or decide something
   - may be satisfied by the caller agent, the caller behavior, or ultimately by the host/user

2. **Direct user input request**
   - the current agent wants to ask the user directly
   - valid for root agents and sequential sub-agents
   - not valid as a synchronous action for parallel batch children

The framework should continue to use the existing `callback` decision kind for now, but the host/runtime handling must become explicit enough that these two paths are no longer conflated internally.

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Direct child interaction | Allow for sequential sub-agents | Matches existing useful behavior and avoids forcing parent relay loops |
| Parallel child interaction | Keep non-blocking only | Avoids multi-prompt concurrency and ambiguous resume ownership |
| Routing identity | `prompt_id` | Already exists in web transport and is the correct stable key |
| Interaction provenance | Include `session_id`, `run_id`, `agent_id`, `caller_id`, `parent_run_id`, `intent` | UI and traces must identify exactly who asked and who is waiting |
| Console UX | Single active blocking prompt, annotated with provenance | Low complexity, no behavior regression |
| Web/HTTP UX | Route replies by `prompt_id`, not by active root | Supports non-single-root sessions safely |
| Model contract | Keep `callback` decision strict | No JSON-contract weakening |

---

## New Runtime Model

### New dataclass: `PendingInteraction`

Add to a new module, for example:

`src/agent_framework/interaction.py`

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PendingInteraction:
    prompt_id: str
    session_id: str | None
    prompt: str
    intent: str
    run_id: str
    agent_id: str
    caller_id: str | None
    parent_run_id: str | None
    interaction_kind: str
    blocking: bool
    created_at: datetime
```

### `interaction_kind`

Allowed initial values:

- `direct_user_input`
- `callback_to_caller`
- `parallel_callback_resume`

This is not a model-facing field. It is host/runtime metadata.

### Host registry

`AgentHost` should maintain:

```python
self.pending_interactions: dict[str, PendingInteraction]
```

Lifecycle:

- register before prompting the user
- remove when answered, cancelled, or timed out

This registry becomes the source of truth for interactive routing.

---

## Host API Changes

### Replace plain `request_user_input(prompt)` with structured registration

Current:

```python
def request_user_input(self, prompt: str) -> str:
```

Target:

```python
def request_user_input(
    self,
    *,
    prompt: str,
    intent: str,
    run_id: str,
    agent_id: str,
    caller_id: str | None,
    parent_run_id: str | None,
    interaction_kind: str = "direct_user_input",
) -> str:
```

Behavior:

1. Create a `PendingInteraction`
2. Pass prompt and metadata to `user_comm.read_user_input(...)`
3. Resolve only the matching prompt
4. Remove the interaction on completion

### Add explicit interaction registration helper

```python
def open_interaction(...) -> PendingInteraction: ...
def close_interaction(prompt_id: str) -> None: ...
def get_pending_interaction(prompt_id: str) -> PendingInteraction | None: ...
```

### Keep `resolve_callback()` but make it provenance-aware

Current:

```python
def resolve_callback(self, *, caller_id: str, callee: Agent, prompt: str) -> str:
```

Target:

```python
def resolve_callback(
    self,
    *,
    caller_id: str,
    callee: Agent,
    prompt: str,
    intent: str,
    run_id: str,
    parent_run_id: str | None,
) -> str:
```

Behavior:

- if caller agent can answer, use that
- if caller agent needs to run, run it
- if caller-side resolution falls back to user input, register a `PendingInteraction` with `interaction_kind="callback_to_caller"`

---

## UserCommunication Changes

### Extend the user communication contract

Current:

```python
async def read_user_input(self, prompt: str = "") -> str | None:
```

Target:

```python
async def read_user_input(
    self,
    prompt: str = "",
    *,
    prompt_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> str | None:
```

Requirements:

- existing implementations may ignore `metadata`, but must accept it
- if `prompt_id` is omitted, implementation may generate one
- the host should prefer generating `prompt_id` itself so the same id exists in host state and transport state

### Console implementation

`ConsoleUserCommunication`:

- accept `metadata`
- render provenance into the prompt text
- still behave as a single blocking prompt transport

Suggested format:

```text
[deck_review_intake <- deck_reviewer | information_request]
Who is the primary audience for this deck?
> 
```

### Web implementation

`WebUserCommunication`:

- continue to emit `prompt_id`
- include host-provided metadata in the outbox event

Suggested outbox payload fields:

- `prompt_id`
- `session_id`
- `prompt`
- `agent_id`
- `caller_id`
- `run_id`
- `parent_run_id`
- `intent`
- `interaction_kind`

This lets the UI show exactly which agent is asking and lets the server route the answer correctly.

---

## Agent Runtime Changes

### Update `Agent.handle_callback()`

Implementation policy:

1. If `run.in_parallel_batch`:
   - preserve current blocked/checkpoint behavior
   - return `AgentResult(status="blocked", ...)`
   - include enough metadata in the blocked payload to recover `intent` and `prompt`

2. Else if `caller_id` is non-host and `can_query_caller`:
   - call `host.resolve_callback(...)` with `intent`, `run_id`, and `parent_run_id`

3. Else:
   - call `host.request_user_input(...)` with structured provenance

No change should weaken decision validation.

### Keep model contract stable in this scope

Do not introduce a new model-facing decision kind yet.

The runtime may continue to interpret:

```json
{
  "kind": "callback",
  "intent": "information_request",
  "message": "...",
  "parameters": {...}
}
```

But the runtime handling must become explicit and provenance-aware.

---

## Batch Semantics

### Sequential `call_subagents`

Sequential children may directly interact with the user.

Reason:

- they execute one at a time
- blocking semantics are clear
- there is a single waiting branch at that point in execution

### Parallel `call_subagents`

Parallel children must continue to avoid synchronous direct user interaction.

Behavior:

- if a parallel child emits `callback`, it returns `status="blocked"`
- the batch orchestrator resolves and resumes it
- the user must never receive multiple simultaneous direct blocking prompts from different children in this scope

This rule should remain explicit in prompts and docs.

---

## Tracing and Observability

### New runtime events

Add or extend trace events so interaction provenance is visible:

- `runtime.interaction_opened`
- `runtime.interaction_answered`
- `runtime.interaction_cancelled`

Payload fields:

- `prompt_id`
- `session_id`
- `run_id`
- `agent_id`
- `caller_id`
- `parent_run_id`
- `intent`
- `interaction_kind`
- `blocking`

### Existing callback audit events

Current callback trace calls should be updated so they carry the same prompt identity where applicable.

The goal is:

- a trace consumer can always identify exactly which agent asked the question
- a trace consumer can correlate the answer with the waiting run

---

## Evaluator and Web Host Requirements

### Server behavior

The evaluator/web host already accepts `prompt_id` on user input submission. That must remain the authoritative routing key.

Required changes:

- preserve and forward the new metadata fields in outbound prompt events
- do not infer the target run from session alone
- do not assume only the root agent can ask the user something

### UI behavior

The UI should display:

- the prompt text
- the requesting `agent_id`
- optionally `caller_id`
- the `intent`

The UI does not need a deeper scheduler in this scope. It only needs enough metadata to explain which sub-agent is blocked.

---

## Compatibility Strategy

This is an internal runtime change. The design should preserve current root-agent interaction behavior.

Compatibility requirements:

- existing root-agent prompts still work
- existing sequential child callbacks still work, but become better identified and traced
- existing parallel child resume logic continues to work
- existing web evaluator `prompt_id` reply flow remains valid

This scope does not require backward compatibility for private host internals if a cleaner API is needed.

---

## Documentation Updates Required

The implementation must update:

- architecture docs for host/callback flow
- agent authoring guidance for callback behavior
- any system prompt templates that currently overstate that children must always bubble user interaction upward

The guidance should become:

- sequential sub-agents may ask the user directly when they own the clarification
- parallel children must not expect synchronous direct user interaction
- use caller escalation when the caller must review, decide, or synthesize the response

---

## Suggested Implementation Phases

### Phase 1: Host interaction model

- add `PendingInteraction`
- add host registry and open/close helpers
- update `request_user_input()` to use structured metadata
- update `resolve_callback()` to carry provenance

### Phase 2: UserCommunication plumbing

- extend protocol and concrete implementations
- preserve `prompt_id` end to end
- add metadata to web outbox messages

### Phase 3: Agent callback integration

- update `Agent.handle_callback()`
- preserve blocked parallel semantics
- attach trace events

### Phase 4: Evaluator/web surface

- expose prompt provenance in websocket payloads
- keep reply path keyed by `prompt_id`

### Phase 5: Docs and tests

- update system prompt guidance
- add unit and integration tests

---

## Acceptance Criteria

The implementation is complete when all of the following are true:

1. A sequential sub-agent can emit a callback and directly receive user input without forcing the parent to relay it.
2. The host records a `PendingInteraction` with `prompt_id`, `run_id`, `agent_id`, `caller_id`, and `intent`.
3. The console displays the agent provenance for the prompt.
4. The web/evaluator transport includes the same provenance metadata alongside `prompt_id`.
5. A user reply is routed by `prompt_id` to the correct waiting run, even if the session has multiple past or future root runs.
6. Parallel batch children still do not synchronously block on direct user input.
7. Trace logs show exactly which agent asked and which run consumed the answer.
8. Existing root-agent callback flows still work.

---

## Test Plan

### Unit tests

- `Agent.handle_callback()` sequential path calls `host.request_user_input(...)` with correct provenance
- `Agent.handle_callback()` caller-resolution path calls `host.resolve_callback(...)` with correct provenance
- parallel child callback returns blocked result and saves checkpoint
- host interaction registry opens and closes entries correctly

### Integration tests

- sequential child callback in console flow
- sequential child callback in web/evaluator flow with `prompt_id`
- user reply resolves the exact pending interaction by `prompt_id`
- two prompts created in the same session at different times do not cross-resolve
- parallel child callback remains blocked and resumes via batch loop

### Trace tests

- `interaction_opened` contains prompt provenance
- `interaction_answered` correlates to the same `prompt_id`

---

## Risks

### Risk: prompt provenance leaks into model context

Mitigation:

- keep provenance in transport and trace metadata
- only include human-readable agent labeling in console prompt text where needed

### Risk: accidental support for concurrent interactive fan-out

Mitigation:

- keep explicit rule that parallel children do not directly block on user input
- reject or checkpoint that path rather than silently allowing it

### Risk: callback and direct-input semantics remain confusing

Mitigation:

- document the distinction clearly
- keep runtime metadata explicit even if the model-facing decision kind stays `callback`

---

## Open Follow-ups

These are out of scope for the first implementation but should be kept in mind:

- introduce a separate model-facing decision kind for direct user input if needed later
- support multiple simultaneous interactive branches in rich web hosts
- add cancellation and timeout semantics for pending interactions
- persist pending interactions across process restarts if the host becomes distributed

---

## Recommended Initial Scope

Implement the full host interaction model and transport provenance now, but keep the behavioral policy conservative:

- sequential sub-agents may directly ask the user
- parallel sub-agents remain checkpoint/resume only
- root agents continue to work as before

That gives the framework a clear and correct interaction model without taking on a multi-prompt scheduler in the same change.
