"""Typed chat message model aligned with the OpenAI chat completions spec.

These types provide type-safe construction of chat messages, including
multimodal content (text + image_url), tool calls, and tool results. They
serialize to and from plain dicts so they are compatible with
``ModelContext.messages``.

Usage::

    from agent_framework.messages import ChatMessage, ContentPart, ImageUrl

    msg = ChatMessage(
        role="user",
        content=(
            ContentPart(type="text", text="Describe this image:"),
            ContentPart(type="image_url", image_url=ImageUrl(url="data:image/png;base64,...")),
        ),
    )
    # Pass to ModelContext.messages as a plain dict:
    context = ModelContext(..., messages=(msg.to_dict(),))
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ImageUrl:
    """Image URL content with optional detail level.

    Attributes:
        url: Either a public https URL or a data URI (``data:image/png;base64,...``).
        detail: Resolution hint — ``"auto"``, ``"low"``, or ``"high"``.
    """

    url: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"url": self.url}
        if self.detail is not None:
            d["detail"] = self.detail
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageUrl:
        return cls(url=data["url"], detail=data.get("detail"))


@dataclass(frozen=True, slots=True)
class ContentPart:
    """A single part of a multimodal message content array.

    Attributes:
        type: Content type — ``"text"`` or ``"image_url"``.
        text: Text content (when ``type == "text"``).
        image_url: Image URL descriptor (when ``type == "image_url"``).
    """

    type: str
    text: str | None = None
    image_url: ImageUrl | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.type == "text" and self.text is not None:
            d["text"] = self.text
        elif self.type == "image_url" and self.image_url is not None:
            d["image_url"] = self.image_url.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentPart:
        image_url_data = data.get("image_url")
        return cls(
            type=data["type"],
            text=data.get("text"),
            image_url=ImageUrl.from_dict(image_url_data) if image_url_data else None,
        )


@dataclass(frozen=True, slots=True)
class FunctionCall:
    """Function call arguments from a model tool call.

    Attributes:
        name: Name of the function to call.
        arguments: JSON-encoded arguments string.
    """

    name: str
    arguments: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionCall:
        return cls(name=data["name"], arguments=data.get("arguments", ""))


@dataclass(frozen=True, slots=True)
class ToolCallMessage:
    """A tool call emitted by the model in an assistant message.

    Attributes:
        id: Unique tool call identifier assigned by the provider.
        type: Always ``"function"`` for current providers.
        function: Function name and arguments.
    """

    id: str
    function: FunctionCall
    type: str = "function"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "function": self.function.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCallMessage:
        return cls(
            id=data["id"],
            type=data.get("type", "function"),
            function=FunctionCall.from_dict(data["function"]),
        )


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A single chat message in the OpenAI chat completions format.

    Supports text-only, multimodal, assistant tool calls, and tool result
    messages. Serializes to / deserializes from plain dicts for use with
    ``ModelContext.messages``.

    Attributes:
        role: Message role — ``"system"``, ``"user"``, ``"assistant"``, or
            ``"tool"``.
        content: String content, a tuple of ``ContentPart`` objects for
            multimodal messages, or ``None`` for assistant messages that only
            contain tool calls.
        name: Optional name for the participant (some providers use this).
        tool_call_id: Tool call id for ``role="tool"`` result messages.
        tool_calls: Tool calls emitted by an assistant message.
    """

    role: str
    content: str | tuple[ContentPart, ...] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCallMessage, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role}
        if self.content is None:
            pass  # omit — valid for assistant messages with only tool_calls
        elif isinstance(self.content, str):
            d["content"] = self.content
        else:
            d["content"] = [part.to_dict() for part in self.content]
        if self.name is not None:
            d["name"] = self.name
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatMessage:
        raw_content = data.get("content")
        if isinstance(raw_content, list):
            content: str | tuple[ContentPart, ...] | None = tuple(
                ContentPart.from_dict(p) for p in raw_content
            )
        else:
            content = raw_content  # str or None

        raw_tool_calls = data.get("tool_calls")
        tool_calls = (
            tuple(ToolCallMessage.from_dict(tc) for tc in raw_tool_calls)
            if raw_tool_calls
            else None
        )

        return cls(
            role=data["role"],
            content=content,
            name=data.get("name"),
            tool_call_id=data.get("tool_call_id"),
            tool_calls=tool_calls,
        )


__all__ = [
    "ChatMessage",
    "ContentPart",
    "FunctionCall",
    "ImageUrl",
    "ToolCallMessage",
]
