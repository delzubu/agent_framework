# agent_framework — Callback Handling Reference

Use this reference when writing or modifying agents, behaviors, or host integrations that need clarification, approval, recovery, or escalation flows.

Do not treat all "need more information" cases as the same generic callback. The runtime supports multiple interaction paths, and choosing the wrong one wastes tokens or produces the wrong UX.

---

## Interaction kinds

| Kind | Use when |
|------|----------|
| `callback` | Generic callback. Mostly for older prompts or normalized intent-style kinds. |
| `callback_to_caller` | The caller should try to resolve first. |
| `request_user_input` | The answer must come from the user. |
| `request_resolution` | The answer must come from agents/tools/memory only. No host/user fallback. |

The `intent` says what type of answer is needed. The `kind` says where the runtime should try to get it.

---

## When to use which

### `callback_to_caller`

Use when:

- the parent may already know the answer
- the parent should approve, transform, or contextualize the request
- the upward escalation path adds real value

Do not use when:

- the parent is only an orchestrator / router
- caller mediation would just burn tokens

### `request_user_input`

Use when:

- only the user can answer
- a specialist child owns the clarification loop
- bubbling through parents adds no reasoning value

Typical fit:

- intake agents
- questionnaire agents
- clarification specialists

### `request_resolution`

Use when:

- the user must not be asked
- the answer should come from tools, memory, or another agent only
- unresolved state should fail loudly

Typical fit:

- system-state lookups
- memory/tool resolution
- policy-constrained internal workflows

### Generic `callback`

Use only when:

- you are targeting an older runtime surface, or
- the distinction truly does not matter for the current host

---

## Sidecar `.json` settings

The adjacent agent `.json` controls whether callbacks may route upward or directly to the host:

```json
{
  "can_query_caller": true,
  "can_use_host_interaction": true
}
```

Meanings:

- `can_query_caller`: allow caller-mediated escalation
- `can_use_host_interaction`: allow direct host/user interaction

Common specialist intake setup:

```json
{
  "can_query_caller": false,
  "can_use_host_interaction": true
}
```

That tells the runtime not to bubble through the parent.

---

## Callback policy defaults

The sidecar also supports:

```json
{
  "callback_policy": {
    "passthrough_child_callbacks": true,
    "max_bubble_hops": 1,
    "fallback_target": "user"
  }
}
```

Fields:

- `passthrough_child_callbacks`
- `max_bubble_hops`
- `fallback_target` = `"user"` or `"fail"`

Use this for orchestration agents that should forward child clarifications rather than process them themselves.

---

## Per-decision routing overrides

Decision `parameters` may carry:

- `bubble_hops`
- `max_bubble_hops`
- `fallback_target`
- `passthrough_agents`
- `resolvable_by`
- `passthrough_child_callbacks`

Example:

```json
{
  "kind": "callback_to_caller",
  "intent": "information_request",
  "message": "Need approval for retry mode.",
  "parameters": {
    "passthrough_agents": ["workflow_step", "controller"],
    "resolvable_by": ["operation_specialist"],
    "fallback_target": "user"
  }
}
```

This means:

- `workflow_step` and `controller` should be skipped
- only `operation_specialist` may resolve
- otherwise redirect to the user

---

## Recommended scenarios

### Parent can answer cheaply

Use `callback_to_caller`.

Why:

- lets the parent answer deterministically or via its own model
- keeps the resolution path in the agent hierarchy

### User must answer directly

Use `request_user_input`.

Why:

- avoids pointless bubbling
- preserves exact blocked-run identity via `prompt_id`

### User must never be asked

Use `request_resolution`.

Why:

- unresolved state stays explicit
- the workflow does not silently drift into UI interaction

### Orchestration layers should not spend tokens

Use:

- sidecar `callback_policy.passthrough_child_callbacks`
- or decision `passthrough_agents`

Why:

- controller/workflow agents are often bad places to resolve specialist clarifications

### Bounded bubbling then user fallback

Use:

- `max_bubble_hops`
- `fallback_target: "user"`

Why:

- keeps escalation available, but prevents long relay chains

---

## Parallel children

Do not assume parallel batch children can synchronously use `request_user_input` the same way sequential children can.

Current design:

- sequential child: may directly ask the user
- parallel child: clarification requests are blocked/checkpointed and resumed later

If your workflow expects real-time clarification, prefer sequential children for that step.

---

## Behavior hooks

Use `AgentBehavior.respond_to_callback(...)` when the parent can answer deterministically without running its own LLM loop.

Good fits:

- answer from caller-owned state
- enforce approval rules
- deny/rewrite a child request before it bubbles further

This is usually cheaper than letting the parent re-enter the full agent loop.

---

## Failure modes

Common mistakes:

- using generic `callback` where `request_user_input` was needed
- forgetting `can_use_host_interaction: true`
- forgetting `can_query_caller: true`
- marking useful parents as passthrough
- using `request_resolution` without any actual resolver path
- designing clarification-heavy logic inside parallel child batches

---

## Tracing clues

Look for:

- `runtime.callback_requested`
- `runtime.callback_answered`
- `runtime.interaction_opened`
- `runtime.interaction_answered`

Ask:

- which `kind` did the model emit?
- did it route to caller or host?
- did a `prompt_id` get created?
- was bubbling skipped by `passthrough_agents` / `resolvable_by`?

If the route is wrong, fix the prompt or agent contract. Do not add Python-side heuristics that reinterpret invalid structured output into a different callback kind.
