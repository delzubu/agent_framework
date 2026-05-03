# Known issues and symptoms — agent_framework

---

## Parameter and prompt flow

### Symptom: Agent ignores a parameter value; uses a different value instead

**Cause A — Parameter not bound before first turn**
`refresh_parameter_state` extracts parameter values from the rendered user prompt. If the prompt template does not include `{{param_name}}` the value is never rendered and thus never extracted.

**Cause B — Hook-injected value overwritten**
An `on_pre_agent` hook may inject a `system_message` fragment that sets the value differently from the seed parameter. The second `refresh_parameter_state` call (after hooks) picks up the fragment and overwrites the seed.

**Fix:** Check `runtime.parameters_bound.bound_parameters` in the log to see the final resolved values. Check `runtime.audit.agent_call_started.system_prompt` to see all injected fragments.

---

### Symptom: `{{param_name}}` token not replaced in user prompt

**Cause:** `apply_runtime_placeholders` replaces `{{param_name}}` with the string value of each parameter. If the parameter was not present in `run.parameter_values` at render time, the token is left as-is.

**Fix:** Check that the parameter is declared in the agent's `.md` frontmatter and that it has a value by the time rendering runs. A missing value from a hook means the hook ran after `apply_runtime_placeholders` was called on the first pass — this is normal. The second `refresh_parameter_state` after hooks will pick it up for subsequent turns but not the initial rendered prompt.

---

## Programmatic workflow agent

A programmatic workflow agent bypasses the LLM loop entirely — `AgentBehavior.before_run` calls `agent.execute_programmatic_workflow` and returns a final result directly. There are no `llm.request`/`llm.response` events for the workflow agent itself (only for any subagents it calls).

### Symptom: `ValueError: Missing required parameter(s) [...] for workflow agent`

**Cause:** `execute_programmatic_workflow` validates required parameters via `refresh_parameter_state` and one or more required parameters were not supplied.

**Fix:** Ensure all `required: true` parameters declared in the workflow agent's `.md` frontmatter are passed to `AgentHost.run_agent(agent_id, prompt, parameters={...})`.

---

### Symptom: A workflow step resolves to `None` instead of the expected value

**Cause A — Wrong path in `_ref` lambda**
The step's parameter lambda resolves a path that doesn't exist in the referenced step result. Common when the step result structure varies across runs (e.g. an empty list, a renamed field).

**Cause B — Step result not yet available**
A step tried to reference the output of another step that wasn't executed before it. Workflow steps execute in the order defined by `next_step` — there is no automatic dependency resolution at runtime (unlike the planning agent).

**Fix:** Add a debug print in `_on_step_end` to inspect `state.step_results` at each step boundary. Confirm the referenced step ran first and produced the expected shape.

---

### Symptom: `WorkflowAbortedError` raised unexpectedly

**Cause A — `on_step_end` returned `WorkflowAbort`**
A custom `_on_step_end` hook returned a `WorkflowAbort` mutation for an unexpected step result.

**Cause B — A `WorkflowRaiseStep` was reached**
The workflow has an explicit `WorkflowRaiseStep` in its step graph (used as an error path in branching workflows).

**Fix:** Check the `_on_step_end` implementation for the condition that triggered the abort. Check `state.step_results` at that point.

---

## Planning agent

### Symptom: Planning agent loops without making progress (keeps replanning)

**Cause A — Step dependencies unresolvable**
A step depends on a `{{token}}` that references a step that hasn't run yet or doesn't exist. The planner keeps replanning to fix dependencies.

**Cause B — `max_iterations` reached before plan completes**
The planning config's `max_iterations` is too low for the number of steps.

**Fix:** Check the `plan_updated` events — is the plan changing each time? Look at the `depends_on` fields. Check `PlanningConfig.max_iterations` in the agent's `.json` sidecar.

---

### Symptom: `StepReferenceError` or `{{token}}` in tool parameters at runtime

**Cause:** A step parameter contains `{{step_id.path}}` but `StepReferenceResolver` could not resolve it because the referenced step hasn't completed yet (circular or out-of-order dependency).

**Fix:** Check the `depends_on` field in the plan for that step. It must list all steps whose output it references.

---

## Evaluator

### Symptom: `evaluate` run passes with high score but `run` produces wrong output

**Cause:** `evaluate` appends `CASE_NO_CALLBACKS_POSTFIX` to the prompt, which suppresses callback decisions. This changes the agent's behaviour. The agent that `run` calls is the one that gets callbacks; `evaluate` runs headless.

**Fix:** Design the agent to produce its primary output without relying on a callback round-trip, or write a custom code evaluator that can handle the callback flow.

---

### Symptom: Evaluator shows `result_field not present in agent result`

**Cause:** The `result_field` in the case file (default: `message`) does not match the agent's output. Some agents return structured data only in `response`, not in `message`.

**Fix:** Set `result_field: response` (or a dot-path like `response.summary`) in the case frontmatter to select the right field.

---

## Model / provider

### Symptom: `AgentDecision.from_model_response` raises `ValueError: unsupported kind`

**Cause:** The model returned a JSON object with a `kind` field that is not in the known set (`final_message`, `call_tool`, `call_subagent`, `call_subagents`, `invoke_skill`, `callback`, `submit_plan`, `continue_plan`). This can happen if the model hallucinates a decision type or if the system prompt is malformed.

**Fix:** Check `llm.response.raw_text` in the log for the exact model output. Fix the system prompt or add `response_format` enforcement if the provider supports it.

---

### Symptom: LLM returns valid JSON but it doesn't parse into a decision

**Cause:** The model wrapped its JSON in a markdown code fence (`` ```json ... ``` ``). `_normalize_json_text` strips these, but only the outermost fence.

**Fix:** Check `llm.response.raw_text`. If nested fences appear, the model needs stronger formatting instructions in the system prompt.
