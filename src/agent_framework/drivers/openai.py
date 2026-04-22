"""OpenAI Responses API driver implementation."""

from __future__ import annotations

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
            raw_text = response.output_text.strip()
            parsed_payload: dict[str, object] | None = None
            normalized_text = raw_text
            if context.response_mode == "text":
                parsed_payload = {"kind": "final_message", "message": raw_text}
            else:
                parsed_payload, normalized_text = parse_json_object_model_output(
                    raw_text,
                    provider_label="OpenAI",
                )
            if callable(self.on_response_trace):
                self.on_response_trace(
                    ProviderResponseTrace(
                        agent_id=agent_id,
                        provider_name=provider_name,
                        model_name=model_name,
                        raw_text=raw_text,
                        parsed_payload=parsed_payload,
                        run_id=context.run_id,
                    )
                )
            return ModelResponse(payload=parsed_payload, raw_text=normalized_text)

        return self._fallback_decide(model_names, _try_model)


__all__ = ["OpenAiModelDriver"]
