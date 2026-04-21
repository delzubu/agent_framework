"""Scoped memory primitives, storage backends, and prompt projection."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html import escape
from typing import Any, Mapping, Protocol, Sequence


def is_memory_uri(value: str) -> bool:
    """Return whether *value* looks like a ``mem://`` URI."""
    return isinstance(value, str) and value.startswith("mem://")


def parse_memory_uri(uri: str) -> tuple[str, str, str]:
    """Parse ``mem://<scope-kind>/<scope-key>/<path>`` into components."""
    if not is_memory_uri(uri):
        raise ValueError(f"Invalid memory URI {uri!r}: must start with 'mem://'.")
    parts = uri[len("mem://"):].split("/", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            f"Invalid memory URI {uri!r}: expected 'mem://<scope-kind>/<scope-key>/<path>'."
        )
    return parts[0], parts[1], parts[2]


def build_memory_uri(scope: "MemoryScope", path: str) -> str:
    """Build a canonical ``mem://`` URI from scope and relative path."""
    clean_path = "/".join(part.strip("/") for part in str(path).split("/") if part.strip("/"))
    if not clean_path:
        raise ValueError("Memory path must be non-empty.")
    return f"mem://{scope.kind}/{scope.key}/{clean_path}"


def find_memory_uris(value: Any) -> tuple[str, ...]:
    """Return all distinct memory URIs contained recursively in *value*."""
    uris: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            if is_memory_uri(node):
                uris.add(node)
            return
        if isinstance(node, Mapping):
            for child in node.values():
                _walk(child)
            return
        if isinstance(node, (list, tuple, set, frozenset)):
            for child in node:
                _walk(child)

    _walk(value)
    return tuple(sorted(uris))


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Visibility scope for memory entries."""

    kind: str
    key: str

    def __post_init__(self) -> None:
        if not self.kind or not self.key:
            raise ValueError("MemoryScope.kind and MemoryScope.key must be non-empty.")

    def as_text(self) -> str:
        return f"{self.kind}:{self.key}"


@dataclass(frozen=True, slots=True)
class MemoryRef:
    """Stable reference to a memory entry."""

    uri: str
    scope: MemoryScope
    mime_type: str
    title: str | None = None
    summary: str | None = None
    size_bytes: int = 0
    version: str = "1"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        scope_kind, scope_key, _ = parse_memory_uri(self.uri)
        if scope_kind != self.scope.kind or scope_key != self.scope.key:
            raise ValueError(
                f"MemoryRef {self.uri!r} scope mismatch: uri={scope_kind}:{scope_key}, "
                f"ref={self.scope.kind}:{self.scope.key}."
            )


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """Stored memory value plus metadata."""

    ref: MemoryRef
    content_text: str | None = None
    content_bytes: bytes | None = None
    content_json: Any | None = None

    def __post_init__(self) -> None:
        populated = sum(
            value is not None
            for value in (self.content_text, self.content_bytes, self.content_json)
        )
        if populated != 1:
            raise ValueError("Exactly one memory content field must be populated.")

    def render_content(self) -> str:
        """Return the entry content as text for tools and projectors."""
        if self.content_text is not None:
            return self.content_text
        if self.content_json is not None:
            return json.dumps(self.content_json, indent=2, ensure_ascii=False)
        assert self.content_bytes is not None
        return self.content_bytes.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class MemoryQueryHit:
    """One discovery result returned by a memory query provider."""

    ref: MemoryRef
    score: float | None = None
    match_reason: str | None = None


class MemoryBackend(Protocol):
    """Storage backend for scoped memory entries."""

    def put(self, entry: MemoryEntry) -> MemoryRef: ...

    def get(self, uri: str) -> MemoryEntry: ...

    def update(
        self,
        uri: str,
        *,
        content: Any,
        mime_type: str | None = None,
        summary: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryRef: ...

    def delete(self, uri: str) -> None: ...

    def list(
        self,
        scopes: Sequence[MemoryScope],
        *,
        prefix: str | None = None,
        mime_type: str | None = None,
        limit: int = 50,
    ) -> tuple[MemoryRef, ...]: ...


class MemoryQueryProvider(Protocol):
    """Discovery provider for memory refs."""

    def list(self, scopes: Sequence[MemoryScope], *, limit: int = 20) -> tuple[MemoryQueryHit, ...]: ...

    def query(
        self,
        text: str,
        scopes: Sequence[MemoryScope],
        *,
        limit: int = 10,
    ) -> tuple[MemoryQueryHit, ...]: ...


class MemoryProjector(Protocol):
    """Renderer that turns memory refs and entries into prompt text."""

    def render_catalog(self, hits: Sequence[MemoryQueryHit]) -> str: ...

    def render_entries(self, entries: Sequence[MemoryEntry]) -> str: ...


@dataclass(slots=True)
class InMemoryMemoryBackend:
    """Simple process-local backend keyed by URI."""

    _entries: dict[str, MemoryEntry] = field(default_factory=dict)

    def put(self, entry: MemoryEntry) -> MemoryRef:
        self._entries[entry.ref.uri] = entry
        return entry.ref

    def get(self, uri: str) -> MemoryEntry:
        try:
            return self._entries[uri]
        except KeyError as exc:
            raise KeyError(f"Unknown memory URI: {uri}") from exc

    def update(
        self,
        uri: str,
        *,
        content: Any,
        mime_type: str | None = None,
        summary: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryRef:
        current = self.get(uri)
        new_ref = MemoryRef(
            uri=current.ref.uri,
            scope=current.ref.scope,
            mime_type=mime_type or current.ref.mime_type,
            title=current.ref.title,
            summary=summary if summary is not None else current.ref.summary,
            size_bytes=_size_bytes_for_content(content),
            version=current.ref.version,
            metadata=dict(current.ref.metadata) | dict(metadata or {}),
        )
        self._entries[uri] = _entry_from_content(new_ref, content)
        return new_ref

    def delete(self, uri: str) -> None:
        self._entries.pop(uri, None)

    def list(
        self,
        scopes: Sequence[MemoryScope],
        *,
        prefix: str | None = None,
        mime_type: str | None = None,
        limit: int = 50,
    ) -> tuple[MemoryRef, ...]:
        allowed = {(scope.kind, scope.key) for scope in scopes}
        refs: list[MemoryRef] = []
        for uri, entry in sorted(self._entries.items()):
            scope_tuple = (entry.ref.scope.kind, entry.ref.scope.key)
            if allowed and scope_tuple not in allowed:
                continue
            if prefix and not uri.startswith(prefix):
                continue
            if mime_type and entry.ref.mime_type != mime_type:
                continue
            refs.append(entry.ref)
            if len(refs) >= limit:
                break
        return tuple(refs)


@dataclass(slots=True)
class CatalogMemoryQueryProvider:
    """Catalog-backed query provider using exact/substring matching."""

    backend: MemoryBackend

    def list(self, scopes: Sequence[MemoryScope], *, limit: int = 20) -> tuple[MemoryQueryHit, ...]:
        return tuple(MemoryQueryHit(ref=ref) for ref in self.backend.list(scopes, limit=limit))

    def query(
        self,
        text: str,
        scopes: Sequence[MemoryScope],
        *,
        limit: int = 10,
    ) -> tuple[MemoryQueryHit, ...]:
        needle = text.strip().lower()
        if not needle:
            return self.list(scopes, limit=limit)

        ranked: list[tuple[int, MemoryQueryHit]] = []
        for ref in self.backend.list(scopes, limit=max(limit * 10, limit)):
            haystacks = [
                ref.uri,
                ref.title or "",
                ref.summary or "",
                " ".join(str(value) for value in ref.metadata.values()),
            ]
            scores = [
                4 if needle in haystacks[0].lower() else 0,
                3 if needle in haystacks[1].lower() else 0,
                2 if needle in haystacks[2].lower() else 0,
                1 if needle in haystacks[3].lower() else 0,
            ]
            best = max(scores)
            if best:
                ranked.append((best, MemoryQueryHit(ref=ref, score=float(best))))
        ranked.sort(key=lambda item: (-item[0], item[1].ref.uri))
        return tuple(hit for _, hit in ranked[:limit])


@dataclass(slots=True)
class XmlMemoryProjector:
    """Render memory catalog and content as XML blocks."""

    def render_catalog(self, hits: Sequence[MemoryQueryHit]) -> str:
        if not hits:
            return ""
        lines = ["<available_memory>"]
        for hit in hits:
            ref = hit.ref
            attrs = [
                f'id="{escape(ref.uri, quote=True)}"',
                f'scope="{escape(ref.scope.as_text(), quote=True)}"',
                f'mime="{escape(ref.mime_type, quote=True)}"',
            ]
            if ref.title:
                attrs.append(f'title="{escape(ref.title, quote=True)}"')
            if ref.summary:
                attrs.append(f'summary="{escape(ref.summary, quote=True)}"')
            lines.append(f"  <memory_ref {' '.join(attrs)} />")
        lines.append("</available_memory>")
        return "\n".join(lines)

    def render_entries(self, entries: Sequence[MemoryEntry]) -> str:
        blocks: list[str] = []
        for entry in entries:
            ref = entry.ref
            attrs = [
                f'id="{escape(ref.uri, quote=True)}"',
                f'scope="{escape(ref.scope.as_text(), quote=True)}"',
                f'mime="{escape(ref.mime_type, quote=True)}"',
            ]
            content = entry.render_content()
            blocks.append(f"<memory {' '.join(attrs)}>\n{content}\n</memory>")
        return "\n\n".join(blocks)


def _entry_from_content(ref: MemoryRef, content: Any) -> MemoryEntry:
    if isinstance(content, str):
        return MemoryEntry(ref=ref, content_text=content)
    if isinstance(content, bytes):
        return MemoryEntry(ref=ref, content_bytes=content)
    return MemoryEntry(ref=ref, content_json=content)


def _size_bytes_for_content(content: Any) -> int:
    if isinstance(content, bytes):
        return len(content)
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    return len(json.dumps(content, ensure_ascii=False).encode("utf-8"))


def next_memory_version(version: str) -> str:
    """Return the next version label for a memory entry."""
    try:
        return str(int(version) + 1)
    except ValueError:
        return f"{version}.next"


__all__ = [
    "CatalogMemoryQueryProvider",
    "InMemoryMemoryBackend",
    "MemoryBackend",
    "MemoryEntry",
    "MemoryProjector",
    "MemoryQueryHit",
    "MemoryQueryProvider",
    "MemoryRef",
    "MemoryScope",
    "XmlMemoryProjector",
    "build_memory_uri",
    "find_memory_uris",
    "is_memory_uri",
    "next_memory_version",
    "parse_memory_uri",
]
