# agent_framework_skills

## Source tree

```
agent_framework_skills/
├── __init__.py          SKILLS_DIR constant (importlib.resources pointer to bundled skills)
├── cli.py               `agent-framework-skills` CLI entry point — install subcommand
├── installer.py         install() and list_targets() — copies skill dirs to agentic tool paths
└── skills/
    ├── authoring-agents/         Writing/iterating agent .md/.json, prompts, tools, evaluator
    ├── embedding-agent-framework/ Hosting AgentHost in Python, memory, callbacks, sub-agents
    ├── operating-agent-framework/ .env config, folder layout, CLI invocation
    ├── debug-authoring-agents/   Decision envelope, prompt, planning, workflow, evaluator issues
    ├── debug-embedding-agent-framework/ Host-layer parameter, callback, memory, sub-agent issues
    └── debug-operating-agent-framework/ Model/provider, .env, MCP, CLI issues
```

Each skill directory contains:
- `SKILL.md` — frontmatter (`name`, `description`) + on-demand reference table
- `references/` — reference files loaded on demand (listed in SKILL.md table)
- `assets/` — supplementary assets (optional)
- `tools/` — helper scripts (optional, e.g. `parse_log.py` in debug skills)

## Skill pairing

The six skills form three "use + debug" pairs aligned to user task categories:

| Use skill | Debug skill | Covers |
|-----------|-------------|--------|
| `authoring-agents` | `debug-authoring-agents` | Agent definition files, prompts, tools, evaluator |
| `embedding-agent-framework` | `debug-embedding-agent-framework` | Python host, memory, callbacks |
| `operating-agent-framework` | `debug-operating-agent-framework` | Config, CLIs, model provider |

## Installer

`installer.py:install()` iterates `SKILLS_DIR` and `shutil.copytree`s each top-level skill folder into the target. Adding a new skill = add a new top-level directory under `skills/`; no code changes needed.

Well-known targets (auto-discovered if the parent tool directory exists):
- `~/.claude/skills` (Claude Code user-level)
- `./.claude/skills` (Claude Code project-level)
- `~/.codex/skills`, `~/.cursor/skills`, `~/.codeium/windsurf/skills`, `~/.gemini/skills`

## Rules

**SKILL.md frontmatter `description` is the on-demand loader trigger.** Keep it precise — the skill index entry is loaded into context to decide which skill to activate. Overly broad descriptions cause the wrong skill (or all skills) to load.

**Add new content as a reference file, not inline in SKILL.md.** SKILL.md should stay short; reference files are loaded only when needed. Add each new file to the SKILL.md reference table with a narrow "When to load" condition.

**Shared diagnostic content (trace-jsonl.md, parse_log.py) is duplicated across the three debug skills.** This is intentional — the installer copies whole directories and there is no cross-skill include mechanism. Keep duplicates in sync when updating.
