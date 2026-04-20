---
title: AgentRegistry
layout: default
sdk_page: true
---


# `AgentRegistry`

Module: [`agent_framework.agent_registry`](../agent_registry.html)

## API Summary

```python
class AgentRegistry
```

Discovers and caches Agent instances from configured directories.

Agents are discovered eagerly (catalog built at startup) but loaded lazily
(``Agent.from_markdown`` called only on first ``get()``).

Attributes:
    directories: Directories to scan for ``*.md`` agent definitions.
    config: Host config used to resolve models and providers when loading agents.
    _catalog: Maps agent_id → markdown path.
    _cache: Maps agent_id or str(source_path) → loaded Agent.

## Attributes

- `config`
- `directories`

## Methods

### `from_config`

```python
def from_config(cls, config: Any) -> 'AgentRegistry'
```

Build an AgentRegistry from a HostConfig.

### `discover`

```python
def discover(self) -> None
```

Scan all directories and build the agent_id→path catalog.

Parses frontmatter ``id`` field; falls back to file stem on any error.
First directory wins on duplicate ids.

### `get`

```python
def get(self, agent_id: str, *, base_dir: Path | None = None) -> 'Agent'
```

Resolve an agent by id, path, sibling, catalog, or default directory.

Resolution order (matches original AgentHost.get_agent logic):
1. Cache hit (by id or str(source_path))
2. Explicit file path if agent_id is an existing path
3. Sibling ``<base_dir>/<agent_id>.md``
4. Catalog lookup
5. Default directory ``<config.agent_directory>/<agent_id>.md``
6. KeyError

### `list_names`

```python
def list_names(self) -> tuple[str, ...]
```

Return all discovered agent ids.

### `reload`

```python
def reload(self) -> None
```

Clear all caches and re-discover from disk.

### `load_from_path`

```python
def load_from_path(self, source_path: Path) -> 'Agent'
```

Load an Agent from markdown, apply model overrides, and cache it.
