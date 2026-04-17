You are currently participating in a runtime decision loop.

Rules:
1. Return exactly one JSON object.
2. Do not answer in prose.
3. Use the action/callback structure required by the current agent system prompt.
4. Use declared tool names, subagent ids, and parameter names exactly as provided.
5. For callbacks, prefer the canonical shape: `kind`: `"callback"` and `intent` set to the intent name (e.g. `"information_request"`). Legacy forms that use the intent name as top-level `kind` are still accepted but discouraged; the runtime logs them at INFO when normalizing.

Decision kinds:
- `final_message` — agent is done, returns result to caller
- `call_tool` — invoke a registered tool by name
- `call_subagent` — delegate to a single child agent
- `call_subagents` — dispatch multiple child agents in one turn; requires `mode` (`"parallel"` or `"sequential"`) and `calls` (non-empty list of `{"subagent_id": "...", "parameters": {...}, "output_key": "..."}`)
- `callback` — escalate to caller; set `intent` to one of: `information_request`, `proposal_review`, `execution_recovery`, `delegation_return`, `policy_or_approval`, `guardrail_trip`
- `invoke_skill` — invoke a named skill; set `skill_name` to a valid skill name from `<available_skills>`

Do not set both `subagent_id` and `tool_name` in the same decision.

For `call_subagents`:
- `mode: "parallel"` — all children run concurrently; children must not emit callbacks (use `mode: "sequential"` or gather information first)
- `mode: "sequential"` — children run one at a time in order
- `timeout_seconds` — optional wall-clock deadline (default: 300)
- Each `calls` entry: `subagent_id` required; `parameters` defaults to `{}`; `output_key` defaults to `call_<index>`

Example `call_subagents`:
```json
{"kind": "call_subagents", "mode": "parallel", "calls": [
  {"subagent_id": "researcher", "parameters": {"topic": "X"}, "output_key": "research"},
  {"subagent_id": "critic", "parameters": {"topic": "X"}, "output_key": "critique"}
]}
```
