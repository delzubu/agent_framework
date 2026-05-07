---
name: debug-authoring-agents
description: |
  Debugging guide for agent_framework agent definitions.
  Use when an agent's prompt, decision envelope, behavior hook, tool call,
  planning loop, workflow step, or evaluator run is misbehaving.
version: "1.0"
priority: 0
---

# debug-authoring-agents skill

Use this skill when debugging issues in agent definition files (.md, .json), prompts, decision contracts, hooks, tools, planning agents, workflow agents, or evaluator test runs.

## How to use this skill

Load reference files on demand:

| Reference | When to load |
|-----------|-------------|
| `references/known-issues-authoring.md` | When you have a symptom — parameter binding, prompt tokens, decision envelope, planning loops, workflow steps, evaluator scoring |
| `references/trace-jsonl.md` | Before reading or querying a `.jsonl` audit log |
| `tools/parse_log.py` | Import or run directly to extract structured data from a `.jsonl` log |

## Related skills

- For the full authoring reference: load **authoring-agents** skill
- For decision envelope JSON shapes: load `authoring-agents` → `references/decision-envelope.md`
- For host-side issues: load **debug-embedding-agent-framework** skill
- For .env / model-provider issues: load **debug-operating-agent-framework** skill

## Base directory

The `references/` and `tools/` folders are in the same directory as this file.

## Debugging workflow

### 1. Locate the log

Audit logs are written by `JsonlTraceSubscriber` to the path configured in `.env` (typically `logs/agent-host-<timestamp>.jsonl`). The `agent_framework_evaluator run` command accepts `--trace-jsonl <path>` to specify the output.

### 2. Identify the run

Every agent invocation has a `run_id` in `context.run_id`. The root run (directly invoked by the host) has no parent. Child runs (subagents) have `run_id` values prefixed with the parent: `<parent_run_id>.<subagent_id>`.

Use `tools/parse_log.py summarize <log>` to see all top-level runs and their agent names.

### 3. Inspect parameter binding

- Look for `runtime.parameters_bound` — the **authoritative** snapshot of all parameters after all pre-run hooks.
- Look for `runtime.audit.agent_call_started` to see the rendered system and user prompts.

### 4. Determine the agent type

| Agent type | What to look for |
|---|---|
| **Standard** | `llm.request`/`llm.response` events; `runtime.audit.decision` with `kind` = `call_tool`, `call_subagent`, `final_message`, etc. |
| **Planning** | `runtime.audit.named_event` with `event.type == "plan_updated"`; decisions with `kind` = `submit_plan` or `continue_plan` |
| **Programmatic workflow** | No `llm.request` events for the agent itself; only `runtime.parameters_bound` and subagent/tool activity |

### 5. Inspect decisions

- Each `runtime.audit.decision` shows one model decision: kind, tool/subagent, parameters.
- For planning agents: `submit_plan` decisions carry the initial plan; `continue_plan` carries revisions.
- `runtime.audit.named_event[plan_updated]` with `event.is_initial == true` is the initial plan.

### 6. Inspect tool and subagent results

- `runtime.agent_finished` on a child run gives the subagent's final result (`status`, `message`, `response`).
- Tool results are NOT captured at the audit-log level. Check the next `llm.request` — the tool result appears in the input messages.

### 7. Inspect programmatic workflow execution

No LLM calls exist. Trace by:
- `runtime.parameters_bound` — parameters resolved on entry
- `runtime.agent_finished` events on child runs — each corresponds to a `WorkflowCallSubagentStep`
- Add a debug `_on_step_end` callback to log `state.step_results` at each boundary

### 8. Inspect LLM calls

- `llm.request` / `llm.response` — full model input and output
- `llm.error` — provider-side failures
