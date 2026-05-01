---
title: Decision JSON Contract
layout: default
---

# Decision JSON Contract

Who this is for: agent authors and contributors working with structured model output.

Every model call must produce exactly one JSON object. The framework parses the `kind` field and dispatches accordingly. Unknown or invented `kind` values raise a `ValueError` — there is no silent repair.

---

## Standard decision kinds

### `final_message` — agent is done

**Text result** (prose answer):
```json
{"kind": "final_message", "message": "The answer is 42."}
```

**Structured result** (typed payload for downstream consumers or evaluators):
```json
{
  "kind": "final_message",
  "message": "",
  "response": {
    "status": "ready",
    "items": []
  }
}
```

Use `"message"` for human-readable prose. Use `"response"` (a JSON object) when the caller or evaluator needs to read specific fields from the result.

**Do not use `"parameters"` on `final_message`.** `parameters` is reserved for `call_tool`, `call_subagent`, and `callback` decisions. Setting it on `final_message` raises `ValueError` at runtime.

### `call_tool` — invoke a registered tool
```json
{"kind": "call_tool", "tool_name": "Read", "arguments": {"file_path": "/config.yaml"}}
```

### `call_subagent` — delegate to one child agent
```json
{"kind": "call_subagent", "subagent_id": "order_lookup", "parameters": {"order_id": "123"}}
```

### `call_subagents` — batch dispatch (parallel or sequential)
```json
{
  "kind": "call_subagents",
  "mode": "parallel",
  "calls": [
    {"subagent_id": "researcher", "parameters": {"topic": "X"}, "output_key": "research"},
    {"subagent_id": "critic",     "parameters": {"topic": "X"}, "output_key": "critique"}
  ]
}
```

### `callback` — escalation or clarification
```json
{
  "kind": "callback",
  "intent": "information_request",
  "message": "What is the preferred shipping address?"
}
```

Valid intents: `information_request`, `proposal_review`, `execution_recovery`, `delegation_return`, `policy_or_approval`, `guardrail_trip`.

Other routing forms: `callback_to_caller` (ask caller first), `request_user_input` (ask user directly), `request_resolution` (agent-side only).

### `invoke_skill` — load a named skill
```json
{"kind": "invoke_skill", "skill_name": "refund_policy"}
```

---

## Planning decision kinds

Used only by agents with `planning: enabled: true` in their frontmatter.

### `submit_plan` — emit or revise a plan
```json
{
  "kind": "submit_plan",
  "message": "Retrieve data and process it.",
  "plan": [
    {
      "id": "fetch",
      "kind": "call_tool",
      "tool_name": "read_db",
      "parameters": {"id": "{{input_id}}"},
      "depends_on": []
    },
    {
      "id": "process",
      "kind": "call_subagent",
      "subagent_id": "processor",
      "parameters": {"data": "{{fetch}}"},
      "depends_on": ["fetch"]
    }
  ]
}
```

Steps with no unmet `depends_on` entries are dispatched in parallel. `{{step_id}}` tokens in `parameters` are resolved to the referenced step's result before the step runs.

### `continue_plan` — acknowledge reflect results and proceed
```json
{"kind": "continue_plan", "message": "Results look good, continuing."}
```

Emitted during the reflect phase when intermediate results are satisfactory and the remaining steps should proceed as planned.

---

## Enforcement rules

- Output MUST be a single JSON object — no markdown fences, no prose wrapper.
- Do NOT include both `subagent_id` and `tool_name` in the same object.
- Do NOT invent `kind` values — only the kinds above are valid.
- Do NOT use `"parameters"` on `final_message` — use `"response"` for structured payloads.
- Any other format raises `ValueError` and fails the run.

---

## Next Steps

- [Creating a Planning Agent]({{ '/build/creating-planning-agents/' | relative_url }}) — planning frontmatter, reflect phase, output contract
- [Handling Callbacks]({{ '/build/handling-callbacks/' | relative_url }})
- [Prompt and Decision Design]({{ '/learn/prompt-and-decision-design/' | relative_url }})
- [Agent Runtime Patterns]({{ '/learn/agent-runtime-patterns/' | relative_url }})
