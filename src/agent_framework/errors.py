"""Structured exception types for the agent_framework runtime."""

from __future__ import annotations


class ModelDriverError(Exception):
    """Error raised by a model driver with optional HTTP context.

    Attributes:
        status_code: HTTP status code from the upstream provider, or None for
            transport-level errors.
        upstream_body: Raw upstream response body excerpt for debugging, or
            None if not available.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        upstream_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_body = upstream_body


class ConversationNotFoundError(KeyError):
    """Raised when a conversation_id is not found in the store."""


__all__ = ["ConversationNotFoundError", "ModelDriverError"]
