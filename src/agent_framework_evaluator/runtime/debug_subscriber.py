from __future__ import annotations

from collections import defaultdict, deque

from agent_framework.tracing import TraceEvent


class DebuggerSubscriber:
    def __init__(self) -> None:
        self._events: dict[str, deque[TraceEvent]] = defaultdict(deque)

    def consume(self, event: TraceEvent) -> None:
        session_id = event.context.session_id or "global"
        self._events[session_id].append(event)

    def drain(self, session_id: str) -> list[TraceEvent]:
        queue = self._events[session_id]
        items = list(queue)
        queue.clear()
        return items
