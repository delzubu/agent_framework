# Handling Callbacks

This guide is for plugin developers and advanced agent authors who need to design clarification, approval, recovery, and escalation flows on top of `agent_framework`.

It focuses on one practical question:

> When an agent needs help, who should answer, and how should the request move through the runtime?

The answer is no longer "always emit `callback` and let the framework figure it out". The runtime now supports multiple interaction paths, explicit host-side interaction tracking, and bubbling controls so orchestration agents do not waste tokens relaying specialist questions.

---

## 1. The callback family

The framework now distinguishes four interaction decisions:

| Kind | Use when |
|------|----------|
| `callback` | Generic callback. Mostly for older prompts or intent-style kinds normalized from `information_request`, `proposal_review`, etc. |
| `callback_to_caller` | The caller should try to resolve first. |
| `request_user_input` | The agent needs a direct answer from the user. |
| `request_resolution` | The request must be resolved by agents/tools/memory only. No host/user fallback. |

All four still carry an `intent`, typically one of:

- `information_request`
- `proposal_review`
- `execution_recovery`
- `delegation_return`
- `policy_or_approval`
- `guardrail_trip`

The `intent` says what kind of answer is needed. The `kind` says where the runtime should try to get that answer.

That distinction matters. If you use the wrong `kind`, the runtime may spend tokens on agents that have nothing useful to add, or may ask the user when the workflow should have failed instead.

---

## 2. How the runtime handles callbacks

### 2.1 `callback_to_caller`

Flow:

1. child emits `callback_to_caller`
2. runtime checks whether the current caller should actually process it
3. if yes, the host tries:
   - `respond_to_callback(...)` on caller behaviors
   - then running the caller agent
4. if unresolved, the request may bubble upward again
5. if the chain reaches the host and user fallback is allowed, the host asks the user
6. the answer is injected back into the original requesting run

Use this when:

- the parent may already know the answer
- the parent should decide whether to escalate
- the parent may transform the request before passing it on
- caller-owned state or behavior can answer deterministically

Good examples:

- a child asks its parent for a workflow-specific identifier
- a specialist asks an orchestrator whether a retry is allowed
- a subagent asks for policy approval that should first be checked against caller-owned rules

### 2.2 `request_user_input`

Flow:

1. child emits `request_user_input`
2. host opens a `PendingInteraction` with a stable `prompt_id`
3. provenance is recorded:
   - `session_id`
   - `run_id`
   - `agent_id`
   - `caller_id`
   - `parent_run_id`
   - `intent`
   - `interaction_kind`
4. host asks the user directly
5. the exact blocked run resumes with the answer

Use this when:

- only the user can answer
- bubbling through parent agents would just waste tokens
- a specialist child owns the clarification loop
- you want web/HTTP transports to resume the exact child run rather than a guessed "active agent"

Good examples:

- intake questionnaire agent
- clarification child for missing business context
- specialist review child asking the customer to choose between options

### 2.3 `request_resolution`

Flow:

1. child emits `request_resolution`
2. runtime allows caller-side resolution and tool/agent/memory lookup
3. runtime does not allow host/user fallback
4. if unresolved, the run fails or takes another explicit path

Use this when:

- the user must never be asked
- the answer should come only from internal state, tools, or agent hierarchy
- unresolved state is a real workflow failure

Good examples:

- resolving an internal customer id from system state
- looking up cached context in memory
- deterministic cross-agent coordination that must not escape to UI

### 2.4 Generic `callback`

Use this only when:

- you are working with a prompt or runtime surface that still speaks in generic callback terms, or
- the route genuinely does not matter for the current host

Do not use plain `callback` as a habit when the real workflow needs one of the more specific forms above.

---

## 3. Host behavior and interaction identity

The host is no longer treated as a single-root prompt loop. It tracks pending interactions explicitly.

Important consequences:

- direct child-to-user interaction is supported for sequential child runs
- user replies are routed by `prompt_id`, not by guessing the active agent for the session
- web/evaluator transports can resume the exact blocked child
- callback passthrough can climb the run lineage without running every intermediate parent

This is especially important for hosts that may have multiple active branches in one session. Console mode is simple because there is usually only one blocking prompt at a time. HTTP/web hosts are not simple, so explicit interaction identity is mandatory.

---

## 4. Agent configuration knobs

Two basic booleans still matter:

| Setting | Meaning |
|---------|---------|
| `can_query_caller` | This agent may send caller-mediated callbacks upward. |
| `can_use_host_interaction` | This agent may ask the host/user directly. |

These live in the adjacent agent sidecar JSON file.

Example:

```json
{
  "can_query_caller": false,
  "can_use_host_interaction": true
}
```

This is the common setup for a specialist intake agent that should ask the user directly instead of bubbling through its caller.

---

## 5. Callback policy defaults

The adjacent sidecar JSON also supports `callback_policy`, which defines default bubbling rules for child callbacks handled by this agent.

Example:

```json
{
  "callback_policy": {
    "passthrough_child_callbacks": true,
    "max_bubble_hops": 1,
    "fallback_target": "user"
  }
}
```

Supported keys:

| Key | Meaning |
|-----|---------|
| `passthrough_child_callbacks` | Forward child callbacks upward immediately instead of spending a model turn on this agent. |
| `max_bubble_hops` | Maximum number of upward caller hops before the request is redirected. |
| `fallback_target` | What to do after bubbling is exhausted: `"user"` or `"fail"`. |

When to use these defaults:

- orchestration agents that should not reason about specialist clarifications
- controller/workflow agents that mainly route and supervise
- workflows where user fallback after a small number of hops is preferable to repeated caller mediation

When not to use them:

- parent agents that genuinely hold useful context
- approval/policy agents that are expected to inspect or rewrite child requests

---

## 6. Per-decision routing overrides

Callback decisions may also carry routing hints in `parameters`.

Supported fields:

| Field | Meaning |
|-------|---------|
| `bubble_hops` | Current hop count. Usually maintained by the runtime, not handwritten. |
| `max_bubble_hops` | Per-callback hop limit override. |
| `fallback_target` | Per-callback fallback target: `"user"` or `"fail"`. |
| `passthrough_agents` | Agent ids that must be skipped instead of asked. |
| `resolvable_by` | Agent ids that are allowed to resolve this specific callback. |
| `passthrough_child_callbacks` | Per-callback override of the agent default. |

Example:

```json
{
  "kind": "callback_to_caller",
  "intent": "information_request",
  "message": "Need approval for retry mode.",
  "parameters": {
    "max_bubble_hops": 1,
    "fallback_target": "user",
    "passthrough_agents": ["workflow_step", "controller"],
    "resolvable_by": ["operation_specialist"]
  }
}
```

This means:

- `workflow_step` and `controller` must not process the callback themselves
- only `operation_specialist` is allowed to resolve it
- if the workflow cannot resolve it within the allowed route, redirect to the user

Use this when the routing policy depends on the specific request, not just on the agent that emitted it.

---

## 7. Recommended patterns by scenario

### Scenario A: caller likely knows the answer

Use:

- `callback_to_caller`
- `can_query_caller: true`
- usually `can_use_host_interaction: true`

Why:

- the parent may answer immediately
- if it cannot, it may escalate with additional context
- the answer path back to the child remains natural

What can go wrong:

- if the parent is just an orchestrator, you waste tokens
- if the prompt does not say when to stop escalating, parent agents may add low-value turns

### Scenario B: only the user can answer

Use:

- `request_user_input`
- `can_use_host_interaction: true`
- usually `can_query_caller: false`

Why:

- avoids spending tokens on parent relay agents
- preserves exact requester identity through `prompt_id`
- cleaner UX for intake and clarification specialists

What can go wrong:

- if used inside parallel child execution, the request cannot synchronously block in the same way
- if `can_use_host_interaction` is false, the agent will fail instead of asking

### Scenario C: must be resolved by the system, not the user

Use:

- `request_resolution`
- `can_query_caller: true` only if parent-side resolution is valid
- `fallback_target: "fail"` where appropriate

Why:

- keeps sensitive or deterministic workflows out of UI
- makes unresolved state explicit

What can go wrong:

- if the prompt is vague, the model may choose a softer callback kind instead
- if no resolver exists, the run fails as designed

### Scenario D: orchestration layers should not process child callbacks

Use:

- parent sidecar `callback_policy.passthrough_child_callbacks: true`
- or per-decision `passthrough_agents`
- optionally `max_bubble_hops`

Why:

- controllers and workflow wrappers often add no value to specialist clarifications
- fast-forwarding to UI saves tokens and complexity

Example chain:

- `controller`
- `workflow_step`
- `operation_specialist`
- `step_specialist`

If `step_specialist` asks a question that only `operation_specialist` or the user can answer, set:

- `resolvable_by: ["operation_specialist"]`
- `passthrough_agents: ["workflow_step", "controller"]`

Now the host can skip those layers without invoking their model.

What can go wrong:

- if you mark a parent as passthrough even though it owns useful context, you lose an opportunity to answer cheaply
- if the run lineage is not registered correctly, passthrough cannot climb the chain

### Scenario E: bounded bubbling, then UI

Use:

- `max_bubble_hops`
- `fallback_target: "user"`

Why:

- useful when some caller escalation is helpful, but only up to a point
- prevents "bounce through three orchestrators, then ask the user anyway"

What can go wrong:

- hop count alone is less expressive than `passthrough_agents` / `resolvable_by`
- use it as a guardrail, not as the only routing policy in complex workflows

---

## 8. Parallel child caveat

Parallel child batches are still a special case.

Do not design parallel children on the assumption that they can synchronously open direct user interaction the same way sequential children can.

Current expectation:

- sequential children may use `request_user_input`
- parallel children that need callback-style interaction are checkpointed / blocked and resumed later

If your plugin architecture relies heavily on user clarification, prefer:

- sequential specialists for clarification-heavy steps
- parallel fan-out only for self-contained analysis steps

---

## 9. Prompting guidance

The system prompt should be explicit about:

- which interaction kinds are available
- when to ask the caller first
- when to ask the user directly
- when failure is preferable to user escalation
- whether large orchestration agents are passthrough-only for child clarifications

Bad prompt guidance:

- "Use callback whenever you need more information."

Why it is bad:

- it collapses three distinct runtime behaviors into one vague instruction

Better prompt guidance:

- "Use `callback_to_caller` when the parent may resolve or mediate."
- "Use `request_user_input` when the answer must come from the user and caller mediation would add no value."
- "Use `request_resolution` when the user must not be asked."

---

## 10. Behavior hooks and deterministic interception

`AgentBehavior.respond_to_callback(...)` is still important.

Use it when a parent can answer deterministically without running its own LLM loop.

Good fits:

- answer child questions from already-known state
- enforce approval rules
- deny or rewrite a child request before it bubbles upward

This is often the cheapest option when the parent truly owns the information.

---

## 11. How this can fail

Common failure modes:

### Wrong interaction kind

Symptom:

- the callback reaches the wrong destination
- orchestration layers spend tokens relaying trivial questions
- the user is asked when the workflow should have failed

Fix:

- choose `callback_to_caller`, `request_user_input`, or `request_resolution` deliberately

### Missing host interaction permission

Symptom:

- direct user input requests fail immediately

Fix:

- set `can_use_host_interaction: true`

### Missing caller permission

Symptom:

- caller-mediated callbacks never bubble

Fix:

- set `can_query_caller: true`

### Over-aggressive passthrough

Symptom:

- useful parents are skipped and the user gets a low-context question

Fix:

- reserve passthrough for orchestration layers that add no reasoning value

### Wrong resolver allow-list

Symptom:

- callbacks skip agents that should have been allowed to resolve

Fix:

- keep `resolvable_by` narrow but correct

### Parallel clarification design

Symptom:

- parallel children end up blocked or unusable for direct questioning

Fix:

- move clarification-heavy steps to sequential children

---

## 12. Tracing and debugging

Look for:

- `runtime.callback_requested`
- `runtime.callback_answered`
- `runtime.interaction_opened`
- `runtime.interaction_answered`

Useful questions when debugging:

- which `kind` did the model emit?
- did the request route to caller or host?
- was a `prompt_id` created?
- did the callback climb the intended caller chain?
- was a parent run skipped by `passthrough_agents` or `resolvable_by`?
- did `fallback_target` redirect to user or fail?

If the trace shows the wrong route, fix the agent contract or prompt first. Do not add heuristic repair that silently changes invalid structured output into a different interaction kind.

---

## 13. Minimal recipes

### Specialist child that should ask the user directly

Sidecar:

```json
{
  "can_query_caller": false,
  "can_use_host_interaction": true
}
```

Decision:

```json
{
  "kind": "request_user_input",
  "intent": "information_request",
  "message": "What audience should this deck target?"
}
```

### Orchestrator that should forward child clarifications

Sidecar:

```json
{
  "callback_policy": {
    "passthrough_child_callbacks": true,
    "fallback_target": "user"
  }
}
```

### Specialist-only resolution

Decision:

```json
{
  "kind": "request_resolution",
  "intent": "information_request",
  "message": "Resolve the account id from known system state.",
  "parameters": {
    "resolvable_by": ["account_lookup_specialist"],
    "fallback_target": "fail"
  }
}
```

---

## 14. Final recommendation

Default to `callback_to_caller` only when caller mediation adds real value.

If the answer must come from the user, prefer `request_user_input`.

If the answer must come from the system, prefer `request_resolution`.

Use passthrough and hop limits to protect orchestration layers from becoming expensive relay agents.
