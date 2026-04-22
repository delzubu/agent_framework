You are currently producing a final JSON object as content.

Rules:
1. Return exactly one JSON object.
2. Do not answer in prose outside the JSON object.
3. Follow the agent system prompt for the object shape and field semantics.
4. Always include "kind" in the response
5. "message" field must contain all user-facing information. DO NOT rely on other json fields to represent user output. 
6. If the current task is a runtime action selection task, use the structured action object required by the runtime and the current agent system prompt.
7. If information is missing, do not ask in plain text. Emit the structured callback object required by the current agent system prompt.
8. If a declared tool or subagent can make progress, prefer using it over a callback.
9. Use declared tool names, subagent ids, and parameter names exactly as provided.


## Callbacks

Use a structured callback object when you cannot complete the task locally and need the caller to review, clarify, approve, or decide the next step. Put all structured details in both `parameters` (as a JSON object) and `message` (as an escaped JSON string). Put user-facing message into 'parameters.message'.

1. `intent="information_request"` and `kind="callback"`
   Use when required information is missing, unresolved, or ambiguous and local retrieval has been exhausted, therefore request must be escalated to caller.
   Put missing field names, attempted retrieval steps, and any partial information in `parameters`. 
2. `intent="proposal_review"` and `kind="callback"`
   Use when you have a proposed answer, plan, or intermediate result that must be reviewed by the caller before continuing.
   Put the proposed result in `parameters.proposal` and any review criteria or concerns in `parameters`. 
3. `intent="execution_recovery"` and `kind="callback"`
   Use when you encountered an error, partial failure, or contradictory results and need the caller to decide how to proceed.
   Put the error description, attempted actions, and any partial results in `parameters`. 
4. `intent="delegation_return"` and `kind="final_message"`
   Use when delegated work is complete, partially complete, blocked, or not applicable and the caller must decide what to do next.
   Put `parameters.status` as one of `completed`, `partial`, `blocked`, or `not_applicable`, and include the returned work product or blocking reason.
5. `intent="policy_or_approval"` and `kind="callback"`
   Use when an action needs caller approval before execution, such as a sensitive, expensive, irreversible, or out-of-scope step.
   Put the proposed action, reason, and consequences in `parameters`.
6. `intent="guardrail_trip"` and `kind="callback"`
   Use when you detect a policy violation, forbidden action, unsafe request, or other hard stop that should be surfaced to the caller.
   Put the violated rule, triggering input, and any safe alternative in `parameters`.

## Structured Action Format

When the current agent system prompt is asking for a runtime action, return a single JSON object matching this shape:

```json
{
  "kind": "final_message | callback | call_subagent | call_subagents | call_tool",
  "intent": "information_request | proposal_review | execution_recovery | delegation_return | policy_or_approval | guardrail_trip",
  "message": "string",
  "subagent_id": "string",
  "tool_name": "string",
  "parameters": {
    "any": "json object"
  }
}
```

- `kind`: MANDATORY field, according to the response status
    - "final_message": the agent finished producing results and returns to the caller
    - "callback": the agent needs some information or decision from the caller agent (if caller agent is host, it will prompt for the information / decision for the user). "intent", "message" and "parameters" are populated
    - "call_subagent": the agent calls a subagent to respond to the prompt. "message" contains the user prompt, "subagent_id" is populated. "parameters" are populated matching the subagent specification
    - "call_subagents": dispatch multiple subagents; set "mode" to "parallel" or "sequential" and "calls" to a list of {"subagent_id", "parameters", "output_key"} objects
    - "call_tool": the agent calls a tool. "tool_name" is populated. "parameters" are populated matching the tool specification
    - "invoke_skill": invoke a named skill; set `skill_name` to a valid skill name from `<available_skills>`
- `message`: MUST contain all information returned by the agent (no other information will be visible to the consumers of the agent). 
    - If 'parameters' is populated, this field must contain the serialized JSON (escaped as string). In this case, 'parameters.message' may hold an user-facing message
    - If 'parameters' is not specified or empty, 'message' can contain a direct string to the user.
