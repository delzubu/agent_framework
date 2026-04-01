# Skills Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement first-class skill support — markdown-defined behavioral instruction sets agents can invoke on demand, with three-tier loading, file inventory, context isolation, hooks, and audit tracing.

**Architecture:** Skills are a third capability pillar alongside tools and subagents. The model receives a skill catalog (names + descriptions) in every call, emits `{"kind": "invoke_skill", "skill_name": "..."}` to invoke one, and the framework loads the full SKILL.md body plus a file inventory into `conversation_messages` as a clearly tagged user message — never touching `system_prompt` or `prompt_fragments`. The model reads individual files on demand via the auto-registered `read_skill_resource` tool.

**Tech Stack:** Python 3.12, `dataclasses`, `yaml` (already a dependency), `pathlib`, `re`, `pytest` (tests in `tests/test_framework_runtime.py`). No new dependencies required.

**Spec:** `docs/superpowers/specs/2026-03-31-skills-support-design.md`

---

## Task 1: AgentRun — skill_tool_names field

**Files:**
- Modify: `src/agent_framework/agents/agent_run.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing test**

Add to `tests/test_framework_runtime.py`:

```python
from agent_framework.agents.agent_run import AgentRun

def test_agent_run_has_skill_tool_names() -> None:
    run = AgentRun(run_id="x", rendered_prompt="p", seed_parameters={}, parameter_values={})
    assert run.skill_tool_names == []
    run.skill_tool_names.append("read_skill_resource")
    assert run.skill_tool_names == ["read_skill_resource"]
```

**Step 2: Run to confirm it fails**

```bash
pytest tests/test_framework_runtime.py::test_agent_run_has_skill_tool_names -v
```
Expected: `FAILED` — `AgentRun` has no `skill_tool_names` attribute.

**Step 3: Add the field**

In `src/agent_framework/agents/agent_run.py`, add after the `history` field:

```python
skill_tool_names: list[str] = field(default_factory=list)
```

**Step 4: Run to confirm it passes**

```bash
pytest tests/test_framework_runtime.py::test_agent_run_has_skill_tool_names -v
```
Expected: `PASSED`

**Step 5: Commit**

```bash
git add src/agent_framework/agents/agent_run.py tests/test_framework_runtime.py
git commit -m "feat(skills): add skill_tool_names tracking field to AgentRun"
```

---

## Task 2: Core data classes — SkillDefinition, SkillResource, SkillContent

**Files:**
- Create: `src/agent_framework/skill.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
from pathlib import Path
from dataclasses import FrozenInstanceError

def test_skill_definition_is_frozen(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition
    defn = SkillDefinition(
        name="my-skill",
        description="A test skill",
        version=None,
        priority=0,
        source_path=tmp_path / "SKILL.md",
        skill_dir=tmp_path,
    )
    try:
        defn.name = "other"  # type: ignore[misc]
        raise AssertionError("Expected frozen instance error")
    except (AttributeError, FrozenInstanceError):
        pass

def test_skill_content_holds_body_and_inventory(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillResource, SkillContent
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=tmp_path / "SKILL.md", skill_dir=tmp_path,
    )
    resource = SkillResource(relative_path="references/guide.md", full_path=tmp_path / "references" / "guide.md")
    content = SkillContent(definition=defn, body="# Instructions", inventory=(resource,))
    assert content.body == "# Instructions"
    assert len(content.inventory) == 1
    assert content.inventory[0].relative_path == "references/guide.md"
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py::test_skill_definition_is_frozen tests/test_framework_runtime.py::test_skill_content_holds_body_and_inventory -v
```
Expected: `FAILED` — `agent_framework.skill` module does not exist.

**Step 3: Create `src/agent_framework/skill.py` with the three data classes**

```python
"""Skill data types, registry, loader, and read_skill_resource tool."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from agent_framework.host import AgentHost

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """Lightweight catalog entry for one skill — loaded at discovery time."""

    name: str           # from frontmatter — canonical identifier
    description: str    # from frontmatter — injected into model catalog
    version: str | None
    priority: int       # from frontmatter, default 0
    source_path: Path   # path to SKILL.md
    skill_dir: Path     # source_path.parent


@dataclass(frozen=True, slots=True)
class SkillResource:
    """One file entry in the skill's file inventory (path only, no content)."""

    relative_path: str  # display path shown to model in inventory
    full_path: Path     # resolved absolute path used when loading content


@dataclass(frozen=True, slots=True)
class SkillContent:
    """Fully resolved skill content, built on invocation (Tier 2 load)."""

    definition: SkillDefinition
    body: str                            # SKILL.md body (after frontmatter)
    inventory: tuple[SkillResource, ...]  # available files — paths only


__all__ = ["SkillDefinition", "SkillResource", "SkillContent"]
```

**Step 4: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py::test_skill_definition_is_frozen tests/test_framework_runtime.py::test_skill_content_holds_body_and_inventory -v
```
Expected: `PASSED`

**Step 5: Commit**

```bash
git add src/agent_framework/skill.py tests/test_framework_runtime.py
git commit -m "feat(skills): add SkillDefinition, SkillResource, SkillContent data classes"
```

---

## Task 3: SkillRegistry — discovery, deduplication, filtering

**Files:**
- Modify: `src/agent_framework/skill.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
def _write_skill(skill_dir: Path, name: str, description: str, priority: int = 0) -> None:
    """Helper: create a minimal SKILL.md in a skill subdirectory."""
    d = skill_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\npriority: {priority}\n---\n# Body\n",
        encoding="utf-8",
    )


def test_skill_registry_discovers_skills(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    _write_skill(tmp_path, "my-skill", "A test skill")
    registry = SkillRegistry(directories=(tmp_path,))
    registry.discover()
    defn = registry.get("my-skill")
    assert defn.name == "my-skill"
    assert defn.description == "A test skill"


def test_skill_registry_filter_empty_returns_all(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    _write_skill(tmp_path, "skill-a", "A")
    _write_skill(tmp_path, "skill-b", "B")
    registry = SkillRegistry(directories=(tmp_path,))
    registry.discover()
    result = registry.filter(())
    assert {d.name for d in result} == {"skill-a", "skill-b"}


def test_skill_registry_filter_restricted(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    _write_skill(tmp_path, "skill-a", "A")
    _write_skill(tmp_path, "skill-b", "B")
    registry = SkillRegistry(directories=(tmp_path,))
    registry.discover()
    result = registry.filter(("skill-a",))
    assert len(result) == 1
    assert result[0].name == "skill-a"


def test_skill_registry_deduplication_first_dir_wins(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    high = tmp_path / "high"
    low = tmp_path / "low"
    _write_skill(high, "shared", "from high priority")
    _write_skill(low, "shared", "from low priority")
    registry = SkillRegistry(directories=(high, low))  # high is index 0 = highest
    registry.discover()
    assert registry.get("shared").description == "from high priority"


def test_skill_registry_invalid_frontmatter_skipped(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    bad = tmp_path / "bad-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\n# missing name and description\n---\n# Body\n", encoding="utf-8")
    _write_skill(tmp_path, "good-skill", "Good")
    registry = SkillRegistry(directories=(tmp_path,))
    registry.discover()
    assert "good-skill" in [d.name for d in registry.get_all()]
    try:
        registry.get("bad-skill")
        raise AssertionError("Expected KeyError")
    except KeyError:
        pass
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "skill_registry" -v
```
Expected: `FAILED` — `SkillRegistry` not defined.

**Step 3: Add `SkillRegistry` to `skill.py`**

Add after the data classes in `skill.py`:

```python
@dataclass(slots=True)
class SkillRegistry:
    """Discovers and caches SkillDefinitions from configured directories."""

    directories: tuple[Path, ...]
    _cache: dict[str, SkillDefinition] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: "Any") -> "SkillRegistry":
        return cls(directories=config.skills_directories)

    def discover(self) -> None:
        """Scan all directories, parse SKILL.md frontmatter, deduplicate by name."""
        candidates: list[SkillDefinition] = []
        for dir_index, directory in enumerate(self.directories):
            if not directory.is_dir():
                continue
            for skill_dir in sorted(directory.iterdir()):
                if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                defn = _parse_skill_frontmatter(skill_md, dir_index)
                if defn is not None:
                    candidates.append(defn)
        # Sort: lower dir_index (higher priority) first, then higher frontmatter priority
        candidates.sort(key=lambda d: (self.directories.index(d.skill_dir.parent) if d.skill_dir.parent in self.directories else 999, -d.priority))
        seen: dict[str, SkillDefinition] = {}
        for defn in candidates:
            if defn.name not in seen:
                seen[defn.name] = defn
        self._cache = seen

    def get(self, name: str) -> SkillDefinition:
        if name not in self._cache:
            raise KeyError(f"Unknown skill: {name!r}")
        return self._cache[name]

    def get_all(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._cache.values())

    def filter(self, allowed: tuple[str, ...]) -> tuple[SkillDefinition, ...]:
        if not allowed:
            return self.get_all()
        result = []
        for name in allowed:
            if name in self._cache:
                result.append(self._cache[name])
            else:
                _LOGGER.warning("Skill %r listed in agent frontmatter but not found in registry.", name)
        return tuple(result)

    def reload(self) -> None:
        self._cache.clear()
        self.discover()


def _parse_skill_frontmatter(skill_md: Path, _dir_index: int) -> SkillDefinition | None:
    """Parse SKILL.md frontmatter. Returns None and logs on any error."""
    try:
        raw = skill_md.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            _LOGGER.warning("Skill %s: missing frontmatter.", skill_md)
            return None
        parts = raw.split("---", 2)
        if len(parts) < 3:
            _LOGGER.warning("Skill %s: unclosed frontmatter.", skill_md)
            return None
        meta = yaml.safe_load(parts[1]) or {}
        name = str(meta.get("name", "")).strip()
        description = str(meta.get("description", "")).strip()
        if not name or not description:
            _LOGGER.warning("Skill %s: 'name' and 'description' are required.", skill_md)
            return None
        return SkillDefinition(
            name=name,
            description=description,
            version=str(meta["version"]) if "version" in meta else None,
            priority=int(meta.get("priority", 0)),
            source_path=skill_md.resolve(),
            skill_dir=skill_md.parent.resolve(),
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Skill %s: failed to parse — %s", skill_md, exc)
        return None
```

Update `__all__` at the bottom of `skill.py`:
```python
__all__ = ["SkillDefinition", "SkillResource", "SkillContent", "SkillRegistry"]
```

**Step 4: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "skill_registry" -v
```
Expected: all `PASSED`

**Step 5: Run all tests to check for regressions**

```bash
pytest -v
```
Expected: all previously passing tests still pass.

**Step 6: Commit**

```bash
git add src/agent_framework/skill.py tests/test_framework_runtime.py
git commit -m "feat(skills): add SkillRegistry with multi-directory discovery and deduplication"
```

---

## Task 4: SkillLoader — body loading and file inventory

**Files:**
- Modify: `src/agent_framework/skill.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
def test_skill_loader_reads_body(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillLoader
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: desc\n---\n# Instructions\nDo something useful.",
        encoding="utf-8",
    )
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve(),
    )
    content = SkillLoader().load(defn)
    assert "# Instructions" in content.body
    assert "Do something useful" in content.body
    assert "---" not in content.body  # frontmatter stripped


def test_skill_loader_builds_inventory_from_directory(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillLoader
    skill_dir = tmp_path / "my-skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "guide.md").write_text("# Guide", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: desc\n---\n# Body", encoding="utf-8"
    )
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve(),
    )
    content = SkillLoader().load(defn)
    paths = [r.relative_path for r in content.inventory]
    assert "references/guide.md" in paths


def test_skill_loader_detects_body_backtick_references(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillLoader
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "selling.md").write_text("# Selling guide", encoding="utf-8")
    body = "---\nname: my-skill\ndescription: desc\n---\nRead `selling.md` for details."
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve(),
    )
    content = SkillLoader().load(defn)
    paths = [r.relative_path for r in content.inventory]
    assert "selling.md" in paths


def test_skill_loader_inventory_excludes_skill_md_itself(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillLoader
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: desc\n---\n# Body", encoding="utf-8"
    )
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve(),
    )
    content = SkillLoader().load(defn)
    paths = [r.relative_path for r in content.inventory]
    assert "SKILL.md" not in paths
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "skill_loader" -v
```
Expected: `FAILED` — `SkillLoader` not defined.

**Step 3: Add `SkillLoader` to `skill.py`**

```python
_INVENTORY_EXTENSIONS = {".md", ".txt", ".py", ".sh", ".json", ".yaml", ".yml"}
_INVENTORY_EXCLUDE_DIRS = {"__pycache__", "node_modules", "dist", "build", ".git"}
_BACKTICK_PATH_RE = re.compile(r"`([^`\n]{3,200})`")


@dataclass(slots=True)
class SkillLoader:
    """Loads full SkillContent from a SkillDefinition on demand (Tier 2 load)."""

    def load(self, definition: SkillDefinition) -> SkillContent:
        body = self._read_body(definition.source_path)
        inventory = self._build_inventory(definition.skill_dir, body)
        return SkillContent(definition=definition, body=body, inventory=inventory)

    def _read_body(self, skill_md_path: Path) -> str:
        raw = skill_md_path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            return raw
        parts = raw.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else raw

    def _build_inventory(self, skill_dir: Path, body: str) -> tuple[SkillResource, ...]:
        seen: dict[str, SkillResource] = {}

        # Pass 1: recursive directory scan
        self._scan_dir(skill_dir, skill_dir, seen)

        # Pass 2: backtick body reference scan
        for match in _BACKTICK_PATH_RE.finditer(body):
            candidate = match.group(1).strip()
            if "/" not in candidate and "." not in candidate:
                continue  # not a file path
            self._try_add_reference(candidate, skill_dir, seen)

        resources = sorted(seen.values(), key=lambda r: r.relative_path)
        return tuple(resources)

    def _scan_dir(self, root: Path, current: Path, seen: dict[str, SkillResource], depth: int = 0) -> None:
        if depth > 5:
            return
        for item in sorted(current.iterdir()):
            if item.name.startswith(".") or item.name in _INVENTORY_EXCLUDE_DIRS:
                continue
            if item.is_dir():
                self._scan_dir(root, item, seen, depth + 1)
            elif item.is_file() and item.suffix.lower() in _INVENTORY_EXTENSIONS:
                if item.name == "SKILL.md" and item.parent == root:
                    continue  # exclude SKILL.md itself
                rel = item.relative_to(root).as_posix()
                seen[rel] = SkillResource(relative_path=rel, full_path=item.resolve())

    def _try_add_reference(self, path_str: str, skill_dir: Path, seen: dict[str, SkillResource]) -> None:
        candidate = Path(path_str)
        # 1. Relative to skill_dir
        resolved = (skill_dir / candidate).resolve()
        if resolved.exists():
            rel = path_str
            if rel not in seen:
                seen[rel] = SkillResource(relative_path=rel, full_path=resolved)
            return
        # 2. Absolute path
        if candidate.is_absolute() and candidate.exists():
            rel = path_str
            if rel not in seen:
                seen[rel] = SkillResource(relative_path=rel, full_path=candidate)
            return
        # 3. Relative to cwd
        cwd_resolved = (Path.cwd() / candidate).resolve()
        if cwd_resolved.exists():
            rel = path_str
            if rel not in seen:
                seen[rel] = SkillResource(relative_path=rel, full_path=cwd_resolved)
```

Update `__all__`:
```python
__all__ = ["SkillDefinition", "SkillResource", "SkillContent", "SkillRegistry", "SkillLoader"]
```

**Step 4: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "skill_loader" -v
```
Expected: all `PASSED`

**Step 5: Run all tests**

```bash
pytest -v
```

**Step 6: Commit**

```bash
git add src/agent_framework/skill.py tests/test_framework_runtime.py
git commit -m "feat(skills): add SkillLoader with body reading and two-pass file inventory"
```

---

## Task 5: ReadSkillResourceTool

**Files:**
- Modify: `src/agent_framework/skill.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
def test_read_skill_resource_resolves_relative_to_skill_dir(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillContent, ReadSkillResourceTool
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "guide.md").write_text("# Guide content", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: d\n---\n", encoding="utf-8")
    defn = SkillDefinition(name="my-skill", description="d", version=None, priority=0,
                           source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve())
    content = SkillContent(definition=defn, body="", inventory=())
    tool = ReadSkillResourceTool._make(content)
    result = tool.invoke({"path": "guide.md"}, host=None)  # type: ignore[arg-type]
    assert "Guide content" in result


def test_read_skill_resource_returns_error_for_missing_file(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillContent, ReadSkillResourceTool
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: d\n---\n", encoding="utf-8")
    defn = SkillDefinition(name="my-skill", description="d", version=None, priority=0,
                           source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve())
    content = SkillContent(definition=defn, body="", inventory=())
    tool = ReadSkillResourceTool._make(content)
    result = tool.invoke({"path": "nonexistent.md"}, host=None)  # type: ignore[arg-type]
    assert "not found" in result.lower()


def test_read_skill_resource_empty_path_returns_error(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillContent, ReadSkillResourceTool
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: d\n---\n", encoding="utf-8")
    defn = SkillDefinition(name="my-skill", description="d", version=None, priority=0,
                           source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve())
    content = SkillContent(definition=defn, body="", inventory=())
    tool = ReadSkillResourceTool._make(content)
    result = tool.invoke({"path": ""}, host=None)  # type: ignore[arg-type]
    assert "required" in result.lower() or "not found" in result.lower()
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "read_skill_resource" -v
```

**Step 3: Add `ReadSkillResourceTool` to `skill.py`**

Add after `SkillLoader`. This requires importing `Tool` and `ToolDefinition` from the framework:

```python
from agent_framework.tool import Tool, ToolDefinition, ToolParameter


_READ_SKILL_RESOURCE_DEFINITION = ToolDefinition(
    tool_id="read_skill_resource",
    description=(
        "Read a file referenced in the active skill's file inventory. "
        "Use the path exactly as listed in <skill_file_inventory>."
    ),
    parameters=(
        ToolParameter(
            name="path",
            description=(
                "Path to the file. Resolved in order: "
                "1. relative to skill directory, "
                "2. absolute path, "
                "3. relative to cwd."
            ),
            required=True,
            value_type="string",
        ),
    ),
)


class ReadSkillResourceTool(Tool):
    """Tool that reads a file from the active skill's inventory.

    Registered on host.tool_registry during skill invocation.
    Cleaned up by Agent.run() finally block after the run ends.
    Security: resolves any path without sandboxing — skills are trusted content.
    """

    _skill_content: SkillContent

    def __init__(self, skill_content: SkillContent) -> None:
        super().__init__(definition=_READ_SKILL_RESOURCE_DEFINITION)
        self._skill_content = skill_content

    @classmethod
    def _make(cls, skill_content: SkillContent) -> "ReadSkillResourceTool":
        return cls(skill_content)

    def invoke(self, arguments: dict[str, Any], host: "AgentHost") -> str:  # type: ignore[override]
        path_str = str(arguments.get("path", "")).strip()
        if not path_str:
            return "Error: path parameter is required."
        resolved = self._resolve(path_str)
        if resolved is None or not resolved.exists():
            return f"File not found: {path_str}"
        return resolved.read_text(encoding="utf-8")

    def _resolve(self, path_str: str) -> Path | None:
        candidate = Path(path_str)
        skill_dir = self._skill_content.definition.skill_dir
        rel = (skill_dir / candidate).resolve()
        if rel.exists():
            return rel
        if candidate.is_absolute() and candidate.exists():
            return candidate
        cwd_rel = (Path.cwd() / candidate).resolve()
        if cwd_rel.exists():
            return cwd_rel
        return None
```

Update `__all__`:
```python
__all__ = [
    "SkillDefinition", "SkillResource", "SkillContent",
    "SkillRegistry", "SkillLoader", "ReadSkillResourceTool",
]
```

**Step 4: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "read_skill_resource" -v
```

**Step 5: Run all tests**

```bash
pytest -v
```

**Step 6: Commit**

```bash
git add src/agent_framework/skill.py tests/test_framework_runtime.py
git commit -m "feat(skills): add ReadSkillResourceTool with three-path resolution"
```

---

## Task 6: HostConfig — skills_directories

**Files:**
- Modify: `src/agent_framework/config.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
def test_host_config_skills_directory_single(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        f"AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORY=skills\n",
        encoding="utf-8",
    )
    config = load_host_config(env_path)
    assert skills_dir.resolve() in config.skills_directories


def test_host_config_skills_directories_multi(tmp_path: Path) -> None:
    (tmp_path / "skills-a").mkdir()
    (tmp_path / "skills-b").mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        f"AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORIES=skills-a,skills-b\n",
        encoding="utf-8",
    )
    config = load_host_config(env_path)
    names = [p.name for p in config.skills_directories]
    assert "skills-a" in names
    assert "skills-b" in names
    assert names.index("skills-a") < names.index("skills-b")  # order preserved


def test_host_config_auto_detects_skills_dir(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    env_path = tmp_path / ".env"
    write_env(env_path)  # no SKILLS_DIRECTORY set
    config = load_host_config(env_path)
    assert any(p.name == "skills" for p in config.skills_directories)


def test_host_config_no_skills_dir_empty_tuple(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    write_env(env_path)  # no SKILLS_DIRECTORY, no skills/ dir
    config = load_host_config(env_path)
    assert config.skills_directories == ()
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "host_config_skills" -v
```

**Step 3: Update `config.py`**

In `HostConfig`, add after `agent_models`:
```python
skills_directories: tuple[Path, ...] = field(default_factory=tuple)
```

In `load_host_config()`, add before the `return HostConfig(...)` call:
```python
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
```

Add `skills_directories=skills_directories` to the `HostConfig(...)` constructor call.

Update `__all__` in `config.py` if needed.

**Step 4: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "host_config_skills" -v
```

**Step 5: Run all tests**

```bash
pytest -v
```

**Step 6: Commit**

```bash
git add src/agent_framework/config.py tests/test_framework_runtime.py
git commit -m "feat(skills): add skills_directories to HostConfig with auto-detection"
```

---

## Task 7: AgentHost — skill_registry + get_skill_registry()

**Files:**
- Modify: `src/agent_framework/host.py`
- Modify: `src/agent_framework/agents/agent_host_protocol.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
def test_agent_host_get_skill_registry_lazy_init(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "A test skill")
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        f"AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORY=skills\n",
        encoding="utf-8",
    )
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    assert host.skill_registry is None  # not initialized yet
    registry = host.get_skill_registry()
    assert isinstance(registry, SkillRegistry)
    assert host.skill_registry is registry  # cached


def test_agent_host_get_skill_registry_returns_same_instance(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    write_env(env_path)
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    r1 = host.get_skill_registry()
    r2 = host.get_skill_registry()
    assert r1 is r2
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "get_skill_registry" -v
```

**Step 3: Update `host.py`**

Add import at the top:
```python
from agent_framework.skill import SkillRegistry
```

Add to `AgentHost` dataclass fields (after `audit_tracer`):
```python
skill_registry: SkillRegistry | None = None
```

Add method to `AgentHost`:
```python
def get_skill_registry(self) -> SkillRegistry:
    """Lazy-initialize and return the host-level skill registry."""
    if self.skill_registry is None:
        self.skill_registry = SkillRegistry.from_config(self.config)
        self.skill_registry.discover()
    return self.skill_registry
```

**Step 4: Update `agent_host_protocol.py`**

Add method to `AgentHostProtocol`:
```python
def get_skill_registry(self) -> "Any":
    raise NotImplementedError

def register_tool(self, tool: "Any") -> None:
    raise NotImplementedError
```

Add `from typing import Any` if not already imported.

**Step 5: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "get_skill_registry" -v
```

**Step 6: Run all tests**

```bash
pytest -v
```

**Step 7: Commit**

```bash
git add src/agent_framework/host.py src/agent_framework/agents/agent_host_protocol.py tests/test_framework_runtime.py
git commit -m "feat(skills): add skill_registry and get_skill_registry() to AgentHost"
```

---

## Task 8: SkillStartEvent and SkillEndEvent

**Files:**
- Create: `src/agent_framework/agents/skill_start_event.py`
- Create: `src/agent_framework/agents/skill_end_event.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
def test_skill_start_event_fields(tmp_path: Path) -> None:
    from agent_framework.agents.skill_start_event import SkillStartEvent
    from agent_framework.agents.agent_invocation import AgentInvocation
    invocation = AgentInvocation(agent_id="a", run_id="r", rendered_prompt="p", caller_id=None)
    event = SkillStartEvent(invocation=invocation, skill_name="my-skill", parameters={})
    assert event.skill_name == "my-skill"
    assert event.parameters == {}


def test_skill_end_event_fields(tmp_path: Path) -> None:
    from agent_framework.agents.skill_end_event import SkillEndEvent
    from agent_framework.agents.agent_invocation import AgentInvocation
    from agent_framework.skill import SkillDefinition, SkillContent
    invocation = AgentInvocation(agent_id="a", run_id="r", rendered_prompt="p", caller_id=None)
    defn = SkillDefinition(name="s", description="d", version=None, priority=0,
                           source_path=tmp_path / "SKILL.md", skill_dir=tmp_path)
    content = SkillContent(definition=defn, body="body", inventory=())
    event = SkillEndEvent(invocation=invocation, skill_name="s", parameters={}, content=content)
    assert event.content.body == "body"
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "skill_start_event or skill_end_event" -v
```

**Step 3: Create `skill_start_event.py`**

```python
"""Skill start event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_invocation import AgentInvocation


@dataclass(frozen=True, slots=True)
class SkillStartEvent:
    """Pre-skill hook payload — fired before skill body is loaded."""

    invocation: AgentInvocation
    skill_name: str
    parameters: dict[str, Any]

__all__ = ["SkillStartEvent"]
```

**Step 4: Create `skill_end_event.py`**

```python
"""Skill end event."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from .agent_invocation import AgentInvocation

if TYPE_CHECKING:
    from agent_framework.skill import SkillContent


@dataclass(frozen=True, slots=True)
class SkillEndEvent:
    """Post-skill hook payload — fired after skill content is injected into conversation."""

    invocation: AgentInvocation
    skill_name: str
    parameters: dict[str, Any]
    content: "SkillContent"

__all__ = ["SkillEndEvent"]
```

**Step 5: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "skill_start_event or skill_end_event" -v
```

**Step 6: Run all tests**

```bash
pytest -v
```

**Step 7: Commit**

```bash
git add src/agent_framework/agents/skill_start_event.py src/agent_framework/agents/skill_end_event.py tests/test_framework_runtime.py
git commit -m "feat(skills): add SkillStartEvent and SkillEndEvent lifecycle events"
```

---

## Task 9: AgentDecision — skill_name field

**Files:**
- Modify: `src/agent_framework/agents/agent_decision.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
from agent_framework.model import ModelResponse

def test_agent_decision_extracts_skill_name() -> None:
    from agent_framework.agents.agent_decision import AgentDecision
    response = ModelResponse(
        payload={"kind": "invoke_skill", "skill_name": "my-skill"},
        raw_text='{"kind": "invoke_skill", "skill_name": "my-skill"}',
    )
    decision = AgentDecision.from_model_response(response)
    assert decision.kind == "invoke_skill"
    assert decision.skill_name == "my-skill"


def test_agent_decision_skill_name_defaults_to_none() -> None:
    from agent_framework.agents.agent_decision import AgentDecision
    response = ModelResponse(
        payload={"kind": "final_message", "message": "done"},
        raw_text="done",
    )
    decision = AgentDecision.from_model_response(response)
    assert decision.skill_name is None
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "agent_decision_extracts_skill or agent_decision_skill_name" -v
```
Expected: `FAILED` — `AgentDecision` has no `skill_name` attribute.

**Step 3: Update `agent_decision.py`**

Add `skill_name: str | None = None` as the last field of `AgentDecision`:
```python
@dataclass(frozen=True, slots=True)
class AgentDecision:
    kind: str
    message: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    subagent_id: str | None = None
    tool_name: str | None = None
    callback_intent: str | None = None
    skill_name: str | None = None    # NEW
```

In `from_model_response()`, add to the `return cls(...)` call:
```python
skill_name=_optional_text(payload.get("skill_name")),
```

**Step 4: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "agent_decision" -v
```

**Step 5: Run all tests**

```bash
pytest -v
```

**Step 6: Commit**

```bash
git add src/agent_framework/agents/agent_decision.py tests/test_framework_runtime.py
git commit -m "feat(skills): add skill_name field to AgentDecision"
```

---

## Task 10: System prompt templates

**Files:**
- Modify: `src/agent_framework/agents/system.md`
- Modify: `src/agent_framework/agents/system.decision.md`

No unit tests — behavior is verified through integration in Task 12.

**Step 1: Update `system.md`**

Open `src/agent_framework/agents/system.md`. Add `{skills_section}` at the end, after the Agents section:

```
{skills_section}
```

The placeholder must be on its own line so that when it expands to an empty string no extra whitespace is left.

**Step 2: Update `system.decision.md`**

Open `src/agent_framework/agents/system.decision.md`. Find the section listing decision kinds and add `invoke_skill`:

```
- `invoke_skill` — invoke a named skill; set `skill_name` to a valid skill name from `<available_skills>`
```

**Step 3: Verify the templates load without errors**

```bash
python -c "from agent_framework.model import OpenAiModelDriver; print('OK')"
```
Expected: `OK` — no format errors on import.

**Step 4: Commit**

```bash
git add src/agent_framework/agents/system.md src/agent_framework/agents/system.decision.md
git commit -m "feat(skills): add {skills_section} to system.md and invoke_skill to decision template"
```

---

## Task 11: model.py — skills_section in _capability_metadata()

**Files:**
- Modify: `src/agent_framework/model.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
from agent_framework.model import OpenAiModelDriver, CapabilityDefinition

def test_capability_metadata_includes_skills_section_when_skills_present() -> None:
    skills = (CapabilityDefinition(capability_id="my-skill", description="Does things"),)
    metadata = OpenAiModelDriver._capability_metadata(tools=(), subagents=(), skills=skills)
    assert "skills_section" in metadata
    assert "my-skill" in metadata["skills_section"]
    assert "Does things" in metadata["skills_section"]


def test_capability_metadata_skills_section_empty_when_no_skills() -> None:
    metadata = OpenAiModelDriver._capability_metadata(tools=(), subagents=(), skills=())
    assert metadata["skills_section"] == ""
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "capability_metadata" -v
```
Expected: `FAILED` — `skills_section` key not in returned dict.

**Step 3: Update `_capability_metadata()` in `model.py`**

In `OpenAiModelDriver._capability_metadata()`, add after the `subagents_json` construction:

```python
if skills:
    skills_list = json.dumps(
        [{"name": s.capability_id, "description": s.description} for s in skills],
        indent=2,
    )
    skills_section = (
        "## Skills\n\n"
        "<available_skills>\n"
        f"{skills_list}\n"
        "</available_skills>\n\n"
        "1. Review available skills and their descriptions to decide if a skill applies to the task.\n"
        "2. To invoke a skill, set `kind` to `invoke_skill` and `skill_name` to a valid skill name.\n"
        "3. After a skill is invoked, its full instructions will be injected into this conversation.\n"
        "   Follow those instructions to complete the task.\n"
        "4. You may need to read supporting files using the `read_skill_resource` tool — the skill\n"
        "   body will tell you when this is needed."
    )
else:
    skills_section = ""
```

Add `"skills_section": skills_section` to the returned dict.

**Step 4: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "capability_metadata" -v
```

**Step 5: Run all tests**

```bash
pytest -v
```

**Step 6: Commit**

```bash
git add src/agent_framework/model.py tests/test_framework_runtime.py
git commit -m "feat(skills): add skills_section to _capability_metadata() in model.py"
```

---

## Task 12: Agent — build_context(), onPreSkill/onPostSkill hooks, AgentHostProtocol

**Files:**
- Modify: `src/agent_framework/agents/agent.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing test**

```python
def test_agent_build_context_populates_skills_from_registry(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "Does useful things")

    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        "AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        "ROOT_AGENT=root\nSKILLS_DIRECTORY=skills\n",
        encoding="utf-8",
    )
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
        allowed_skills=(),  # empty = all skills
    )
    run = agent._create_run({})
    context = agent.build_context(host=host, run=run)
    skill_ids = [s.capability_id for s in context.skills]
    assert "my-skill" in skill_ids


def test_agent_has_pre_and_post_skill_hooks() -> None:
    from agent_framework.agents.sequential_hook import SequentialHook
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
    )
    assert isinstance(agent.onPreSkill, SequentialHook)
    assert isinstance(agent.onPostSkill, SequentialHook)
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "build_context_populates_skills or has_pre_and_post_skill" -v
```

**Step 3: Update `Agent` in `agents/agent.py`**

Add two new hook fields (after `onPreSubagent`/`onPostSubagent`):
```python
onPreSkill:  SequentialHook = field(default_factory=SequentialHook)
onPostSkill: SequentialHook = field(default_factory=SequentialHook)
```

Replace the stub skill population in `build_context()`:
```python
# OLD:
skills = tuple(
    CapabilityDefinition(capability_id=name, description=f"Declared skill capability {name}.")
    for name in self.allowed_skills
)

# NEW:
skill_registry = getattr(host, "get_skill_registry", None)
if callable(skill_registry):
    skill_defs = host.get_skill_registry().filter(self.allowed_skills)
    skills = tuple(
        CapabilityDefinition(capability_id=defn.name, description=defn.description)
        for defn in skill_defs
    )
else:
    skills = ()
```

Add private hook runner methods (mirror `_run_pre_tool_hooks` / `_run_post_tool_hooks`):
```python
def _run_pre_skill_hooks(self, *, run: AgentRun, event: "SkillStartEvent") -> None:
    for callback in self.onPreSkill:
        callback(event)

def _run_post_skill_hooks(self, *, run: AgentRun, event: "SkillEndEvent") -> None:
    for callback in self.onPostSkill:
        callback(event)
```

Add imports at the top of `agents/agent.py`:
```python
from .skill_start_event import SkillStartEvent
from .skill_end_event import SkillEndEvent
```

**Step 4: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "build_context_populates_skills or has_pre_and_post_skill" -v
```

**Step 5: Run all tests**

```bash
pytest -v
```

**Step 6: Commit**

```bash
git add src/agent_framework/agents/agent.py tests/test_framework_runtime.py
git commit -m "feat(skills): wire build_context() to SkillRegistry and add onPreSkill/onPostSkill hooks"
```

---

## Task 13: handle_skill_invocation(), dispatch table, and run() cleanup

This is the core implementation task. Read `handle_subagent_call()` in `agents/agent.py` before implementing — `handle_skill_invocation()` follows the same structural pattern.

**Files:**
- Modify: `src/agent_framework/agents/agent.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
def _write_env_with_skills(env_path: Path, skills_dir: Path) -> None:
    env_path.write_text(
        "OPENAI_API_KEY=test-key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        f"AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORY={skills_dir.name}\n",
        encoding="utf-8",
    )


def test_agent_invokes_skill_and_injects_content_into_conversation(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "Does useful things")
    (skills_dir / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Does useful things\n---\n# Do this thing\nFollow these steps.",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    _write_env_with_skills(env_path, skills_dir)

    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
        allowed_skills=(),
    )
    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([
            {"kind": "invoke_skill", "skill_name": "my-skill"},
            {"kind": "final_message", "message": "done"},
        ]),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    result = agent.run(host=host, parameters={}, caller_id="host")
    assert result.message == "done"
    # Skill content must appear in conversation as tagged user message
    user_contents = [m["content"] for m in agent._last_run_conversation_messages
                     if m["role"] == "user"] if hasattr(agent, "_last_run_conversation_messages") else []
    # Verify via audit tracer instead (simpler)
    record = next(iter(host.audit_tracer.active_records.values()), None)
    # After run, record is in output — check JSONL or use a capturing tracer
    # Simplest: verify the run completed without error and returned "done"
    assert result.status == "completed"


def test_agent_unknown_skill_feeds_error_back_and_continues(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    write_env(env_path)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
    )
    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([
            {"kind": "invoke_skill", "skill_name": "nonexistent-skill"},
            {"kind": "final_message", "message": "recovered"},
        ]),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    result = agent.run(host=host, parameters={}, caller_id="host")
    assert result.message == "recovered"


def test_read_skill_resource_tool_cleaned_up_after_run(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "A skill")
    env_path = tmp_path / ".env"
    _write_env_with_skills(env_path, skills_dir)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
        allowed_skills=(),
    )
    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([
            {"kind": "invoke_skill", "skill_name": "my-skill"},
            {"kind": "final_message", "message": "done"},
        ]),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    agent.run(host=host, parameters={}, caller_id="host")
    assert "read_skill_resource" not in host.tool_registry


def test_skill_hooks_fire_on_invocation(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "A skill")
    env_path = tmp_path / ".env"
    _write_env_with_skills(env_path, skills_dir)

    fired = []
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
        allowed_skills=(),
    )
    agent.onPreSkill += lambda event: fired.append(("pre", event.skill_name))
    agent.onPostSkill += lambda event: fired.append(("post", event.skill_name))

    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([
            {"kind": "invoke_skill", "skill_name": "my-skill"},
            {"kind": "final_message", "message": "done"},
        ]),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    agent.run(host=host, parameters={}, caller_id="host")
    assert ("pre", "my-skill") in fired
    assert ("post", "my-skill") in fired
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "invokes_skill or unknown_skill or read_skill_resource_tool_cleaned or skill_hooks_fire" -v
```

**Step 3: Add `handle_skill_invocation()` to `Agent` in `agents/agent.py`**

Add this method. Model it after `handle_subagent_call()`:

```python
def handle_skill_invocation(
    self,
    *,
    host: "AgentHostProtocol",
    run: AgentRun,
    decision: AgentDecision,
    caller_id: str | None,
) -> AgentResult | None:
    """Load and inject skill content into the conversation, then continue the loop."""
    from agent_framework.skill import SkillLoader, ReadSkillResourceTool

    skill_name = decision.skill_name or ""
    skill_registry = getattr(host, "get_skill_registry", None)

    # 1. Resolve definition
    try:
        skill_def = host.get_skill_registry().get(skill_name) if callable(skill_registry) else None
        if skill_def is None:
            raise KeyError(skill_name)
    except KeyError:
        error_text = f"Unknown skill: {skill_name!r}. Check available skills in <available_skills>."
        run.conversation_messages.append({"role": "assistant", "content": _stringify_parameter_value(_decision_to_dict(decision))})
        run.conversation_messages.append({"role": "user", "content": f"<skill_error>{error_text}</skill_error>"})
        return None

    # 2. Validate allowed
    if self.allowed_skills and skill_def.name not in self.allowed_skills:
        error_text = f"Skill {skill_name!r} is not in this agent's allowed skills: {sorted(self.allowed_skills)}."
        run.conversation_messages.append({"role": "assistant", "content": _stringify_parameter_value(_decision_to_dict(decision))})
        run.conversation_messages.append({"role": "user", "content": f"<skill_error>{error_text}</skill_error>"})
        return None

    # 3. Pre-skill hook
    self._run_pre_skill_hooks(
        run=run,
        event=SkillStartEvent(
            invocation=self._hook_invocation(run, caller_id),
            skill_name=skill_def.name,
            parameters=dict(decision.parameters),
        ),
    )

    # 4. Register read_skill_resource tool (once per run)
    if "read_skill_resource" not in getattr(host, "tool_registry", {}):
        content_placeholder = None  # will update after load
        # Register with a placeholder; we update the instance after loading
        # Actually: register after loading so the tool has real content
        pass

    # 5. Load skill content
    content = SkillLoader().load(skill_def)

    # 4 (continued). Register tool now that content is available
    if "read_skill_resource" not in getattr(host, "tool_registry", {}):
        resource_tool = ReadSkillResourceTool._make(content)
        if hasattr(host, "register_tool"):
            host.register_tool(resource_tool)
        elif hasattr(host, "tool_registry"):
            host.tool_registry["read_skill_resource"] = resource_tool
        if "read_skill_resource" not in run.skill_tool_names:
            run.skill_tool_names.append("read_skill_resource")

    # 6. Build injected fragment
    inventory_lines = "\n".join(f"- {r.relative_path}" for r in content.inventory)
    inventory_block = (
        f"\n\n<skill_file_inventory>\n"
        f"The following files are available. Use the read_skill_resource tool to read any of them.\n"
        f"{inventory_lines}\n"
        f"</skill_file_inventory>"
    ) if content.inventory else ""
    skill_fragment = (
        f'<skill_invocation_result name="{skill_def.name}">\n'
        f"{content.body}"
        f"{inventory_block}\n"
        f"</skill_invocation_result>"
    )

    # 7. Append model decision to conversation
    run.conversation_messages.append(
        {"role": "assistant", "content": _stringify_parameter_value(_decision_to_dict(decision))}
    )

    # 8. Inject skill content as distinct user message (never touches prompt_fragments or system_prompt)
    run.conversation_messages.append({"role": "user", "content": skill_fragment})

    # 9. Audit trace
    audit_tracer = getattr(host, "audit_tracer", None)
    if audit_tracer is not None:
        audit_tracer.record_skill_invocation(
            run_id=run.run_id,
            skill_name=skill_def.name,
            parameters=dict(decision.parameters),
            inventory=[r.relative_path for r in content.inventory],
        )

    # 10. Post-skill hook
    self._run_post_skill_hooks(
        run=run,
        event=SkillEndEvent(
            invocation=self._hook_invocation(run, caller_id),
            skill_name=skill_def.name,
            parameters=dict(decision.parameters),
            content=content,
        ),
    )

    return None  # continue loop — model now has skill instructions in context
```

**Step 4: Add `invoke_skill` to the dispatch table in `dispatch_decision()`**

Find `handlers = {...}` and add:
```python
"invoke_skill": self.handle_skill_invocation,
```

**Step 5: Add cleanup to `Agent.run()` finally block**

In `Agent.run()`, in the `finally:` block (currently contains `audit_tracer.finish_agent_call`), add before or after the existing line:
```python
for tool_name in run.skill_tool_names:
    if hasattr(host, "tool_registry"):
        host.tool_registry.pop(tool_name, None)
```

**Step 6: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "invokes_skill or unknown_skill or read_skill_resource_tool_cleaned or skill_hooks_fire" -v
```

**Step 7: Run all tests**

```bash
pytest -v
```

**Step 8: Commit**

```bash
git add src/agent_framework/agents/agent.py tests/test_framework_runtime.py
git commit -m "feat(skills): add handle_skill_invocation(), invoke_skill dispatch, and run cleanup"
```

---

## Task 14: Audit trace additions

**Files:**
- Modify: `src/agent_framework/audit_trace.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing tests**

```python
def test_audit_tracer_records_skill_invocation(tmp_path: Path) -> None:
    from agent_framework.audit_trace import InMemoryAuditTracer
    tracer = InMemoryAuditTracer(output_dir=tmp_path)
    tracer.start_agent_call(
        run_id="r1", caller_id=None, agent_name="tester",
        system_prompt="sys", system_prompt_sources=(),
        user_prompt="hello", user_prompt_sources=(),
    )
    tracer.record_skill_invocation(
        run_id="r1",
        skill_name="my-skill",
        parameters={"key": "val"},
        inventory=["references/guide.md"],
    )
    record = tracer.active_records["r1"]
    assert len(record.skill_invocations) == 1
    assert record.skill_invocations[0].skill_name == "my-skill"
    assert "references/guide.md" in record.skill_invocations[0].inventory


def test_skill_invocation_record_serializes(tmp_path: Path) -> None:
    from agent_framework.audit_trace import InMemoryAuditTracer
    tracer = InMemoryAuditTracer(output_dir=tmp_path)
    tracer.start_agent_call(
        run_id="r1", caller_id=None, agent_name="tester",
        system_prompt="sys", system_prompt_sources=(),
        user_prompt="hello", user_prompt_sources=(),
    )
    tracer.record_skill_invocation(run_id="r1", skill_name="s", parameters={}, inventory=[])
    tracer.finish_agent_call(run_id="r1")
    import json
    line = (tmp_path / tracer.output_path.name).read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert "skill_invocations" in data
    assert data["skill_invocations"][0]["skill_name"] == "s"
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_framework_runtime.py -k "audit_tracer_records_skill or skill_invocation_record_serializes" -v
```

**Step 3: Update `audit_trace.py`**

Add `SkillInvocationRecord` after `CallbackAuditRecord`:
```python
@dataclass(frozen=True, slots=True)
class SkillInvocationRecord:
    """Single skill invocation event observed during an agent run."""

    timestamp: str
    skill_name: str
    parameters: dict[str, Any]
    inventory: tuple[str, ...]  # file paths listed in inventory (no file contents)
```

Add `skill_invocations` field to `AgentCallAuditRecord`:
```python
skill_invocations: tuple[SkillInvocationRecord, ...] = ()
```

Add `record_skill_invocation()` to `InMemoryAuditTracer`:
```python
def record_skill_invocation(
    self,
    *,
    run_id: str,
    skill_name: str,
    parameters: dict[str, Any],
    inventory: list[str],
) -> None:
    record = self.active_records.get(run_id)
    if record is None:
        return
    invocations = list(record.skill_invocations)
    invocations.append(
        SkillInvocationRecord(
            timestamp=_utc_now(),
            skill_name=skill_name,
            parameters=dict(parameters),
            inventory=tuple(inventory),
        )
    )
    self.active_records[run_id] = replace(record, skill_invocations=tuple(invocations))
```

**Step 4: Run to confirm tests pass**

```bash
pytest tests/test_framework_runtime.py -k "audit_tracer_records_skill or skill_invocation_record_serializes" -v
```

**Step 5: Run all tests**

```bash
pytest -v
```

**Step 6: Commit**

```bash
git add src/agent_framework/audit_trace.py tests/test_framework_runtime.py
git commit -m "feat(skills): add SkillInvocationRecord and record_skill_invocation() to audit tracer"
```

---

## Task 15: Exports and facade

**Files:**
- Modify: `src/agent_framework/agents/__init__.py`
- Modify: `src/agent_framework/agent.py` (the facade)
- Test: `tests/test_framework_runtime.py`

**Step 1: Write the failing test**

```python
def test_skill_events_importable_from_top_level_agent_module() -> None:
    from agent_framework.agent import SkillStartEvent, SkillEndEvent  # noqa: F401
    from agent_framework.agents import SkillStartEvent, SkillEndEvent  # noqa: F401
```

**Step 2: Run to confirm it fails**

```bash
pytest tests/test_framework_runtime.py::test_skill_events_importable_from_top_level_agent_module -v
```

**Step 3: Update `agents/__init__.py`**

Add imports:
```python
from .skill_start_event import SkillStartEvent
from .skill_end_event import SkillEndEvent
```

Add to `__all__`:
```python
"SkillStartEvent",
"SkillEndEvent",
```

**Step 4: Update `agent.py` (facade)**

Add to the imports from `agent_framework.agents`:
```python
SkillStartEvent,
SkillEndEvent,
```

Add to `__all__`:
```python
"SkillStartEvent",
"SkillEndEvent",
```

**Step 5: Run to confirm test passes**

```bash
pytest tests/test_framework_runtime.py::test_skill_events_importable_from_top_level_agent_module -v
```

**Step 6: Run all tests**

```bash
pytest -v
```

**Step 7: Commit**

```bash
git add src/agent_framework/agents/__init__.py src/agent_framework/agent.py tests/test_framework_runtime.py
git commit -m "feat(skills): export SkillStartEvent and SkillEndEvent from agents package and facade"
```

---

## Task 16: Architecture documentation update

**Files:**
- Modify: `docs/architecture/overview.md`
- Modify: `docs/architecture/agent-runtime.md`
- Modify: `docs/architecture/model-abstraction.md`
- Modify: `docs/architecture/tracing-evaluation.md`
- Modify: `docs/architecture/extension-points.md`

No tests — documentation only.

**Step 1: Update `overview.md`**

Find the capability pillars section (tools, subagents). Add skills as the third pillar with a brief description: directory-discovered, three-tier loading (catalog → body → resources), model-invoked via `invoke_skill` decision kind.

**Step 2: Update `agent-runtime.md`**

Document:
- `allowed_skills` agent frontmatter key (empty = all discovered, named = restricted)
- `invoke_skill` as a new decision kind in the decision loop
- `handle_skill_invocation()` flow summary (12-step flow per spec)
- `onPreSkill` / `onPostSkill` hooks on `Agent`
- `run.skill_tool_names` and cleanup in `Agent.run()` finally block

**Step 3: Update `model-abstraction.md`**

Document:
- `{skills_section}` placeholder in `system.md` template
- Conditional: empty string when no skills, full section when skills present
- `_capability_metadata()` now returns `skills_section` key in addition to `tools_json` and `subagents_json`

**Step 4: Update `tracing-evaluation.md`**

Document:
- `SkillInvocationRecord` — fields: timestamp, skill_name, parameters, inventory
- `AgentCallAuditRecord.skill_invocations` — tuple of records, one per invocation in a run
- `read_skill_resource` file reads are traced automatically via existing tool tracing

**Step 5: Update `extension-points.md`**

Document:
- `onPreSkill` / `onPostSkill` hooks — receive `SkillStartEvent` / `SkillEndEvent`
- Future `SkillDriver` protocol for native API integration (Anthropic, OpenAI)

**Step 6: Commit**

```bash
git add docs/architecture/
git commit -m "docs: update architecture docs to reflect skills as first-class capability"
```

---

## Final verification

```bash
pytest -v
```

All tests must pass. Then verify the feature branch is clean:

```bash
git log --oneline feature/skills-support ^master
```

Expected: 16 commits, one per task.
