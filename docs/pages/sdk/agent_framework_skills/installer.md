---
title: agent_framework_skills.installer
layout: default
sdk_page: true
---


# `agent_framework_skills.installer`

## API Summary

Skill installer: copies bundled skills into known agentic tool directories.

## Source

`src/agent_framework_skills/installer.py`

## Classes

- [`InstallTarget`](installer/InstallTarget.html)

## Functions

### `list_targets`

```python
def list_targets() -> list[InstallTarget]
```

Return all known targets with their resolved paths and existence status.

### `install`

```python
def install(*, target: Path | None = None, dry_run: bool = False, force: bool = False) -> list[tuple[str, str]]
```

Copy all bundled skills into target directories.

Returns a list of (path, status) tuples where status is one of
'installed', 'skipped', 'dry-run', 'error'.
