---
title: agent_framework.audit_trace
layout: default
sdk_page: true
---


# `agent_framework.audit_trace`

## API Summary

Immutable in-memory audit tracing for agent runs.

This module remains the compatibility API for existing JSONL audit dumps.
Unified runtime tracing lives in :mod:`agent_framework.tracing` and may
eventually be backed by shared subscribers rather than duplicating writes here.

## Source

`src/agent_framework/audit_trace.py`

## Classes

- [`CallbackAuditRecord`](audit_trace/CallbackAuditRecord.html)
- [`SkillInvocationRecord`](audit_trace/SkillInvocationRecord.html)
- [`UserOutputRecord`](audit_trace/UserOutputRecord.html)
- [`UserInputRecord`](audit_trace/UserInputRecord.html)
- [`PermissionRequestRecord`](audit_trace/PermissionRequestRecord.html)
- [`AgentCallAuditRecord`](audit_trace/AgentCallAuditRecord.html)
- [`InMemoryAuditTracer`](audit_trace/InMemoryAuditTracer.html)
- [`AuditTraceSubscriber`](audit_trace/AuditTraceSubscriber.html)
