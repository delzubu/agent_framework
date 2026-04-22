---
title: Handling Callbacks
layout: default
---

# Handling Callbacks

Who this is for: plugin developers and agent authors designing clarification, approval, recovery, and escalation flows.

## Callback Kinds

- `callback`: generic callback, mainly for older prompts and normalized intent kinds.
- `callback_to_caller`: ask the caller side to try resolving first.
- `request_user_input`: ask the user directly.
- `request_resolution`: resolve through agents, tools, or memory only; do not ask the user.

The `intent` explains what type of answer is needed. The `kind` explains where the runtime should try to get it.

## When To Use Which

- Use `callback_to_caller` when the parent may already know the answer or should mediate approvals.
- Use `request_user_input` when only the user can answer and bubbling upward would just waste tokens.
- Use `request_resolution` when the workflow must not escape to UI and unresolved state should fail loudly.
- Use plain `callback` only when you are targeting an older prompt/runtime contract.

## Agent Metadata

Agent sidecar JSON controls whether callbacks may go upward or directly to the host:

```json
{
  "can_query_caller": true,
  "can_use_host_interaction": true,
  "callback_policy": {
    "passthrough_child_callbacks": false,
    "max_bubble_hops": 2,
    "fallback_target": "user"
  }
}
```

Useful keys:

- `can_query_caller`
- `can_use_host_interaction`
- `callback_policy.passthrough_child_callbacks`
- `callback_policy.max_bubble_hops`
- `callback_policy.fallback_target`

## Per-Decision Routing Hints

Callback `parameters` may override the defaults:

- `max_bubble_hops`
- `fallback_target`
- `passthrough_agents`
- `resolvable_by`
- `passthrough_child_callbacks`

These are useful when one callback should skip orchestration agents or only a specific specialist parent is allowed to resolve it.

## Common Scenarios

### Caller-mediated clarification

Use `callback_to_caller` when a parent agent may resolve, transform, or escalate the request with better context.

### Direct user clarification

Use `request_user_input` for intake or clarification specialists that should talk to the user directly.

### Agent-only resolution

Use `request_resolution` when the answer must come from tools, memory, or another agent, not from the user.

### Skip orchestration layers

If controllers or workflow wrappers should not spend tokens on a child callback, use:

- sidecar `callback_policy.passthrough_child_callbacks`
- per-decision `passthrough_agents`
- per-decision `resolvable_by`

## Parallel Child Caveat

Sequential children may ask the user directly. Parallel batch children still should not assume they can synchronously block on user interaction in the same way.

## Failure Modes

- Wrong `kind`: request goes to the wrong destination.
- Missing `can_use_host_interaction`: direct user input fails.
- Missing `can_query_caller`: caller-mediated escalation never happens.
- Over-aggressive passthrough: useful parents are skipped.
- Bad `resolvable_by`: the wrong agents are allowed or denied.

## Next Steps

- [Authoring Agents]({{ '/build/authoring-agents/' | relative_url }})
- [Decision JSON Contract]({{ '/reference/decision-json-contract/' | relative_url }})
- [Developer Documentation]({{ '/reference/developer-documentation/' | relative_url }})
