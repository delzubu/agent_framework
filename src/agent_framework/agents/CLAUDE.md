# agent_framework/agents — Agent class and per-run data structures

## Source tree

```
agents/
├── agent.py                  Agent — loaded from .md; owns run(), execute_programmatic_workflow()
├── agent_run.py              AgentRun — mutable per-invocation state (prompt, parameters,
│                             history, fragments, missing_parameters, …)
├── agent_decision.py         AgentDecision — parsed from model JSON; SubagentCallSpec
├── agent_parameter.py        AgentParameter spec (name, type, required, default, pattern)
├── agent_behavior.py         AgentBehavior base class (before_run, after_run, respond_to_callback)
├── agent_hook_decision.py    AgentHookDecision — returned by before_run to short-circuit
├── agent_invocation.py       AgentInvocation — snapshot passed to on_pre_agent hooks
├── agent_result.py           AgentResult(status, message, response, prompt)
├── agent_host_protocol.py    AgentHostProtocol — structural Protocol used inside agents
│                             to avoid circular imports with host.py
├── turn_driver.py            TurnDriver protocol + StandardTurnDriver (single model call
│                             → dispatch loop per outer iteration)
├── workflow.py               ProgrammaticWorkflow + all step types + WorkflowMutation types
├── helpers.py                load_runtime_metadata (reads .json sidecar), split_markdown_sections,
│                             extract_prompt_value, apply_runtime_placeholders, coerce_parameter_value
├── call_context.py           CallContext stack (run_id chain for nested agents)
├── sequential_hook.py        SequentialHook — typed event bus for lifecycle callbacks
├── result_envelope.py        ResultEnvelope for subagent batch results
├── subagent_hook_decision.py Decision returned by respond_to_callback
└── *_event.py / *_hook_decision.py   Typed event and hook-decision dataclasses for each
                              lifecycle point (model, tool, subagent, skill, end)
```

## System prompt templates

```
agents/system.md              Base — tools + subagent catalog
agents/system.decision.md     Structured JSON decision format (default)
agents/system.text.md         Plain text response mode
agents/system.json_object.md  Arbitrary JSON output mode
agents/system.plan_execute.md Extended instructions for PlanningTurnDriver agents
```

## Programmatic workflow dispatch

Used by compiled agents. `AgentBehavior.before_run` short-circuits the model loop entirely.

```
AgentBehavior.before_run(agent, host, run, caller_id)
  └─ agent.execute_programmatic_workflow(host, run, caller_id, workflow, initial_parameters)
       ├─ refresh_parameter_state(run)   # validate required params; raise ValueError if missing
       ├─ ProgrammaticWorkflowState(initial_parameters=run.parameter_values)
       └─ while step_id != None:
            step = workflow.steps[step_id]
            dispatch by type:
              WorkflowCallToolStep     → host.execute_tool(tool_name, resolve(arguments, state))
              WorkflowCallSubagentStep → host.call_subagent(subagent_id, resolve(parameters, state))
              WorkflowCallSubagentsStep→ host.call_subagent_batch(calls, mode, timeout)
              WorkflowInvokeSkillStep  → host.invoke_skill(skill_name, resolve(parameters, state))
              WorkflowBranchStep       → evaluate condition(state) → then_step or else_step
              WorkflowReturnStep       → return coerce_workflow_result(resolve(value, state))
              WorkflowRaiseStep        → raise WorkflowAbortedError
            state.step_results[step.step_id] = result
            if workflow.on_step_end:
              mutation = on_step_end(step_id, result, state, workflow)
              WorkflowContinue  → advance to step.next_step
              WorkflowGoto      → jump to mutation.step_id
              WorkflowReplace   → swap workflow, restart at new entry_step
              WorkflowAbort     → raise WorkflowAbortedError(reason)
```

`_ref(state, step_id, *path)` — runtime helper inlined in generated behavior files. Resolves `state.step_results[step_id]` first, then falls back to `state.initial_parameters[step_id]`. Path segments traverse dicts, lists (by numeric index), and object attributes.

## Rules

**Three `---` section structure for agent files.** `helpers.split_markdown_sections` expects exactly three `---` delimiters: frontmatter / system prompt / user prompt template. The user template is rendered with `{{param_name}}` substitution before the LLM sees it.

**Runtime metadata belongs in the `.json` sidecar, not `.md` YAML.** The `behavior`, `model`, `temperature`, `planning`, and provider fields are read from `<agent>.json` by `helpers.load_runtime_metadata`. The `.md` frontmatter holds only the agent definition fields (`id`, `role`, `parameters`, `tools`, `subagents`, `terminal_tools`).

**Workflow step parameter values are lambdas, not string tokens.** `ProgrammaticWorkflow` steps accept `dict[str, Any] | WorkflowValueResolver`. Use `lambda s: _ref(s, ...)` to defer resolution to runtime. `resolve_workflow_value` recurses into dicts so per-key lambdas work. Do not introduce `{{token}}` strings at the workflow runtime layer — that is a compiler concern only.
