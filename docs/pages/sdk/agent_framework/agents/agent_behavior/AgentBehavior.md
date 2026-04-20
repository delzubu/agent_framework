---
title: AgentBehavior
layout: default
sdk_page: true
---


# `AgentBehavior`

Module: [`agent_framework.agents.agent_behavior`](../agent_behavior.html)

## API Summary

```python
class AgentBehavior
```

Behavior extension attached to an agent at load time.

## Methods

### `attach`

```python
def attach(self, agent: 'Agent') -> None
```

No method docstring is available yet.

### `before_run`

```python
def before_run(self, agent: 'Agent', host: 'AgentHostProtocol', *, run: 'AgentRun', caller_id: str | None) -> AgentHookDecision | None
```

Run before the main loop after initial parameter state has been refreshed.

Behaviors may inspect `run.parameter_values`, `run.missing_parameters`,
and `run.invalid_parameters` here. If a behavior mutates prompt state or
seed inputs during bootstrap, it may call `agent.refresh_parameter_state(run)`
again to re-resolve the invocation contract before returning.

### `respond_to_callback`

```python
def respond_to_callback(self, agent: 'Agent', host: 'AgentHostProtocol', *, callee_id: str, prompt: str) -> str | None
```

No method docstring is available yet.

### `after_run`

```python
def after_run(self, agent: 'Agent', host: 'AgentHostProtocol', *, run: 'AgentRun', caller_id: str | None, result: 'AgentResult') -> AgentEndHookDecision | 'AgentResult' | None
```

Run after an agent produces a result.

Behaviors may:

- return `None` to leave the result unchanged
- return `AgentResult` to replace the result
- return `AgentEndHookDecision(continue_run=True, ...)` to request one
  more loop iteration with additional prompt fragments

Fragment handling policy:

- `prompt_fragments` replaces existing fragments with the same leading
  XML-like tag name and is the default update path
- `append_prompt_fragments` appends verbatim without replacement
