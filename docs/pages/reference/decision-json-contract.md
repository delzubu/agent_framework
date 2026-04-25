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
- `callback_to_caller`
- `request_user_input`
- `request_resolution`
- `invoke_skill`

Invalid or unsupported decision kinds should fail clearly instead of being repaired silently.

## Next Steps

- [Handling Callbacks]({{ '/build/handling-callbacks/' | relative_url }})
- [Prompt and Decision Design]({{ '/learn/prompt-and-decision-design/' | relative_url }})
- [Agent Runtime Patterns]({{ '/learn/agent-runtime-patterns/' | relative_url }})
