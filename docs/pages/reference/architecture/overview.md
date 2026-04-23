---
title: Architecture Overview
layout: default
---

# Architecture Overview

Who this is for: developers and architects evaluating runtime design.

## Areas

- Host and orchestration.
- Agent runtime.
- Programmatic workflow orchestration.
- Decision loop.
- Model drivers.
- Conversation model.
- Tools and skills.
- Tracing and evaluation.

## Current architecture notes

The runtime now supports two parent orchestration styles:

- model-driven routing through `AgentDecision` values such as `call_subagent` and `call_subagents`
- deterministic programmatic routing through `Agent.execute_programmatic_workflow(...)`

The second path is intentionally agent-owned rather than host-owned. A behavior can short-circuit from `before_run(...)`, but the workflow runner still delegates child execution through the same parent-side subagent orchestration internals used by model-driven decisions. That preserves parent transcript, audit, hook, and callback behavior instead of requiring custom trace emulation in application code.

## Next Steps

- [Host and Orchestration]({{ '/reference/architecture/host-and-orchestration/' | relative_url }})
- [Agent Runtime]({{ '/reference/architecture/agent-runtime/' | relative_url }})
- [Model Drivers]({{ '/reference/architecture/model-drivers/' | relative_url }})
- [Programmatic Workflow Agents]({{ '/reference/programmatic-workflow-agents/' | relative_url }})
