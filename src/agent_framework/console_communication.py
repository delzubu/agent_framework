"""Console (stdin/stdout) implementation of the UserCommunication protocol."""

from __future__ import annotations

import asyncio
import sys
from typing import AsyncIterator

from agent_framework.user_communication import (
    NullUserCommunication,
    PermissionDecision,
    PermissionRequest,
    UserCommunication,
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

    async def send_message(self, text: str, *, role: str = "assistant") -> None:
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
            return self._session_decisions[key]

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

        return decision

    async def read_user_input(self, prompt: str = "") -> str | None:
        try:
            result = await asyncio.to_thread(input, prompt)
            return result
        except EOFError:
            return None

    async def stream_text(self, chunks: AsyncIterator[str]) -> None:
        parts: list[str] = []
        async for chunk in chunks:
            parts.append(chunk)
        if parts:
            await self.send_message("".join(parts))


__all__ = ["ConsoleUserCommunication"]
