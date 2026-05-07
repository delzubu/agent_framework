# Decision envelope — all kinds

Every model call produces exactly one decision. The framework parses and dispatches it, then loops.

---

## `final_message` — done, return result

**Text result** (plain prose answer, no structured payload):
```json
{"kind": "final_message", "message": "The answer is 42."}
```

**Structured result** (machine-readable payload for downstream consumers):
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

Rules:
- Use `"message"` for human-readable prose answers.
- Use `"response"` (a JSON object) when the caller or evaluator needs a typed payload — e.g. a list of extracted records, a routing decision, a status block.
- **Do NOT use `"parameters"` on `final_message`** — `parameters` is reserved for `call_tool` / `call_subagent` / `callback` decisions. Setting it on `final_message` raises `ValueError`.

---

## `call_tool` — invoke a registered tool

```json
{
  "kind": "call_tool",
  "tool_name": "Read",
  "parameters": {
    "file_path": "/project/config.yaml"
  },
  "message": "Reading config."
}
```

Tool result is injected back into the conversation as a user message. Loop continues.

---

## `call_subagent` — delegate to one child agent

```json
{
  "kind": "call_subagent",
  "subagent_id": "order_lookup",
  "parameters": {
    "order_id": "ORD-12345"
  },
  "message": "Looking up the order."
}
```

Child runs to completion. Its `AgentResult.message` is injected back. Loop continues.

---

## `call_subagents` — batch dispatch (parallel or sequential)

```json
{
  "kind": "call_subagents",
  "mode": "parallel",
  "timeout_seconds": 120,
  "calls": [
    {"subagent_id": "researcher", "parameters": {"topic": "X"}, "output_key": "research"},
    {"subagent_id": "critic",     "parameters": {"topic": "X"}, "output_key": "critique"}
  ]
}
```

- `mode`: `"parallel"` | `"sequential"` — **required**, no default.
- `timeout_seconds`: optional; falls back to `SUBAGENT_BATCH_TIMEOUT_SECONDS` env var (default 300).
- `calls`: non-empty list; each requires `subagent_id`; `parameters` defaults to `{}`; `output_key` defaults to `call_<index>`.
- **Parallel children must not emit `callback`** — if they do, that child returns `status="blocked"` and siblings continue normally.
- All results are injected as one `<subagent_results>` block. Loop continues.

---

## Interaction decisions

Treat interaction routing as a first-class decision. Do not collapse these semantically when designing prompts or runtime behavior.

### `callback` — generic escalation / clarification

```json
{
  "kind": "callback",
  "intent": "information_request",
  "message": "What is the preferred shipping address?",
  "parameters": {
    "parameter_name": "shipping_address"
  }
}
```

The model may also emit the intent name directly as `kind` (e.g. `"kind": "information_request"`); the framework normalises both forms.

### `callback_to_caller` — ask the caller agent first

```json
{
  "kind": "callback_to_caller",
  "intent": "information_request",
  "message": "Need caller-side resolution.",
  "parameters": {
    "parameter_name": "shipping_address"
  }
}
```

Use when the caller may resolve, transform, or escalate the request.

### `request_user_input` — ask the user directly

```json
{
  "kind": "request_user_input",
  "intent": "information_request",
  "message": "What is the preferred shipping address?",
  "parameters": {
    "parameter_name": "shipping_address"
  }
}
```

Use when the answer must come from the user and bubbling through parent agents would only add token cost.

### `request_resolution` — resolve through agents/tools only

```json
{
  "kind": "request_resolution",
  "intent": "information_request",
  "message": "Resolve the customer id from known system state.",
  "parameters": {
    "allowed_resolvers": ["parent", "memory", "tools"]
  }
}
```

Use when the host/user must not be asked. If unresolved, fail or choose another explicit path.

---

## Callback intents

| Intent | Use |
|--------|-----|
| `information_request` | Agent needs a value from the user |
| `proposal_review` | Agent proposes an action and wants approval |
| `execution_recovery` | Something went wrong; agent needs guidance |
| `delegation_return` | Child returning to parent with result/question |
| `policy_or_approval` | Agent needs authorisation for a sensitive action |
| `guardrail_trip` | Policy violation detected; agent stops to report |

---

## Routing guidance

- `callback_to_caller`: caller-mediated resolution, with optional upward escalation
- `request_user_input`: direct host/user interaction for the requesting run
- `request_resolution`: agent-side resolution only, no host/user fallback
- generic `callback`: use only when the runtime version you are editing does not expose the more specific forms

---

## `invoke_skill` — load a named skill

```json
{
  "kind": "invoke_skill",
  "skill_name": "refund_policy",
  "message": "Loading refund policy.",
  "parameters": {}
}
```

The skill's full file content is injected as a user message. Loop continues.
