"""Tests for OpenAiModelDriver — temperature retry and caching."""

from unittest.mock import MagicMock, call, patch

import pytest

from agent_framework.drivers.openai import OpenAiModelDriver, _is_temperature_unsupported
from agent_framework.model import ModelContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context() -> ModelContext:
    return ModelContext(
        system_prompt="sys",
        user_prompt="hello",
        messages=({"role": "user", "content": "hello"},),
    )


def _make_response(text: str = '{"kind": "final_message", "message": "ok"}') -> MagicMock:
    r = MagicMock()
    r.output_text = text
    r.usage = None
    r.model_dump_json.return_value = text
    return r


def _make_driver() -> OpenAiModelDriver:
    return OpenAiModelDriver(api_key="sk-test")


# ---------------------------------------------------------------------------
# _is_temperature_unsupported
# ---------------------------------------------------------------------------


def test_is_temperature_unsupported_matches_unsupported():
    exc = ValueError("temperature is not supported for this model")
    assert _is_temperature_unsupported(exc) is True


def test_is_temperature_unsupported_matches_not_supported():
    exc = Exception("temperature: not supported")
    assert _is_temperature_unsupported(exc) is True


def test_is_temperature_unsupported_ignores_unrelated():
    exc = ValueError("invalid model name")
    assert _is_temperature_unsupported(exc) is False


def test_is_temperature_unsupported_requires_both_keywords():
    exc = ValueError("temperature is great")
    assert _is_temperature_unsupported(exc) is False


# ---------------------------------------------------------------------------
# Temperature retry
# ---------------------------------------------------------------------------


def test_temperature_excluded_on_retry_and_model_cached():
    """When provider rejects temperature, driver retries without it and caches the model."""
    driver = _make_driver()
    response = _make_response()

    temp_error = ValueError("temperature is not supported for this model")

    client_mock = MagicMock()
    client_mock.responses.create.side_effect = [temp_error, response]

    with patch.object(driver, "_get_client", return_value=client_mock):
        result = driver.decide(
            agent_id="a",
            provider_name="openai",
            model_names=("gpt-5-mini",),
            temperature=1.0,
            context=_make_context(),
        )

    assert result.payload == {"kind": "final_message", "message": "ok"}
    first_call, second_call = client_mock.responses.create.call_args_list
    assert "temperature" in first_call.kwargs
    assert "temperature" not in second_call.kwargs
    assert "gpt-5-mini" in driver._no_temperature_models


def test_temperature_skipped_for_cached_model():
    """Second call to a cached no-temperature model omits temperature immediately."""
    driver = _make_driver()
    driver._no_temperature_models.add("gpt-5-mini")
    response = _make_response()

    client_mock = MagicMock()
    client_mock.responses.create.return_value = response

    with patch.object(driver, "_get_client", return_value=client_mock):
        driver.decide(
            agent_id="a",
            provider_name="openai",
            model_names=("gpt-5-mini",),
            temperature=1.0,
            context=_make_context(),
        )

    assert client_mock.responses.create.call_count == 1
    assert "temperature" not in client_mock.responses.create.call_args.kwargs


def test_non_temperature_error_propagates():
    """Errors unrelated to temperature are re-raised without retry."""
    driver = _make_driver()

    client_mock = MagicMock()
    client_mock.responses.create.side_effect = ValueError("quota exceeded")

    with patch.object(driver, "_get_client", return_value=client_mock):
        with pytest.raises(ValueError, match="quota exceeded"):
            driver.decide(
                agent_id="a",
                provider_name="openai",
                model_names=("gpt-4o",),
                temperature=1.0,
                context=_make_context(),
            )

    assert client_mock.responses.create.call_count == 1
    assert "gpt-4o" not in driver._no_temperature_models


def test_temperature_included_for_normal_model():
    """Standard models receive temperature in create kwargs."""
    driver = _make_driver()
    response = _make_response()

    client_mock = MagicMock()
    client_mock.responses.create.return_value = response

    with patch.object(driver, "_get_client", return_value=client_mock):
        driver.decide(
            agent_id="a",
            provider_name="openai",
            model_names=("gpt-4o",),
            temperature=0.7,
            context=_make_context(),
        )

    kwargs = client_mock.responses.create.call_args.kwargs
    assert kwargs["temperature"] == 0.7
