# Guidance for AI coding agents (Cursor, Claude Code, etc.)

## Structured model responses — no guessing

When working on this repository:

- **Do not** implement repair logic, fuzzy mapping, or heuristics that turn **invalid** or **non-contract** model JSON into valid `AgentDecision` objects (e.g. unknown `kind` values like `gather_context` must **not** be coerced into `call_tool` / `callback`).
- **Do** enforce the contract: unsupported `kind` → **raise** (`ValueError` from `AgentDecision.from_model_response`) so failures are explicit.
- **Do** fix invalid output upstream: prompts, `response_format` / JSON mode, provider settings — not silent recovery in Python.

This policy is mirrored in `CLAUDE.md` under **Non-negotiable: structured model output**.
