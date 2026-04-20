---
title: What is agent_framework
layout: default
---

# What is agent_framework?

Who this is for: anyone evaluating whether this project is relevant to their work.

`agent_framework` is a Python framework for defining agents in Markdown, running them through an orchestration host, connecting them to tools and model providers, tracing their behavior, and evaluating their outputs.

## What It Is For

- Building markdown-defined agents with explicit prompts and parameter contracts.
- Running tool-calling and multi-agent workflows.
- Integrating multiple model providers through driver abstractions.
- Evaluating agents with repeatable test cases.
- Inspecting runtime behavior through traces and audit logs.

## What Makes It Different

- Agent behavior lives in Markdown, so prompts and runtime contracts are visible and reviewable.
- The host model separates orchestration, tools, drivers, skills, conversation state, and tracing.
- Evaluation and debugging are first-class parts of the workflow, not an afterthought.
- Strict structured-output contracts favor explicit failure over hidden repair logic.

## Next Steps

- [Why Another Agent Framework?](Why-Another-Agent-Framework.html)
- [Getting Started](Getting-Started.html)
- [Core Concepts](Core-Agentic-AI-Concepts.html)
