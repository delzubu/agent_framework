"""@filename injection — file reference resolution strategy."""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

# Matches:
#   @"path/with spaces.txt"   — quoted (any chars except newline/quote)
#   @word.ext                 — unquoted with at least one dot (avoids @dataclass etc.)
_REF_PATTERN = re.compile(r'@(?:"([^"\n]+)"|([^\s"@\n]*\.[^\s"@\n]+))')


@runtime_checkable
class FileReferenceResolver(Protocol):
    """Strategy for turning a resolved file ``Path`` into prompt text."""

    def resolve(self, path: Path) -> str:
        """Return the string to substitute for the ``@ref`` token.

        Raise ``OSError`` if the file cannot be read; the token is then left
        unchanged in the prompt.
        """
        ...


class DefaultFileReferenceResolver:
    """Read text files as UTF-8; fall back to base64 for binary files.

    Both variants are wrapped in ``<file>`` XML tags so the model can
    identify the source and encoding.
    """

    def resolve(self, path: Path) -> str:
        try:
            content = path.read_text(encoding="utf-8")
            return f'<file name="{path.name}">\n{content}\n</file>'
        except UnicodeDecodeError:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f'<file name="{path.name}" encoding="base64">\n{encoded}\n</file>'


def expand_file_refs(
    text: str,
    resolver: FileReferenceResolver,
    base_dir: Path | None = None,
) -> str:
    """Replace every ``@ref`` token in *text* with its resolved content.

    Tokens that cannot be resolved (file not found, permission error) are left
    unchanged so the caller can decide how to handle them.

    Args:
        text: Prompt string possibly containing ``@filename`` or ``@"path"`` tokens.
        resolver: Strategy that converts a resolved :class:`Path` to a string.
        base_dir: Directory used to resolve relative paths. Defaults to ``Path.cwd()``.
    """
    if "@" not in text:
        return text
    base = Path(base_dir) if base_dir is not None else Path.cwd()

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        path = (base / raw).resolve()
        try:
            return resolver.resolve(path)
        except OSError:
            return m.group(0)

    return _REF_PATTERN.sub(_replace, text)
