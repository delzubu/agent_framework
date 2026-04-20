---
title: EvaluateResultBody
layout: default
sdk_page: true
---


# `EvaluateResultBody`

Module: [`agent_framework_evaluator.app`](../app.html)

## API Summary

```python
class EvaluateResultBody(BaseModel)
```

POST body for post-run scoring. ``evaluator_prompt`` is never sent to the agent.

## Attributes

- `evaluator_prompt`
- `log_level`
- `result_field`
- `session_id`
