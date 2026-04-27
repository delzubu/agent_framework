---
title: Agent Runtime
layout: default
---

# Agent Runtime

Who this is for: readers studying the markdown agent execution model.

## Topics

- Agent loading.
- Prompt assembly.
- Decision parsing.
- Tool and sub-agent dispatch.
- Callback handling.
- Skill invocation.

## TurnDriver seam

The per-turn loop body in `Agent.run` is factored into a `TurnDriver` protocol
(`src/agent_framework/agents/turn_driver.py`). `Agent.run` selects a driver once
per invocation via `_select_turn_driver(planning_override)` and calls
`driver.run_turn(agent, host, run, caller_id)` on each iteration. The outer loop
retains responsibility for post-agent hooks and the `continue_run` branch.

`StandardTurnDriver` is the default implementation. It is a faithful extraction of
the inline loop body that existed before this seam was introduced — all existing
behavior is unchanged.

`PlanningTurnDriver` (introduced by the planning feature) plugs in here without
modifying `Agent` or any of its `handle_*` dispatch methods.

## Next Steps

- [Decision Loop]({{ '/reference/architecture/decision-loop/' | relative_url }})
- [Agent Markdown Format]({{ '/reference/agent-markdown-format/' | relative_url }})
