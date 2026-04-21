---
title: Why Another Agent Framework
layout: default
---

# Why Another Agent Framework?

Who this is for: developers and architects comparing `agent_framework` with other agentic systems.

Agent frameworks are converging around the same basic building blocks: model calls, tools, memory or state, orchestration, observability, and evaluation. `agent_framework` exists because it chooses a different center of gravity: agents are durable documentation artifacts first, runtime objects second.

## Positioning

`agent_framework` focuses on markdown-defined agents, explicit runtime contracts, provider abstraction, orchestration, tracing, and evaluation. It is designed for teams that want agent behavior to be readable, reviewable, testable, and easy to move between local development, evaluation, and embedded application use.

The project is not trying to be the largest integration catalog, the most automated multi-agent role-play system, or a managed cloud platform. It is intentionally smaller and more explicit: prompts, decision contracts, tools, skills, callbacks, model drivers, and traces are separate concepts that can be inspected and tested.

## The Short Answer

Use `agent_framework` when you care about:

- Agent definitions that live in Markdown and can be reviewed like product or engineering documents.
- Strict structured output, where invalid decision JSON fails clearly instead of being silently repaired.
- A host-centered runtime that coordinates agents, tools, skills, callbacks, model drivers, conversation state, and traces.
- Evaluation and debugging as part of normal development.
- Provider independence without hiding provider-specific behavior.
- A framework small enough to understand, modify, and embed.

Use another framework when you primarily need:

- A very large integration ecosystem.
- Managed deployment and hosted observability.
- Deep Azure or Microsoft Foundry integration.
- RAG-first data pipelines.
- Prescriptive role-playing multi-agent crews.

## Similarities With Other Frameworks

`agent_framework` shares the same broad problem space as LangGraph/LangChain, CrewAI, Microsoft Agent Framework, LlamaIndex agents, AutoGen, Semantic Kernel, and the OpenAI Agents SDK.

Common capabilities include:

- Running LLM-backed agents.
- Calling tools or functions.
- Maintaining conversation state.
- Supporting multi-step execution.
- Providing some path to multi-agent orchestration.
- Supporting tracing, debugging, or evaluation workflows.
- Abstracting model providers to some degree.

The differences are mostly about where each framework puts structure.

## Key Differences

### Markdown Is the Agent Source

In `agent_framework`, an agent is primarily a Markdown file with YAML frontmatter, a system prompt, and a user prompt template. This makes the behavioral contract easy to read in code review and easier to maintain alongside documentation.

Most other frameworks define agents primarily as Python or .NET objects. That is powerful, but it can hide prompt and behavior changes inside code paths, decorators, factories, or graph construction.

### Strict Decisions Over Implicit Repair

`agent_framework` treats the model's structured decision as a contract. Unsupported decision kinds, missing required fields, and invalid tool arguments fail explicitly. This makes failures visible during development and evaluation.

Some frameworks optimize for convenience by wrapping common loops or recovering from partial model outputs. That can be helpful for demos and quick prototypes, but it can also make production debugging harder when the model and runtime disagree.

### Host-Centered Orchestration

The `AgentHost` owns the runtime boundary: agent registry, tool registry, command registry, model driver, conversation store, call context, tracing, user communication, and optional MCP integration. This gives the project one obvious place to understand execution.

Graph-oriented frameworks put orchestration structure in nodes and edges. Crew-style frameworks put structure in agent roles, tasks, crews, and flows. Microsoft Agent Framework puts structure around agents, workflows, state, and enterprise/provider integration.

### Evaluation Is a First-Class Workflow

`agent_framework` includes an evaluator, case files, trace streaming, result-field selection, and CLI batch evaluation. The goal is to make agent behavior testable without requiring a hosted observability product.

LangGraph commonly pairs with LangSmith for tracing, debugging, evaluation, and deployment. Microsoft Agent Framework has Microsoft ecosystem support. CrewAI has its own ecosystem and education/community tooling. These can be stronger options when the hosted platform matters more than a local-first evaluation loop.

## Comparison Matrix

This table compares `agent_framework` with three high-signal candidates in the current agent-framework ecosystem. The popularity indicators are approximate public GitHub signals observed in April 2026, not a quality ranking.

| Dimension | `agent_framework` | LangGraph / LangChain | CrewAI | Microsoft Agent Framework |
| --- | --- | --- | --- | --- |
| Primary supplier | Independent project | LangChain Inc. | CrewAI Inc. | Microsoft |
| Public popularity signal | Emerging project | LangGraph has about 29.7k GitHub stars | CrewAI has about 49.3k GitHub stars | Microsoft Agent Framework has about 9.6k GitHub stars, but inherits mindshare from AutoGen and Semantic Kernel |
| Core design unit | Markdown-defined agent plus host runtime | Graph/state machine runtime; LangChain agents are built on LangGraph | Role-based agents, crews, tasks, and flows | Agents and workflows across Python and .NET |
| Best fit | Reviewable agents, explicit contracts, local evaluation, embedded runtimes | Long-running, stateful, graph-shaped agents with durable execution and human-in-the-loop control | Fast multi-agent automation with role/task metaphors and simpler orchestration | Enterprise agent apps, Azure/Microsoft Foundry alignment, .NET/Python teams, migration from AutoGen or Semantic Kernel |
| Agent definition style | Markdown with YAML frontmatter and prompt sections | Python/JS graph and agent APIs | Python classes, decorators, YAML/project templates, crews and flows | Python and .NET SDK objects, providers, workflows |
| Orchestration model | AgentHost decision loop, tool calls, sub-agents, callbacks, skills | Explicit graph nodes, edges, state, persistence, interrupts | Crews for autonomous collaboration; Flows for deterministic control | Agent workflows with explicit multi-agent execution paths and state management |
| Structured output stance | Strict decision JSON contract; invalid decisions fail clearly | Flexible graph and message state model; structure depends on graph and node design | Higher-level abstractions hide much of the loop; task outputs can be structured | SDK-level agent/workflow abstractions with type-safety emphasis in the Microsoft ecosystem |
| Provider strategy | Model drivers; OpenAI and DIAL support today; custom drivers possible | Broad LangChain ecosystem integrations; LangGraph can be used without LangChain but commonly pairs with it | Broad LLM integration through its own ecosystem and adapters | Strong Azure/Microsoft Foundry path plus OpenAI examples; Python and .NET |
| Observability and evaluation | Built-in local traces, audit records, trace viewer, evaluator CLI and web UI | Strong when paired with LangSmith for tracing, evaluation, deployment, and Studio | Ecosystem includes tooling and enterprise/community resources | Microsoft telemetry/state/workflow direction; strong enterprise integration story |
| Multi-agent support | Sub-agent calls, batched sub-agents, callbacks | Multi-actor graph patterns, supervisors, swarms, handoffs | Central concept: autonomous role-playing agents working together | Central concept: single and multi-agent workflows |
| Learning curve | Small conceptual surface, but expects explicit contracts | Powerful but lower-level; graph/state concepts matter | Approachable for role/task automation | Familiar for Microsoft/.NET/Azure users; broader enterprise SDK surface |
| Main tradeoff | Less ecosystem breadth and fewer ready-made integrations | More power and ecosystem, more architectural surface | Fast to compose crews, less aligned with document-first agent specs | Strong platform alignment, less lightweight and less independent |

## Notes on Other Candidates

### LlamaIndex Agents

LlamaIndex is highly relevant when the problem is data- and retrieval-heavy. Its agent docs define agents around LLMs, memory, and tools, and its ecosystem is especially strong for RAG, query engines, indexes, and knowledge workflows. It is less directly comparable to `agent_framework` because its center of gravity is data infrastructure plus agent workflows, not markdown-defined agent contracts.

### AutoGen and Semantic Kernel

AutoGen and Semantic Kernel are important historically and technically, but Microsoft now positions Microsoft Agent Framework as their successor. Microsoft describes Agent Framework as combining AutoGen's single- and multi-agent abstractions with Semantic Kernel's enterprise features such as state management, type safety, filters, telemetry, and broad model support.

### OpenAI Agents SDK

The OpenAI Agents SDK is a strong candidate when the application is tightly centered on OpenAI models, tools, tracing, and hosted platform features. `agent_framework` is a better fit when the agent definition itself should remain provider-neutral and document-like.

## When `agent_framework` Is the Wrong Choice

Choose a different framework if:

- You want managed hosted deployment as the main product experience.
- You need hundreds of prebuilt integrations immediately.
- Your team is already committed to LangSmith, Microsoft Foundry, or a CrewAI workflow.
- Your main need is RAG/data indexing rather than agent runtime design.
- You prefer to define every behavior directly in code instead of Markdown.

## When `agent_framework` Is the Right Choice

Choose `agent_framework` if:

- Agent prompts and behavior contracts should be reviewed by both engineers and non-engineers.
- You want strict, testable model decisions.
- You want a local-first evaluator and trace workflow.
- You want provider abstraction without hiding model-output contracts.
- You want to understand the runtime by reading a small set of concepts: host, agents, tools, skills, callbacks, drivers, conversation state, and traces.

## Sources

- [Microsoft Agent Framework overview](https://learn.microsoft.com/en-us/agent-framework/overview/)
- [Microsoft Agent Framework GitHub repository](https://github.com/microsoft/agent-framework)
- [LangGraph documentation](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph GitHub repository](https://github.com/langchain-ai/langgraph)
- [CrewAI GitHub repository](https://github.com/crewAIInc/crewAI)
- [LlamaIndex agents documentation](https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/)

## Next Steps

- [What is agent_framework?]({{ '/start-here/what-is-agent_framework/' | relative_url }})
- [Architecture Overview]({{ '/reference/architecture/overview/' | relative_url }})
- [Framework Comparison]({{ '/reference/framework-comparison/' | relative_url }})
