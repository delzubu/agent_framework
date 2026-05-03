"""Token detection and code generation for {{token}} references in plan parameters."""
from __future__ import annotations

import re
from typing import Any

# Same regex as agent_framework.planning.step_reference
_TOKEN_RE = re.compile(
    r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.(?:[a-zA-Z_][a-zA-Z0-9_]*|[0-9]+))*)\s*\}\}"
)


def _contains_token(value: Any) -> bool:
    """Return True if *value* (or any nested value) contains a {{token}}."""
    if isinstance(value, str):
        return bool(_TOKEN_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_token(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_token(item) for item in value)
    return False


def _value_to_python_expr(value: Any, indent: int = 0) -> str:
    """Convert a parameter value to a Python expression string suitable for codegen.

    Values containing ``{{token}}`` references become lambda expressions over
    ``ProgrammaticWorkflowState`` (named ``s``).  Pure literals become plain
    Python repr strings.

    Args:
        value: The raw parameter value (may contain token strings).
        indent: Indentation level for multi-line expressions (unused currently).

    Returns:
        A Python source string — either a literal (``repr(value)`` variant) or
        a ``lambda s: ...`` expression.
    """
    if not _contains_token(value):
        return _literal_python(value)

    body = _value_to_lambda_body(value)
    return f"lambda s: {body}"


def _literal_python(value: Any) -> str:
    """Emit a Python literal for a plain (token-free) value."""
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if value is None:
        return "None"
    if isinstance(value, list):
        items = ", ".join(_literal_python(v) for v in value)
        return f"[{items}]"
    if isinstance(value, dict):
        pairs = ", ".join(f"{repr(k)}: {_literal_python(v)}" for k, v in value.items())
        return "{" + pairs + "}"
    return repr(value)


def _value_to_lambda_body(value: Any) -> str:
    """Recursively build the body of a lambda expression for a token-containing value."""
    if isinstance(value, str):
        return _string_to_lambda_body(value)
    if isinstance(value, dict):
        pairs = ", ".join(
            f"{repr(k)}: {_value_to_lambda_body(v) if _contains_token(v) else _literal_python(v)}"
            for k, v in value.items()
        )
        return "{" + pairs + "}"
    if isinstance(value, list):
        items = ", ".join(
            _value_to_lambda_body(item) if _contains_token(item) else _literal_python(item)
            for item in value
        )
        return f"[{items}]"
    return _literal_python(value)


def _string_to_lambda_body(s: str) -> str:
    """Convert a string (which may contain tokens) to a lambda body expression."""
    whole_match = _TOKEN_RE.fullmatch(s.strip())
    if whole_match:
        # Whole-string token — type-preserving ref
        return _token_to_ref_expr(whole_match.group(1))

    # Embedded tokens — f-string construction
    # Replace each {{token}} with {<ref_expr>} inside an f-string
    def _sub(m: re.Match) -> str:
        return "{" + _token_to_ref_expr(m.group(1)) + "}"

    fstring_body = _TOKEN_RE.sub(_sub, s)
    # Escape any pre-existing braces that are not our placeholders
    # (We already replaced all {{token}} so remaining literal braces need doubling)
    # Since we replaced {{ }} already, remaining { } are literal — but they've already
    # been replaced to {expr}, so we must not double-escape them.
    # The safest approach: build the f-string with the substituted body.
    return f'f"{fstring_body}"'


def _token_to_ref_expr(token: str) -> str:
    """Convert 'step_id.field.0' to a _ref(s, ...) call expression."""
    parts = token.split(".")
    args = ", ".join(repr(p) for p in parts)
    return f"_ref(s, {args})"


def find_invocation_param(value: Any, invocation_parameters: dict[str, Any]) -> str | None:
    """Return the first parameter name whose value equals *value*, or None.

    Matches scalars (str, int, float, bool) and collections by equality.
    None is never matched.
    """
    if value is None:
        return None
    for name, param_value in invocation_parameters.items():
        if param_value == value:
            return name
    return None


def find_value_paths(
    needle: Any,
    obj: Any,
    *,
    step_id: str = "",
    _path: tuple[str, ...] = (),
) -> list[str]:
    """Return all dot-paths (prefixed with *step_id*) where *needle* appears in *obj*.

    Only matches scalars (str, int, float, bool). Skips None.
    """
    if needle is None:
        return []
    results: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            results.extend(
                find_value_paths(needle, v, step_id=step_id, _path=_path + (str(k),))
            )
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            results.extend(
                find_value_paths(needle, v, step_id=step_id, _path=_path + (str(i),))
            )
    else:
        if obj == needle and obj is not None:
            full = ".".join((step_id,) + _path) if step_id else ".".join(_path)
            results.append(full)
    return results


def infer_param_ref(
    param_value: Any,
    step_results: dict[str, Any],
) -> tuple[str | None, list[str]]:
    """Try to find *param_value* in *step_results*.

    Returns (best_path, all_paths) where:
    - best_path is the first path found (dot-separated, step_id-prefixed), or None
    - all_paths is every path across all step results where the value appears
    """
    all_paths: list[str] = []
    for step_id, result in step_results.items():
        all_paths.extend(find_value_paths(param_value, result, step_id=step_id))
    if not all_paths:
        return None, []
    return all_paths[0], all_paths
