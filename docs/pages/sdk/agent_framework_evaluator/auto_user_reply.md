---
title: agent_framework_evaluator.auto_user_reply
layout: default
sdk_page: true
---


# `agent_framework_evaluator.auto_user_reply`

## API Summary

Headless replies for WebUserCommunication prompts (agent evaluator only).

## Source

`src/agent_framework_evaluator/auto_user_reply.py`

## Functions

### `reply_text_for_outbox_item`

```python
def reply_text_for_outbox_item(item: dict[str, Any], *, case_run_mode: str = 'standard') -> str | None
```

Return text to submit for a pending outbox item, or ``None`` if the client should answer.

With ``case_run_mode="no_callbacks"``, all prompts, questions, confirmations, and
permissions are auto-answered so the run completes without user interaction.
With ``case_run_mode="standard"``, every outbox item is forwarded to the client
unanswered, allowing the user to respond manually.
