---
title: agent_framework.config
layout: default
sdk_page: true
---


# `agent_framework.config`

## API Summary

Configuration loading for the console agent host.

This module keeps `.env` parsing isolated from the runtime classes so the
execution layer can depend on a typed configuration object instead of raw
environment strings.

## Source

`src/agent_framework/config.py`

## Classes

- [`HostConfig`](config/HostConfig.html)

## Functions

### `load_host_config`

```python
def load_host_config(env_path: str | Path = '.env') -> HostConfig
```

Load typed host configuration from a `.env` file.

Args:
    env_path: Path to the `.env` file.

Returns:
    A fully resolved `HostConfig` instance.

### `read_optional_path_relative_to_env_file`

```python
def read_optional_path_relative_to_env_file(env_file: Path, key: str) -> Path | None
```

Return a filesystem path from a single key in ``.env``, or ``None`` if missing or empty.

Relative values resolve against the directory containing the env file (same
rules as :func:`load_host_config`).
