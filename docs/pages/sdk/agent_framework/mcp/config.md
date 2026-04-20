---
title: agent_framework.mcp.config
layout: default
sdk_page: true
---


# `agent_framework.mcp.config`

## API Summary

MCP server configuration loading.

## Source

`src/agent_framework/mcp/config.py`

## Functions

### `load_mcp_configs`

```python
def load_mcp_configs(*, env_path: Path | None = None, explicit_path: Path | None = None) -> dict[str, McpServerConfig]
```

Load MCP server configurations.

Search order (highest priority first):
1. ``explicit_path`` (if provided)
2. Walk up from cwd looking for ``.mcp.json`` (project config)
3. ``~/.agent_framework/mcp.json`` (user config)

Disabled servers are silently skipped.

Args:
    env_path: Optional .env file path (unused currently; reserved for future
        env-relative config resolution).
    explicit_path: Explicit path to an MCP config JSON file (from
        ``HostConfig.mcp_config_path``).

Returns:
    Dict mapping server name -> McpServerConfig.
