"""User communication abstraction for AgentHost.

Defines the async Protocol that all concrete user-communication implementations
must satisfy, plus data types for permission gating and a no-op implementation
suitable for headless/test use. For browser-driven runs, see
:class:`agent_framework.web_communication.WebUserCommunication`, which queues
outbound UI messages and resolves input asynchronously via
``submit_user_input``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """Result of a permission prompt."""

    allowed: bool
    remember_for_session: bool = False


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Details of an action that requires user permission."""

    tool_name: str
    action: Literal["write", "execute", "network", "delete", "other"]
    resource: str   # file path, shell command, URL, etc.
    summary: str    # short human-readable description of the action
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class UserCommunication(Protocol):
    """Async protocol for host ↔ user communication.

    All implementations must be safe to call from a sync context via the
    host's ``_run_user_comm_coro()`` bridge.

    The default implementation for console sessions is
    ``ConsoleUserCommunication``.  For headless / test use, ``NullUserCommunication``
    returns safe defaults without any I/O.
    """

    async def send_message(self, text: str, *, role: str = "assistant") -> None:
        """Send a message to the user."""
        ...

    async def ask_question(
        self,
        prompt: str,
        *,
        options: tuple[str, ...] | None = None,
        allow_freetext: bool = True,
    ) -> str:
        """Ask the user a question and return the answer."""
        ...

    async def ask_confirmation(self, prompt: str, *, default: bool = False) -> bool:
        """Ask the user a yes/no question and return True for yes."""
        ...

    async def request_permission(self, request: PermissionRequest) -> PermissionDecision:
        """Ask whether a gated action is allowed."""
        ...

    async def read_user_input(
        self,
        prompt: str = "",
        *,
        prompt_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str | None:
        """Read a line of input from the user.

        `prompt_id` and `metadata` let the host preserve routing identity and
        provenance across transports. Console implementations may ignore them;
        web implementations should surface them to the client.
        """
        ...

    async def stream_text(self, chunks: AsyncIterator[str]) -> None:
        """Stream text chunks to the user.

        The default strategy is to concatenate all chunks and call
        ``send_message`` once.  Implementations may override this for
        real-time streaming (e.g. SSE or WebSocket pushes).
        """
        ...


class NullUserCommunication:
    """No-op user communication for headless and test contexts.

    All methods return safe defaults without performing any I/O.
    """

    async def send_message(self, text: str, *, role: str = "assistant") -> None:
        pass

    async def ask_question(
        self,
        prompt: str,
        *,
        options: tuple[str, ...] | None = None,
        allow_freetext: bool = True,
    ) -> str:
        return ""

    async def ask_confirmation(self, prompt: str, *, default: bool = False) -> bool:
        return default

    async def request_permission(self, request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision(allowed=True, remember_for_session=False)

    async def read_user_input(
        self,
        prompt: str = "",
        *,
        prompt_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str | None:
        return None

    async def stream_text(self, chunks: AsyncIterator[str]) -> None:
        async for _ in chunks:
            pass


__all__ = [
    "PermissionDecision",
    "PermissionRequest",
    "UserCommunication",
    "NullUserCommunication",
]
