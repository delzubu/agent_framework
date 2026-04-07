"""Tests for the typed chat message model."""

import pytest

from agent_framework.messages import (
    ChatMessage,
    ContentPart,
    FunctionCall,
    ImageUrl,
    ToolCallMessage,
)


class TestImageUrl:
    def test_round_trip_minimal(self):
        img = ImageUrl(url="https://example.com/img.png")
        assert ImageUrl.from_dict(img.to_dict()) == img

    def test_round_trip_with_detail(self):
        img = ImageUrl(url="data:image/png;base64,abc", detail="high")
        assert ImageUrl.from_dict(img.to_dict()) == img

    def test_to_dict_omits_none_detail(self):
        d = ImageUrl(url="https://x.com/img.png").to_dict()
        assert "detail" not in d


class TestContentPart:
    def test_text_part_round_trip(self):
        part = ContentPart(type="text", text="Hello")
        assert ContentPart.from_dict(part.to_dict()) == part

    def test_image_url_part_round_trip(self):
        part = ContentPart(
            type="image_url",
            image_url=ImageUrl(url="data:image/png;base64,abc", detail="low"),
        )
        assert ContentPart.from_dict(part.to_dict()) == part

    def test_text_dict_structure(self):
        d = ContentPart(type="text", text="Hi").to_dict()
        assert d == {"type": "text", "text": "Hi"}

    def test_image_url_dict_structure(self):
        d = ContentPart(type="image_url", image_url=ImageUrl(url="https://x.com/i.png")).to_dict()
        assert d == {"type": "image_url", "image_url": {"url": "https://x.com/i.png"}}


class TestChatMessage:
    def test_simple_text_message(self):
        msg = ChatMessage(role="user", content="Hello")
        d = msg.to_dict()
        assert d == {"role": "user", "content": "Hello"}
        assert ChatMessage.from_dict(d) == msg

    def test_system_message(self):
        msg = ChatMessage(role="system", content="You are a helpful assistant.")
        assert ChatMessage.from_dict(msg.to_dict()) == msg

    def test_multimodal_message_round_trip(self):
        msg = ChatMessage(
            role="user",
            content=(
                ContentPart(type="text", text="Describe:"),
                ContentPart(type="image_url", image_url=ImageUrl(url="data:image/png;base64,xyz")),
            ),
        )
        d = msg.to_dict()
        assert isinstance(d["content"], list)
        assert len(d["content"]) == 2
        recovered = ChatMessage.from_dict(d)
        assert recovered.role == "user"
        assert len(recovered.content) == 2
        assert recovered.content[1].image_url.url == "data:image/png;base64,xyz"

    def test_tool_call_message_round_trip(self):
        msg = ChatMessage(
            role="assistant",
            content=None,
            tool_calls=(
                ToolCallMessage(
                    id="call_123",
                    function=FunctionCall(name="search", arguments='{"q":"test"}'),
                ),
            ),
        )
        d = msg.to_dict()
        assert d["tool_calls"][0]["id"] == "call_123"
        recovered = ChatMessage.from_dict(d)
        assert recovered.tool_calls[0].function.name == "search"

    def test_tool_result_message(self):
        msg = ChatMessage(role="tool", content="result text", tool_call_id="call_123")
        d = msg.to_dict()
        assert d["tool_call_id"] == "call_123"
        assert ChatMessage.from_dict(d) == msg

    def test_content_none_omitted_from_dict(self):
        """None content should not appear as a key in the dict."""
        msg = ChatMessage(
            role="assistant",
            content=None,
            tool_calls=(ToolCallMessage(id="c1", function=FunctionCall(name="f", arguments="{}")),),
        )
        d = msg.to_dict()
        assert "content" not in d
