---
title: CommandDefinition
layout: default
sdk_page: true
---


# `CommandDefinition`

Module: [`agent_framework.command`](../command.html)

## API Summary

```python
class CommandDefinition
```

A single discovered command, fully loaded at discovery time.

Attributes:
    name: Command name (filename stem, e.g. ``hello``).
    description: Short human-readable description.
    argument_hint: Optional hint shown to the user for arguments.
    allowed_tools: Optional set of tool names the command may use.
    model: Optional model override for this command.
    prompt_template: The raw prompt body with ``$ARGUMENTS`` / ``$1``–``$9``
        placeholders.
    source_path: Absolute path to the ``.md`` file.

## Attributes

- `allowed_tools`
- `argument_hint`
- `description`
- `model`
- `name`
- `prompt_template`
- `source_path`
