---
title: Bundled Skills
layout: default
---

# Bundled Skills

Who this is for: users and contributors who want to understand the reusable skill content shipped with the project.

Bundled skills are maintained as source-controlled skill directories, but the public documentation for them is static site content. They are not regenerated into the SDK reference.

## Available Skills

### authoring-agents

Guide for writing agent and tool definition files — prompts, frontmatter, sub-agents, callbacks, tool authoring, and workflow agents.

Source location:

```text
src/agent_framework_skills/skills/authoring-agents/SKILL.md
```

### embedding-agent-framework

Guide for embedding the `agent_framework` runtime in a Python application — host setup, configuration, callbacks, memory, and evaluations.

Source location:

```text
src/agent_framework_skills/skills/embedding-agent-framework/SKILL.md
```

### operating-agent-framework

Guide for running, deploying, and operating agent applications — CLI usage, environment configuration, evaluator, and project layout.

Source location:

```text
src/agent_framework_skills/skills/operating-agent-framework/SKILL.md
```

### debug-authoring-agents

Debugging guide for problems that originate in agent or tool definition files.

Source location:

```text
src/agent_framework_skills/skills/debug-authoring-agents/SKILL.md
```

### debug-embedding-agent-framework

Debugging guide for problems that arise when embedding the runtime in a Python application.

Source location:

```text
src/agent_framework_skills/skills/debug-embedding-agent-framework/SKILL.md
```

### debug-operating-agent-framework

Debugging guide for operational problems — environment, configuration, evaluator, and trace log analysis.

Source location:

```text
src/agent_framework_skills/skills/debug-operating-agent-framework/SKILL.md
```

## Installation

Install bundled skills with the package CLI:

```bash
agent-framework-skills install --list
agent-framework-skills install --target ~/.codex/skills
agent-framework-skills install --target ~/.claude/skills
```

Use `--dry-run` to inspect the target paths without writing files, and `--force` to overwrite an existing installed copy.

## Documentation Boundary

The SDK reference documents Python APIs such as `AgentHost`, `SkillRegistry`, `SkillLoader`, and evaluator helpers.

This page documents the bundled skill catalog as user-facing content. Keep skill explanations here unless the change belongs in the skill source itself.

## Next Steps

- [Using Skills]({{ '/build/using-skills/' | relative_url }})
- [SDK Reference]({{ '/reference/sdk-reference/' | relative_url }})
- [CLI Reference]({{ '/reference/cli-reference/' | relative_url }})
