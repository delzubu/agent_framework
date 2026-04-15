"""Formal agent registry for AgentHost."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from agent_framework.agents.helpers import SECTION_PATTERN

if TYPE_CHECKING:
    from agent_framework.agents.agent import Agent

_LOGGER = logging.getLogger(__name__)


def normalize_agent_id(agent_ref: str) -> str:
    """Turn ``deck-review-agent.md`` into ``deck-review-agent`` for catalog lookup."""
    text = (agent_ref or "").strip()
    if not text:
        return text
    p = Path(text)
    if p.suffix.lower() == ".md":
        return p.stem
    return text


@dataclass(slots=True)
class AgentRegistry:
    """Discovers and caches Agent instances from configured directories.

    Agents are discovered eagerly (catalog built at startup) but loaded lazily
    (``Agent.from_markdown`` called only on first ``get()``).

    Attributes:
        directories: Directories to scan for ``*.md`` agent definitions.
        config: Host config used to resolve models and providers when loading agents.
        _catalog: Maps agent_id → markdown path.
        _cache: Maps agent_id or str(source_path) → loaded Agent.
    """

    directories: tuple[Path, ...]
    config: Any = None   # HostConfig | None — stored for model resolution
    _catalog: dict[str, Path] = field(default_factory=dict, repr=False)
    _cache: dict[str, "Agent"] = field(default_factory=dict, repr=False)

    @classmethod
    def from_config(cls, config: Any) -> "AgentRegistry":
        """Build an AgentRegistry from a HostConfig."""
        agent_dir = getattr(config, "agent_directory", None)
        directories: tuple[Path, ...] = (Path(agent_dir),) if agent_dir else ()
        return cls(directories=directories, config=config)

    def discover(self) -> None:
        """Scan all directories and build the agent_id→path catalog.

        Parses frontmatter ``id`` field; falls back to file stem on any error.
        First directory wins on duplicate ids.
        """
        catalog: dict[str, Path] = {}
        for directory in self.directories:
            dpath = Path(directory)
            if not dpath.is_dir():
                _LOGGER.debug("agent discover: skip missing directory %s", dpath)
                continue
            for md_path in sorted(dpath.glob("*.md")):
                agent_id = _extract_agent_id(md_path)
                if not agent_id:
                    continue
                if agent_id not in catalog:
                    catalog[agent_id] = md_path.resolve()
                else:
                    _LOGGER.debug(
                        "agent discover: skip duplicate id %r (%s wins)",
                        agent_id,
                        catalog[agent_id],
                    )
        self._catalog = catalog
        _LOGGER.debug("agent discover: %d catalog entries", len(self._catalog))

    def get(self, agent_id: str, *, base_dir: Path | None = None) -> "Agent":
        """Resolve an agent by id, path, sibling, catalog, or default directory.

        Resolution order (matches original AgentHost.get_agent logic):
        1. Cache hit (by id or str(source_path))
        2. Explicit file path if agent_id is an existing path
        3. Sibling ``<base_dir>/<agent_id>.md``
        4. Catalog lookup
        5. Default directory ``<config.agent_directory>/<agent_id>.md``
        6. KeyError
        """
        if agent_id in self._cache:
            return self._cache[agent_id]

        # Direct path reference
        path_candidate = Path(agent_id)
        if path_candidate.exists():
            return self.load_from_path(path_candidate)

        nid = normalize_agent_id(agent_id)
        if nid in self._cache:
            return self._cache[nid]

        # Sibling path
        if base_dir is not None:
            sibling = (base_dir / f"{nid}.md").resolve()
            if sibling.exists():
                return self.load_from_path(sibling)

        # Catalog
        if nid in self._catalog:
            return self.load_from_path(self._catalog[nid])

        # Default directory fallback
        if self.config is not None:
            agent_dir = getattr(self.config, "agent_directory", None)
            if agent_dir:
                default_candidate = (Path(agent_dir) / f"{nid}.md").resolve()
                if default_candidate.exists():
                    return self.load_from_path(default_candidate)

        _LOGGER.warning("unknown agent %r (not in catalog or on disk)", agent_id)
        raise KeyError(f"Unknown agent: {agent_id!r}")

    def list_names(self) -> tuple[str, ...]:
        """Return all discovered agent ids."""
        return tuple(sorted(self._catalog))

    def reload(self) -> None:
        """Clear all caches and re-discover from disk."""
        self._catalog.clear()
        self._cache.clear()
        self.discover()

    def load_from_path(self, source_path: Path) -> "Agent":
        """Load an Agent from markdown, apply model overrides, and cache it."""
        from agent_framework.agents.agent import Agent

        cfg = self.config
        default_provider = getattr(cfg, "default_provider", "openai") if cfg else "openai"
        default_model = getattr(cfg, "default_model", ("gpt-4o-mini",)) if cfg else ("gpt-4o-mini",)

        try:
            agent = Agent.from_markdown(
                source_path,
                default_provider=default_provider,
                default_model=default_model,
            )
        except Exception:
            _LOGGER.error("failed to load agent markdown %s", source_path, exc_info=True)
            raise

        # Apply per-agent model overrides
        if cfg is not None:
            agent_models = getattr(cfg, "agent_models", {}) or {}
            stem = source_path.stem
            if stem in agent_models:
                agent.model_names = agent_models[stem]
            if agent.agent_id in agent_models:
                agent.model_names = agent_models[agent.agent_id]

        self._cache[agent.agent_id] = agent
        if agent.source_path is not None:
            self._cache[str(agent.source_path)] = agent
        _LOGGER.debug("loaded agent %r from %s", agent.agent_id, source_path)
        return agent


def _extract_agent_id(md_path: Path) -> str | None:
    """Extract the agent id from frontmatter, falling back to stem.

    Supports the same layouts as ``split_markdown_sections``: classic ``---``-wrapped YAML,
    or YAML lines before the first ``---`` (no opening fence).
    """
    try:
        raw = md_path.read_text(encoding="utf-8")
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 2:
                meta = yaml.safe_load(parts[1]) or {}
                if isinstance(meta, dict):
                    agent_id = str(meta.get("id", "") or meta.get("name", "")).strip()
                    if agent_id:
                        return agent_id
        else:
            m = SECTION_PATTERN.search(raw)
            if m is not None:
                meta = yaml.safe_load(raw[: m.start()]) or {}
                if isinstance(meta, dict):
                    agent_id = str(meta.get("id", "") or meta.get("name", "")).strip()
                    if agent_id:
                        return agent_id
        return md_path.stem
    except Exception:  # noqa: BLE001
        return md_path.stem


__all__ = ["AgentRegistry", "normalize_agent_id"]
