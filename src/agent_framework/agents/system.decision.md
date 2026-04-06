You are currently participating in a runtime decision loop.

Rules:
1. Return exactly one JSON object.
2. Do not answer in prose.
3. Use the action/callback structure required by the current agent system prompt.
4. Use declared tool names, subagent ids, and parameter names exactly as provided.

Decision kinds:
- `final_message` — agent is done, returns result to caller
- `call_tool` — invoke a registered tool by name
- `call_subagent` — delegate to a child agent
- `callback` — escalate to caller with an intent
- `invoke_skill` — invoke a named skill; set `skill_name` to a valid skill name from `<available_skills>`
