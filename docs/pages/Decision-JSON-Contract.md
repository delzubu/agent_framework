---
title: Decision JSON Contract
layout: default
---

# Decision JSON Contract

Who this is for: agent authors and contributors working with structured model output.

## Decision Kinds

- `final_message`
- `call_tool`
- `call_subagent`
- `call_subagents`
- `callback`
- `invoke_skill`

Invalid or unsupported decision kinds should fail clearly instead of being repaired silently.

## Next Steps

- [Prompt and Decision Design](Prompt-and-Decision-Design.html)
- [Agent Runtime Patterns](Agent-Runtime-Patterns.html)
