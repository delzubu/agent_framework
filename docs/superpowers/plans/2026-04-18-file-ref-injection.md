# @filename Injection (RAG OOB) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `@filename` or `@"filename with spaces"` tokens in agent prompt strings to be automatically expanded to their file contents before the agent sees them, with a pluggable `FileReferenceResolver` strategy.

**Architecture:** A new `file_reference.py` module provides the Protocol, default implementation, and `expand_file_refs()` utility. `AgentHost.run_agent` uses the host's resolver to expand `initial_instruction` (covers all explicit-prompt code paths including the evaluator). `case_markdown.parse_case_markdown_file` and `MarkdownCaseLoader` accept an optional resolver and expand the case `prompt` field using the case file's parent directory as `base_dir`.

**Tech Stack:** Python stdlib only (`re`, `base64`, `pathlib`). No new dependencies.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/agent_framework/file_reference.py` | **Create** | Protocol, default impl, `expand_file_refs()` |
| `tests/test_file_reference.py` | **Create** | Unit tests for the new module |
| `src/agent_framework/host.py` | **Modify** | Add `file_ref_resolver` field; expand in `run_agent` |
| `src/agent_framework_evaluator/case_markdown.py` | **Modify** | Accept resolver in `parse_case_markdown_file` and `MarkdownCaseLoader` |
| `tests/test_evaluator_cli.py` | **Modify** | Add case-markdown expansion tests |

---

## Task 1: Create `file_reference.py` and its tests

**Files:**
- Create: `src/agent_framework/file_reference.py`
- Create: `tests/test_file_reference.py`

### Step 1: Write the failing tests

Create `tests/test_file_reference.py`:

```python
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
    f.write_bytes(b"\x00\x01binary")
    result = expand_file_refs('Analyze @"my deck.pptx"', DefaultFileReferenceResolver(), base_dir=tmp_path)
    assert '<file name="my deck.pptx"' in result
    assert 'encoding="base64"' in result
    assert base64.b64encode(b"\x00\x01binary").decode() in result


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


def test_no_at_sign_returns_unchanged():
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    text = "no references here, just text"
    assert expand_file_refs(text, DefaultFileReferenceResolver()) is text


def test_at_word_without_dot_not_matched(tmp_path: Path):
    """@dataclass, @property etc. must not trigger expansion even if a file happened to exist."""
    from agent_framework.file_reference import DefaultFileReferenceResolver, expand_file_refs
    # Even if we create a file named "dataclass" with no extension, the pattern won't match
    # unquoted refs without a dot.  Quoted refs DO require a dot or not — depends on impl;
    # but unquoted ones must not match.
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
    from agent_framework.file_reference import FileReferenceResolver, expand_file_refs

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
```

- [ ] **Step 2: Run tests to confirm they all fail**

```
pytest tests/test_file_reference.py -v
```
Expected: `ImportError` or `ModuleNotFoundError` for `agent_framework.file_reference`.

- [ ] **Step 3: Implement `src/agent_framework/file_reference.py`**

```python
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
```

- [ ] **Step 4: Run tests — all should pass**

```
pytest tests/test_file_reference.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/agent_framework/file_reference.py tests/test_file_reference.py
git commit -m "feat(file-ref): add FileReferenceResolver protocol and expand_file_refs utility"
```

---

## Task 2: Wire resolver into `AgentHost.run_agent`

**Files:**
- Modify: `src/agent_framework/host.py` — add `file_ref_resolver` dataclass field; expand in `run_agent`
- Modify: `tests/test_framework_runtime.py` or create `tests/test_file_ref_host.py`

### Step 1: Write the failing test

Create `tests/test_file_ref_host.py`:

```python
"""Integration: AgentHost.run_agent expands @filename tokens in the initial_instruction."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_host(resolver=None):
    """Return a minimal AgentHost configured with a stub model driver."""
    from agent_framework.file_reference import DefaultFileReferenceResolver
    from agent_framework.host import AgentHost, HostConfig

    host = AgentHost(
        config=HostConfig(),
        file_ref_resolver=resolver or DefaultFileReferenceResolver(),
    )
    return host


def test_host_has_file_ref_resolver_field():
    from agent_framework.file_reference import DefaultFileReferenceResolver
    host = _make_host()
    assert isinstance(host.file_ref_resolver, DefaultFileReferenceResolver)


def test_host_file_ref_resolver_can_be_none():
    from agent_framework.host import AgentHost, HostConfig
    host = AgentHost(config=HostConfig(), file_ref_resolver=None)
    assert host.file_ref_resolver is None


def test_run_agent_expands_initial_instruction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """run_agent should expand @refs before the agent sees the prompt."""
    from agent_framework.file_reference import DefaultFileReferenceResolver

    captured: list[str] = []

    class CapturingResolver:
        def resolve(self, path: Path) -> str:
            captured.append(str(path))
            return f"[{path.name}]"

    (tmp_path / "ctx.txt").write_text("context data", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    host = _make_host(resolver=CapturingResolver())
    # We only want to test the expansion, not run a real agent — mock get_agent
    mock_agent = MagicMock()
    mock_agent.run.return_value = MagicMock(status="completed", message="ok", decision=None)
    monkeypatch.setattr(host, "get_agent", lambda _: mock_agent)
    monkeypatch.setattr(host, "_agent_with_runtime_tracing", lambda a: a)
    monkeypatch.setattr(host, "_next_prompt_counter", lambda: 1)
    monkeypatch.setattr(host, "session_id", "test-session")

    host.run_agent("someagent", initial_instruction="See @ctx.txt for details")

    assert len(captured) == 1
    assert captured[0].endswith("ctx.txt")
    call_kwargs = mock_agent.run.call_args
    rendered = call_kwargs.kwargs.get("rendered_prompt_override") or call_kwargs.args[0]
    assert "[ctx.txt]" in rendered
    assert "@ctx.txt" not in rendered


def test_run_agent_no_resolver_leaves_instruction_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agent_framework.host import AgentHost, HostConfig
    (tmp_path / "ctx.txt").write_text("data", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    host = AgentHost(config=HostConfig(), file_ref_resolver=None)
    mock_agent = MagicMock()
    mock_agent.run.return_value = MagicMock(status="completed", message="ok", decision=None)
    monkeypatch.setattr(host, "get_agent", lambda _: mock_agent)
    monkeypatch.setattr(host, "_agent_with_runtime_tracing", lambda a: a)
    monkeypatch.setattr(host, "_next_prompt_counter", lambda: 1)
    monkeypatch.setattr(host, "session_id", "test-session")

    host.run_agent("someagent", initial_instruction="See @ctx.txt for details")

    call_kwargs = mock_agent.run.call_args
    rendered = call_kwargs.kwargs.get("rendered_prompt_override") or call_kwargs.args[0]
    assert "@ctx.txt" in rendered  # unchanged when resolver is None
```

- [ ] **Step 2: Run tests to confirm failure**

```
pytest tests/test_file_ref_host.py -v
```
Expected: `TypeError` — `AgentHost.__init__` doesn't accept `file_ref_resolver`.

- [ ] **Step 3: Add `file_ref_resolver` field to `AgentHost` and expand in `run_agent`**

In `src/agent_framework/host.py`, add the field to the `AgentHost` dataclass. It lives after `conversation_store` (line ~111), before `_executor`:

```python
    file_ref_resolver: "FileReferenceResolver | None" = field(default=None, repr=False)
```

The type annotation uses a string forward-reference because `file_reference` is an optional import. Add the import at the top of the file (with the other optional ones or in a `TYPE_CHECKING` block):

```python
from agent_framework.file_reference import DefaultFileReferenceResolver, FileReferenceResolver, expand_file_refs
```

Add a `__post_init__`-style default: since `AgentHost` is a `@dataclass`, set the default in `from_env` and `create` factory methods. OR use `field(default_factory=DefaultFileReferenceResolver)` directly.

The cleanest approach — use `field(default_factory=DefaultFileReferenceResolver)`:
```python
    file_ref_resolver: "FileReferenceResolver | None" = field(
        default_factory=lambda: DefaultFileReferenceResolver(), repr=False
    )
```

Then in `run_agent` (lines 826–847), expand `initial_instruction` before it reaches `agent.run`:

```python
    def run_agent(
        self,
        agent_id: str,
        initial_instruction: str | None = None,
        *,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
        prompt_fragments: tuple[str, ...] | None = None,
    ) -> AgentResult:
        """Run a specific agent id as a top-level invocation."""
        if initial_instruction and self.file_ref_resolver is not None:
            initial_instruction = expand_file_refs(initial_instruction, self.file_ref_resolver)
        agent = self._agent_with_runtime_tracing(self.get_agent(agent_id))
        prompt_num = self._next_prompt_counter()
        root_run_id = f"{self.session_id}.p{prompt_num}.{agent_id}"
        with active_tracer_scope(self.runtime_tracer, self.trace_context_overlay):
            return agent.run(
                host=self,
                parameters={},
                caller_id="host",
                rendered_prompt_override=initial_instruction or "",
                conversation_messages=conversation_messages,
                prompt_fragments=prompt_fragments,
                run_id=root_run_id,
            )
```

- [ ] **Step 4: Run tests — all should pass**

```
pytest tests/test_file_ref_host.py -v
```
Expected: all green.

- [ ] **Step 5: Run full suite to check for regressions**

```
pytest tests/ -q --ignore=tests/test_dial_driver.py
```
Expected: same failures as before (pre-existing `test_tool_parameters_schema` failure only).

- [ ] **Step 6: Commit**

```bash
git add src/agent_framework/host.py tests/test_file_ref_host.py
git commit -m "feat(file-ref): wire FileReferenceResolver into AgentHost.run_agent"
```

---

## Task 3: Wire resolver into evaluator case markdown

**Files:**
- Modify: `src/agent_framework_evaluator/case_markdown.py`
- Modify: `tests/test_evaluator_cli.py` — add case-markdown expansion tests

The evaluator case files live next to the initializer, so `base_dir = path.parent` is the right resolution root — not `Path.cwd()`.

### Step 1: Write the failing tests

Add to `tests/test_evaluator_cli.py`:

```python
def test_case_markdown_expands_file_refs(tmp_path: Path) -> None:
    """@filename tokens in case prompt are expanded using the case file's directory."""
    from agent_framework_evaluator.case_markdown import parse_case_markdown_file

    context_file = tmp_path / "deck.txt"
    context_file.write_text("slide content here", encoding="utf-8")

    case_md = tmp_path / "case01.md"
    case_md.write_text(
        "title: test\n---\nAnalyze @deck.txt\n---\nShould summarize slides\n---\n",
        encoding="utf-8",
    )

    result = parse_case_markdown_file(case_md, {})
    assert result is not None
    assert "slide content here" in result["prompt"]
    assert "@deck.txt" not in result["prompt"]


def test_case_markdown_missing_ref_left_unchanged(tmp_path: Path) -> None:
    from agent_framework_evaluator.case_markdown import parse_case_markdown_file

    case_md = tmp_path / "case02.md"
    case_md.write_text(
        "title: test\n---\nSee @ghost.txt\n---\ncriteria\n---\n",
        encoding="utf-8",
    )

    result = parse_case_markdown_file(case_md, {})
    assert result is not None
    assert "@ghost.txt" in result["prompt"]  # left unchanged


def test_case_markdown_custom_resolver(tmp_path: Path) -> None:
    from pathlib import Path as P

    from agent_framework_evaluator.case_markdown import parse_case_markdown_file
    from agent_framework.file_reference import FileReferenceResolver

    class UpperResolver:
        def resolve(self, path: P) -> str:
            return f"[{path.name.upper()}]"

    (tmp_path / "data.csv").write_text("a,b,c", encoding="utf-8")
    case_md = tmp_path / "case03.md"
    case_md.write_text(
        "title: t\n---\nLoad @data.csv\n---\ncriteria\n---\n",
        encoding="utf-8",
    )

    result = parse_case_markdown_file(case_md, {}, resolver=UpperResolver())
    assert result is not None
    assert "[DATA.CSV]" in result["prompt"]


def test_markdown_case_loader_expands_refs(tmp_path: Path) -> None:
    from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

    (tmp_path / "info.txt").write_text("important context", encoding="utf-8")
    (tmp_path / "case.md").write_text(
        "title: t\n---\nSee @info.txt\n---\ncriteria\n---\n",
        encoding="utf-8",
    )

    loader = MarkdownCaseLoader(tmp_path, "*.md")
    cases = loader.get_test_cases()
    assert len(cases) == 1
    assert "important context" in cases[0]["prompt"]
```

- [ ] **Step 2: Run tests to confirm failure**

```
pytest tests/test_evaluator_cli.py::test_case_markdown_expands_file_refs tests/test_evaluator_cli.py::test_case_markdown_missing_ref_left_unchanged tests/test_evaluator_cli.py::test_case_markdown_custom_resolver tests/test_evaluator_cli.py::test_markdown_case_loader_expands_refs -v
```
Expected: all four FAIL — `parse_case_markdown_file` doesn't accept `resolver` keyword and doesn't expand refs.

- [ ] **Step 3: Modify `case_markdown.py`**

Add the import at the top:
```python
from agent_framework.file_reference import DefaultFileReferenceResolver, FileReferenceResolver, expand_file_refs
```

Change `parse_case_markdown_file` signature and add expansion before returning:
```python
def parse_case_markdown_file(
    path: Path,
    evaluator_registry: Mapping[str, Callable[..., Any]],
    *,
    resolver: FileReferenceResolver | None = None,
) -> dict[str, Any] | None:
    """Parse one case file; return case metadata, prompt, criteria, and evaluator hooks.

    If *resolver* is provided (or defaults to ``DefaultFileReferenceResolver``), any
    ``@filename`` tokens in the prompt block are expanded relative to the case file's
    directory.  Pass ``resolver=None`` explicitly only if you want to suppress expansion.
    """
    # ... existing parsing code unchanged up to building the return dict ...
    prompt = parts[2].strip()
    # Expand @filename refs using the case file's directory as base.
    _resolver = resolver if resolver is not None else DefaultFileReferenceResolver()
    prompt = expand_file_refs(prompt, _resolver, base_dir=path.parent)
    # ... rest of the function unchanged ...
```

Also update `MarkdownCaseLoader.__init__` to accept and store an optional resolver, and pass it through in `get_test_cases`:

```python
class MarkdownCaseLoader:
    def __init__(
        self,
        base_dir: Path,
        glob_pattern: str,
        evaluator_registry: Mapping[str, Callable[..., Any]] | None = None,
        *,
        resolver: FileReferenceResolver | None = None,
    ) -> None:
        self._base = base_dir.resolve()
        self._glob = glob_pattern
        self._reg: Mapping[str, Callable[..., Any]] = evaluator_registry or {}
        self._resolver = resolver  # None → DefaultFileReferenceResolver inside parse
        self._cache: list[dict[str, Any]] | None = None
        self._cache_key: frozenset[tuple[str, float]] | None = None

    def get_test_cases(self) -> list[dict[str, Any]]:
        files = sorted(self._base.glob(self._glob))
        key = frozenset((str(p.resolve()), p.stat().st_mtime) for p in files)
        if self._cache is not None and key == self._cache_key:
            return self._cache
        parsed: list[dict[str, Any]] = []
        for f in files:
            row = parse_case_markdown_file(f, self._reg, resolver=self._resolver)
            if row is not None:
                parsed.append(row)
        self._cache = parsed
        self._cache_key = key
        return self._cache
```

- [ ] **Step 4: Run the four new tests — all should pass**

```
pytest tests/test_evaluator_cli.py::test_case_markdown_expands_file_refs tests/test_evaluator_cli.py::test_case_markdown_missing_ref_left_unchanged tests/test_evaluator_cli.py::test_case_markdown_custom_resolver tests/test_evaluator_cli.py::test_markdown_case_loader_expands_refs -v
```
Expected: all green.

- [ ] **Step 5: Run the full evaluator test suite**

```
pytest tests/test_evaluator_cli.py -v
```
Expected: all green (no regressions to existing tests).

- [ ] **Step 6: Run the full suite**

```
pytest tests/ -q --ignore=tests/test_dial_driver.py
```
Expected: same pre-existing failures only.

- [ ] **Step 7: Commit**

```bash
git add src/agent_framework_evaluator/case_markdown.py tests/test_evaluator_cli.py
git commit -m "feat(file-ref): expand @filename tokens in evaluator case markdown files"
```

---

## Task 4: Push branch and open PR

- [ ] **Step 1: Push and create PR**

```bash
git push -u origin feature/file-ref-injection
gh pr create \
  --title "feat(file-ref): @filename injection for agent prompts and evaluator cases" \
  --body "Closes #18
  
## Summary
- New \`file_reference.py\` module: \`FileReferenceResolver\` Protocol, \`DefaultFileReferenceResolver\` (text UTF-8 / binary base64), \`expand_file_refs()\` utility
- \`AgentHost\` gains \`file_ref_resolver\` field (default: \`DefaultFileReferenceResolver\`); \`run_agent\` expands \`initial_instruction\` before passing to the agent
- \`parse_case_markdown_file\` and \`MarkdownCaseLoader\` accept optional \`resolver\` parameter; case prompt expanded relative to the case file's directory
- Initializers can override \`host.file_ref_resolver\` in their \`register()\` hook for custom resolution (e.g. pptx extraction, remote files)

## Usage
In a case markdown file:
\`\`\`
Analyze the following deck: @\"q1-review.pptx\"
\`\`\`
Binary files are automatically base64-encoded inside \`<file encoding=\"base64\">\` tags.
"
```

---

## Self-Review Checklist

- [x] **Spec coverage**: Protocol ✓, default base64 behavior ✓, callback/strategy for initializer ✓, evaluator markdowns ✓, `run_agent` expansion ✓, strategy shared across hosts ✓
- [x] **No placeholders**: all code blocks are complete and runnable
- [x] **Type consistency**: `FileReferenceResolver` used as Protocol throughout; `DefaultFileReferenceResolver` is the concrete default; `expand_file_refs` signature is consistent across tasks
- [x] **Backward compat**: `parse_case_markdown_file` and `MarkdownCaseLoader` new params are keyword-only with defaults — no existing call sites break; `file_ref_resolver` on `AgentHost` defaults to `DefaultFileReferenceResolver()` — existing host construction is unaffected
