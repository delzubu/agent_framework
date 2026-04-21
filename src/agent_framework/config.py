"""Configuration loading for the console agent host.

This module keeps `.env` parsing isolated from the runtime classes so the
execution layer can depend on a typed configuration object instead of raw
environment strings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

_LOGGER = logging.getLogger(__name__)
DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES = 32768


@dataclass(frozen=True, slots=True)
class HostConfig:
    """Resolved host configuration loaded from a `.env` file.

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
    """

    openai_api_key: str = ""
    default_provider: str = "openai"
    default_model: tuple[str, ...] = ("gpt-4o-mini",)
    agent_directory: Path = field(default_factory=lambda: Path("agents"))
    tools_directory: Path = field(default_factory=lambda: Path("tools"))
    world_directory: Path = field(default_factory=lambda: Path("world"))
    root_agent_id: str = "root"
    agent_models: dict[str, tuple[str, ...]] = field(default_factory=dict)
    skills_directories: tuple[Path, ...] = field(default_factory=tuple)
    skills_catalog_max_tokens: int = 2000
    # DIAL provider credentials
    dial_base_url: str = ""
    dial_api_version: str = "2024-10-21"
    dial_api_key: str = ""
    commands_directories: tuple[Path, ...] = field(default_factory=tuple)
    mcp_config_path: Path | None = None
    mcp_enabled: bool = True
    missing_tool_policy: Literal["graceful", "strict"] = "graceful"
    memory_enabled: bool = True
    memory_auto_store_threshold_bytes: int = DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES
    memory_builtin_tools_enabled: bool = True
    memory_default_projection_mode: str = "catalog_and_selected_content"
    memory_backend_kind: str = "memory"
    memory_query_provider_kind: str = "catalog"
    memory_projector_kind: str = "xml"

    def model_for(self, agent_id: str, fallback: tuple[str, ...] | None = None) -> tuple[str, ...]:
        """Return the configured model list for an agent.

        Args:
            agent_id: Runtime agent identifier or source file stem.
            fallback: Optional fallback model list if the agent is not
                explicitly configured in ``agent_models``.

        Returns:
            Ordered tuple of model names to try (first = highest priority).
        """
        if agent_id in self.agent_models:
            return self.agent_models[agent_id]
        if fallback:
            return fallback
        return self.default_model


def load_host_config(env_path: str | Path = ".env") -> HostConfig:
    """Load typed host configuration from a `.env` file.

    Args:
        env_path: Path to the `.env` file.

    Returns:
        A fully resolved `HostConfig` instance.
    """
    env_file = Path(env_path)
    values = _parse_env_file(env_file)
    if env_file.exists():
        _LOGGER.debug("loaded host config from %s", env_file.resolve())
    else:
        _LOGGER.debug("env file not found at %s, using defaults", env_file.resolve())
    default_provider = values.get("DEFAULT_PROVIDER", "openai")
    raw_default_model = values.get("DEFAULT_MODEL", "gpt-4o-mini")
    default_model: tuple[str, ...] = tuple(
        m.strip() for m in raw_default_model.split(",") if m.strip()
    ) or ("gpt-4o-mini",)
    agent_directory = _resolve_config_path(
        env_file,
        values.get("AGENTS_LOCAL_PATH", "").strip() or values.get("AGENT_DIRECTORY", "").strip(),
        default_relative="agents",
    )
    tools_directory = _resolve_config_path(
        env_file,
        values.get("TOOLS_LOCAL_PATH", "").strip() or values.get("TOOLS_DIRECTORY", "").strip(),
        default_relative="tools",
    )
    world_directory = _resolve_config_path(
        env_file,
        values.get("WORLD_LOCAL_PATH", "").strip() or values.get("WORLD_DIRECTORY", "").strip(),
        default_relative="world",
    )
    root_agent_id = values.get("ROOT_AGENT", "root").strip()
    raw_multi = values.get("SKILLS_DIRECTORIES", "").strip() or values.get("SKILLS_LOCAL_DIRECTORIES", "").strip()
    raw_single = values.get("SKILLS_DIRECTORY", "").strip() or values.get("SKILLS_LOCAL_PATH", "").strip()
    if raw_multi:
        skills_directories: tuple[Path, ...] = tuple(
            (env_file.parent / p.strip()).resolve()
            for p in raw_multi.split(",")
            if p.strip()
        )
    elif raw_single:
        skills_directories = ((env_file.parent / raw_single.strip()).resolve(),)
    else:
        default = (env_file.parent / "skills").resolve()
        skills_directories = (default,) if default.is_dir() else ()
    raw_max_tokens = values.get("SKILLS_CATALOG_MAX_TOKENS", "")
    skills_catalog_max_tokens = int(raw_max_tokens) if raw_max_tokens.strip() else 2000
    # Commands directories
    raw_commands_multi = values.get("COMMANDS_DIRECTORIES", "")
    raw_commands_single = values.get("COMMANDS_DIRECTORY", "")
    if raw_commands_multi:
        commands_directories: tuple[Path, ...] = tuple(
            (env_file.parent / p.strip()).resolve()
            for p in raw_commands_multi.split(",")
            if p.strip()
        )
    elif raw_commands_single:
        commands_directories = ((env_file.parent / raw_commands_single.strip()).resolve(),)
    else:
        commands_directories = ()

    # MCP config
    raw_mcp_config_path = values.get("MCP_CONFIG_PATH", "").strip()
    mcp_config_path: Path | None = (env_file.parent / raw_mcp_config_path).resolve() if raw_mcp_config_path else None
    mcp_enabled = values.get("MCP_ENABLED", "true").strip().lower() not in ("false", "0", "no")
    raw_missing_tool = values.get("MISSING_TOOL_POLICY", "graceful").strip().lower()
    if raw_missing_tool in ("strict", "fail", "error"):
        missing_tool_policy: Literal["graceful", "strict"] = "strict"
    else:
        missing_tool_policy = "graceful"
    memory_enabled = values.get("MEMORY_ENABLED", "true").strip().lower() not in ("false", "0", "no")
    raw_memory_threshold = values.get("MEMORY_AUTO_STORE_THRESHOLD_BYTES", "").strip()
    memory_auto_store_threshold_bytes = (
        int(raw_memory_threshold) if raw_memory_threshold else DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES
    )
    memory_builtin_tools_enabled = (
        values.get("MEMORY_BUILTIN_TOOLS_ENABLED", "true").strip().lower() not in ("false", "0", "no")
    )
    memory_default_projection_mode = (
        values.get("MEMORY_DEFAULT_PROJECTION_MODE", "catalog_and_selected_content").strip()
        or "catalog_and_selected_content"
    )
    memory_backend_kind = values.get("MEMORY_BACKEND", "memory").strip() or "memory"
    memory_query_provider_kind = values.get("MEMORY_QUERY_PROVIDER", "catalog").strip() or "catalog"
    memory_projector_kind = values.get("MEMORY_PROJECTOR", "xml").strip() or "xml"
    return HostConfig(
        openai_api_key=values.get("OPENAI_API_KEY", ""),
        default_provider=default_provider,
        default_model=default_model,
        agent_directory=agent_directory,
        tools_directory=tools_directory,
        world_directory=world_directory,
        root_agent_id=root_agent_id,
        agent_models=_parse_agent_models(values.get("AGENT_MODELS", "")),
        skills_directories=skills_directories,
        skills_catalog_max_tokens=skills_catalog_max_tokens,
        dial_base_url=values.get("DIAL_BASE_URL", ""),
        dial_api_version=values.get("DIAL_API_VERSION", "2024-10-21"),
        dial_api_key=values.get("DIAL_API_KEY", ""),
        commands_directories=commands_directories,
        mcp_config_path=mcp_config_path,
        mcp_enabled=mcp_enabled,
        missing_tool_policy=missing_tool_policy,
        memory_enabled=memory_enabled,
        memory_auto_store_threshold_bytes=memory_auto_store_threshold_bytes,
        memory_builtin_tools_enabled=memory_builtin_tools_enabled,
        memory_default_projection_mode=memory_default_projection_mode,
        memory_backend_kind=memory_backend_kind,
        memory_query_provider_kind=memory_query_provider_kind,
        memory_projector_kind=memory_projector_kind,
    )


def _resolve_config_path(env_file: Path, raw: str, *, default_relative: str) -> Path:
    """Resolve a path from ``.env``: absolute paths as-is, else relative to the env file's parent."""
    text = (raw or "").strip() or default_relative
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate.resolve()
    return (env_file.parent / candidate).resolve()


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal `.env` file into a string dictionary."""
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        values[key.strip()] = _strip_quotes(raw_value.strip())
    return values


def _parse_agent_models(raw_value: str) -> dict[str, tuple[str, ...]]:
    """Parse the ``AGENT_MODELS`` mapping.

    Format: ``agent1=model1,model2|agent2=model3``

    Agents are separated by ``|``; models for each agent are separated by
    ``,``.  This keeps the value copy-pasteable as a comma-separated model
    list while still supporting per-agent overrides.
    """
    if not raw_value:
        return {}
    mappings: dict[str, tuple[str, ...]] = {}
    for item in raw_value.split("|"):
        chunk = item.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        models = tuple(m.strip() for m in value.split(",") if m.strip())
        if models:
            mappings[key.strip()] = models
    return mappings


def _strip_quotes(value: str) -> str:
    """Remove matching single or double quotes around a value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_optional_path_relative_to_env_file(env_file: Path, key: str) -> Path | None:
    """Return a filesystem path from a single key in ``.env``, or ``None`` if missing or empty.

    Relative values resolve against the directory containing the env file (same
    rules as :func:`load_host_config`).
    """
    values = _parse_env_file(env_file)
    raw = values.get(key, "").strip()
    if not raw:
        return None
    return _resolve_config_path(env_file, raw, default_relative=".")


__all__ = [
    "DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES",
    "HostConfig",
    "load_host_config",
    "read_optional_path_relative_to_env_file",
]
