"""Configuration loading for the console agent host.

This module keeps `.env` parsing isolated from the runtime classes so the
execution layer can depend on a typed configuration object instead of raw
environment strings.

## Source priority (lowest → highest)

1. Code defaults (field defaults on ``_RawSettings``)
2. Environment variables (``os.environ``)
3. Startup ``.env`` file (the path passed to :func:`load_host_config`)
4. CLI-specified ``.env`` file (``cli_env`` argument)
5. Explicit ``overrides`` dict (for programmatic / test use)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

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
        memory_*: Configuration for the scoped memory subsystem. These fields
            control whether memory is enabled, when large parameters are
            auto-stored, which read tools are injected by default, and which
            extra non-session scopes are visible to runs.
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
    memory_global_scopes: tuple[str, ...] = ()
    memory_group_scopes: tuple[str, ...] = ()
    memory_use_case_scopes: tuple[str, ...] = ()
    memory_enable_agent_scope: bool = False

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


class _RawSettings(BaseSettings):
    """Internal pydantic-settings model for multi-source configuration loading.

    All values are stored as raw strings; type conversion and path resolution
    happen in :func:`load_host_config`.

    Source priority (highest → lowest):

    1. ``overrides`` passed to :meth:`load` (init kwargs)
    2. CLI-specified ``.env`` file
    3. Startup ``.env`` file
    4. Environment variables (``os.environ``)
    5. Field defaults below

    Field names are the lowercase equivalents of the corresponding env-var
    names, so pydantic-settings resolves them automatically (e.g.
    ``openai_api_key`` ↔ ``OPENAI_API_KEY``).
    """

    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    # ── Core ──────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    default_provider: str = "openai"
    default_model: str = "gpt-4o-mini"

    # ── Directory paths (raw strings; resolved relative to env-file dir) ──
    agent_directory: str = "agents"
    agents_local_path: str = ""
    tools_directory: str = "tools"
    tools_local_path: str = ""
    world_directory: str = "world"
    world_local_path: str = ""
    root_agent: str = "root"

    # ── Agent model overrides ─────────────────────────────────────────────
    agent_models: str = ""

    # ── Skills ────────────────────────────────────────────────────────────
    skills_directory: str = ""
    skills_local_path: str = ""
    skills_directories: str = ""
    skills_local_directories: str = ""
    skills_catalog_max_tokens: str = ""

    # ── DIAL provider ─────────────────────────────────────────────────────
    dial_base_url: str = ""
    dial_api_version: str = "2024-10-21"
    dial_api_key: str = ""

    # ── Commands ──────────────────────────────────────────────────────────
    commands_directory: str = ""
    commands_directories: str = ""

    # ── MCP ───────────────────────────────────────────────────────────────
    mcp_config_path: str = ""
    mcp_enabled: str = "true"

    # ── Policies ──────────────────────────────────────────────────────────
    missing_tool_policy: str = "graceful"

    # ── Memory subsystem ──────────────────────────────────────────────────
    memory_enabled: str = "true"
    memory_auto_store_threshold_bytes: str = ""
    memory_builtin_tools_enabled: str = "true"
    memory_default_projection_mode: str = "catalog_and_selected_content"
    memory_backend: str = "memory"
    memory_query_provider: str = "catalog"
    memory_projector: str = "xml"
    memory_global_scopes: str = ""
    memory_group_scopes: str = ""
    memory_use_case_scopes: str = ""
    memory_enable_agent_scope: str = "false"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        **_kwargs: Any,
    ) -> tuple[Any, ...]:
        # dotenv files take priority over env vars so that a project-local
        # .env can override system-wide variables set in the shell.
        return (init_settings, dotenv_settings, env_settings)

    @classmethod
    def load(
        cls,
        startup_env: Path | None = None,
        cli_env: Path | None = None,
        overrides: dict[str, str] | None = None,
    ) -> "_RawSettings":
        """Merge all configuration sources and return a populated instance.

        Args:
            startup_env: Path to the startup-directory ``.env`` file.
            cli_env: Optional second ``.env`` file (e.g. from ``--env`` CLI
                flag) that takes priority over ``startup_env``.
            overrides: Explicit key/value pairs that override every other
                source.  Keys may be uppercase env-var names or lowercase
                field names.
        """
        env_files: list[Path] = []
        if startup_env is not None and startup_env.exists():
            env_files.append(startup_env)
        if cli_env is not None and cli_env.exists():
            env_files.append(cli_env)

        # Accept uppercase env-var names in overrides and convert to field
        # names (lowercase) so pydantic init kwargs work correctly.
        kwargs: dict[str, Any] = {k.lower(): v for k, v in (overrides or {}).items()}

        return cls(
            _env_file=tuple(env_files) if env_files else None,
            _env_file_encoding="utf-8",
            **kwargs,
        )


def load_host_config(
    env_path: str | Path = ".env",
    *,
    cli_env: str | Path | None = None,
    overrides: dict[str, str] | None = None,
) -> HostConfig:
    """Load typed host configuration from layered sources.

    Sources are merged from lowest to highest priority:

    1. Code defaults
    2. Environment variables (``os.environ``)
    3. Startup ``.env`` file (``env_path``)
    4. CLI-specified ``.env`` file (``cli_env``)
    5. ``overrides`` dict (for programmatic / test use)

    Args:
        env_path: Path to the startup ``.env`` file (may not exist).
        cli_env: Optional second ``.env`` file whose values override those
            from ``env_path``.
        overrides: Explicit string values that override every other source.
            Keys may be uppercase env-var names (``"OPENAI_API_KEY"``) or
            lowercase field names (``"openai_api_key"``).

    Returns:
        A fully resolved ``HostConfig`` instance.
    """
    startup_env = Path(env_path)
    cli_env_path = Path(cli_env) if cli_env else None

    if startup_env.exists():
        _LOGGER.debug("loaded host config from %s", startup_env.resolve())
    else:
        _LOGGER.debug("env file not found at %s, using defaults", startup_env.resolve())

    raw = _RawSettings.load(startup_env, cli_env_path, overrides)

    # Base directory for relative path resolution: the startup env file's
    # parent when it exists, otherwise the current working directory.
    base_dir = startup_env.parent if startup_env.exists() else Path.cwd()

    default_model: tuple[str, ...] = tuple(
        m.strip() for m in raw.default_model.split(",") if m.strip()
    ) or ("gpt-4o-mini",)
    agent_directory = _resolve_config_path(
        base_dir,
        raw.agents_local_path.strip() or raw.agent_directory.strip(),
        default_relative="agents",
    )
    tools_directory = _resolve_config_path(
        base_dir,
        raw.tools_local_path.strip() or raw.tools_directory.strip(),
        default_relative="tools",
    )
    world_directory = _resolve_config_path(
        base_dir,
        raw.world_local_path.strip() or raw.world_directory.strip(),
        default_relative="world",
    )
    root_agent_id = raw.root_agent.strip()

    raw_multi = raw.skills_directories.strip() or raw.skills_local_directories.strip()
    raw_single = raw.skills_directory.strip() or raw.skills_local_path.strip()
    if raw_multi:
        skills_directories: tuple[Path, ...] = tuple(
            (base_dir / p.strip()).resolve()
            for p in raw_multi.split(",")
            if p.strip()
        )
    elif raw_single:
        skills_directories = ((base_dir / raw_single).resolve(),)
    else:
        default = (base_dir / "skills").resolve()
        skills_directories = (default,) if default.is_dir() else ()

    raw_max_tokens = raw.skills_catalog_max_tokens.strip()
    skills_catalog_max_tokens = int(raw_max_tokens) if raw_max_tokens else 2000

    raw_commands_multi = raw.commands_directories.strip()
    raw_commands_single = raw.commands_directory.strip()
    if raw_commands_multi:
        commands_directories: tuple[Path, ...] = tuple(
            (base_dir / p.strip()).resolve()
            for p in raw_commands_multi.split(",")
            if p.strip()
        )
    elif raw_commands_single:
        commands_directories = ((base_dir / raw_commands_single).resolve(),)
    else:
        commands_directories = ()

    raw_mcp = raw.mcp_config_path.strip()
    mcp_config_path: Path | None = (base_dir / raw_mcp).resolve() if raw_mcp else None
    mcp_enabled = raw.mcp_enabled.strip().lower() not in ("false", "0", "no")

    raw_missing_tool = raw.missing_tool_policy.strip().lower()
    missing_tool_policy: Literal["graceful", "strict"] = (
        "strict" if raw_missing_tool in ("strict", "fail", "error") else "graceful"
    )

    memory_enabled = raw.memory_enabled.strip().lower() not in ("false", "0", "no")
    raw_memory_threshold = raw.memory_auto_store_threshold_bytes.strip()
    memory_auto_store_threshold_bytes = (
        int(raw_memory_threshold) if raw_memory_threshold else DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES
    )
    memory_builtin_tools_enabled = (
        raw.memory_builtin_tools_enabled.strip().lower() not in ("false", "0", "no")
    )
    memory_default_projection_mode = (
        raw.memory_default_projection_mode.strip() or "catalog_and_selected_content"
    )
    memory_backend_kind = raw.memory_backend.strip() or "memory"
    memory_query_provider_kind = raw.memory_query_provider.strip() or "catalog"
    memory_projector_kind = raw.memory_projector.strip() or "xml"
    memory_global_scopes = _parse_csv_values(raw.memory_global_scopes)
    memory_group_scopes = _parse_csv_values(raw.memory_group_scopes)
    memory_use_case_scopes = _parse_csv_values(raw.memory_use_case_scopes)
    memory_enable_agent_scope = (
        raw.memory_enable_agent_scope.strip().lower() in ("true", "1", "yes", "on")
    )

    return HostConfig(
        openai_api_key=raw.openai_api_key,
        default_provider=raw.default_provider,
        default_model=default_model,
        agent_directory=agent_directory,
        tools_directory=tools_directory,
        world_directory=world_directory,
        root_agent_id=root_agent_id,
        agent_models=_parse_agent_models(raw.agent_models),
        skills_directories=skills_directories,
        skills_catalog_max_tokens=skills_catalog_max_tokens,
        dial_base_url=raw.dial_base_url,
        dial_api_version=raw.dial_api_version,
        dial_api_key=raw.dial_api_key,
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
        memory_global_scopes=memory_global_scopes,
        memory_group_scopes=memory_group_scopes,
        memory_use_case_scopes=memory_use_case_scopes,
        memory_enable_agent_scope=memory_enable_agent_scope,
    )


def _resolve_config_path(base_dir: Path, raw: str, *, default_relative: str) -> Path:
    """Resolve a path value: absolute paths as-is, relative paths resolved against ``base_dir``."""
    text = (raw or "").strip() or default_relative
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


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


def _parse_csv_values(raw_value: str) -> tuple[str, ...]:
    """Parse a comma-separated configuration value into a deduplicated tuple."""
    seen: set[str] = set()
    values: list[str] = []
    for item in raw_value.split(","):
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return tuple(values)


def _strip_quotes(value: str) -> str:
    """Remove matching single or double quotes around a value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_optional_path_relative_to_env_file(env_file: Path, key: str) -> Path | None:
    """Return a filesystem path from a single key, or ``None`` if missing or empty.

    Lookup order: ``.env`` file value (if present) → ``os.environ`` fallback.
    Relative values resolve against the directory containing the env file (same
    rules as :func:`load_host_config`).
    """
    file_values = _parse_env_file(env_file)
    raw = (file_values.get(key) or os.environ.get(key, "")).strip()
    if not raw:
        return None
    return _resolve_config_path(env_file.parent, raw, default_relative=".")


__all__ = [
    "DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES",
    "HostConfig",
    "load_host_config",
    "read_optional_path_relative_to_env_file",
]
