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

    With ``case_run_mode="no_callbacks"``, all prompts, questions, confirmations, and
    permissions are auto-answered so the run completes without user interaction.
    With ``case_run_mode="standard"``, every outbox item is forwarded to the client
    unanswered, allowing the user to respond manually.
    """
    pid = item.get("prompt_id")
    if not isinstance(pid, str) or not pid:
        return None
    kind = item.get("kind")
    no_cb = case_run_mode.strip() == "no_callbacks"
    if kind in ("prompt", "question"):
        return EVALUATOR_AUTO_CLARIFICATION_REPLY if no_cb else None
    if kind == "confirmation":
        return "y" if no_cb else None
    if kind == "permission":
        return "allow" if no_cb else None
    return None
