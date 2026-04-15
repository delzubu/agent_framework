"""Headless replies for WebUserCommunication prompts (agent evaluator only)."""

from __future__ import annotations

from typing import Any

# Sent when the agent asks for clarification / confirmation via user_comm (including host callbacks).
EVALUATOR_AUTO_CLARIFICATION_REPLY = (
    "go by making your best assumption based on the available data, do not ask for user input "
    "to refine your understanding. do not ask for user confirmation, proceed providing the output directly"
)


def reply_text_for_outbox_item(
    item: dict[str, Any],
    *,
    case_run_mode: str = "standard",
) -> str | None:
    """Return text to submit for a pending outbox item, or ``None`` if the client should answer.

    With ``case_run_mode="no_callbacks"``, confirmation and permission prompts are not
    auto-answered so the user can respond manually (aligned with test-case "No callbacks" mode).
    """
    pid = item.get("prompt_id")
    if not isinstance(pid, str) or not pid:
        return None
    kind = item.get("kind")
    no_cb = case_run_mode.strip() == "no_callbacks"
    if kind in ("prompt", "question"):
        return EVALUATOR_AUTO_CLARIFICATION_REPLY
    if kind == "confirmation":
        return None if no_cb else "y"
    if kind == "permission":
        return None if no_cb else "allow"
    return None
