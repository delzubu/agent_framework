# SDK Overlay Documentation

SDK pages under `docs/pages/sdk/` are generated from source code by `scripts/generate_sdk_docs.py`.

Use this directory for human-written narrative that should be inserted into generated SDK pages.

## Mapping Rules

Module overlay:

```text
docs/sdk-overlays/agent_framework/host.md
```

is inserted into:

```text
docs/pages/sdk/agent_framework/host.md
```

Class overlay:

```text
docs/sdk-overlays/agent_framework/host/AgentHost.md
```

is inserted into:

```text
docs/pages/sdk/agent_framework/host/AgentHost.md
```

## What Belongs Here

- Purpose and responsibility of a module or class.
- Usage patterns.
- Lifecycle notes.
- How the API fits into the wider runtime.
- Common mistakes and design guidance.

## What Belongs in Source Docstrings

- Public API facts.
- Parameter descriptions.
- Return values.
- Exceptions.
- Short behavior notes.
- Small examples that are tightly coupled to the implementation.

The generated SDK page combines both sources: overlay narrative first, then API summary extracted from Python docstrings.
