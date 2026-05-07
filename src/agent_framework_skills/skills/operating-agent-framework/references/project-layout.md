# Project layout — agent_framework directories

This reference covers the conventional folder structure for an `agent_framework` deployment and how each directory is configured in `.env`.

---

## Directory overview

| Directory | Env key | What goes here |
|-----------|---------|---------------|
| `agents/` | `AGENT_DIRECTORY` | Agent `.md` files and optional `.json` sidecars |
| `tools/` | `TOOLS_DIRECTORY` | Tool `.md` + `.py` pairs |
| `skills/` | `SKILLS_DIRECTORY` / `SKILLS_DIRECTORIES` | Installed skill directories |
| `commands/` | `COMMANDS_DIRECTORY` | Command definitions |

All paths resolve relative to the `.env` file directory unless they are absolute.

---

## Agent file naming

- The file stem must match the `id:` field in the frontmatter exactly.
  - Example: `id: deck_reviewer` → file is `deck_reviewer.md`
- An optional JSON sidecar (`deck_reviewer.json`) may live in the same directory to supply structured defaults or overrides.
- The runtime discovers agents by scanning `AGENT_DIRECTORY` for `.md` files.

Agent `.md` files have three sections separated by `---`:

1. YAML frontmatter (`id`, `role`, `description`, `tools`, `subagents`, `parameters`, ...)
2. System prompt
3. User prompt template (may use `{{parameter_name}}` placeholders)

---

## Tool file naming

- Each tool is a sibling pair: `my_tool.md` (definition) and `my_tool.py` (implementation).
- Both files must share the same stem; the stem becomes the tool id.
  - Example: `lookup.md` + `lookup.py` → tool id `lookup`
- The `.py` file must export a `build_tool(definition)` function that returns the callable tool.
- Tools are loaded from `TOOLS_DIRECTORY` at startup.
- An agent must list each tool in its `tools:` frontmatter field for the tool to be available to that agent.

---

## Multiple skill directories

- `SKILLS_DIRECTORY` accepts a single path.
- `SKILLS_DIRECTORIES` accepts a comma-separated list of paths.
- Each listed path is a **container directory** whose immediate subdirectories are individual skill folders.
- If neither key is set, the loader auto-detects a local `skills/` directory if one exists.

Example:

```env
SKILLS_DIRECTORIES=skills,src/agent_framework_skills/skills
```

This mounts all skill folders found under `skills/` and under `src/agent_framework_skills/skills/`.

---

## Typical project tree

A minimal deployment looks like this:

```
myproject/
  .env
  agents/
    root_agent.md
    root_agent.json       # optional sidecar
    helper_agent.md
  tools/
    lookup.md
    lookup.py
  skills/
    use-agent-framework/
      SKILL.md
      references/
        ...
  commands/
    summarise.md
  world/                  # sandbox root for file-oriented tools (WORLD_DIRECTORY)
```

`.env` for the above:

```env
AGENT_DIRECTORY=agents
TOOLS_DIRECTORY=tools
SKILLS_DIRECTORY=skills
COMMANDS_DIRECTORY=commands
WORLD_DIRECTORY=world
ROOT_AGENT=root_agent
```

---

## Notes

- `ROOT_AGENT` specifies which agent is invoked by default when no `--agent` flag is passed to the CLI.
- The `world/` directory is used as the sandbox root by built-in file-oriented tools; it is not required if those tools are not used.
- Skills are read-only at runtime; the runtime never writes into a skill directory.
