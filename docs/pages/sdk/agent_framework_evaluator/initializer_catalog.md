---
title: agent_framework_evaluator.initializer_catalog
layout: default
sdk_page: true
---


# `agent_framework_evaluator.initializer_catalog`

## API Summary

Discover and resolve agent-eval initializer modules (setup + callbacks, prompt defaults).

## Source

`src/agent_framework_evaluator/initializer_catalog.py`

## Functions

### `resolve_env_path`

```python
def resolve_env_path(env_path: str | Path) -> Path
```

Resolve a user-supplied ``.env`` path the same way on server and client expectations.

Relative paths are resolved against the **current working directory** (the process
that runs Uvicorn). Use an absolute ``env_path`` in the UI if the server cwd is not
your project root.

### `evaluator_initializer_root`

```python
def evaluator_initializer_root(env_file: Path) -> Path | None
```

Return ``AGENT_EVAL_INITIALIZER_DIR`` from ``env_file``, or ``None``.

### `list_initializer_scripts`

```python
def list_initializer_scripts(env_file: Path) -> list[str]
```

Relative paths (posix) of ``*.py`` files under the configured initializer directory.

### `resolve_initializer_path`

```python
def resolve_initializer_path(env_file: Path, initializer_ref: str) -> Path | None
```

Resolve ``initializer_ref`` to a readable ``.py`` under the initializer root.

### `resolve_setup_path_for_run`

```python
def resolve_setup_path_for_run(env_file: Path, ref: str | None) -> Path | None
```

Resolve UI/CLI initializer field to a ``setup_path`` for :class:`SessionRunner`.

Order: path under ``AGENT_EVAL_INITIALIZER_DIR``, then absolute ``.py``, then
``.py`` relative to cwd, then a **unique** basename match under the initializer
tree (so ``deck-review.py`` finds ``…/scripts/eval/deck-review.py`` when unambiguous).

### `load_initializer_default_prompt`

```python
def load_initializer_default_prompt(env_file: Path, initializer_ref: str) -> str
```

Load initializer/setup module and return its default prompt text.

### `load_initializer_default_evaluator_criteria`

```python
def load_initializer_default_evaluator_criteria(env_file: Path, initializer_ref: str) -> str
```

Load initializer/setup module and return default evaluator criteria text, if any.

### `load_initializer_default_agent`

```python
def load_initializer_default_agent(env_file: Path, initializer_ref: str) -> str
```

Load initializer/setup module and return default agent id, if any.

### `load_initializer_default_eval_model`

```python
def load_initializer_default_eval_model(env_file: Path, initializer_ref: str) -> str
```

Return preferred evaluator model(s) from ``DEFAULT_EVAL_MODEL`` / ``get_default_eval_model()``.

### `load_raw_test_cases`

```python
def load_raw_test_cases(env_file: Path, initializer_ref: str) -> list[dict[str, Any]]
```

Load test case dicts from initializer (includes ``code_evaluator`` callables when present).

### `serialize_test_cases`

```python
def serialize_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]
```

API-safe rows (no callables).

### `load_test_cases`

```python
def load_test_cases(env_file: Path, initializer_ref: str) -> list[dict[str, Any]]
```

Serializable test cases for ``GET /api/initializer-cases``.
