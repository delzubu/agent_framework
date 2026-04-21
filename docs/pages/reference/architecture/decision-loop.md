---
title: Decision Loop
layout: default
---

# Decision Loop

Who this is for: readers who want to understand how an agent run progresses.

## Flow

1. Assemble model context.
2. Call the model.
3. Parse a structured decision.
4. Execute the requested action.
5. Add the result to context.
6. Repeat until completion or callback.

## Next Steps

- [Decision JSON Contract]({{ '/reference/decision-json-contract/' | relative_url }})
- [Agent Runtime]({{ '/reference/architecture/agent-runtime/' | relative_url }})
