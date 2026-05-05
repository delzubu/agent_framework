"""Prompt reference parsing and projection helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PromptReference:
    """Structured reference to prompt content provided by a resolver."""

    scheme: str
    target: str
    projection: str | None = None
    options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PromptRef:
    """Workflow-friendly wrapper for a prompt reference string."""

    ref: str


@dataclass(frozen=True, slots=True)
class PromptResolveContext:
    """Runtime context available when resolving prompt references."""

    host: Any
    agent_id: str
    run_id: str | None = None
    workflow_step_id: str | None = None
    phase_id: str | None = None
    base_dir: Any | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPromptReference:
    """Resolved prompt content plus audit-oriented metadata."""

    content: str
    content_type: str = "text/markdown"
    metadata: dict[str, Any] = field(default_factory=dict)
    preloads: tuple[PromptReference, ...] = ()


@runtime_checkable
class PromptReferenceResolver(Protocol):
    """Strategy for resolving scheme-aware prompt references."""

    def can_resolve(self, ref: PromptReference, context: PromptResolveContext) -> bool:
        """Return whether this resolver can handle *ref*."""
        ...

    def resolve(self, ref: PromptReference, context: PromptResolveContext) -> ResolvedPromptReference:
        """Resolve *ref* into prompt content."""
        ...


_REF_PATTERN = re.compile(
    r"^@?(?P<scheme>[A-Za-z][A-Za-z0-9_+.-]*):(?P<target>[^#?\s]+)"
    r"(?:#(?P<projection>[^?\s]+))?"
    r"(?:\?(?P<query>\S+))?$"
)


def parse_prompt_reference(value: str) -> PromptReference:
    """Parse ``agent:name#projection`` or ``@agent:name#projection``."""
    match = _REF_PATTERN.match(value.strip())
    if match is None:
        raise ValueError(f"Invalid prompt reference {value!r}.")
    options: dict[str, str] = {}
    raw_query = match.group("query")
    if raw_query:
        for item in raw_query.split("&"):
            if not item:
                continue
            key, _, val = item.partition("=")
            options[key] = val
    return PromptReference(
        scheme=match.group("scheme"),
        target=match.group("target"),
        projection=match.group("projection"),
        options=options,
    )


def is_prompt_reference_text(value: str) -> bool:
    """Return whether *value* is exactly one prompt reference token."""
    return _REF_PATTERN.match(value.strip()) is not None


@dataclass(frozen=True, slots=True)
class _MarkdownSection:
    path: str
    title: str
    content: str


class AgentPromptReferenceResolver:
    """Resolve ``agent:<id>#workflow`` references from agent sidecar metadata."""

    def can_resolve(self, ref: PromptReference, context: PromptResolveContext) -> bool:
        return ref.scheme == "agent"

    def resolve(self, ref: PromptReference, context: PromptResolveContext) -> ResolvedPromptReference:
        if ref.scheme != "agent":
            raise ValueError(f"Unsupported prompt reference scheme {ref.scheme!r}.")
        projection = ref.projection or "workflow"
        if projection != "workflow":
            raise ValueError(
                f"Unsupported agent prompt projection {projection!r} for {ref.target!r}; "
                "only 'workflow' is currently supported."
            )
        agent = context.host.get_agent(ref.target, base_dir=context.base_dir)
        source_path = getattr(agent, "source_path", None)
        if source_path is None:
            raise ValueError(f"Agent {ref.target!r} has no source path for prompt projection.")

        from agent_framework.agents.helpers import load_runtime_metadata

        metadata = load_runtime_metadata(Path(source_path))
        raw_compose = metadata.get("workflow-compose")
        if raw_compose is None:
            raw_compose = metadata.get("workflow_compose")
        if not isinstance(raw_compose, dict):
            raise ValueError(
                f"Agent {agent.agent_id!r} sidecar {Path(source_path).with_suffix('.json')} "
                "must define a 'workflow-compose' object for workflow prompt projection."
            )

        sections = _parse_markdown_sections(getattr(agent, "system_prompt", ""))
        include_specs = _string_list(raw_compose.get("include-sections"), field_name="include-sections")
        exclude_specs = _string_list(raw_compose.get("exclude-sections"), field_name="exclude-sections")
        include_paths = _resolve_section_specs(include_specs, sections, required=True)
        exclude_paths = _resolve_section_specs(exclude_specs, sections, required=False)

        if include_paths:
            selected = [
                section
                for section in sections
                if _path_is_selected(section.path, include_paths)
                and not _path_is_selected(section.path, exclude_paths)
            ]
        else:
            selected = [
                section
                for section in sections
                if not _path_is_selected(section.path, exclude_paths)
            ]

        append_text = raw_compose.get("append")
        if append_text is not None and not isinstance(append_text, str):
            raise ValueError("'workflow-compose.append' must be a string when present.")
        preloads = tuple(
            PromptReference(scheme="skill", target=name)
            for name in _string_list(raw_compose.get("pre-load-skills"), field_name="pre-load-skills")
        )
        content_parts = [section.content.strip() for section in selected if section.content.strip()]
        if append_text and append_text.strip():
            content_parts.append(append_text.strip())
        content = "\n\n".join(content_parts).strip()
        metadata_payload = {
            "source_agent": agent.agent_id,
            "source_path": str(source_path),
            "projection": projection,
            "included_sections": [section.path for section in selected],
            "excluded_sections": sorted(exclude_paths),
            "preloaded_skills": [item.target for item in preloads],
            "token_estimate": _estimate_tokens(content),
        }
        return ResolvedPromptReference(
            content=content,
            metadata=metadata_payload,
            preloads=preloads,
        )


__all__ = [
    "AgentPromptReferenceResolver",
    "PromptRef",
    "PromptReference",
    "PromptReferenceResolver",
    "PromptResolveContext",
    "ResolvedPromptReference",
    "is_prompt_reference_text",
    "parse_prompt_reference",
]


_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _parse_markdown_sections(markdown: str) -> list[_MarkdownSection]:
    matches = list(_HEADING_PATTERN.finditer(markdown))
    if not matches:
        return [_MarkdownSection(path="/", title="", content=markdown)] if markdown.strip() else []
    sections: list[_MarkdownSection] = []
    stack: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = "/" + "/".join(_normalize_path_part(item[1]) for item in stack)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections.append(_MarkdownSection(path=path, title=title, content=markdown[match.start():end].strip()))
    return sections


def _normalize_path_part(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _string_list(value: Any, *, field_name: str) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"'workflow-compose.{field_name}' must be a list of strings when present.")
    return [item.strip() for item in value if item.strip()]


def _resolve_section_specs(
    specs: list[str],
    sections: list[_MarkdownSection],
    *,
    required: bool,
) -> set[str]:
    paths: set[str] = set()
    all_paths = {section.path for section in sections}
    title_index: dict[str, list[str]] = {}
    for section in sections:
        title_index.setdefault(_normalize_path_part(section.title).casefold(), []).append(section.path)
    for spec in specs:
        if spec.startswith("/"):
            normalized = "/" + "/".join(
                _normalize_path_part(part)
                for part in spec.strip("/").split("/")
                if _normalize_path_part(part)
            )
            if normalized not in all_paths:
                if required:
                    raise ValueError(
                        f"Missing workflow-compose section {spec!r}. Available sections: {sorted(all_paths)}"
                    )
                continue
            paths.add(normalized)
            continue

        candidates = title_index.get(_normalize_path_part(spec).casefold(), [])
        if not candidates:
            if required:
                raise ValueError(
                    f"Missing workflow-compose section title {spec!r}. Available sections: {sorted(all_paths)}"
                )
            continue
        if len(candidates) > 1:
            raise ValueError(
                f"Ambiguous workflow-compose section title {spec!r}; candidates: {sorted(candidates)}"
            )
        paths.add(candidates[0])
    return paths


def _path_is_selected(path: str, selected_paths: set[str]) -> bool:
    return any(path == selected or path.startswith(selected + "/") for selected in selected_paths)


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0
