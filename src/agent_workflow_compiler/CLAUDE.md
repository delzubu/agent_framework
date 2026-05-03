# agent_workflow_compiler — compile planning-agent audit logs into deterministic agents

## Source tree

```
agent_workflow_compiler/
├── cli.py             compile-workflow CLI entry point
├── log_reader.py      JSONL → ordered list[AuditEvent]
├── plan_extractor.py  extract_plan() → PlanCompilation from AuditEvents
├── models.py          PlanCompilation, CompiledStep, ReplanCheckpoint, AuditEvent
└── emitter/
    ├── markdown.py    Emits <id>.md (agent definition)
    ├── json_def.py    Emits <id>.json (behavior pointer) + <id>.workflow.json (human-readable)
    ├── behavior.py    Emits <id>.py (AgentBehavior + _build_workflow + _on_step_end stubs)
    └── _tokens.py     Token detection, _value_to_python_expr,
                       find_invocation_param, infer_param_ref
```

## Compilation pipeline

```
compile-workflow compile --log audit.jsonl --agent-id <id> ...
  └─ log_reader.read_events(log)          → list[AuditEvent]
  └─ plan_extractor.extract_plan(events)  → PlanCompilation
       ├─ filter to planner run_id
       ├─ invocation_parameters from runtime.parameters_bound (fallback: runtime.agent_started)
       ├─ final plan = last plan_updated event's plan array
       ├─ topological sort → CompiledStep list with next_step pointers
       ├─ replan checkpoints from non-initial plan_updated events
       └─ step_results from runtime.agent_finished events (subagents only)
  └─ emitters (all three run for every compile):
       markdown.py  → <id>.md
       json_def.py  → <id>.json (behavior pointer) + <id>.workflow.json (human-readable)
       behavior.py  → <id>.py  (AgentBehavior + _build_workflow + _on_step_end stubs)
```

## Parameter resolution in emitted code

Priority order applied per literal value in each step's parameters:

1. `{{token}}` string → `_value_to_python_expr` → `lambda s: _ref(s, step_id, *path)`
2. Matches an invocation parameter by value → `lambda s: _ref(s, 'param_name')` — comment: `# Bound from agent parameter`
3. Replan-introduced step + value found in step_results → `lambda s: _ref(s, step_id, *path)` — comment: `# Parameter inferred: …`
4. Plain literal

The `_ref` helper (inlined in generated `.py` files) resolves `state.step_results[step_id]` first, then falls back to `state.initial_parameters[step_id]`, then traverses further path segments through dicts, lists, and object attributes.
