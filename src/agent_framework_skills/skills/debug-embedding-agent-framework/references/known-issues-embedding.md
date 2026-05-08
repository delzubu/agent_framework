# Known issues and symptoms — host embedding

Issues that arise in the host-layer code: AgentHost setup, parameter passing, sub-agent dispatch, memory wiring, and callback handling.

---

## Parameter passing from host to agent

### Symptom: Agent behaves as if a parameter is missing, but you passed it to `AgentHost.run_agent()`

**Cause A — Parameter not declared in agent frontmatter**
`AgentHost.run_agent(agent_id, prompt, parameters={"key": "value"})` passes the value, but if the agent's `.md` frontmatter does not declare `key` under `parameters:`, the runtime does not bind it. The value is silently dropped.

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

### Symptom: `AgentResult.status` is `"blocked"` and the host doesn't handle it

**Cause:** The host checks only `result.status == "completed"` but the agent emitted a callback decision that could not be resolved internally. `"blocked"` is the status when a callback bubbles all the way out of the agent without being answered.

**Fix:** Add a branch for `result.status == "blocked"`. Inspect `result.callback_intent` (e.g. `"information_request"`) and `result.message` to understand what the agent needed. To handle callbacks programmatically, implement `AgentBehavior.respond_to_callback()` on the parent agent — this intercepts child callbacks before they bubble to the host.

---

### Symptom: Agent emits callbacks in an interactive console session but headless calls get `status="blocked"`

**Cause:** Interactive console mode wires a `ConsoleUserCommunication` that handles `request_user_input` by prompting the user in the terminal. Headless calls (e.g. `AgentHost.run_agent(...)` without a `user_comm`) use `NullUserCommunication`, which cannot answer prompts — so the callback status propagates as `"blocked"`.

**Fix:** For headless programmatic use, implement `AgentBehavior.respond_to_callback()` on the calling agent, or supply a custom `UserCommunication` implementation to `AgentHost.create(user_comm=...)` that answers queries from application logic.

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
The host must populate memory before calling `AgentHost.run_agent(...)`. If the write happens after the run starts, the agent sees stale data.

**Fix:** Check `MEMORY_BACKEND` and `MEMORY_SCOPE` in `.env`. Use `tools/parse_log.py` to find the `call_tool` decision for the memory-read tool and inspect the result in the subsequent `llm.request`.
