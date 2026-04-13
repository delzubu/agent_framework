"""Browser-backed user communication for web-hosted agent runs."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, AsyncIterator

from agent_framework.tracing_bridge import try_publish_trace
from agent_framework.user_communication import PermissionDecision, PermissionRequest


class WebUserCommunication:
    """Queue-based user I/O for driving the agent from a web client."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._pending_input: asyncio.Queue[str | None] = asyncio.Queue()
        self._outbox: deque[dict[str, Any]] = deque()

    def submit_user_input(self, text: str | None) -> None:
        self._pending_input.put_nowait(text)

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
        self._outbox.append(
            {
                "kind": "question",
                "prompt": prompt,
                "options": list(options or ()),
                "allow_freetext": allow_freetext,
            }
        )
        return await self.read_user_input(prompt)

    async def ask_confirmation(self, prompt: str, *, default: bool = False) -> bool:
        self._outbox.append({"kind": "confirmation", "prompt": prompt, "default": default})
        value = await self.read_user_input(prompt)
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
        self._outbox.append({"kind": "permission", "request": request})
        raw = await self.read_user_input(request.summary)
        allowed = str(raw or "").strip().lower() in {"y", "yes", "allow", "true", "1"}
        try_publish_trace(
            channel="user",
            kind="user.permission_resolved",
            title="Permission decision",
            payload={"session_id": self.session_id, "allowed": allowed},
        )
        return PermissionDecision(allowed=allowed, remember_for_session=False)

    async def read_user_input(self, prompt: str = "") -> str | None:
        try_publish_trace(
            channel="user",
            kind="user.prompt_requested",
            title="Waiting for user input",
            summary=prompt[:200],
            payload={"session_id": self.session_id, "prompt": prompt[:2000]},
        )
        self._outbox.append({"kind": "prompt", "prompt": prompt})
        result = await self._pending_input.get()
        try_publish_trace(
            channel="user",
            kind="user.prompt_answered",
            title="User input received",
            payload={"session_id": self.session_id, "text": (result or "")[:2000]},
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
