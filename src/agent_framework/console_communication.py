"""Console (stdin/stdout) implementation of the UserCommunication protocol."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from agent_framework.tracing_bridge import try_publish_trace
from agent_framework.user_communication import (
    PermissionDecision,
    PermissionRequest,
)


class ConsoleUserCommunication:
    """UserCommunication implementation backed by sys.stdin / sys.stdout.

    All blocking I/O is run in a thread via ``asyncio.to_thread`` so it is
    safe to await from an async context.

    Permission decisions can be remembered for the entire session by keying on
    ``(tool_name, action)``.  When a previous allow/deny is remembered the
    prompt is suppressed.
    """

    def __init__(self) -> None:
        # (tool_name, action) -> PermissionDecision
        self._session_decisions: dict[tuple[str, str], PermissionDecision] = {}

    @staticmethod
    def _format_prompt(prompt: str, metadata: dict[str, object] | None) -> str:
        """Render an interactive prompt with optional provenance.

        Console sessions are single-threaded from the user's perspective, so a
        short provenance prefix is enough to explain which agent is asking.
        """
        if not metadata:
            return prompt
        agent_id = str(metadata.get("agent_id") or "").strip()
        caller_id = str(metadata.get("caller_id") or "").strip()
        intent = str(metadata.get("intent") or "").strip()
        pieces: list[str] = []
        if agent_id:
            if caller_id:
                pieces.append(f"{agent_id} <- {caller_id}")
            else:
                pieces.append(agent_id)
        if intent:
            pieces.append(intent)
        if not pieces:
            return prompt
        prefix = f"[{' | '.join(pieces)}]"
        return f"{prefix}\n{prompt}"

    async def send_message(self, text: str, *, role: str = "assistant") -> None:
        try_publish_trace(
            channel="user",
            kind="user.message_sent",
            title="Console message",
            summary=text[:200],
            payload={"role": role, "text": text[:2000]},
        )
        await asyncio.to_thread(print, text)

    async def ask_question(
        self,
        prompt: str,
        *,
        options: tuple[str, ...] | None = None,
        allow_freetext: bool = True,
    ) -> str:
        if options:
            numbered = "\n".join(f"  {i + 1}. {opt}" for i, opt in enumerate(options))
            full_prompt = f"{prompt}\n{numbered}\n> "
        else:
            full_prompt = f"{prompt}\n> "
        return await asyncio.to_thread(input, full_prompt)

    async def ask_confirmation(self, prompt: str, *, default: bool = False) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        answer = await asyncio.to_thread(input, f"{prompt} {hint} ")
        answer = answer.strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")

    async def request_permission(self, request: PermissionRequest) -> PermissionDecision:
        key = (request.tool_name, request.action)

        # Return remembered decision immediately
        if key in self._session_decisions:
            dec = self._session_decisions[key]
            try_publish_trace(
                channel="user",
                kind="user.permission_resolved",
                title="Permission remembered",
                payload={"allowed": dec.allowed, "remembered": True},
            )
            return dec

        try_publish_trace(
            channel="user",
            kind="user.permission_requested",
            title=f"Permission: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "action": request.action,
                "summary": request.summary[:500],
            },
        )
        summary_line = f"\n[Permission] {request.summary}"
        detail_line = f"  Tool: {request.tool_name}  Action: {request.action}  Resource: {request.resource}"
        prompt_line = "Allow? [y]es / [n]o / [a]llow-all / [d]eny-all: "

        full_prompt = f"{summary_line}\n{detail_line}\n{prompt_line}"
        raw = await asyncio.to_thread(input, full_prompt)
        choice = raw.strip().lower()

        if choice in ("a", "allow-all"):
            decision = PermissionDecision(allowed=True, remember_for_session=True)
            self._session_decisions[key] = decision
        elif choice in ("d", "deny-all"):
            decision = PermissionDecision(allowed=False, remember_for_session=True)
            self._session_decisions[key] = decision
        elif choice in ("y", "yes"):
            decision = PermissionDecision(allowed=True, remember_for_session=False)
        else:
            decision = PermissionDecision(allowed=False, remember_for_session=False)

        try_publish_trace(
            channel="user",
            kind="user.permission_resolved",
            title="Permission decision",
            payload={"allowed": decision.allowed},
        )
        return decision

    async def read_user_input(
        self,
        prompt: str = "",
        *,
        prompt_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str | None:
        full_prompt = self._format_prompt(prompt, metadata)
        try_publish_trace(
            channel="user",
            kind="user.prompt_requested",
            title="Console input",
            summary=full_prompt[:200],
            payload={
                "prompt": full_prompt[:2000],
                "prompt_id": prompt_id,
                "metadata": dict(metadata or {}),
            },
        )
        try:
            result = await asyncio.to_thread(input, full_prompt)
            try_publish_trace(
                channel="user",
                kind="user.prompt_answered",
                title="Console input received",
                payload={
                    "text": (result or "")[:2000],
                    "prompt_id": prompt_id,
                },
            )
            return result
        except EOFError:
            try_publish_trace(
                channel="user",
                kind="user.prompt_answered",
                title="Console EOF",
                payload={"text": None, "prompt_id": prompt_id},
            )
            return None

    async def stream_text(self, chunks: AsyncIterator[str]) -> None:
        parts: list[str] = []
        async for chunk in chunks:
            try_publish_trace(
                channel="user",
                kind="user.stream_chunk",
                title="Stream chunk",
                payload={"chunk": chunk[:2000]},
            )
            parts.append(chunk)
        if parts:
            await self.send_message("".join(parts))


__all__ = ["ConsoleUserCommunication"]
