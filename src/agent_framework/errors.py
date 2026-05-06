"""Structured exception types for the agent_framework runtime."""

from __future__ import annotations

from typing import Literal

ModelFailureCategory = Literal[
    "communication",
    "availability",
    "rate_limit",
    "output_parse",
    "validation",
]


class ModelDriverError(Exception):
    """Error raised by a model driver with optional HTTP context.

    Attributes:
        status_code: HTTP status code from the upstream provider, or None for
            transport-level errors.
        upstream_body: Raw upstream response body excerpt for debugging, or
            None if not available.
        fallback_eligible: Whether model-list fallback may try another model.
        failure_category: Broad failure class for fallback decisions and logs.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        upstream_body: str | None = None,
        fallback_eligible: bool | None = None,
        failure_category: ModelFailureCategory | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_body = upstream_body
        self.fallback_eligible = (
            _default_fallback_eligible(status_code)
            if fallback_eligible is None
            else fallback_eligible
        )
        self.failure_category = failure_category or _default_failure_category(
            status_code,
            fallback_eligible=self.fallback_eligible,
        )


def _default_fallback_eligible(status_code: int | None) -> bool:
    if status_code is None:
        return False
    return status_code in {403, 404, 408, 429} or status_code >= 500


def _default_failure_category(
    status_code: int | None,
    *,
    fallback_eligible: bool,
) -> ModelFailureCategory:
    if status_code == 429:
        return "rate_limit"
    if status_code in {403, 404}:
        return "availability"
    if status_code is not None and fallback_eligible:
        return "communication"
    return "output_parse"


class ConversationNotFoundError(KeyError):
    """Raised when a conversation_id is not found in the store."""


__all__ = ["ConversationNotFoundError", "ModelDriverError", "ModelFailureCategory"]
