"""Tests for the @filename injection utility."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Protocol / custom resolver
# ---------------------------------------------------------------------------

def test_default_resolver_is_protocol_compatible():
    from agent_framework.file_reference import DefaultFileReferenceResolver, FileReferenceResolver
    r = DefaultFileReferenceResolver()
    assert isinstance(r, FileReferenceResolver)


def test_custom_resolver_satisfies_protocol():
    from agent_framework.file_reference import FileReferenceResolver

    class MyResolver:
        def resolve(self, path: Path) -> str:
            return "custom"

    assert isinstance(MyResolver(), FileReferenceResolver)


# ---------------------------------------------------------------------------
# expand_file_refs — text files
# ---------------------------------------------------------------------------

def test_expand_text_file(tmp_path: Path):
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    f = tmp_path / "note.txt"
    f.write_text("hello world", encoding="utf-8")
    result = expand_file_refs("See @note.txt for details", DefaultFileReferenceResolver(), base_dir=tmp_path)
    assert '<file name="note.txt">' in result
    assert "hello world" in result
    assert "</file>" in result
    assert "@note.txt" not in result


def test_expand_quoted_path_with_spaces(tmp_path: Path):
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    f = tmp_path / "my deck.pptx"
    f.write_bytes(b"\xff\xfe\x00\x00binary")  # Invalid UTF-8 sequence
    result = expand_file_refs('Analyze @"my deck.pptx"', DefaultFileReferenceResolver(), base_dir=tmp_path)
    assert '<file name="my deck.pptx"' in result
    assert 'encoding="base64"' in result
    assert base64.b64encode(b"\xff\xfe\x00\x00binary").decode() in result


def test_expand_binary_file_base64(tmp_path: Path):
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    data = bytes(range(256))
    f = tmp_path / "data.bin"
    f.write_bytes(data)
    result = expand_file_refs("@data.bin", DefaultFileReferenceResolver(), base_dir=tmp_path)
    assert 'encoding="base64"' in result
    assert base64.b64encode(data).decode() in result


def test_missing_file_left_unchanged(tmp_path: Path):
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    result = expand_file_refs("See @ghost.txt here", DefaultFileReferenceResolver(), base_dir=tmp_path)
    assert result == "See @ghost.txt here"


def test_no_at_sign_returns_same_object():
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    text = "no references here, just text"
    assert expand_file_refs(text, DefaultFileReferenceResolver()) is text


def test_at_word_without_dot_not_matched(tmp_path: Path):
    """Unquoted @refs without a dot (e.g. @dataclass) must not trigger expansion."""
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    result = expand_file_refs("@dataclass @property", DefaultFileReferenceResolver(), base_dir=tmp_path)
    assert result == "@dataclass @property"


def test_multiple_refs_in_one_string(tmp_path: Path):
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    (tmp_path / "a.txt").write_text("AAA", encoding="utf-8")
    (tmp_path / "b.txt").write_text("BBB", encoding="utf-8")
    result = expand_file_refs("@a.txt and @b.txt", DefaultFileReferenceResolver(), base_dir=tmp_path)
    assert "AAA" in result
    assert "BBB" in result


def test_custom_resolver_used(tmp_path: Path):
    from agent_framework.file_reference import expand_file_refs

    class Fixed:
        def resolve(self, path: Path) -> str:
            return f"[{path.name}]"

    (tmp_path / "x.txt").write_text("ignored", encoding="utf-8")
    result = expand_file_refs("@x.txt", Fixed(), base_dir=tmp_path)
    assert result == "[x.txt]"


def test_base_dir_defaults_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    monkeypatch.chdir(tmp_path)
    (tmp_path / "local.txt").write_text("cwd content", encoding="utf-8")
    result = expand_file_refs("@local.txt", DefaultFileReferenceResolver())
    assert "cwd content" in result
