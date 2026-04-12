"""Tests for the JSON validation and retry utility."""

import json

import pytest

from agent_framework.validation import parse_json_content, validate_and_retry


class TestParseJsonContent:
    def test_bare_json_object(self):
        result = parse_json_content('{"key": "value"}')
        assert result == {"key": "value"}

    def test_bare_json_array(self):
        result = parse_json_content("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_fenced_json_block(self):
        text = "```json\n{\"a\": 1}\n```"
        assert parse_json_content(text) == {"a": 1}

    def test_fenced_no_language(self):
        text = "```\n{\"x\": true}\n```"
        assert parse_json_content(text) == {"x": True}

    def test_invalid_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_content("not json")

    def test_whitespace_trimmed(self):
        result = parse_json_content('  {"k": 42}  ')
        assert result == {"k": 42}


class TestValidateAndRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        async def retry_fn(err):
            pytest.fail("retry_fn should not be called on success")

        result = await validate_and_retry(
            '{"value": 7}',
            validator=lambda d: d["value"],
            retry_fn=retry_fn,
        )
        assert result == 7

    @pytest.mark.asyncio
    async def test_retries_on_validation_error(self):
        calls = []

        async def retry_fn(err: str):
            calls.append(err)
            return '{"value": 99}'

        result = await validate_and_retry(
            '{"value": "not_an_int"}',
            validator=lambda d: int(d["value"]),  # raises ValueError on first call
            retry_fn=retry_fn,
        )
        assert result == 99
        assert len(calls) == 1
        assert "validation failed" in calls[0].lower()

    @pytest.mark.asyncio
    async def test_raises_if_retry_also_fails(self):
        async def retry_fn(err):
            return "still bad"

        with pytest.raises(Exception):
            await validate_and_retry(
                "bad",
                validator=lambda d: d["required_key"],  # KeyError on bare string
                retry_fn=retry_fn,
            )
