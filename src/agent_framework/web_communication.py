"""Browser-backed user communication for web-hosted agent runs."""

from __future__ import annotations

import asyncio
import queue
import uuid
from collections import deque
from dataclasses import asdict
from typing import Any, AsyncIterator

from agent_framework.tracing_bridge import try_publish_trace
from agent_framework.user_communication import PermissionDecision, PermissionRequest


class WebUserCommunication:
    """Queue-based user I/O for driving the agent from a web client.

    Uses a thread-safe :class:`queue.Queue` so inputs submitted from the FastAPI
    WebSocket thread unblock :func:`read_user_input` running under
    ``asyncio.run`` in a worker thread. Each wait is assigned a ``prompt_id`` for
    HTTP correlation.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._pending_input: queue.Queue[str | None] = queue.Queue()
        self._outbox: deque[dict[str, Any]] = deque()
        self._pending_prompt_id: str | None = None

    def cancel_wait(self) -> bool:
        """Unblock a pending :meth:`read_user_input` with ``None`` (session closed or disconnect)."""
        if self._pending_prompt_id is None:
            return False
        self._pending_input.put(None)
        return True

    def submit_user_input(self, text: str | None, *, prompt_id: str | None = None) -> bool:
        """Deliver one line of user input to the current wait.

        If ``prompt_id`` is given, it must match the active wait. If omitted, any
        active wait accepts the value (WebSocket / legacy). Returns ``False`` if
        nothing is waiting or the id does not match.
        """
        if self._pending_prompt_id is None:
            return False
        if prompt_id is not None and prompt_id != self._pending_prompt_id:
            return False
        self._pending_input.put(text)
        return True

    def drain_outbox(self) -> list[dict[str, Any]]:
        items = list(self._outbox)
        self._outbox.clear()
        return items

    async def send_message(self, text: str, *, role: str = "assistant") -> None:
        try_publish_trace(
            channel="user",
            kind="user.message_sent",
            title="Message to user",
            summary=text[:200],
            payload={"session_id": self.session_id, "role": role, "text": text[:2000]},
        )
        self._outbox.append({"kind": "message", "role": role, "text": text})

    async def ask_question(
        self,
        prompt: str,
        *,
        options: tuple[str, ...] | None = None,
        allow_freetext: bool = True,
    ) -> str:
        value = await self._read_input_after_enqueue(
            {
                "kind": "question",
                "prompt": prompt,
                "options": list(options or ()),
                "allow_freetext": allow_freetext,
            },
            prompt,
        )
        return value or ""

    async def ask_confirmation(self, prompt: str, *, default: bool = False) -> bool:
        value = await self._read_input_after_enqueue(
            {"kind": "confirmation", "prompt": prompt, "default": default},
            prompt,
        )
        if value is None or value == "":
            return default
        return value.strip().lower() in {"y", "yes", "true", "1"}

    async def request_permission(self, request: PermissionRequest) -> PermissionDecision:
        try_publish_trace(
            channel="user",
            kind="user.permission_requested",
            title=f"Permission: {request.tool_name}",
            payload={
                "session_id": self.session_id,
                "tool_name": request.tool_name,
                "action": request.action,
                "summary": request.summary[:500],
            },
        )
        raw = await self._read_input_after_enqueue(
            {"kind": "permission", "request": asdict(request)},
            request.summary,
        )
        allowed = str(raw or "").strip().lower() in {"y", "yes", "allow", "true", "1"}
        try_publish_trace(
            channel="user",
            kind="user.permission_resolved",
            title="Permission decision",
            payload={"session_id": self.session_id, "allowed": allowed},
        )
        return PermissionDecision(allowed=allowed, remember_for_session=False)

    async def read_user_input(
        self,
        prompt: str = "",
        *,
        prompt_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str | None:
        item = {"kind": "prompt", "prompt": prompt}
        if metadata:
            item.update(metadata)
        return await self._read_input_after_enqueue(item, prompt, prompt_id=prompt_id, metadata=metadata)

    async def _read_input_after_enqueue(
        self,
        item: dict[str, Any],
        trace_prompt: str,
        *,
        prompt_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str | None:
        prompt_id = prompt_id or str(uuid.uuid4())
        item["prompt_id"] = prompt_id
        self._pending_prompt_id = prompt_id
        try_publish_trace(
            channel="user",
            kind="user.prompt_requested",
            title="Waiting for user input",
            summary=trace_prompt[:200],
            payload={
                "session_id": self.session_id,
                "prompt": trace_prompt[:2000],
                "prompt_id": prompt_id,
                "metadata": dict(metadata or {}),
            },
        )
        self._outbox.append(item)
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, self._pending_input.get)
        finally:
            self._pending_prompt_id = None
        try_publish_trace(
            channel="user",
            kind="user.prompt_answered",
            title="User input received",
            payload={
                "session_id": self.session_id,
                "text": (result or "")[:2000],
                "prompt_id": prompt_id,
                "metadata": dict(metadata or {}),
            },
        )
        return result

    async def stream_text(self, chunks: AsyncIterator[str]) -> None:
        async for chunk in chunks:
            try_publish_trace(
                channel="user",
                kind="user.stream_chunk",
                title="Stream chunk",
                payload={"session_id": self.session_id, "chunk": chunk[:2000]},
            )
            self._outbox.append({"kind": "stream", "text": chunk})
