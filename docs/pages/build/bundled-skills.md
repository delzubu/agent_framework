---
title: Bundled Skills
layout: default
---

# Bundled Skills

Who this is for: users and contributors who want to understand the reusable skill content shipped with the project.

Bundled skills are maintained as source-controlled skill directories, but the public documentation for them is static site content. They are not regenerated into the SDK reference.

## Available Skills

### use-agent-framework

Complete guide for writing agents, tools, evaluations, and tests with the `agent_framework` package.

Use this skill when working with the framework itself: writing or modifying agents, tools, sub-agents, callbacks, evaluations, test cases, or framework integrations.

Source location:

```text
src/agent_framework_skills/skills/use-agent-framework/SKILL.md
```

The skill includes on-demand references for framework usage, evaluator usage, agent prompt patterns, prompt design research, and structured prompt organization.

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
