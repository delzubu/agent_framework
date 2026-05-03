# Tracing the agent_framework JSONL audit log

## File format

Each line is a JSON object serialised from `TraceEvent` (via `dataclasses.asdict`). Lines are independent; order is chronological by `timestamp`.

```json
{
  "event_id": "<uuid4>",
  "parent_event_id": null,
  "span_id": "<run_id or run_id:model>",
  "parent_span_id": null,
  "timestamp": "2026-05-03T13:09:31.993558+00:00",
  "channel": "runtime",
  "level": "info",
  "kind": "runtime.audit.agent_call_started",
  "title": "Audit: agent call started",
  "summary": "",
  "context": {
    "session_id": "<uuid>",
    "run_id": "<run_id>",
    "agent_id": "<agent_id>",
    "caller_id": "<parent_agent_id or 'host'>",
    "tool_name": null,
    "subagent_id": null,
    "conversation_id": null
  },
  "payload": { ... }
}
```

## Run ID hierarchy

- Root run: `<short_uuid>.<session_suffix>.<agent_id>` (e.g. `fa0ea394-215.p1.player_controller`)
- Child run (subagent): `<parent_run_id>.<subagent_id>` (e.g. `fa0ea394-215.p1.player_controller.player_intent_parser`)
- All events for a run (including subagents) have `context.run_id` that **starts with** the parent run_id.

To get all events for a run and its children: `[e for e in events if e["context"]["run_id"].startswith(run_id)]`
To get events for only the planning agent itself: `[e for e in events if e["context"]["run_id"] == run_id]`

---

## Event reference

### `runtime.audit.agent_call_started`

Emitted before the first model call. Contains the fully rendered prompts.

```
payload:
  run_id              str     — this run's run_id
  parent_run_id       str|null
  caller_id           str     — parent agent id or "host"
  agent_name          str     — agent id
  system_prompt       str     — full assembled system prompt
  system_prompt_sources list[str]
  user_prompt         str     — rendered user prompt template
  user_prompt_sources list[str]
```

### `runtime.parameters_bound`

Emitted after all `on_pre_agent` hooks have run. This is the **authoritative** parameter snapshot — use this, not `runtime.agent_started.parameters`.

```
payload:
  bound_parameters    dict[str, Any]   — all resolved parameter values
```

**If this event is absent:** the log was captured before the framework emitted it (pre-`runtime.parameters_bound` version). Only seed parameters are available via `runtime.audit.agent_call_started` context.

### `runtime.audit.decision`

Emitted each time the model produces a decision.

```
payload:
  decision:
    kind              str     — "final_message" | "call_tool" | "call_subagent" |
                                "call_subagents" | "invoke_skill" | "callback" |
                                "submit_plan" | "continue_plan"
    message           str|null
    tool_name         str|null
    parameters        dict|null   — tool or subagent parameters
    subagent_id       str|null
    callback_intent   str|null
    skill_name        str|null
```

For planning agents, `kind == "submit_plan"` carries the initial plan in `parameters.plan`; `kind == "continue_plan"` carries the revised plan.

### `runtime.audit.named_event`

Wraps arbitrary named events emitted by planning logic.

```
payload:
  event:
    type              str     — e.g. "plan_updated"
    plan_revision     int
    is_initial        bool
    plan              list[step]   — full step array
    added_step_ids    list[str]
    message           str|null     — replan trigger message
```

Each step in `plan`:
```
{
  "id": "step_get_state_slice",
  "kind": "call_tool",            # or "call_subagent", "invoke_skill"
  "tool_name": "get_state_slice", # if call_tool
  "subagent_id": null,
  "parameters": { ... },          # literal values as resolved at plan-time
  "depends_on": []
}
```

### `runtime.agent_finished`

Emitted when an agent run completes (including subagents and programmatic workflow agents).

```
payload:
  status              str           — "completed" | "error" | "callback" | "stopped"
  caller_id           str|null
  message             str|null      — agent's final message (may be a JSON envelope string)
  response            Any|null      — structured response payload (if any)
  usage_self          dict          — token usage for this run only
  usage_inclusive     dict          — token usage including all child runs
  decision_envelope   dict|null     — present when message is a parseable JSON decision envelope;
                                      contains {kind, message, response, ...} as structured fields
```

`decision_envelope` is populated from `result.decision` (LLM-loop completions) or by parsing `result.message` as JSON when it carries a `{"kind": ...}` envelope (common for workflow agents and subagent results). Use this field instead of parsing `message` manually.

To get a subagent's result: find `runtime.agent_finished` where `context.run_id == parent_run_id + "." + subagent_id`.

### `llm.request`

```
payload:
  run_id, agent_id, provider_name, model_name, temperature
  input_payload   dict    — full request body sent to the provider
```

### `llm.response`

```
payload:
  run_id, agent_id, provider_name, model_name
  raw_text        str     — raw model output
  parsed_payload  dict    — parsed decision (after JSON extraction)
  usage:
    prompt_tokens, completion_tokens, total_tokens
```

### `llm.error`

```
payload:
  run_id, agent_id, error_type, message, status_code, upstream_body
```

### `runtime.audit.callback`

```
payload:
  intent          str
  prompt          str
  target          str
  response        str|null
  event           dict
```

### `runtime.audit.skill_invocation`

```
payload:
  skill_name      str
  parameters      dict
  inventory       list[str]   — file paths available in the skill
```

---

## Common queries

### All event kinds in a log
```python
from tools.parse_log import load_events
events = load_events("logs/agent-host-....jsonl")
kinds = sorted({e["kind"] for e in events})
```

### Bound parameters for a specific run
```python
for e in events:
    if e["kind"] == "runtime.parameters_bound" and e["context"]["run_id"] == run_id:
        print(e["payload"]["bound_parameters"])
```

### All plan_updated events (planning agent)
```python
for e in events:
    if e["kind"] == "runtime.audit.named_event":
        ev = e["payload"].get("event", {})
        if ev.get("type") == "plan_updated":
            print(f"rev={ev['plan_revision']} initial={ev['is_initial']} added={ev.get('added_step_ids')}")
            for step in ev.get("plan", []):
                print(f"  {step['id']} ({step['kind']}) params={step['parameters']}")
```

### Final plan steps
```python
plan_updates = [
    e["payload"]["event"]
    for e in events
    if e["kind"] == "runtime.audit.named_event"
    and e["payload"].get("event", {}).get("type") == "plan_updated"
    and e["context"]["run_id"] == run_id
]
final_plan = plan_updates[-1]["plan"] if plan_updates else []
```

### All LLM calls for a run
```python
for e in events:
    if e["kind"] == "llm.request" and e["context"]["run_id"] == run_id:
        print(e["payload"]["input_payload"])
```

### Subagent results
```python
for e in events:
    if e["kind"] == "runtime.agent_finished":
        rid = e["context"]["run_id"]
        if rid.startswith(run_id + ".") and rid != run_id:
            subagent = rid.rsplit(".", 1)[-1]
            print(f"{subagent}: status={e['payload']['status']}")
            print(f"  response={e['payload'].get('response')}")
```

### Decisions made by the planning agent
```python
for e in events:
    if e["kind"] == "runtime.audit.decision" and e["context"]["run_id"] == run_id:
        d = e["payload"]["decision"]
        print(f"kind={d['kind']}  tool={d.get('tool_name')}  subagent={d.get('subagent_id')}")
```
