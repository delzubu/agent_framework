---
title: agent_framework.command
layout: default
sdk_page: true
---


# `agent_framework.command`

## API Summary

Command definitions, registry, and prompt rendering.

Commands are parametrized Markdown prompts stored in a dedicated directory.
Each command file follows the Claude Code frontmatter format:

    ---
    description: Short description of what this command does
    argument-hint: <argument description>    # optional
    allowed-tools:                           # optional
      - Read
      - Bash
    model: gpt-4o                            # optional model override
    ---
    The prompt template. Use $ARGUMENTS for the full raw argument string,
    or $1, $2, … $9 for positional tokens.

Command name = filename stem.  Nested directories are not supported in this
iteration (flat directory only).  Unknown commands dispatch to a
consumer-supplied callback registered on the host.

## Source

`src/agent_framework/command.py`

## Classes

- [`CommandDefinition`](command/CommandDefinition.html)
- [`CommandRegistry`](command/CommandRegistry.html)

## Functions

### `render`

```python
def render(cmd: CommandDefinition, raw_args: str) -> str
```

Render a command prompt by substituting argument placeholders.

- ``$ARGUMENTS`` is replaced with the full ``raw_args`` string.
- ``$1``–``$9`` are replaced with whitespace-split positional tokens
  (missing tokens expand to an empty string).

Args:
    cmd: The command whose ``prompt_template`` is rendered.
    raw_args: Raw argument string supplied by the user (e.g. ``"World"``).

Returns:
    The rendered prompt string ready to be injected as a user message.
