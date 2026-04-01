# Skills Support Design

**Date:** 2026-03-31
**Branch:** feature/skills-support
**Status:** Approved — ready for implementation

---

## Overview

This document specifies the full design for first-class skill support in the agent framework. Skills are markdown-defined behavioral instruction sets that agents can invoke on demand. They follow the open standard (`SKILL.md` + subdirectory bundle) that Anthropic, OpenAI, and the codemie framework have converged on.

Skills become a **third capability pillar** alongside tools and subagents, following the same structural patterns already established in the codebase throughout.

### Key design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Invocation model | Model-driven `invoke_skill` decision kind | First-class traceable dispatch, mirrors `call_tool`/`call_subagent` |
| API integration | Prompt injection only (soft skills) | Both Anthropic and OpenAI native skill APIs require cloud upload; deferred to future |
| File loading | On-demand via `read_skill_resource` tool | Token-efficient; model requests only what it needs |
| File inventory | Built at invocation time from directory scan + body reference scan | Codemie pattern; no eager content loading |
| Context isolation | Skill content enters `conversation_messages` only | Never pollutes `system_prompt` or `user_prompt`/`prompt_fragments` |
| Discovery | Multi-source directories, priority-based deduplication by name | Supports project-level, config-level overrides |

---

## Research Basis

### Open standard: SKILL.md

Both Anthropic (`skills-2025-10-02` beta) and OpenAI (Responses API) have converged on the same open standard: a skill is a named subdirectory containing a `SKILL.md` file with YAML frontmatter plus a markdown body. This framework adopts the same format for full compatibility.

### codemie-ai/codemie-code reference implementation

The codemie framework (`github.com/codemie-ai/codemie-code`) contains a production-grade soft-skill loader. Key patterns adopted from it:

- **Three-tier loading**: metadata at startup, body on invocation, file contents on model request
- **File inventory**: list available files in the skill context; do not pre-load content
- **Multi-source discovery with priority-based deduplication**
- **Cache keyed by session** (`{cwd}::{agent_id}`)

Key patterns **not adopted** (deferred or out of scope):
- Pattern-based pre-injection (`/skill-name` detection in user messages) — future optimization
- CLI skill management commands — out of scope for now

---

## SKILL.md Format

Skills follow the open standard format:

```markdown
---
name: consultant-presenter        # required, lowercase + hyphens only, max 64 chars
description: >                    # required, max 1024 chars — used for model catalog
  Reviews PowerPoint decks from a senior IT consultant's perspective...
version: "1.0.0"                  # optional
author: "..."                     # optional, ignored by runtime
priority: 10                      # optional, integer, default 0, used for deduplication
---

# Skill Body

Full markdown instructions injected into the model's context when this skill is invoked.

May reference files using backtick paths: `references/selling-and-credibility.md`
These paths are detected and listed in the file inventory injected alongside the skill body.
The model reads them on demand using the `read_skill_resource` tool.
```

### Skill directory layout (convention, not enforced)

```
skills/
  skill-name/
    SKILL.md               ← required
    references/            ← supporting docs (listed in inventory)
      guide.md
    scripts/               ← executable scripts (listed in inventory)
      setup.py
    assets/                ← templates, data files (listed in inventory)
      template.json
```

### Frontmatter validation rules

- `name`: required, non-empty, lowercase letters + digits + hyphens, max 64 chars
- `description`: required, non-empty, max 1024 chars
- `version`, `author`, `priority`: optional, no validation beyond type
- Invalid frontmatter → skill is logged and skipped, never fatal to startup

---

## Architecture & Component Map

### Three-tier loading model

```
Tier 1 — Catalog       (always in model context for every call)
  SkillDefinition: name + description only
  Cost: one line per skill in the assembled system prompt

Tier 2 — Body          (loaded when model emits invoke_skill decision)
  Full SKILL.md text after frontmatter
  Cost: one skill body per invocation

Tier 3 — Resources     (loaded when model calls read_skill_resource tool)
  Individual files from the skill's file inventory
  Cost: per-file, model-controlled
```

### New components

```
src/agent_framework/skill.py
  SkillDefinition        ← lightweight catalog entry
  SkillResource          ← one file entry in the inventory (path only, no content)
  SkillContent           ← resolved body + inventory, built on invocation
  SkillRegistry          ← discovers and caches SkillDefinitions from configured dirs
  SkillLoader            ← builds SkillContent from SkillDefinition on demand

src/agent_framework/agents/skill_start_event.py
  SkillStartEvent        ← fired before skill body is loaded

src/agent_framework/agents/skill_end_event.py
  SkillEndEvent          ← fired after skill content is injected into conversation
```

### Modified components

```
src/agent_framework/config.py
  HostConfig             ← add skills_directories: tuple[Path, ...]

src/agent_framework/host.py
  AgentHost              ← add skill_registry field + get_skill_registry()

src/agent_framework/model.py
  OpenAiModelDriver      ← add skills_json to _capability_metadata()

src/agent_framework/agents/system.md
                         ← add {skills_section} block

src/agent_framework/agents/system.decision.md
                         ← add invoke_skill to decision kind documentation

src/agent_framework/agents/agent.py
  Agent                  ← add onPreSkill, onPostSkill hooks
                         ← add handle_skill_invocation() handler
                         ← update build_context() to populate skills from registry
                         ← update dispatch_decision() table
                         ← update run() finally block to clean up skill-registered tools

src/agent_framework/agents/agent_decision.py
  AgentDecision          ← add skill_name: str | None field

src/agent_framework/agents/agent_run.py
  AgentRun               ← add skill_tool_names: list[str] for cleanup tracking

src/agent_framework/agents/agent.py (facade)
src/agent_framework/agents/__init__.py
                         ← export SkillStartEvent, SkillEndEvent

src/agent_framework/audit_trace.py
  SkillInvocationRecord  ← new record type
  AgentCallAuditRecord   ← add skill_invocations field
  InMemoryAuditTracer    ← add record_skill_invocation()
```

---

## Data Classes

### `SkillDefinition`

```python
@dataclass(frozen=True, slots=True)
class SkillDefinition:
    name: str           # from frontmatter — canonical identifier
    description: str    # from frontmatter — injected into model catalog
    version: str | None
    priority: int       # from frontmatter, default 0
    source_path: Path   # path to SKILL.md
    skill_dir: Path     # source_path.parent
```

### `SkillResource`

```python
@dataclass(frozen=True, slots=True)
class SkillResource:
    relative_path: str   # display path shown to model in inventory
    full_path: Path      # resolved absolute path used when loading content
```

### `SkillContent`

```python
@dataclass(frozen=True, slots=True)
class SkillContent:
    definition: SkillDefinition
    body: str                              # SKILL.md body (after frontmatter)
    inventory: tuple[SkillResource, ...]   # available files — paths only, no content
```

---

## Skill Discovery & Registry

### `SkillRegistry`

```python
@dataclass(slots=True)
class SkillRegistry:
    directories: tuple[Path, ...]       # ordered: index 0 = highest priority
    _cache: dict[str, SkillDefinition]  # name → winning definition

    @classmethod
    def from_config(cls, config: HostConfig) -> "SkillRegistry": ...

    def discover(self) -> None:
        """Scan all directories, parse frontmatter, deduplicate by name.

        Resolution:
          1. For each directory (highest priority first), walk for */SKILL.md
          2. Parse frontmatter — invalid entries are logged and skipped
          3. Among candidates with the same name, the one from the highest-priority
             directory wins. Within the same directory, higher `priority` frontmatter
             value wins.
        """

    def get(self, name: str) -> SkillDefinition:       # raises KeyError if missing
    def get_all(self) -> tuple[SkillDefinition, ...]:  # full catalog
    def filter(self, allowed: tuple[str, ...]) -> tuple[SkillDefinition, ...]:
        """
        Empty allowed → return all (agent is unrestricted).
        Non-empty allowed → return only matching; unknown names logged, not fatal.
        """
    def reload(self) -> None:  # clear cache and re-discover
```

**Scan rules:**
- Look for `<skill-name>/SKILL.md` exactly one level deep in each configured directory
- Skip hidden directories, `__pycache__`, `node_modules`
- Frontmatter parse errors → log warning, skip skill

### `AgentHost` integration

```python
# host.py
skill_registry: SkillRegistry | None = None

def get_skill_registry(self) -> SkillRegistry:
    """Lazy-initialize skill registry from config on first access."""
    if self.skill_registry is None:
        self.skill_registry = SkillRegistry.from_config(self.config)
        self.skill_registry.discover()
    return self.skill_registry
```

---

## Skill Loading

### `SkillLoader`

```python
@dataclass(slots=True)
class SkillLoader:

    def load(self, definition: SkillDefinition) -> SkillContent:
        """Load body + build file inventory. No file contents are read here."""
        body = self._read_body(definition.source_path)
        inventory = self._build_inventory(definition.skill_dir, body)
        return SkillContent(definition=definition, body=body, inventory=inventory)

    def _read_body(self, skill_md_path: Path) -> str:
        """Read SKILL.md, strip YAML frontmatter delimiters, return body text."""

    def _build_inventory(
        self, skill_dir: Path, body: str
    ) -> tuple[SkillResource, ...]:
        """
        Two-pass inventory construction:

        Pass 1 — Directory scan:
          - Recurse up to 5 levels into skill_dir
          - Exclude: SKILL.md itself, hidden files/dirs (leading dot),
                     __pycache__, node_modules, dist, build
          - Include extensions: .md .txt .py .sh .json .yaml .yml
          - Record as SkillResource(relative_path=<relative to skill_dir>, full_path=<abs>)

        Pass 2 — Body reference scan:
          - Regex: r'`([^`\n]{3,200})`'  (backtick-quoted strings, 3-200 chars)
          - Filter to strings that look like file paths (contain '/' or '.ext')
          - Resolution order for each candidate:
              1. Relative to skill_dir  → if exists, add to inventory
              2. As absolute path       → if exists, add to inventory
              3. Relative to cwd        → if exists, add to inventory
          - Add to inventory only if not already present from Pass 1
          - Note: cross-folder references (outside skill_dir) are valid and supported
                  for exceptional cases where skills reference shared resources

        Returns deduplicated tuple sorted by relative_path.
        """
```

### `read_skill_resource` tool

Registered as a standard `Tool` on `AgentHost.tool_registry` during skill invocation, de-registered in the `finally` block after skill content is injected.

```
Tool id:     read_skill_resource
Description: Read a file referenced in the active skill's file inventory.
             Use the path exactly as listed in <skill_file_inventory>.
Parameters:
  path (string, required): Path to the file. Resolved in order:
    1. Relative to skill directory
    2. Absolute path
    3. Relative to current working directory
```

**Implementation:**

```python
class ReadSkillResourceTool(Tool):
    skill_content: SkillContent

    def invoke(self, arguments: dict[str, Any], host: AgentHost) -> str:
        path_str = str(arguments.get("path", "")).strip()
        if not path_str:
            return "Error: path parameter is required."
        resolved = self._resolve(path_str, self.skill_content.definition.skill_dir)
        if resolved is None or not resolved.exists():
            return f"File not found: {path_str}"
        return resolved.read_text(encoding="utf-8")

    def _resolve(self, path_str: str, skill_dir: Path) -> Path | None:
        candidate = Path(path_str)
        # 1. Relative to skill dir
        rel = (skill_dir / candidate).resolve()
        if rel.exists():
            return rel
        # 2. Absolute
        if candidate.is_absolute() and candidate.exists():
            return candidate
        # 3. Relative to cwd
        cwd_rel = (Path.cwd() / candidate).resolve()
        if cwd_rel.exists():
            return cwd_rel
        return None
```

**Security note:** `read_skill_resource` resolves any path without sandboxing. This is consistent with the existing world-file tool model. Skills are authored by the framework user (trusted content), not by external or untrusted sources.

---

## Model Context Integration

### Catalog injection in `build_context()`

The stub in `Agent.build_context()` that currently generates placeholder `CapabilityDefinition` entries for `self.allowed_skills` is replaced with a live registry lookup:

```python
skill_registry = host.get_skill_registry()
skill_defs = skill_registry.filter(self.allowed_skills)
skills = tuple(
    CapabilityDefinition(
        capability_id=defn.name,
        description=defn.description,
    )
    for defn in skill_defs
)
```

`ModelContext.skills` type remains `tuple[CapabilityDefinition, ...]` — no structural change.

### System prompt template (`system.md`)

A `{skills_section}` placeholder is added to `system.md`. When skills are available, it expands to:

```
## Skills

<available_skills>
{skills_json}
</available_skills>

1. Review available skills and their descriptions to decide if a skill applies to the task.
2. To invoke a skill, set `kind` to `invoke_skill` and `skill_name` to a valid skill name.
3. After a skill is invoked, its full instructions will be injected into this conversation.
   Follow those instructions to complete the task.
4. You may need to read supporting files using the `read_skill_resource` tool — the skill
   body will tell you when this is needed.
```

When no skills are available (`skills_json == "[]"`), `{skills_section}` expands to an empty string. This conditional is resolved in `_capability_metadata()` before template formatting, using a `{skills_section}` key (not `{skills_json}` directly), to avoid broken `.format()` calls when the section is empty.

### `system.decision.md` update

The decision kind documentation is updated to include `invoke_skill`:

```
Decision kinds:
  final_message  — task complete, populate `message`
  call_tool      — populate `tool_name` and `parameters`
  call_subagent  — populate `subagent_id` and `parameters`
  callback       — populate `intent` and `message`
  invoke_skill   — populate `skill_name`
```

---

## Invocation Path

### `AgentDecision` update

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

`from_model_response()` extracts `skill_name`:
```python
skill_name=_optional_text(payload.get("skill_name")),
```

### Dispatch table update

```python
handlers = {
    "final_message":  self.handle_final_message,
    "callback":       self.handle_callback,
    "call_subagent":  self.handle_subagent_call,
    "call_tool":      self.handle_tool_call,
    "invoke_skill":   self.handle_skill_invocation,   # NEW
}
```

### `AgentRun` addition

```python
# agent_run.py — one new field
skill_tool_names: list[str] = field(default_factory=list)
# Names of tools registered on the host during this run by skill invocations.
# Agent.run() removes them from host.tool_registry in its finally block,
# ensuring they do not persist into future runs.
```

### `handle_skill_invocation()` flow

```
1.  Resolve SkillDefinition from host.get_skill_registry()
      → unknown skill: inject <skill_error> message, return None (continue loop)

2.  Validate skill is in agent's allowed_skills
      → not allowed: inject <skill_error> message, return None (continue loop)

3.  Fire onPreSkill hooks → SkillStartEvent

4.  Register ReadSkillResourceTool on host.tool_registry (key: "read_skill_resource")
      Append "read_skill_resource" to run.skill_tool_names (first invocation only —
      skip if already registered from an earlier skill invocation in the same run)

5.  SkillLoader().load(skill_def) → SkillContent
      (body + file inventory, no file contents)

6.  Build skill_fragment string (see Injected message format below)

7.  Append model's invoke_skill decision to conversation_messages
      {"role": "assistant", "content": <serialized decision>}

8.  Append skill content as distinct user message to conversation_messages
      {"role": "user", "content": skill_fragment}
      ← NEVER touches system_prompt or user_prompt/prompt_fragments

9.  record_skill_invocation() on audit_tracer

10. Fire onPostSkill hooks → SkillEndEvent

11. Return None → loop continues; read_skill_resource remains registered so the
      model can call it in subsequent iterations to read files from the inventory

NOTE: read_skill_resource is NOT de-registered here. It is cleaned up by
Agent.run() in its finally block via run.skill_tool_names, after the run ends.
```

### `Agent.run()` finally block addition

```python
finally:
    # Clean up skill-registered tools scoped to this run
    for tool_name in run.skill_tool_names:
        host.tool_registry.pop(tool_name, None)
    # ... existing audit_tracer.finish_agent_call() ...
```

### Context isolation rule

Skill content enters the conversation through **one path only**: appended to `run.conversation_messages` as a clearly tagged user message. It never touches:
- `run.prompt_fragments` (behavior augmentations to user_prompt)
- `system_prompt` (stable agent instructions)
- `user_prompt` (rendered invocation input)

The message layers:

| Layer | Contents | Skill content enters? |
|---|---|---|
| `system_prompt` | Agent role + runtime capability catalog | Never |
| `user_prompt` + `prompt_fragments` | Invocation input + behavior augmentations | Never |
| `conversation_messages` | Turn-by-turn history + skill results | Yes — as tagged user message |

### Injected message format

```xml
<skill_invocation_result name="consultant-presenter">
[full SKILL.md body verbatim]

<skill_file_inventory>
The following files are available. Use the read_skill_resource tool to read any of them.
- references/audience-profiles.md
- references/selling-and-credibility.md
- references/storyline-frameworks.md
- references/tactical-techniques.md
- references/design-evaluation.md
- scripts/setup.py
</skill_file_inventory>
</skill_invocation_result>
```

---

## Hooks, Events & Tracing

### New event types

**`skill_start_event.py`:**
```python
@dataclass(frozen=True, slots=True)
class SkillStartEvent:
    invocation: AgentInvocation
    skill_name: str
    parameters: dict[str, Any]
```

**`skill_end_event.py`:**
```python
@dataclass(frozen=True, slots=True)
class SkillEndEvent:
    invocation: AgentInvocation
    skill_name: str
    parameters: dict[str, Any]
    content: SkillContent   # resolved body + inventory (no file contents)
```

### New hooks on `Agent`

```python
onPreSkill:  SequentialHook = field(default_factory=SequentialHook)
onPostSkill: SequentialHook = field(default_factory=SequentialHook)
```

Fired in `handle_skill_invocation()` — `onPreSkill` before body load, `onPostSkill` after content is injected into conversation.

### `read_skill_resource` file-read tracing

Since `read_skill_resource` is registered as a standard `Tool`, all file reads pass through the existing `handle_tool_call` path, which fires `onPreTool`/`onPostTool` and records into `audit_tracer` automatically. No additional tracing infrastructure is needed for file reads.

### Audit trace additions

**`SkillInvocationRecord`:**
```python
@dataclass(frozen=True, slots=True)
class SkillInvocationRecord:
    timestamp: str
    skill_name: str
    parameters: dict[str, Any]
    inventory: tuple[str, ...]   # relative paths listed in inventory (no file contents)
```

**`AgentCallAuditRecord`** gains:
```python
skill_invocations: tuple[SkillInvocationRecord, ...] = ()
```

**`InMemoryAuditTracer`** gains:
```python
def record_skill_invocation(
    self,
    *,
    run_id: str,
    skill_name: str,
    parameters: dict[str, Any],
    inventory: list[str],
) -> None:
```

---

## Configuration

### `.env` additions

```ini
# Single directory (default: skills/ relative to .env, used only if it exists)
SKILLS_DIRECTORY=skills

# Multiple directories, comma-separated, left = higher priority
# Takes precedence over SKILLS_DIRECTORY if both are set
SKILLS_DIRECTORIES=skills,.codemie/skills
```

### `HostConfig` addition

```python
skills_directories: tuple[Path, ...] = ()
# Empty tuple = no skills configured (feature is opt-in via .env or directory existence)
```

**Resolution logic in `load_host_config()`:**

```python
raw_multi = values.get("SKILLS_DIRECTORIES", "")
raw_single = values.get("SKILLS_DIRECTORY", "")

if raw_multi:
    skills_directories = tuple(
        (env_file.parent / p.strip()).resolve()
        for p in raw_multi.split(",") if p.strip()
    )
elif raw_single:
    skills_directories = ((env_file.parent / raw_single.strip()).resolve(),)
else:
    # Auto-detect: use skills/ if it exists alongside .env
    default = env_file.parent / "skills"
    skills_directories = (default,) if default.exists() else ()
```

Directories that do not exist at config-load time are silently skipped during discovery.

### Agent frontmatter semantics

```yaml
# Restricted — only named skills are available to this agent
skills:
  - consultant-presenter
  - summarize

# Unrestricted — all discovered skills are available (omit or leave empty)
# skills: []
```

`SkillRegistry.filter(allowed_skills)`:
- Empty tuple → return all discovered skills
- Non-empty tuple → return matching only; unknown names are logged, not fatal

---

## Future Work

### 1. Native LLM API integration

Both Anthropic (`skills-2025-10-02` beta) and OpenAI (Responses API shell tool) support native skill injection, but both require uploading skill bundles to provider cloud infrastructure:

- **Anthropic:** `POST /v1/skills` → reference via `container.skills[]` in Messages API; requires `code-execution-2025-08-25` beta
- **OpenAI:** upload to skill registry → `tools[].environment.skills` in Responses API; requires shell tool + `container_auto` environment

**Trigger to revisit:** when either provider offers inline skill content injection without a prior upload step.

**Extension point:** a `SkillDriver` protocol behind which `AnthropicSkillDriver` and `OpenAISkillDriver` implementations can slot in without touching `Agent` or `AgentHost`.

### 2. Pattern-based pre-injection

Detect `/skill-name` patterns in the user's prompt *before* each model call and pre-load matching skills into the conversation — as implemented in codemie. Enables user-driven skill invocation without requiring the model to discover the skill autonomously.

**Implementation path:** an `AgentBehavior` subclass (`SkillPatternBehavior`) that hooks `before_run()` or `before_iteration()`, scans the rendered prompt for `/skill-name` patterns, and injects matching skill content into `conversation_messages` using the same format as `handle_skill_invocation()`. No core changes required.

### 3. CLI skill management commands

`python -m agent_framework --skill list|validate|reload` for verifying discovery results, checking frontmatter validity, and forcing registry cache refresh in long-running deployments.

---

## Architecture Documentation

The existing architecture documentation in `docs/architecture/` must be updated to reflect skills as a first-class capability. Files to update:

- `docs/architecture/overview.md` — add skills to capability pillars summary
- `docs/architecture/agent-runtime.md` — document `handle_skill_invocation`, new hooks, new decision kind
- `docs/architecture/model-abstraction.md` — document `{skills_section}` in system prompt assembly
- `docs/architecture/tracing-evaluation.md` — document `SkillInvocationRecord` and `skill_invocations`
- `docs/architecture/extension-points.md` — document `onPreSkill`/`onPostSkill` hooks and future `SkillDriver` protocol
