"""Sequential callback collection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class SequentialHook:
    """Sequential callback collection used by core agent lifecycle hooks."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[..., Any]] = []

    def __iadd__(self, callback: Callable[..., Any]) -> "SequentialHook":
        self._callbacks.append(callback)
        return self

    def __isub__(self, callback: Callable[..., Any]) -> "SequentialHook":
        self._callbacks.remove(callback)
        return self

    def __iter__(self):
        return iter(self._callbacks)

__all__ = ["SequentialHook"]
