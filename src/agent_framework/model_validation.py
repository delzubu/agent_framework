"""Runtime-extensible model output validation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from agent_framework.errors import ModelDriverError
from agent_framework.model import ModelContext, ModelResponse


@dataclass(frozen=True, slots=True)
class ModelValidationContext:
    """Minimal context exposed to model validation strategies."""

    agent_id: str
    provider_name: str
    model_names: tuple[str, ...]
    response_mode: str
    run_id: str

    @classmethod
    def from_model_context(
        cls,
        *,
        agent_id: str,
        provider_name: str,
        model_names: tuple[str, ...],
        context: ModelContext,
    ) -> "ModelValidationContext":
        return cls(
            agent_id=agent_id,
            provider_name=provider_name,
            model_names=model_names,
            response_mode=context.response_mode,
            run_id=context.run_id,
        )


class ModelExceptionValidator(Protocol):
    """Strategy that may replace an exception with a more specific one."""

    def validate_exception(
        self,
        exc: BaseException,
        *,
        context: ModelValidationContext,
    ) -> BaseException | None:
        """Return a replacement exception, or ``None`` to leave it unchanged."""


class ModelResponseValidator(Protocol):
    """Strategy that may reject a parsed model response."""

    def validate_response(
        self,
        response: ModelResponse,
        *,
        context: ModelValidationContext,
    ) -> None:
        """Raise when the parsed response violates a runtime rule."""


_EXTRA_DATA_PATTERN = re.compile(
    r"Extra data: line (?P<line>\d+) column (?P<column>\d+) \(char (?P<char>\d+)\)"
)


class MultipleStructuredJsonDocumentsValidator:
    """Explain the common case where the model concatenates multiple JSON objects."""

    def validate_exception(
        self,
        exc: BaseException,
        *,
        context: ModelValidationContext,
    ) -> BaseException | None:
        if not isinstance(exc, ModelDriverError):
            return None
        message = str(exc)
        if "structured response is not valid JSON" not in message or "Extra data:" not in message:
            return None
        match = _EXTRA_DATA_PATTERN.search(message)
        where = ""
        if match is not None:
            where = (
                f" Parser stopped at line {match.group('line')}, "
                f"column {match.group('column')}."
            )
        provider_label = context.provider_name or "model"
        return ModelDriverError(
            (
                f"{provider_label} returned more than one JSON value in a single structured response."
                f"{where} agent_framework expects exactly one top-level JSON object per turn. "
                "This usually means the model emitted multiple decisions in one reply, such as "
                "two JSON objects back to back."
            ),
            status_code=exc.status_code,
            upstream_body=exc.upstream_body,
        )


@dataclass(slots=True)
class ModelValidationChain:
    """Ordered runtime validators for model responses and model-call failures."""

    exception_validators: list[ModelExceptionValidator] = field(default_factory=list)
    response_validators: list[ModelResponseValidator] = field(default_factory=list)

    @classmethod
    def with_defaults(cls) -> "ModelValidationChain":
        chain = cls()
        chain.register_exception_validator(MultipleStructuredJsonDocumentsValidator())
        return chain

    def register_exception_validator(self, validator: ModelExceptionValidator) -> None:
        self.exception_validators.append(validator)

    def register_response_validator(self, validator: ModelResponseValidator) -> None:
        self.response_validators.append(validator)

    def validate_exception(
        self,
        exc: BaseException,
        *,
        context: ModelValidationContext,
    ) -> BaseException:
        current = exc
        for validator in self.exception_validators:
            replacement = validator.validate_exception(current, context=context)
            if replacement is not None:
                current = replacement
        return current

    def validate_response(
        self,
        response: ModelResponse,
        *,
        context: ModelValidationContext,
    ) -> None:
        for validator in self.response_validators:
            validator.validate_response(response, context=context)


__all__ = [
    "ModelExceptionValidator",
    "ModelResponseValidator",
    "ModelValidationChain",
    "ModelValidationContext",
    "MultipleStructuredJsonDocumentsValidator",
]
