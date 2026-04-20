---
title: HostConfig
layout: default
sdk_page: true
---


# `HostConfig`

Module: [`agent_framework.config`](../config.html)

## API Summary

```python
class HostConfig
```

Resolved host configuration loaded from a `.env` file.

Attributes:
    openai_api_key: API key used by the default OpenAI-backed model driver.
    default_provider: Provider name assigned to agents that do not declare
        their own provider in frontmatter.
    default_model: Ordered list of models tried in priority order for agents
        that do not declare their own model and have no override in
        ``agent_models``.  The first reachable model wins.
    agent_directory: Directory containing Markdown-defined agents.
        From ``AGENT_DIRECTORY`` or, if set, ``AGENTS_LOCAL_PATH`` (same
        resolution rules; the ``*_LOCAL_PATH`` vars are optional overrides).
    tools_directory: From ``TOOLS_DIRECTORY`` or ``TOOLS_LOCAL_PATH``.
    world_directory: From ``WORLD_DIRECTORY`` or ``WORLD_LOCAL_PATH``.
    root_agent_id: Logical name of the root agent. The runtime resolves it
        against `agent_directory` and infers the `.md` extension.
    agent_models: Optional per-agent model overrides keyed by agent id or
        source file stem.  Values are ordered model lists (first = highest
        priority).  In ``.env`` use pipe ``|`` to separate agents and comma
        ``,`` to separate models: ``agent1=m1,m2|agent2=m3``.
    commands_directories: Directories to scan for command `.md` files.
        Loaded from ``COMMANDS_DIRECTORY`` / ``COMMANDS_DIRECTORIES`` env vars.
    mcp_config_path: Explicit path to MCP config JSON. When ``None``, the host
        walks up from cwd looking for ``.mcp.json`` (project) and falls back
        to ``~/.agent_framework/mcp.json`` (user). Loaded from ``MCP_CONFIG_PATH``.
    mcp_enabled: Whether to start and use MCP server connections.
        Loaded from ``MCP_ENABLED`` (default: true).
    missing_tool_policy: When an agent lists a tool in frontmatter that cannot
        be loaded (missing files, unknown name, import error). ``graceful``
        skips that tool for the model API and prompt metadata but logs and
        emits a trace event; ``strict`` fails the run when resolving tools.
        Loaded from ``MISSING_TOOL_POLICY`` (default: graceful).

## Attributes

- `agent_directory`
- `agent_models`
- `commands_directories`
- `default_model`
- `default_provider`
- `dial_api_key`
- `dial_api_version`
- `dial_base_url`
- `mcp_config_path`
- `mcp_enabled`
- `missing_tool_policy`
- `openai_api_key`
- `root_agent_id`
- `skills_catalog_max_tokens`
- `skills_directories`
- `tools_directory`
- `world_directory`

## Methods

### `model_for`

```python
def model_for(self, agent_id: str, fallback: tuple[str, ...] | None = None) -> tuple[str, ...]
```

Return the configured model list for an agent.

Args:
    agent_id: Runtime agent identifier or source file stem.
    fallback: Optional fallback model list if the agent is not
        explicitly configured in ``agent_models``.

Returns:
    Ordered tuple of model names to try (first = highest priority).
