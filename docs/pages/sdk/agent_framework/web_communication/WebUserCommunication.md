---
title: WebUserCommunication
layout: default
sdk_page: true
---


# `WebUserCommunication`

Module: [`agent_framework.web_communication`](../web_communication.html)

## API Summary

```python
class WebUserCommunication
```

Queue-based user I/O for driving the agent from a web client.

Uses a thread-safe :class:`queue.Queue` so inputs submitted from the FastAPI
WebSocket thread unblock :func:`read_user_input` running under
``asyncio.run`` in a worker thread. Each wait is assigned a ``prompt_id`` for
HTTP correlation.

## Methods

### `cancel_wait`

```python
def cancel_wait(self) -> bool
```

Unblock a pending :meth:`read_user_input` with ``None`` (session closed or disconnect).

### `submit_user_input`

```python
def submit_user_input(self, text: str | None, *, prompt_id: str | None = None) -> bool
```

Deliver one line of user input to the current wait.

If ``prompt_id`` is given, it must match the active wait. If omitted, any
active wait accepts the value (WebSocket / legacy). Returns ``False`` if
nothing is waiting or the id does not match.

### `drain_outbox`

```python
def drain_outbox(self) -> list[dict[str, Any]]
```

No method docstring is available yet.

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
