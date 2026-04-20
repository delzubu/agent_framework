---
title: ConsoleUserCommunication
layout: default
sdk_page: true
---


# `ConsoleUserCommunication`

Module: [`agent_framework.console_communication`](../console_communication.html)

## API Summary

```python
class ConsoleUserCommunication
```

UserCommunication implementation backed by sys.stdin / sys.stdout.

All blocking I/O is run in a thread via ``asyncio.to_thread`` so it is
safe to await from an async context.

Permission decisions can be remembered for the entire session by keying on
``(tool_name, action)``.  When a previous allow/deny is remembered the
prompt is suppressed.

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
