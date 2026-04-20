---
title: SkillRegistry
layout: default
sdk_page: true
---


# `SkillRegistry`

Module: [`agent_framework.skill`](../skill.html)

## API Summary

```python
class SkillRegistry
```

Discovers and caches SkillDefinitions from configured directories.

## Attributes

- `directories`

## Methods

### `from_config`

```python
def from_config(cls, config: 'Any') -> 'SkillRegistry'
```

No method docstring is available yet.

### `discover`

```python
def discover(self) -> None
```

Scan all directories, parse SKILL.md frontmatter, deduplicate by name.

### `get`

```python
def get(self, name: str) -> SkillDefinition
```

No method docstring is available yet.

### `get_all`

```python
def get_all(self) -> tuple[SkillDefinition, ...]
```

No method docstring is available yet.

### `filter`

```python
def filter(self, allowed: tuple[str, ...]) -> tuple[SkillDefinition, ...]
```

No method docstring is available yet.

### `reload`

```python
def reload(self) -> None
```

No method docstring is available yet.
