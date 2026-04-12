"""MCP server configuration loading."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_framework.mcp.types import McpServerConfig, McpTransport

_LOGGER = logging.getLogger(__name__)

_USER_CONFIG_PATH = Path.home() / ".agent_framework" / "mcp.json"
_PROJECT_CONFIG_NAME = ".mcp.json"
_MAX_WALK_DEPTH = 10


def load_mcp_configs(
    *,
    env_path: Path | None = None,
    explicit_path: Path | None = None,
) -> dict[str, McpServerConfig]:
    """Load MCP server configurations.

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
    """
    raw_configs: dict[str, dict] = {}

    # 3. User config (lowest priority - loaded first, overridden later)
    if _USER_CONFIG_PATH.exists():
        _merge_configs(raw_configs, _USER_CONFIG_PATH)

    # 2. Walk up from cwd for project config
    project_config = _find_project_config()
    if project_config:
        _merge_configs(raw_configs, project_config)

    # 1. Explicit path (highest priority)
    if explicit_path and Path(explicit_path).exists():
        _merge_configs(raw_configs, Path(explicit_path))

    configs: dict[str, McpServerConfig] = {}
    for name, raw in raw_configs.items():
        cfg = _parse_server_config(name, raw)
        if cfg is not None and not cfg.disabled:
            configs[name] = cfg
    return configs


def _find_project_config() -> Path | None:
    """Walk up from cwd looking for .mcp.json."""
    current = Path.cwd().resolve()
    for _ in range(_MAX_WALK_DEPTH):
        candidate = current / _PROJECT_CONFIG_NAME
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _merge_configs(target: dict, config_path: Path) -> None:
    """Parse a config file and merge its mcpServers into target."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {}) or {}
        if not isinstance(servers, dict):
            return
        for name, raw in servers.items():
            if isinstance(raw, dict):
                target[str(name)] = raw
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("MCP config %s: failed to load - %s", config_path, exc)


def _parse_server_config(name: str, raw: dict) -> McpServerConfig | None:
    """Parse a single server config dict."""
    try:
        transport_str = str(raw.get("type", "stdio")).lower()
        try:
            transport = McpTransport(transport_str)
        except ValueError:
            transport = McpTransport.STDIO

        args_raw = raw.get("args", []) or []
        args = tuple(str(a) for a in args_raw)
        env_raw = raw.get("env", {}) or {}
        env = {str(k): str(v) for k, v in env_raw.items() if k and v is not None}
        headers_raw = raw.get("headers", {}) or {}
        headers = {str(k): str(v) for k, v in headers_raw.items()}

        return McpServerConfig(
            name=name,
            transport=transport,
            command=str(raw.get("command", "")),
            args=args,
            env=env,
            url=str(raw.get("url", "")),
            headers=headers,
            timeout=int(raw.get("timeout", 30)),
            disabled=bool(raw.get("disabled", False)),
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("MCP config server %r: failed to parse - %s", name, exc)
        return None


__all__ = ["load_mcp_configs"]
