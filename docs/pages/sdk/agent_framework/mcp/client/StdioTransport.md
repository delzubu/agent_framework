---
title: StdioTransport
layout: default
sdk_page: true
---


# `StdioTransport`

Module: [`agent_framework.mcp.client`](../client.html)

## API Summary

```python
class StdioTransport
```

MCP transport over subprocess stdin/stdout (newline-delimited JSON-RPC 2.0).

## Methods

### `start`

```python
async def start(self) -> None
```

No method docstring is available yet.

### `stop`

```python
async def stop(self) -> None
```

No method docstring is available yet.

### `request`

```python
async def request(self, method: str, params: dict | None = None, timeout: int = 30) -> Any
```

No method docstring is available yet.

### `notify`

```python
async def notify(self, method: str, params: dict | None = None) -> None
```

No method docstring is available yet.

### `last_stderr`

```python
def last_stderr(self) -> str
```

No method docstring is available yet.
