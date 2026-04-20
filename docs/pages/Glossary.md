---
title: Glossary
layout: default
---

# Glossary

Who this is for: readers who need quick definitions.

## Terms

- Agent: a markdown-defined unit of behavior with prompts and metadata.
- AgentHost: the orchestration runtime that runs agents and coordinates tools, drivers, conversation state, and tracing.
- Tool: an executable capability exposed to an agent.
- Skill: reusable markdown instruction material discoverable by the runtime.
- Model driver: provider adapter for model completion calls.
- Decision loop: model output is parsed into an action, executed, and repeated until completion.
- Callback: an escalation from an agent to its caller.
- Terminal tool: a declared tool name that ends the decision loop without executing.
- Evaluator: tooling for running and judging agent test cases.
- Trace: structured record of runtime activity.

## Next Steps

- [Core Agentic AI Concepts](Core-Agentic-AI-Concepts.html)
- [Decision JSON Contract](Decision-JSON-Contract.html)
