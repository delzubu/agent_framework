"""Configuration loading for the console agent host.

This module keeps `.env` parsing isolated from the runtime classes so the
execution layer can depend on a typed configuration object instead of raw
environment strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HostConfig:
    """Resolved host configuration loaded from a `.env` file.

    Attributes:
        openai_api_key: API key used by the default OpenAI-backed model driver.
        default_provider: Provider name assigned to agents that do not declare
            their own provider in frontmatter.
        default_model: Model assigned to agents that do not declare their own
            model and do not have an override in `agent_models`.
        agent_directory: Directory containing Markdown-defined agents.
        tools_directory: Directory containing Markdown-defined tools.
        world_directory: Sandboxed root for world file tools such as
            `read_file` and `write_file`.
        root_agent_id: Logical name of the root agent. The runtime resolves it
            against `agent_directory` and infers the `.md` extension.
        agent_models: Optional per-agent model overrides keyed by agent id or
            source file stem.
    """

    openai_api_key: str
    default_provider: str
    default_model: str
    agent_directory: Path
    tools_directory: Path
    world_directory: Path
    root_agent_id: str
    agent_models: dict[str, str] = field(default_factory=dict)
    skills_directories: tuple[Path, ...] = field(default_factory=tuple)

    def model_for(self, agent_id: str, fallback: str | None = None) -> str:
        """Return the configured model for an agent.

        Args:
            agent_id: Runtime agent identifier or source file stem.
            fallback: Optional fallback model if the agent is not explicitly
                configured in `agent_models`.

        Returns:
            The selected model name.
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
    default_provider = values.get("DEFAULT_PROVIDER", "openai")
    default_model = values.get("DEFAULT_MODEL", "gpt-4o-mini")
    agent_directory = (env_file.parent / values.get("AGENT_DIRECTORY", "agents")).resolve()
    tools_directory = (env_file.parent / values.get("TOOLS_DIRECTORY", "tools")).resolve()
    world_directory = (env_file.parent / values.get("WORLD_DIRECTORY", "world")).resolve()
    root_agent_id = values.get("ROOT_AGENT", "root").strip()
    raw_multi = values.get("SKILLS_DIRECTORIES", "")
    raw_single = values.get("SKILLS_DIRECTORY", "")
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
    )


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


def _parse_agent_models(raw_value: str) -> dict[str, str]:
    """Parse the `AGENT_MODELS` comma-separated mapping."""
    if not raw_value:
        return {}
    mappings: dict[str, str] = {}
    for item in raw_value.split(","):
        chunk = item.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        mappings[key.strip()] = value.strip()
    return mappings


def _strip_quotes(value: str) -> str:
    """Remove matching single or double quotes around a value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


__all__ = ["HostConfig", "load_host_config"]
