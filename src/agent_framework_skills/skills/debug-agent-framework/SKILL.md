---
name: debug-agent-framework
description: Debugging guide for the agent_framework runtime. Covers audit log analysis, known failure modes, and reusable JSONL parse tools. Use when diagnosing unexpected agent behaviour, tracing parameter flow, or investigating planning and tool execution issues.
version: "1.0"
priority: 0
---

# debug-agent-framework skill

Use this skill whenever you are debugging agent behaviour, tracing what happened inside a planning run, or diagnosing unexpected tool or subagent results.

## How to use this skill

Load reference files on demand as your work requires:

| Reference | When to load |
|---|---|
| `references/trace-jsonl.md` | Before reading or querying a `.jsonl` audit log |
| `references/known-issues.md` | When you have a symptom and need to identify the root cause |
| `tools/parse_log.py` | Import or run directly to extract structured data from a `.jsonl` log |

## Base directory

The `references/` and `tools/` folders are in the same directory as this file. Load any file with its relative path (e.g. `references/trace-jsonl.md`).

## Debugging workflow

### 1. Locate the log

Audit logs are written by `JsonlTraceSubscriber` to the path configured in `.env` (typically `logs/agent-host-<timestamp>.jsonl`). The `agent_framework_evaluator run` command accepts `--trace-jsonl <path>` to specify the output.

### 2. Identify the run

Every agent invocation has a `run_id` in `context.run_id`. The root run (directly invoked by the host) has no parent. Child runs (subagents) have `run_id` values that are prefixed with the parent run_id: `<parent_run_id>.<subagent_id>`.

Use `tools/parse_log.py summarize <log>` to see all top-level runs and their agent names.

### 3. Inspect parameter binding

- Look for `runtime.parameters_bound` — this is the **authoritative** snapshot of all parameters after all pre-run hooks. If this event is absent (old log), only seed parameters were captured.
- Look for `runtime.audit.agent_call_started` to see the rendered system and user prompts.

### 4. Determine the agent type

The log tells you which execution path was taken:

| Agent type | What to look for |
|---|---|
| **Standard** | `llm.request`/`llm.response` events; `runtime.audit.decision` with `kind` = `call_tool`, `call_subagent`, `final_message`, etc. |
| **Planning** | `runtime.audit.named_event` with `event.type == "plan_updated"`; decisions with `kind` = `submit_plan` or `continue_plan` |
| **Programmatic workflow** | No `llm.request` events for the agent itself; only `runtime.parameters_bound` and subagent/tool activity; look for `runtime.agent_finished` on child runs |

### 5. Inspect decisions (standard and planning agents)

- Each `runtime.audit.decision` shows one model decision: what kind, which tool/subagent, and what parameters.
- For planning agents: `submit_plan` decisions carry the initial plan; `continue_plan` carries a revised plan. Cross-reference with `runtime.audit.named_event[plan_updated]` events which contain the same plan in structured form.
- `runtime.audit.named_event` with `event.type == "plan_updated"` and `event.is_initial == true` is the initial plan. Subsequent events (`is_initial == false`) are replans, each carrying `added_step_ids`.

### 6. Inspect tool and subagent results

- `runtime.audit.decision` with `kind == "call_tool"` or `kind == "call_subagent"` shows what the agent decided to call and with what parameters.
- `runtime.agent_finished` on a child run gives the subagent's final result (`status`, `message`, `response`).
- Tool results are **not** captured at the audit-log level (they go to the conversation context only). To see tool output, check the next `llm.request` — the tool result appears in the input messages.

### 7. Inspect programmatic workflow execution

Programmatic workflow agents have no LLM calls of their own. Trace them by:
- `runtime.parameters_bound` — what parameters were resolved on entry
- `runtime.agent_finished` events on child runs — each corresponds to a `WorkflowCallSubagentStep`
- Tool calls are invisible in the log (same as standard agents)
- Add a debug `_on_step_end` callback to the workflow to log `state.step_results` at each boundary

### 8. Inspect LLM calls (standard and planning agents)

- `llm.request` / `llm.response` events show the full model input and output.
- `llm.error` shows provider-side failures.
