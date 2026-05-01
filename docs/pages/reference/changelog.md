---
title: Changelog
layout: default
---

# Changelog

Who this is for: users tracking project changes.

This page mirrors the repository root `CHANGELOG.md`, which remains the source of truth.

## Releases

### 0.6.0

Planning agents and structured result contract.

- **Planning agents** — agents can now emit a structured plan (`submit_plan`) on their first turn. The runtime executes ready steps in parallel batches, resolves `{{step_id}}` ref-token substitutions between steps, and gives the model a reflect turn after each batch to continue, replan, or finalize. See [Creating a Planning Agent]({{ '/build/creating-planning-agents/' | relative_url }}).
- **Structured output contract** — `final_message` now supports a `"response"` field (a JSON object) for typed payloads consumed by callers or evaluators. `"parameters"` on `final_message` is no longer valid and raises a `ValueError`. See [Decision JSON Contract]({{ '/reference/decision-json-contract/' | relative_url }}).
- **Evaluator UI** — plan trace restored: `plan_updated` events now render in both the Trace and Flow tabs.

### 0.5.0

Scoped memory handling (memory refs, auto-storage for oversized parameters, default memory read tools, XML prompt projection).

### 0.4.0

Support for parallel agent execution.

### 0.3.0

Agent evaluator included.

## Next Steps

- [Compatibility and Versioning]({{ '/reference/compatibility-and-versioning/' | relative_url }})
- [Project Status]({{ '/start-here/project-status/' | relative_url }})
