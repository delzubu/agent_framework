---
title: NullUserCommunication
layout: default
sdk_page: true
---


# `NullUserCommunication`

Module: [`agent_framework.user_communication`](../user_communication.html)

## API Summary

```python
class NullUserCommunication
```

No-op user communication for headless and test contexts.

All methods return safe defaults without performing any I/O.

## Methods

### `send_message`

```python
async def send_message(self, text: str, *, role: str = 'assistant') -> None
```

No method docstring is available yet.

### `ask_question`

```python
async def ask_question(self, prompt: str, *, options: tuple[str, ...] | None = None, allow_freetext: bool = True) -> str
```

No method docstring is available yet.

### `ask_confirmation`

```python
async def ask_confirmation(self, prompt: str, *, default: bool = False) -> bool
```

No method docstring is available yet.

### `request_permission`

```python
async def request_permission(self, request: PermissionRequest) -> PermissionDecision
```

No method docstring is available yet.

### `read_user_input`

```python
async def read_user_input(self, prompt: str = '') -> str | None
```

No method docstring is available yet.

### `stream_text`

```python
async def stream_text(self, chunks: AsyncIterator[str]) -> None
```

No method docstring is available yet.
