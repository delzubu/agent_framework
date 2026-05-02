"""Step reference resolver for ``{{token}}`` substitution in plan step parameters.

Resolves ``{{name}}`` and ``{{name.path.to.field}}`` tokens in any JSON-shaped
value (dict, list, str, or scalar) before a plan step is dispatched.

Resolution rules
----------------
- Whole-string-is-token: ``"{{step_id}}"`` is replaced with the resolved value
  verbatim, preserving its original JSON type (dict, list, int, bool, etc.).
- String-contains-tokens: ``"prefix {{x}} suffix"`` — tokens are stringified and
  substituted via ``re.sub``; the result is always a string.
- Dot-path: ``{{step_id.field.nested}}`` navigates into the resolved root value
  using the dot-separated key sequence. Segments may be dict keys or numeric
  list indices (e.g. ``{{step_id.items.0}}``). Missing segments resolve to
  ``""`` and emit a WARNING.
- Numeric list indexing: a digit-only segment (e.g. ``.0``) indexes into a list.
  Out-of-range indices resolve to ``""`` and emit a WARNING.
- Subagent results: ``call_subagent`` step results are stored as a dict with
  ``message``, ``response``, and ``status`` keys. Use ``{{step_id.response.field}}``
  to drill into the structured response.
- Lookup precedence: ``step_results`` is checked first, then
  ``invocation_parameters``. Step results take priority over invocation params
  if names collide.
- Lenient: missing references resolve to ``""`` and emit a WARNING log (never
  raise). The step still executes.
- Leftover tokens: strings that still contain ``{{`` after substitution emit a
  WARNING (e.g. a token whose path segment is syntactically invalid).

Examples
--------
Given::

    step_results = {"fetch": {"content": "hello", "items": ["a", "b"]}}
    invocation_parameters = {"player_id": "42"}

``"{{fetch.content}}"``   → ``"hello"``
``"{{fetch}}"``           → ``{"content": "hello", "items": ["a", "b"]}``  (whole-string, type preserved)
``"{{fetch.items.0}}"``   → ``"a"``  (numeric list index)
``"id={{player_id}}"``    → ``"id=42"``  (embedded token, stringified)
``"{{missing}}"``         → ``""``  (WARNING emitted)
``"{{fetch.items.bad}}"`` → ``""``  (WARNING: non-numeric segment on list)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

_LOGGER = logging.getLogger("agent_framework.planning.step_reference")

_TOKEN_RE = re.compile(
    r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.(?:[a-zA-Z_][a-zA-Z0-9_]*|[0-9]+))*)\s*\}\}"
)
# Matches anything that looks like an unresolved {{…}} leftover after substitution.
_LEFTOVER_RE = re.compile(r"\{\{[^}]*\}\}")


class StepReferenceResolver(Protocol):
    """Protocol for pluggable step reference resolvers.

    Mirrors ``FileReferenceResolver`` on ``AgentHost``. Set
    ``host.step_ref_resolver`` to override the default resolver.
    """

    def resolve(
        self,
        value: Any,
        *,
        invocation_parameters: dict[str, Any],
        step_results: dict[str, Any],
        run_id: str,
        agent_id: str,
        step_id: str,
    ) -> Any: ...


def _lookup_token(
    token: str,
    *,
    invocation_parameters: dict[str, Any],
    step_results: dict[str, Any],
    run_id: str,
    agent_id: str,
    step_id: str,
    path_hint: str,
) -> Any:
    """Resolve a single token string to its value.

    Returns the resolved value (any JSON type) or ``""`` on miss.
    """
    parts = token.split(".")
    root_key = parts[0]

    # Lookup precedence: step_results first, then invocation_parameters.
    if root_key in step_results:
        root_value = step_results[root_key]
    elif root_key in invocation_parameters:
        root_value = invocation_parameters[root_key]
    else:
        _LOGGER.warning(
            "step_reference: token {{%s}} not found — resolving to empty string "
            "(run_id=%s, agent_id=%s, step_id=%s, path=%s)",
            token, run_id, agent_id, step_id, path_hint,
        )
        return ""

    # Dot-path traversal for remaining segments.
    value = root_value
    for segment in parts[1:]:
        if isinstance(value, list) and segment.isdigit():
            idx = int(segment)
            if idx >= len(value):
                _LOGGER.warning(
                    "step_reference: token {{%s}} — list index %d out of range "
                    "(length=%d) — resolving to empty string "
                    "(run_id=%s, agent_id=%s, step_id=%s, path=%s)",
                    token, idx, len(value),
                    run_id, agent_id, step_id, path_hint,
                )
                return ""
            value = value[idx]
        elif isinstance(value, dict):
            if segment not in value:
                _LOGGER.warning(
                    "step_reference: token {{%s}} — key %r not found in dict at this "
                    "traversal level — resolving to empty string "
                    "(run_id=%s, agent_id=%s, step_id=%s, path=%s)",
                    token, segment, run_id, agent_id, step_id, path_hint,
                )
                return ""
            value = value[segment]
        else:
            _LOGGER.warning(
                "step_reference: token {{%s}} — cannot traverse segment %r into "
                "%s — resolving to empty string "
                "(run_id=%s, agent_id=%s, step_id=%s, path=%s)",
                token, segment, type(value).__name__,
                run_id, agent_id, step_id, path_hint,
            )
            return ""

    _LOGGER.debug(
        "step_reference: {{%s}} → %s (run_id=%s, step_id=%s)",
        token, type(value).__name__, run_id, step_id,
    )
    return value


def _resolve_string(
    s: str,
    *,
    invocation_parameters: dict[str, Any],
    step_results: dict[str, Any],
    run_id: str,
    agent_id: str,
    step_id: str,
    path_hint: str,
) -> Any:
    """Resolve tokens in a single string value.

    If the entire string is a single token, the resolved value is returned with
    its original type preserved. Otherwise tokens are stringified in-place.
    """
    # Whole-string-is-token check.
    match = _TOKEN_RE.fullmatch(s.strip())
    if match:
        token = match.group(1)
        return _lookup_token(
            token,
            invocation_parameters=invocation_parameters,
            step_results=step_results,
            run_id=run_id, agent_id=agent_id, step_id=step_id, path_hint=path_hint,
        )

    # Embedded tokens — substitute as strings.
    def _sub(m: re.Match) -> str:
        token = m.group(1)
        resolved = _lookup_token(
            token,
            invocation_parameters=invocation_parameters,
            step_results=step_results,
            run_id=run_id, agent_id=agent_id, step_id=step_id, path_hint=path_hint,
        )
        return str(resolved)

    result = _TOKEN_RE.sub(_sub, s)

    # Warn if the string still contains {{ }} after substitution — this indicates
    # a token whose path syntax was invalid (e.g. a digit-only root, unsupported
    # bracket notation) that the regex didn't match and therefore passed through silently.
    if isinstance(result, str) and _LEFTOVER_RE.search(result):
        _LOGGER.warning(
            "step_reference: string at path %r still contains unresolved {{…}} after "
            "substitution — check token syntax (run_id=%s, agent_id=%s, step_id=%s): %r",
            path_hint, run_id, agent_id, step_id, result,
        )

    return result


def resolve(
    value: Any,
    *,
    invocation_parameters: dict[str, Any],
    step_results: dict[str, Any],
    run_id: str,
    agent_id: str,
    step_id: str,
    _path: str = "",
) -> Any:
    """Recursively substitute ``{{token}}`` references in a JSON-shaped value.

    Args:
        value: The value to resolve. May be a dict, list, str, or scalar.
        invocation_parameters: Top-level invocation parameter values.
        step_results: Results of already-completed plan steps.
        run_id: Current run ID (for logging).
        agent_id: Current agent ID (for logging).
        step_id: Current step ID being prepared (for logging).

    Returns:
        The resolved value with the same shape as the input, with tokens
        substituted. Missing tokens resolve to ``""``.
    """
    if isinstance(value, dict):
        return {
            k: resolve(
                v,
                invocation_parameters=invocation_parameters,
                step_results=step_results,
                run_id=run_id, agent_id=agent_id, step_id=step_id,
                _path=f"{_path}.{k}" if _path else k,
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            resolve(
                item,
                invocation_parameters=invocation_parameters,
                step_results=step_results,
                run_id=run_id, agent_id=agent_id, step_id=step_id,
                _path=f"{_path}[{i}]",
            )
            for i, item in enumerate(value)
        ]
    if isinstance(value, str):
        return _resolve_string(
            value,
            invocation_parameters=invocation_parameters,
            step_results=step_results,
            run_id=run_id, agent_id=agent_id, step_id=step_id,
            path_hint=_path,
        )
    # Scalars (int, float, bool, None) pass through unchanged.
    return value


__all__ = ["StepReferenceResolver", "resolve"]
