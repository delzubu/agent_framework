---
title: SessionRunner
layout: default
sdk_page: true
---


# `SessionRunner`

Module: [`agent_framework_evaluator.runtime.session_runner`](../session_runner.html)

## API Summary

```python
class SessionRunner
```

No class docstring is available yet.

## Methods

### `run_once`

```python
def run_once(self, *, agent_id: str, prompt: str, setup_path: Path | None = None, user_comm: Any | None = None, runtime_tracer: Any | None = None, session_id: str | None = None, on_first_llm_call: Callable[[Any], None] | None = None) -> dict[str, object]
```

No method docstring is available yet.

### `suite_teardown_if_any`

```python
def suite_teardown_if_any(self) -> None
```

Invoke ``suite_teardown`` on the last loaded setup module, if present.
