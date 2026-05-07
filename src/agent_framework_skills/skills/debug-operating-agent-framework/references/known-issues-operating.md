# Known issues and symptoms — operational

Issues arising from model/provider configuration, .env setup, MCP wiring, and CLI invocation.

## Model / provider

### Symptom: `AgentDecision.from_model_response` raises `ValueError: unsupported kind`

**Cause:** The model returned a JSON object with a `kind` field that is not in the known set (`final_message`, `call_tool`, `call_subagent`, `call_subagents`, `invoke_skill`, `callback`, `submit_plan`, `continue_plan`). This can happen if the model hallucinates a decision type or if the system prompt is malformed.

**Fix:** Check `llm.response.raw_text` in the log for the exact model output. Fix the system prompt or add `response_format` enforcement if the provider supports it.

---

### Symptom: LLM returns valid JSON but it doesn't parse into a decision

**Cause:** The model wrapped its JSON in a markdown code fence (`` ```json ... ``` ``). `_normalize_json_text` strips these, but only the outermost fence.

**Fix:** Check `llm.response.raw_text`. If nested fences appear, the model needs stronger formatting instructions in the system prompt.

---

## .env configuration

### Symptom: `KeyError` or `Missing required configuration` at startup

**Cause:** A required `.env` key is absent or misspelled. Common: `DEFAULT_PROVIDER` is set but the matching key (`OPENAI_API_KEY` or `DIAL_API_KEY`) is missing.

**Fix:** Load `operating-agent-framework` → `references/env-reference.md` for the full key list. Check the error message for the exact key name.

---

### Symptom: Agent directory or tool directory not found

**Cause:** `AGENT_DIRECTORY` or `TOOLS_DIRECTORY` in `.env` points to a path that doesn't exist relative to the working directory when the CLI is invoked.

**Fix:** Use absolute paths in `.env`, or ensure the working directory matches expectations. Check with `python -m agent_framework --env .env` from the project root.

---

## MCP tools

### Symptom: MCP tool appears in agent frontmatter but is not callable

**Cause A — MCP not enabled**
`MCP_ENABLED` is not set or is `false`.

**Cause B — MCP server connection failed**
The MCP server process failed to start or the HTTP endpoint is unreachable. Check `MCP_CONFIG_PATH` and verify the server is running.

**Cause C — Tool name mismatch**
The tool name in the agent's `tools:` list must match the name the MCP server registers. MCP tool names are case-sensitive.

**Fix:** Set `MCP_ENABLED=true`. Verify `MCP_CONFIG_PATH` points to a valid `.mcp.json`. Run the MCP server independently to confirm it starts.
