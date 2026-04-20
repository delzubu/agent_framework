---
title: agent_framework.tool
layout: default
sdk_page: true
---


# `agent_framework.tool`

## API Summary

Typed tool contracts and loader for markdown-defined tools.

Tools follow the same split as agents:
- Markdown defines the caller-visible contract.
- A sibling Python module defines the implementation.

## Source

`src/agent_framework/tool.py`

## Classes

- [`ToolParameter`](tool/ToolParameter.html)
- [`ToolDefinition`](tool/ToolDefinition.html)
- [`Tool`](tool/Tool.html)
