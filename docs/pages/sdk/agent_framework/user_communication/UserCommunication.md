---
title: UserCommunication
layout: default
sdk_page: true
---


# `UserCommunication`

Module: [`agent_framework.user_communication`](../user_communication.html)

## API Summary

```python
class UserCommunication(Protocol)
```

Async protocol for host ↔ user communication.

All implementations must be safe to call from a sync context via the
host's ``_run_user_comm_coro()`` bridge.

The default implementation for console sessions is
``ConsoleUserCommunication``.  For headless / test use, ``NullUserCommunication``
returns safe defaults without any I/O.

## Methods

### `send_message`

```python
async def send_message(self, text: str, *, role: str = 'assistant') -> None
```

Send a message to the user.

### `ask_question`

```python
async def ask_question(self, prompt: str, *, options: tuple[str, ...] | None = None, allow_freetext: bool = True) -> str
```

Ask the user a question and return the answer.

### `ask_confirmation`

```python
async def ask_confirmation(self, prompt: str, *, default: bool = False) -> bool
```

Ask the user a yes/no question and return True for yes.

### `request_permission`

```python
async def request_permission(self, request: PermissionRequest) -> PermissionDecision
```

Ask whether a gated action is allowed.

### `read_user_input`

```python
async def read_user_input(self, prompt: str = '') -> str | None
```

Read a line of input from the user.  Returns None on EOF / disconnect.

### `stream_text`

```python
async def stream_text(self, chunks: AsyncIterator[str]) -> None
```

Stream text chunks to the user.

The default strategy is to concatenate all chunks and call
``send_message`` once.  Implementations may override this for
real-time streaming (e.g. SSE or WebSocket pushes).
