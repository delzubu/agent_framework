---
title: CommandRegistry
layout: default
sdk_page: true
---


# `CommandRegistry`

Module: [`agent_framework.command`](../command.html)

## API Summary

```python
class CommandRegistry
```

Discovers and caches CommandDefinitions from configured directories.

Commands are fully loaded at discovery time (prompts are cheap; no Python
sidecars).

Attributes:
    directories: Directories to scan for ``*.md`` command files.
    _cache: Maps command name → CommandDefinition.

## Attributes

- `directories`

## Methods

### `from_config`

```python
def from_config(cls, config: Any) -> 'CommandRegistry'
```

Build a CommandRegistry from a HostConfig.

### `discover`

```python
def discover(self) -> None
```

Scan all directories and fully parse every ``*.md`` command file.

Missing or malformed frontmatter is logged as WARNING and skipped.
First directory wins on duplicate command names.

### `get`

```python
def get(self, name: str) -> CommandDefinition
```

Return a CommandDefinition by name.  Raises KeyError if not found.

### `get_all`

```python
def get_all(self) -> tuple[CommandDefinition, ...]
```

Return all discovered commands.

### `reload`

```python
def reload(self) -> None
```

Clear cache and re-discover from disk.
