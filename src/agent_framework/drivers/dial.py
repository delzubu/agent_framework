"""Async DIAL chat-completions driver for agent_framework.

DIAL uses the OpenAI-compatible chat completions API format with EPAM-specific
extensions (``custom_fields``, ``custom_content``, attachments).  This driver
handles:

- DIAL endpoint format: ``POST {base_url}/openai/deployments/{deployment}/chat/completions?api-version={version}``
- Auth: ``Api-Key`` header
- Request construction via ``aidial_sdk.chat_completion.request`` typed models
- Multimodal messages (``image_url`` content parts)
- ``response_format`` forwarding (``json_object``, ``json_schema``)
- HTTP 400 retry without ``response_format`` when the deployment doesn't support it (G-06)
- Structured ``ModelDriverError`` with HTTP status and upstream body (G-07)
- ``ProviderRequestTrace`` / ``ProviderResponseTrace`` callbacks — same mechanism as
  ``OpenAiModelDriver`` (G-14)

Install with::

    pip install agent_framework[dial]

This installs ``httpx`` and ``aidial-sdk`` as additional dependencies.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar

from agent_framework.errors import ModelDriverError
from agent_framework.model import (
    DEFAULT_RESPONSE_MODE,
    DriverCapabilities,
    ModelContext,
    ModelDriverBase,
    ModelResponse,
    ProviderRequestTrace,
    ProviderResponseTrace,
    _FallbackMixin,
    normalize_chat_completions_usage,
    parse_json_object_model_output,
    resolved_response_format_dict,
)
from agent_framework.tool import ToolDefinition

_LOGGER = logging.getLogger(__name__)

# aidial-sdk types — installed via agent_framework[dial]
try:
    import httpx
    from aidial_sdk.chat_completion.request import (
        Function,
        ImageURL,
        Message,
        MessageContentImagePart,
        MessageContentTextPart,
        ResponseFormatJsonObject,
        ResponseFormatJsonSchema,
        ResponseFormatJsonSchemaObject,
        Role,
        Tool,
        ToolCall,
        FunctionCall as AidialFunctionCall,
    )
    from aidial_sdk._pydantic import PYDANTIC_V2
    _DIAL_AVAILABLE = True
except ImportError:
    _DIAL_AVAILABLE = False


def _require_dial() -> None:
    if not _DIAL_AVAILABLE:
        raise ImportError(
            "DialChatCompletionsDriver requires the [dial] extra. "
            "Install with: pip install agent_framework[dial]"
        )


def _model_dump(obj: Any, *, exclude_none: bool = True) -> dict[str, Any]:
    """Serialize a pydantic v1/v2 model to a dict."""
    if PYDANTIC_V2:
        return obj.model_dump(exclude_none=exclude_none, mode="json")
    else:
        return obj.dict(exclude_none=exclude_none)


def _build_messages(messages: tuple[dict[str, Any], ...]) -> list[Any]:
    """Convert ``ModelContext.messages`` dicts to ``aidial_sdk.Message`` objects."""
    result = []
    for msg in messages:
        raw_content = msg.get("content")
        raw_tool_calls = msg.get("tool_calls")
        raw_role = msg.get("role", "user")

        # Map role string to aidial Role enum
        role = Role(raw_role)

        # Build tool_calls list
        tool_calls = None
        if raw_tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.get("id", ""),
                    type="function",
                    function=AidialFunctionCall(
                        name=tc.get("function", {}).get("name", ""),
                        arguments=tc.get("function", {}).get("arguments", ""),
                    ),
                )
                for tc in raw_tool_calls
            ]

        # Build content
        if isinstance(raw_content, list):
            # Multimodal content parts
            parts = []
            for part in raw_content:
                if part.get("type") == "image_url":
                    image_data = part.get("image_url", {})
                    parts.append(
                        MessageContentImagePart(
                            type="image_url",
                            image_url=ImageURL(
                                url=image_data.get("url", ""),
                                detail=image_data.get("detail"),
                            ),
                        )
                    )
                else:
                    parts.append(
                        MessageContentTextPart(
                            type="text",
                            text=part.get("text", ""),
                        )
                    )
            content = parts
        else:
            content = raw_content  # str or None

        result.append(
            Message(
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_call_id=msg.get("tool_call_id"),
                name=msg.get("name"),
            )
        )
    return result


def _build_tools(tools: tuple[ToolDefinition, ...]) -> list[Any] | None:
    """Convert ``ToolDefinition`` objects to ``aidial_sdk.Tool`` objects."""
    if not tools:
        return None
    result = []
    for t in tools:
        if t.parameters_schema is not None:
            parameters = dict(t.parameters_schema)
        else:
            parameters = {
                "type": "object",
                "properties": {
                    p.name: {
                        "type": p.value_type,
                        "description": p.description,
                    }
                    for p in t.parameters
                },
                "required": [p.name for p in t.parameters if p.required],
            }
        result.append(
            Tool(
                type="function",
                function=Function(
                    name=t.tool_id,
                    description=t.description,
                    parameters=parameters,
                ),
            )
        )
    return result


def _build_response_format(response_format: dict[str, Any] | None) -> Any | None:
    """Convert a ``response_format`` dict to the appropriate aidial-sdk type."""
    if response_format is None:
        return None
    fmt_type = response_format.get("type")
    if fmt_type == "json_object":
        return ResponseFormatJsonObject(type="json_object")
    if fmt_type == "json_schema":
        schema_obj = response_format.get("json_schema", {})
        return ResponseFormatJsonSchema(
            type="json_schema",
            json_schema=ResponseFormatJsonSchemaObject(
                name=schema_obj.get("name", "response"),
                schema=schema_obj.get("schema", {}),
                description=schema_obj.get("description"),
                strict=schema_obj.get("strict", False),
            ),
        )
    return None


@dataclass(slots=True)
class DialChatCompletionsDriver(ModelDriverBase, _FallbackMixin):
    """Async driver for DIAL (OpenAI-compatible chat completions).

    Uses ``aidial_sdk.chat_completion.request`` types for well-typed request
    construction.  Uses agent_framework's standard ``ProviderRequestTrace`` /
    ``ProviderResponseTrace`` callbacks for tracing — dial-agent should adopt
    this trace mechanism rather than its custom logging hooks.

    Attributes:
        base_url: DIAL API base URL (e.g. ``https://dial.example.com``).
        deployment: Optional default deployment name.  Kept for backward
            compatibility and direct construction in tests.  In normal use the
            active deployment is taken from the ``model_names`` argument passed
            to ``decide()`` on each call.
        api_version: ``api-version`` query parameter (default ``"2024-10-21"``).
        api_key: DIAL API key sent as the ``Api-Key`` header.
        custom_fields: Optional ``custom_fields`` dict merged into the request
            body (DIAL-specific extensions).
        retry_without_response_format: If True (default), re-try once without
            ``response_format`` when DIAL returns HTTP 400.
        timeout: HTTP timeout in seconds (default 120).
        on_request_trace: Optional ``ProviderRequestTrace`` callback.
        on_response_trace: Optional ``ProviderResponseTrace`` callback.
        _fallback_state: Per-model-list fallback index map (managed by
            ``_FallbackMixin``).  Call ``reset_model_fallback()`` to restart
            from the first model.
    """

    capabilities: ClassVar[DriverCapabilities] = DriverCapabilities(
        is_async=True,
        supports_multimodal=True,
        supports_response_format=True,
        supports_tools=True,
    )

    base_url: str
    deployment: str = ""
    api_version: str = "2024-10-21"
    api_key: str = ""
    custom_fields: dict[str, Any] | None = None
    retry_without_response_format: bool = True
    timeout: float = 120.0
    on_request_trace: Any | None = None
    on_response_trace: Any | None = None
    _client: Any | None = field(default=None, repr=False)
    _httpx_loop: Any | None = field(default=None, repr=False)
    _fallback_state: dict[tuple[str, ...], int] = field(default_factory=dict, repr=False)

    def _emit_provider_response_error_trace(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_name: str,
        context: ModelContext,
        exc: ModelDriverError,
    ) -> None:
        """Invoke ``on_response_trace`` for failed HTTP/transport outcomes (mirrors success path)."""
        if not callable(self.on_response_trace):
            return
        raw = exc.upstream_body if exc.upstream_body else str(exc)
        self.on_response_trace(
            ProviderResponseTrace(
                agent_id=agent_id,
                provider_name=provider_name,
                model_name=model_name,
                raw_text=raw[:8000] + ("…" if len(raw) > 8000 else ""),
                parsed_payload={
                    "error": True,
                    "status_code": exc.status_code,
                    "message": str(exc),
                },
                usage=None,
                raw_usage=None,
                run_id=context.run_id,
            )
        )

    async def decide(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_names: tuple[str, ...],
        temperature: float,
        context: ModelContext,
    ) -> ModelResponse:
        """Request a structured response from a DIAL deployment.

        Tries each model in ``model_names`` in order, starting from the last
        known-good index.  The active deployment name for each attempt is taken
        directly from the model list; ``self.deployment`` is not used.
        """
        _require_dial()

        async def _try_model(model: str) -> ModelResponse:
            endpoint = (
                f"/openai/deployments/{model}/chat/completions"
                f"?api-version={self.api_version}"
            )
            body = self._build_request_body(context, temperature, model=model)

            if callable(self.on_request_trace):
                self.on_request_trace(
                    ProviderRequestTrace(
                        agent_id=agent_id,
                        provider_name=provider_name,
                        model_name=model,
                        input_payload=body,
                        temperature=temperature,
                        run_id=context.run_id,
                    )
                )

            try:
                response = await self._post(endpoint, body)
            except ModelDriverError as exc:
                self._emit_provider_response_error_trace(
                    agent_id=agent_id,
                    provider_name=provider_name,
                    model_name=model,
                    context=context,
                    exc=exc,
                )
                raise

            if response is None:
                # HTTP 400 with response_format — retry without it
                body_no_fmt = {k: v for k, v in body.items() if k != "response_format"}
                try:
                    response = await self._post(endpoint, body_no_fmt, allow_retry=False)
                except ModelDriverError as exc:
                    self._emit_provider_response_error_trace(
                        agent_id=agent_id,
                        provider_name=provider_name,
                        model_name=model,
                        context=context,
                        exc=exc,
                    )
                    raise

            assert response is not None
            response_data, raw_response_text = response
            response_raw_usage = response_data.get("usage")
            if not isinstance(response_raw_usage, dict):
                response_raw_usage = None
            response_usage = normalize_chat_completions_usage(response_raw_usage)
            try:
                result = self._parse_response(response_data, context)
            except Exception:
                if callable(self.on_response_trace):
                    self.on_response_trace(
                        ProviderResponseTrace(
                            agent_id=agent_id,
                            provider_name=provider_name,
                            model_name=model,
                            raw_text=raw_response_text,
                            parsed_payload=None,
                            usage=response_usage,
                            raw_usage=response_raw_usage,
                            run_id=context.run_id,
                        )
                    )
                raise

            if callable(self.on_response_trace):
                self.on_response_trace(
                    ProviderResponseTrace(
                        agent_id=agent_id,
                        provider_name=provider_name,
                        model_name=model,
                        raw_text=raw_response_text,
                        parsed_payload=dict(result.payload) if result.payload else None,
                        usage=result.usage,
                        raw_usage=result.raw_usage,
                        run_id=context.run_id,
                    )
                )

            return result

        return await self._fallback_decide_async(model_names, _try_model)

    def set_trace_callbacks(
        self,
        *,
        on_request: Any | None = None,
        on_response: Any | None = None,
    ) -> None:
        """Attach optional trace callbacks for provider I/O logging."""
        self.on_request_trace = on_request
        self.on_response_trace = on_response

    async def aclose(self) -> None:
        """Release the underlying ``httpx.AsyncClient``."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                _LOGGER.debug("DIAL httpx client aclose failed", exc_info=True)
            self._client = None
            self._httpx_loop = None
            _LOGGER.debug("DIAL httpx client closed")

    async def _acquire_client(self) -> Any:
        """Return an ``httpx.AsyncClient`` bound to the *current* running event loop.

        ``AsyncToSyncAdapter`` uses ``asyncio.run()`` per call, which creates and
        then closes a fresh loop each time. A cached client from a previous loop
        must not be reused — it triggers ``RuntimeError: Event loop is closed``
        during connection teardown.
        """
        _require_dial()
        cur = asyncio.get_running_loop()
        if self._client is not None:
            if self._httpx_loop is None:
                self._httpx_loop = cur
            elif self._httpx_loop is not cur or self._httpx_loop.is_closed():
                self._client = None
                self._httpx_loop = None
        if self._client is None:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["Api-Key"] = self.api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
            self._httpx_loop = cur
        return self._client

    def _build_request_body(self, context: ModelContext, temperature: float, *, model: str) -> dict[str, Any]:
        """Assemble the DIAL chat completions request body for the given model."""
        messages = _build_messages(context.messages)
        tools = _build_tools(context.tools)
        response_format = _build_response_format(resolved_response_format_dict(context))

        from aidial_sdk.chat_completion.request import (
            ChatCompletionRequest,
            ChatCompletionRequestCustomFields,
        )

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
        if response_format is not None:
            kwargs["response_format"] = response_format
        if self.custom_fields:
            kwargs["custom_fields"] = ChatCompletionRequestCustomFields(
                configuration=self.custom_fields
            )

        req = ChatCompletionRequest(**kwargs)
        return _model_dump(req)

    async def _post(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        allow_retry: bool = True,
    ) -> tuple[dict[str, Any], str] | None:
        """POST to the DIAL endpoint.

        Returns the parsed response dict, or ``None`` if HTTP 400 was returned
        with ``response_format`` in the body and ``allow_retry`` is True
        (signalling the caller should retry without ``response_format``).

        Raises:
            ModelDriverError: On HTTP error or transport failure.
        """
        client = await self._acquire_client()
        try:
            resp = await client.post(endpoint, json=body)
        except httpx.TransportError as exc:
            _LOGGER.error("DIAL transport error for %s: %s", self.base_url, exc)
            raise ModelDriverError(
                f"Cannot reach DIAL at {self.base_url!r}: {exc}. "
                "Check network connectivity and VPN access to the DIAL endpoint.",
                status_code=502,
                upstream_body=None,
            ) from exc

        if resp.status_code == 400 and "response_format" in body and allow_retry and self.retry_without_response_format:
            _LOGGER.warning(
                "DIAL HTTP 400 with response_format on %s; retrying without response_format",
                endpoint,
            )
            return None

        if resp.status_code >= 400:
            upstream = resp.text[:2000] if resp.text else None
            _LOGGER.warning(
                "DIAL HTTP %s %s: %s",
                resp.status_code,
                endpoint,
                (upstream or "")[:500],
            )
            raise ModelDriverError(
                f"DIAL returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                upstream_body=upstream,
            )

        return resp.json(), resp.text

    def _parse_response(
        self,
        data: dict[str, Any],
        context: ModelContext,
    ) -> ModelResponse:
        """Parse a DIAL chat completions response into ``ModelResponse``."""
        choices = data.get("choices", [])
        if not choices:
            raise ModelDriverError("DIAL response contained no choices", status_code=None)

        choice = choices[0]
        message = choice.get("message", {})
        raw_content: str = message.get("content") or ""
        finish_reason: str | None = choice.get("finish_reason")

        # Tool calls from the response
        raw_tool_calls = message.get("tool_calls")
        tool_calls: tuple[dict[str, Any], ...] | None = None
        if raw_tool_calls:
            tool_calls = tuple(raw_tool_calls)

        # Usage
        raw_usage = data.get("usage")
        if not isinstance(raw_usage, dict):
            raw_usage = None
        usage = normalize_chat_completions_usage(raw_usage)

        # Build payload
        if tool_calls:
            if len(tool_calls) != 1:
                raise ModelDriverError(
                    "DIAL returned multiple native tool calls in one response; "
                    "agent_framework decision mode expects exactly one tool target per turn.",
                    status_code=None,
                    upstream_body=json.dumps(data)[:2000],
                )
            tool_call = tool_calls[0]
            function_payload = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
            tool_name = str(function_payload.get("name", "")).strip()
            if not tool_name:
                raise ModelDriverError(
                    "DIAL returned a native tool call without a function name.",
                    status_code=None,
                    upstream_body=json.dumps(data)[:2000],
                )
            raw_arguments = function_payload.get("arguments", "{}")
            if not isinstance(raw_arguments, str):
                raise ModelDriverError(
                    "DIAL native tool call arguments must be a JSON object string.",
                    status_code=None,
                    upstream_body=json.dumps(data)[:2000],
                )
            try:
                parsed_arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ModelDriverError(
                    f"DIAL native tool call arguments are not valid JSON: {exc}",
                    status_code=None,
                    upstream_body=raw_arguments[:2000],
                ) from exc
            if not isinstance(parsed_arguments, dict):
                raise ModelDriverError(
                    "DIAL native tool call arguments must decode to a JSON object.",
                    status_code=None,
                    upstream_body=raw_arguments[:2000],
                )
            payload = {
                "kind": "call_tool",
                "tool_name": tool_name,
                "parameters": parsed_arguments,
                "message": "",
            }
            raw_text = raw_content
        elif context.response_mode == DEFAULT_RESPONSE_MODE:
            payload, raw_text = parse_json_object_model_output(
                raw_content, provider_label="DIAL"
            )
        else:
            payload = {"kind": "final_message", "message": raw_content}
            raw_text = raw_content

        return ModelResponse(
            payload=payload,
            raw_text=raw_text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            raw_usage=raw_usage,
        )


__all__ = ["DialChatCompletionsDriver"]
