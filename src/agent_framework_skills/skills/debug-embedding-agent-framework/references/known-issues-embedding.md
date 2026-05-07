# Known issues and symptoms — host embedding

Issues that arise in the host-layer code: AgentHost setup, parameter passing, sub-agent dispatch, memory wiring, and callback handling.

---

## Parameter passing from host to agent

### Symptom: Agent behaves as if a parameter is missing, but you passed it to `AgentHost.run()`

**Cause A — Parameter not declared in agent frontmatter**
`AgentHost.run(agent_id, prompt, parameters={"key": "value"})` passes the value, but if the agent's `.md` frontmatter does not declare `key` under `parameters:`, the runtime does not bind it. The value is silently dropped.

**Fix:** Add the parameter declaration to the agent's `.md` frontmatter with the correct `type` and `required` fields.

---

### Symptom: Parameter value arrives as `None` in agent context

**Cause A — Default applied instead of passed value**
If the agent's `.md` declares `default: null` (or omits a default) and the parameter is marked `required: false`, a missing value resolves to `None`. Confirm the caller is passing a non-None value.

**Cause B — Hook overrides the value**
An `on_pre_agent` hook may inject a `system_message` fragment that resets the parameter. Check `runtime.parameters_bound.bound_parameters` in the log for the final resolved value.

**Fix:** Check `runtime.audit.agent_call_started.system_prompt` to see all injected fragments. Remove or fix the conflicting hook.

---

## Callback handling in host code

### Symptom: `AgentResult.status` is `"waiting"` but host code doesn't resume

**Cause:** The host checks `result.status == "completed"` but the agent emitted a callback decision. `"waiting"` is the status for all callback kinds (`callback`, `callback_to_caller`, `request_user_input`, `request_resolution`).

**Fix:** Add a branch for `result.status == "waiting"`. Inspect `result.callback_intent` and provide the appropriate response via `host.resume(run_id=result.run_id, response={...})`.

---

### Symptom: Callback response is ignored — agent loops or errors after resume

**Cause:** The resume call passed `response` with the wrong key. The agent's callback decision specified `parameters.parameter_name`; the resume must pass a dict with that exact key.

**Fix:** Read `result.parameters` to find the expected key(s), then pass `response={expected_key: value}`.

---

## Sub-agent dispatch from Python

### Symptom: Sub-agent not found when invoked from `AgentBehavior.before_run`

**Cause:** The sub-agent id passed to `agent.execute_programmatic_workflow(...)` or `agent.run_subagent(...)` does not match any `.md` file in `AGENT_DIRECTORY`.

**Fix:** Verify `AGENT_DIRECTORY` is set correctly in `.env` and that the sub-agent file exists with an `id:` field matching the id string you passed.

---

## Memory wiring

### Symptom: Agent receives empty or stale memory

**Cause A — Memory scope mismatch**
The agent reads from `mem://session/key` but the host wrote to `mem://global/key` (or vice versa). Memory scopes are distinct namespaces.

**Cause B — Memory not pre-populated before run**
The host must populate memory via `MemoryStore.write(...)` before calling `AgentHost.run()`. If the write happens after the run starts, the agent sees stale data.

**Fix:** Check `MEMORY_BACKEND` and `MEMORY_SCOPE` in `.env`. Use `tools/parse_log.py` to find the `call_tool` decision for the memory-read tool and inspect the result in the subsequent `llm.request`.
