---
title: ToolRegistry
layout: default
sdk_page: true
---


# `ToolRegistry`

Module: [`agent_framework.tool_registry`](../tool_registry.html)

## API Summary

```python
class ToolRegistry
```

Discovers and caches Tool instances from configured directories.

Tools are discovered eagerly (catalog built at startup) but loaded lazily
(Python sidecar imported only on first ``get()``).  Programmatically
registered tools (built-in tools, MCP-bridged tools) take priority over
disk-discovered tools.

Attributes:
    directories: Directories to scan for ``<name>.md`` tool definitions.
    _catalog: Maps tool name → markdown path (disk-discovered tools).
    _cache: Maps tool name → loaded Tool instance.
    _programmatic: Maps tool name → directly registered Tool instance.

## Attributes

- `directories`

## Methods

### `from_config`

```python
def from_config(cls, config: Any) -> 'ToolRegistry'
```

Build a ToolRegistry from a HostConfig.

### `discover`

```python
def discover(self) -> None
```

Scan all directories and build the name→path catalog.

Does NOT load Python sidecars — that happens lazily on ``get()``.

### `register`

```python
def register(self, tool: 'Tool') -> None
```

Register a Tool instance directly (built-ins, MCP bridges, tests).

### `get`

```python
def get(self, name: str) -> 'Tool'
```

Return a Tool by name, loading it lazily from the catalog if needed.

Resolution order: programmatic → cache → catalog → KeyError.

### `list_names`

```python
def list_names(self) -> tuple[str, ...]
```

Return all known tool names (programmatic + catalog).

### `get_all`

```python
def get_all(self) -> tuple['Tool', ...]
```

Load and return all known tools.

### `reload`

```python
def reload(self) -> None
```

Clear all caches and re-discover from disk.
