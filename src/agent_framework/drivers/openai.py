"""OpenAI Responses API driver implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from openai import OpenAI

from agent_framework.model import (
    DriverCapabilities,
    ModelContext,
    ModelDriverBase,
    ModelResponse,
    ProviderRequestTrace,
    ProviderResponseTrace,
    _FallbackMixin,
    normalize_openai_usage,
    openai_responses_text_format_field,
    parse_json_object_model_output,
    resolved_response_format_dict,
)


@dataclass(slots=True)
class OpenAiModelDriver(ModelDriverBase, _FallbackMixin):
    """OpenAI-backed model driver for the Responses API."""

    capabilities: ClassVar[DriverCapabilities] = DriverCapabilities(
        is_async=False,
        supports_multimodal=False,
        supports_response_format=True,
        supports_tools=False,
    )

    api_key: str
    on_request_trace: Any | None = None
    on_response_trace: Any | None = None
    _fallback_state: dict[tuple[str, ...], int] = field(default_factory=dict, repr=False)
    _client: Any = field(default=None, repr=False)

    def set_trace_callbacks(
        self,
        *,
        on_request: Any | None = None,
        on_response: Any | None = None,
    ) -> None:
        """Attach optional trace callbacks for exact provider I/O logging."""
        self.on_request_trace = on_request
        self.on_response_trace = on_response

    def _get_client(self) -> OpenAI:
        """Return a cached OpenAI client, constructing it lazily on first use."""
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    @staticmethod
    def _raw_response_text(response: Any) -> str:
        """Return the raw provider response body when the SDK exposes it."""
        if hasattr(response, "model_dump_json"):
            try:
                return response.model_dump_json(indent=2)
            except TypeError:
                return response.model_dump_json()
        if hasattr(response, "model_dump"):
            return json.dumps(response.model_dump(), indent=2, ensure_ascii=False)
        if hasattr(response, "to_dict"):
            return json.dumps(response.to_dict(), indent=2, ensure_ascii=False)
        return getattr(response, "output_text", "") or str(response)

    @staticmethod
    def _usage_payload(value: Any) -> dict[str, Any] | None:
        """Return a plain JSON-serializable usage dict from an SDK usage object."""
        if value is None:
            return None
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "model_dump"):
            dumped = value.model_dump()
            return dumped if isinstance(dumped, dict) else None
        if hasattr(value, "to_dict"):
            dumped = value.to_dict()
            return dumped if isinstance(dumped, dict) else None
        payload: dict[str, Any] = {}
        for name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_tokens_details",
            "output_tokens_details",
        ):
            field_value = getattr(value, name, None)
            if field_value is None:
                continue
            if isinstance(field_value, dict):
                payload[name] = dict(field_value)
            elif hasattr(field_value, "model_dump"):
                dumped = field_value.model_dump()
                payload[name] = dumped if isinstance(dumped, dict) else field_value
            elif hasattr(field_value, "to_dict"):
                dumped = field_value.to_dict()
                payload[name] = dumped if isinstance(dumped, dict) else field_value
            else:
                payload[name] = field_value
        return payload or None

    def decide(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_names: tuple[str, ...],
        temperature: float,
        context: ModelContext,
    ) -> ModelResponse:
        """Request a structured decision from the OpenAI Responses API."""
        if provider_name != "openai":
            raise ValueError(f"Unsupported provider: {provider_name}")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI-backed agents.")

        if context.exact_input_payload is not None:
            model_input = context.exact_input_payload
        elif context.messages:
            model_input = list(context.messages)
        else:
            model_input = [
                {"role": "system", "content": context.system_prompt},
                {"role": "user", "content": context.user_prompt},
            ]

        def _try_model(model_name: str) -> ModelResponse:
            client = self._get_client()
            fmt_dict = resolved_response_format_dict(context)
            request_trace_payload: Any = model_input
            if fmt_dict is not None:
                request_trace_payload = {
                    "input": model_input,
                    "text": {"format": openai_responses_text_format_field(fmt_dict)},
                }
            if callable(self.on_request_trace):
                self.on_request_trace(
                    ProviderRequestTrace(
                        agent_id=agent_id,
                        provider_name=provider_name,
                        model_name=model_name,
                        input_payload=request_trace_payload,
                        temperature=temperature,
                        run_id=context.run_id,
                    )
                )
            create_kwargs: dict[str, Any] = {
                "model": model_name,
                "temperature": temperature,
                "input": model_input,
            }
            if fmt_dict is not None:
                create_kwargs["text"] = {
                    "format": openai_responses_text_format_field(fmt_dict),
                }
            response = client.responses.create(**create_kwargs)
            raw_response_text = self._raw_response_text(response)
            raw_text = response.output_text.strip()
            raw_usage = self._usage_payload(getattr(response, "usage", None))
            usage = normalize_openai_usage(getattr(response, "usage", None))
            parsed_payload: dict[str, object] | None = None
            normalized_text = raw_text
            if context.response_mode == "text":
                parsed_payload = {"kind": "final_message", "message": raw_text}
            else:
                try:
                    parsed_payload, normalized_text = parse_json_object_model_output(
                        raw_text,
                        provider_label="OpenAI",
                    )
                except Exception:
                    if callable(self.on_response_trace):
                        self.on_response_trace(
                            ProviderResponseTrace(
                                agent_id=agent_id,
                                provider_name=provider_name,
                                model_name=model_name,
                                raw_text=raw_response_text,
                                parsed_payload=None,
                                usage=usage,
                                raw_usage=raw_usage,
                                run_id=context.run_id,
                            )
                        )
                    raise
            if callable(self.on_response_trace):
                self.on_response_trace(
                    ProviderResponseTrace(
                        agent_id=agent_id,
                        provider_name=provider_name,
                        model_name=model_name,
                        raw_text=raw_response_text,
                        parsed_payload=parsed_payload,
                        usage=usage,
                        raw_usage=raw_usage,
                        run_id=context.run_id,
                    )
                )
            return ModelResponse(
                payload=parsed_payload,
                raw_text=normalized_text,
                usage=usage,
                raw_usage=raw_usage,
            )

        return self._fallback_decide(model_names, _try_model)


__all__ = ["OpenAiModelDriver"]
