# agent_framework — Planning Agents Reference

Use this reference when building or modifying a planning agent — an agent that emits a structured plan and lets the runtime execute steps in parallel batches with data-dependency wiring.

---

## When to use a planning agent

Use a planning agent when:
- Steps have **data dependencies** — later steps consume the output of earlier steps.
- Some steps are **independent and can run in parallel** — the runtime dispatches ready batches concurrently.
- The model may need to **revise the plan** mid-execution based on intermediate results.
- You want an **explicit, traceable plan artifact** rather than a chain of reactive model turns.

Use a standalone agent when the task is open-ended or the step count is small. Use a programmatic workflow when the control flow is deterministic and code-expressible without LLM reasoning.

---

## Frontmatter block

Add a `planning:` block to the agent's YAML frontmatter:

```yaml
---
id: my_planning_agent
role: my_planning_agent
parameters:
  input_id:
    required: true
    type: string
tools:
  - read_db
subagents:
  - processor
planning:
  enabled: true
  parallel_execution: true      # dispatch independent steps concurrently (default: true)
  max_steps: 30                 # hard cap on total steps across all plan revisions
  max_plan_revisions: 3         # max number of replan decisions
  step_timeout_seconds: 60      # per-step wall-clock deadline
---
```

`parallel_execution: false` forces sequential step dispatch even when dependencies would allow parallelism. Useful for debugging or when step side-effects must be strictly ordered.

---

## Decision kinds

### `submit_plan` — emit a plan

Emit on the first turn (and on replan during the reflect phase):

```json
{
  "kind": "submit_plan",
  "message": "Retrieve data and process it.",
  "plan": [
    {
      "id": "get_data",
      "kind": "call_tool",
      "tool_name": "read_db",
      "parameters": {"id": "{{input_id}}"}
    },
    {
      "id": "enrich",
      "kind": "call_tool",
      "tool_name": "enrich",
      "parameters": {"record": "{{get_data}}"}
    },
    {
      "id": "process",
      "kind": "call_subagent",
      "subagent_id": "processor",
      "depends_on": ["get_data", "enrich"],
      "parameters": {
        "raw": "{{get_data}}",
        "enriched": "{{enrich}}"
      }
    }
  ]
}
```

Step fields:

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Unique step identifier within the plan. Used in `depends_on` and `{{ref}}` tokens. |
| `kind` | yes | `call_tool` \| `call_subagent` \| `invoke_skill` \| `callback` |
| `tool_name` | when `kind=call_tool` | Tool id to invoke |
| `subagent_id` | when `kind=call_subagent` | Child agent id |
| `skill_name` | when `kind=invoke_skill` | Skill id |
| `depends_on` | no | List of step ids that must complete before this step runs. Omit or `[]` for independent steps. |
| `parameters` | no | Step inputs. May contain `{{ref}}` tokens. |
| `message` | no | Human-readable annotation for tracing. |

### `final_message` — done (emitted in reflect)

```json
{
  "kind": "final_message",
  "message": "",
  "response": {
    "status": "ready",
    "results": []
  }
}
```

Use `"response"` (a JSON object) for structured output consumed by a caller or evaluator. Use `"message"` for prose answers. **Do not use `"parameters"` on `final_message`.**

---

## Ref-token substitution

`{{step_id}}` in a parameter value is replaced with the string representation of that step's result at execution time. `{{step_id.field}}` extracts a single JSON field from an object result.

```json
{
  "id": "summarize",
  "kind": "call_subagent",
  "subagent_id": "summariser",
  "depends_on": ["fetch"],
  "parameters": {
    "content": "{{fetch}}",
    "title": "{{fetch.title}}"
  }
}
```

Token resolution is `lenient` by default — unresolvable tokens are passed as empty strings rather than raising an error. Set `ref_resolution: strict` in the `planning:` block to fail on unresolved refs.

---

## Execution phases

```
PLAN     → model emits submit_plan
EXECUTE  → runtime dispatches ready batches (parallel or sequential)
REFLECT  → model evaluates results, decides: final_message | submit_plan (replan) | callback
```

The EXECUTE → REFLECT cycle repeats until `final_message` is emitted, `max_plan_revisions` is exceeded, or `max_steps` is reached.

---

## Parallel execution

Steps with no `depends_on` overlap, or whose dependencies are all already completed, form a **ready batch** and are dispatched together. The runtime resolves `{{ref}}` tokens just before each step runs.

Example — three parallel retrieval steps, one dependent step:

```json
{
  "kind": "submit_plan",
  "message": "Fetch context in parallel, then parse.",
  "plan": [
    {"id": "get_state",   "kind": "call_tool",    "tool_name": "get_state",   "parameters": {"id": "{{actor_id}}"}},
    {"id": "get_actor",   "kind": "call_tool",    "tool_name": "get_actor",   "parameters": {"id": "{{actor_id}}"}},
    {"id": "get_history", "kind": "call_tool",    "tool_name": "get_history", "parameters": {"id": "{{actor_id}}"}},
    {
      "id": "parse",
      "kind": "call_subagent",
      "subagent_id": "intent_parser",
      "depends_on": ["get_state", "get_actor", "get_history"],
      "parameters": {
        "state":   "{{get_state}}",
        "actor":   "{{get_actor}}",
        "history": "{{get_history}}"
      }
    }
  ]
}
```

The runtime dispatches `get_state`, `get_actor`, and `get_history` as one parallel batch, waits for all three, then dispatches `parse`.

---

## Reflect phase

After each batch completes the model receives a reflect turn. The reflect prompt should instruct the model to:

1. Check each completed step for errors or unexpected results.
2. Decide whether remaining steps are still valid given intermediate results.
3. Either emit `final_message` (done), `submit_plan` (replan), or `callback` (escalate).

Reflect template fragment:

```
## Reflect

After each execution batch, review completed step results and decide:
- If all required steps are complete and results are valid → emit `final_message`.
- If results reveal that a different approach is needed → emit `submit_plan` with a revised plan.
- If a required input is missing and cannot be derived → emit `callback` to escalate.

Do not emit `final_message` before all plan steps complete.
```

---

## System prompt template

Recommended structure for a planning agent's system prompt:

```markdown
## Responsibilities
[Role + primary output + what this agent must NOT decide itself.]

## Boundaries
[Hard limits. What belongs to sub-agents, tools, or deterministic code.]

## Standard Plan
[Describe the expected plan shape for the common case. Include an exact JSON example.]

### Phase 1 — [parallel retrieval / setup]
[Steps that can run in parallel. No depends_on.]

### Phase 2 — [dependent processing] (replan in REFLECT)
[Steps that depend on Phase 1 results. Show the replan trigger condition.]

### Final reflect
[When to emit final_message.]

## Output Shape
[Exact JSON fragments for final_message (text and/or structured).]
[What to put in "message" vs "response".]

## Callback Handling Rules
[When to escalate vs resolve locally.]
```

---

## Output contract for planning agents

Planning agents that return structured results must use `"response"`, not `"parameters"`:

```json
// CORRECT — structured result
{"kind": "final_message", "message": "", "response": {"status": "ready", "items": [...]}}

// WRONG — raises ValueError at runtime
{"kind": "final_message", "message": "", "parameters": {"status": "ready", "items": [...]}}
```

The `"parameters"` field on `final_message` was deprecated; setting it raises `ValueError: Invalid model decision JSON: final_message with structured output must use 'response'`.

---

## Configuration reference

```
SUBAGENT_BATCH_TIMEOUT_SECONDS=300   # wall-clock deadline per step batch
SUBAGENT_MAX_PARALLELISM=8           # max concurrent steps per batch
```

---

## Related references

- `references/framework-usage.md` — full decision loop and runtime contracts
- `references/agent-usage.md` — agent file structure and implementation surfaces
- `references/workflow-agents.md` — deterministic programmatic workflow agents
- `references/callback-handling.md` — escalation and clarification routing
