# Callback routing — host-side reference

When an agent emits a `callback`, `callback_to_caller`, `request_user_input`, or `request_resolution` decision, the host receives an `AgentResult` with `status="waiting"`.

For the full reference on what each callback kind means, how to inspect `callback_intent`, and routing patterns:

**Load `authoring-agents` skill → `references/callback-handling.md`**

The callback contract is defined in that skill because callback *authoring* (what the agent emits) and *handling* (what the host receives) are two sides of the same spec — keeping them together avoids drift.

## Quick host-side reference

When you receive `result.status == "waiting"`:

```python
result = host.run(agent_id="my_agent", parameters={...})
if result.status == "waiting":
    intent = result.callback_intent   # e.g. "information_request"
    message = result.message          # human-readable prompt
    params = result.parameters        # structured payload from the agent
    # resume:
    resumed = host.resume(run_id=result.run_id, response={"answer": "..."})
```

Common `callback_intent` values: `information_request`, `proposal_review`, `execution_recovery`, `policy_or_approval`, `guardrail_trip`.
