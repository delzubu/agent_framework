# GitHub Pages Information Architecture

This document defines the intended information architecture for the `agent_framework` GitHub Pages site.

The GitHub Pages site is a public-facing community hub. It should help newcomers understand the project, help practitioners adopt it, help experts evaluate and extend it, and help contributors find useful work. Product documentation remains maintained under this repository's `docs/` folder; public pages are maintained under `docs/pages/` and deployed by the GitHub Pages workflow.

## Goals

- Explain what `agent_framework` is, why it exists, and when to use it.
- Make the project approachable for readers without deep agentic AI experience.
- Give experienced developers a fast path to architecture, SDK behavior, extension points, and contribution workflows.
- Surface project status: docs coverage, SDK reference, changelog, roadmap, known limitations, and open work.
- Curate learning material about agentic AI without mixing general education into API reference pages.
- Showcase examples, samples, use cases, and case studies that make the framework concrete.
- Attract users and contributors while also supporting the current community.

## Audience Paths

### Newcomers

Readers who want to understand the project before installing anything.

Primary pages:

- `Home`
- `What is agent_framework?`
- `Why Another Agent Framework?`
- `Use Cases`
- `Glossary`
- `First Agent in 10 Minutes`

### Builders

Developers who want to use the framework in an application, automation, evaluation workflow, or internal tool.

Primary pages:

- `Getting Started`
- `Core Concepts`
- `Three Kinds of Agents`
- `Authoring Agents`
- `Creating a Planning Agent`
- `Tools`
- `Skills`
- `MCP Integration`
- `Evaluation and Debugging`
- `Configuration`
- `Samples`

### Experts and Architects

Readers who need to understand tradeoffs, runtime behavior, extension points, provider abstraction, tracing, and comparison with other systems.

Primary pages:

- `Architecture Overview`
- `Decision Loop`
- `Model Drivers`
- `Host and Orchestration`
- `Tracing and Observability`
- `Extension Points`
- `Design Decisions`
- `Framework Comparison`

### Contributors

People who may improve documentation, examples, code, tests, drivers, or integrations.

Primary pages:

- `Contributing`
- `Development Setup`
- `Testing and Evaluation`
- `Documentation Workflow`
- `Roadmap`
- `Good First Issues`
- `Community`

### Learners

People using The site to learn about agentic AI concepts beyond this repository.

Primary pages:

- `Agentic AI Learning Hub`
- `Concepts and Patterns`
- `Recommended Reading`
- `Research and Articles`
- `Glossary`

## Navigation Model

The site should be organized around reader intent, not repository structure. The top-level navigation should have six stable sections:

1. Start Here
2. Learn
3. Build
4. Reference
5. Examples
6. Community

Each page should begin with a short "Who this is for" note and end with "Next steps" links to the most likely follow-up pages.

## Proposed Page Tree

```text
Home

Start Here
  What is agent_framework?
  Why Another Agent Framework?
  What Can You Build With It?
  Project Status
  Roadmap
  Glossary

Learn
  Agentic AI Learning Hub
  Core Agentic AI Concepts
  Three Kinds of Agents
  How Agents Plan
  Agent Runtime Patterns
  Prompt and Decision Design
  Evaluation Concepts
  Recommended Reading
  Research and Articles

Build
  Getting Started
  First Agent in 10 Minutes
  Installation
  Configuration
  Authoring Agents
  Creating a Planning Agent
  Creating Tools
  Using Skills
  Multi-Agent Orchestration
  Model Providers and Drivers
  MCP Integration
  Evaluation and Debugging
  Tracing and Observability
  Embedding in Applications

Reference
  SDK Reference
  CLI Reference
  Agent Markdown Format
  Tool Markdown Format
  Decision JSON Contract
  Environment Variables
  Extension Points
  Error Handling
  Changelog
  Compatibility and Versioning

Examples
  Samples Overview
  Minimal Agent
  Tool-Calling Agent
  Multi-Agent Workflow
  Evaluation Project
  DIAL Provider Example
  MCP Tool Example
  Case Studies

Community
  Contributing
  Development Setup
  Testing Guide
  Documentation Workflow
  Good First Issues
  Support and Discussions
  Governance
```

## Home Page Structure

`Home.md` should act as a routing page, not a long manual.

Recommended sections:

- Project one-liner: "Markdown-defined agent runtime, orchestration host, tracing, and evaluator utilities."
- Short positioning: why it exists and what makes it different.
- Four audience cards:
  - "I am new to agentic AI"
  - "I want to build an agent"
  - "I want to evaluate or debug agents"
  - "I want to contribute"
- Current status links:
  - SDK reference
  - Developer docs
  - Changelog
  - Roadmap
- Featured examples:
  - Minimal agent
  - Tool-calling agent
  - Evaluation project
- Community links:
  - GitHub repository
  - Issues
  - Discussions, if enabled

## Section Details

### Start Here

Purpose: create trust and orientation quickly.

Pages:

- `What is agent_framework?`: plain-language explanation, core capabilities, non-goals.
- `Why Another Agent Framework?`: positioning against common frameworks by design philosophy, not marketing claims.
- `What Can You Build With It?`: workflows, assistants, evaluators, internal automation, multi-agent prototypes.
- `Project Status`: maturity level, docs status, SDK status, known gaps, supported providers.
- `Roadmap`: near-term work and contribution opportunities.
- `Glossary`: terms such as agent, host, tool, skill, model driver, callback, evaluator, trace.

### Learn

Purpose: educate readers on agentic AI and connect general concepts to this framework.

Pages:

- `Agentic AI Learning Hub`: curated entry point with beginner, intermediate, and advanced learning paths.
- `Core Agentic AI Concepts`: agents, tools, memory/conversation, planning, evaluation, orchestration.
- `Three Kinds of Agents`: comparison of standalone agents, programmatic workflow executors, and planning agents — when to use each, tradeoffs, and how they compose.
- `How Agents Plan`: narrative deep-dive into agent planning — from ReAct and Plan-and-Solve through Reflexion, two-ledger patterns, and parallel batch execution; final section describes agent_framework's implementation.
- `Agent Runtime Patterns`: loops, delegation, callbacks, tool execution, terminal tools.
- `Prompt and Decision Design`: structured output, JSON contracts, prompt organization, failure modes.
- `Evaluation Concepts`: regression testing, criteria, traces, human review, LLM-as-judge caveats.
- `Recommended Reading`: external links grouped by beginner, practitioner, research, and safety.
- `Research and Articles`: deeper papers, talks, and design notes.

### Build

Purpose: help users succeed with practical implementation.

Pages should be task-oriented and include runnable snippets.

Key pages:

- `Authoring Agents`: agent markdown structure, YAML frontmatter, system/user prompts, parameters, terminal tools.
- `Creating a Planning Agent`: step-by-step guide — frontmatter `planning:` block, plan-phase prompt, execute/reflect contract, `{{ref}}` tokens, callback handling, testing and debugging.

Key source material:

- `README.md`
- `docs/guides/using-agent-framework.md`
- `docs/guides/using-agent-evaluator.md`
- `docs/guides/using-dial.md`
- `docs/guides/debugging-with-vscode.md`
- `docs/architecture/adr-planning.md`

### Reference

Purpose: provide stable, lookup-oriented material for people already using the project.

Reference pages should avoid tutorial flow and prioritize exact contracts, defaults, and examples.

Key source material:

- `docs/architecture/*.md`
- package docstrings and public APIs
- `CHANGELOG.md`
- `pyproject.toml`
- `CLAUDE.md` for repository-specific operating rules

### Examples

Purpose: make the framework concrete and reusable.

Examples should include:

- problem statement
- relevant concepts
- files involved
- setup steps
- expected output
- extension ideas

Case studies should use a consistent structure:

- Context
- Problem
- Why `agent_framework`
- Implementation sketch
- Lessons learned
- Links to code or docs

### Community

Purpose: convert interest into participation and support existing users.

Pages:

- `Contributing`: contribution types, expectations, review process.
- `Development Setup`: local install, test commands, optional providers.
- `Testing Guide`: unit tests, evaluator tests, regression cases.
- `Documentation Workflow`: docs live in `docs/`; site is refreshed only on explicit request.
- `Good First Issues`: curated issue categories or manually maintained links.
- `Support and Discussions`: where to ask questions and report bugs.
- `Governance`: maintainer model and decision process, if applicable.

## Source Mapping

The site should reuse repository documentation rather than fork it manually.

| site area | Primary source |
| --- | --- |
| Getting started | `README.md`, `docs/guides/using-agent-framework.md` |
| Architecture | `docs/architecture/` |
| Evaluator | `docs/guides/using-agent-evaluator.md`, evaluator examples |
| DIAL | `docs/guides/using-dial.md` |
| Debugging | `docs/guides/debugging-with-vscode.md` |
| Changelog | `CHANGELOG.md` |
| Project status | `README.md`, `CHANGELOG.md`, roadmap/plans |
| Roadmap | `docs/plans/`, issue tracker, maintainer notes |
| Learning hub | curated external links plus local conceptual pages |
| Samples | `docs/guides/evaluator-examples/`, future `examples/` content |

## Page Templates

### Concept Page

```text
# Page Title

Who this is for: ...

Summary: ...

Why it matters: ...

How agent_framework approaches it: ...

Example: ...

Common pitfalls: ...

Next steps:
- ...
```

### Guide Page

```text
# Page Title

Who this is for: ...

Goal: ...

Prerequisites:
- ...

Steps:
1. ...
2. ...
3. ...

Expected result: ...

Troubleshooting:
- ...

Next steps:
- ...
```

### Reference Page

```text
# Page Title

Status: stable | evolving | experimental

Summary: ...

API / contract: ...

Defaults: ...

Examples: ...

Related pages:
- ...
```

### Case Study Page

```text
# Page Title

Context: ...

Problem: ...

Approach: ...

Implementation: ...

Outcome: ...

Lessons learned: ...

Related code/docs:
- ...
```

## Naming Rules

- Use short, readable page titles.
- Prefer reader-facing names over internal module names.
- Use consistent noun phrases for reference pages, such as `Decision JSON Contract`, `CLI Reference`, and `Environment Variables`.
- Avoid duplicating repository paths in page names unless the page is explicitly about the source layout.
- Keep page names stable because GitHub Pages site links are filename-based.

## Publishing Rules

- Maintain public site pages under `docs/pages/`.
- Do not edit `../agent_framework.wiki` as part of normal documentation work.
- Treat deeper product and developer documentation in `docs/` as source material for public pages where appropriate.
- Keep the IA, workflow, and publishing instructions in this repository.
- Deploy `docs/pages/` through the GitHub Pages workflow.
- Prefer generated or copied content from existing `docs/` material plus hand-maintained hub pages over divergent manual rewrites.

## Initial Implementation Priority

Phase 1 should establish the public hub:

1. `Home`
2. `What is agent_framework?`
3. `Getting Started`
4. `Project Status`
5. `Core Concepts`
6. `Samples Overview`
7. `Contributing`

Phase 2 should improve adoption:

1. `First Agent in 10 Minutes`
2. `Evaluation and Debugging`
3. `Authoring Agents`
4. `Creating Tools`
5. `Model Providers and Drivers`
6. `Recommended Reading`

Phase 3 should deepen expert and community value:

1. `Architecture Overview`
2. `Decision JSON Contract`
3. `Extension Points`
4. `Case Studies`
5. `Roadmap`
6. `Governance`
