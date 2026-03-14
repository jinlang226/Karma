from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


_PARAM_TOKEN_RE = re.compile(r"{{\s*params\.([a-zA-Z0-9_.-]+)\s*}}")
_FULL_PARAM_TOKEN_RE = re.compile(r"^\s*{{\s*params\.([a-zA-Z0-9_.-]+)\s*}}\s*$")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"cannot parse bool from {value!r}")


def _coerce_value(name: str, spec: dict[str, Any], value: Any) -> Any:
    param_type = str(spec.get("type") or "string").strip().lower()
    if value is None:
        return None

    if param_type in ("string", "duration", "quantity"):
        value = str(value)
    elif param_type == "int":
        value = int(value)
    elif param_type in ("float", "number"):
        value = float(value)
    elif param_type == "bool":
        value = _to_bool(value)
    elif param_type == "enum":
        choices = spec.get("values")
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"param {name}: enum requires non-empty values")
        choice_set = {str(item) for item in choices}
        if str(value) not in choice_set:
            raise ValueError(f"param {name}: value {value!r} not in {sorted(choice_set)}")
        value = str(value)
    else:
        raise ValueError(f"param {name}: unsupported type {param_type!r}")

    min_value = spec.get("min")
    max_value = spec.get("max")
    if min_value is not None and isinstance(value, (int, float)) and value < min_value:
        raise ValueError(f"param {name}: value {value} < min {min_value}")
    if max_value is not None and isinstance(value, (int, float)) and value > max_value:
        raise ValueError(f"param {name}: value {value} > max {max_value}")

    pattern = spec.get("pattern")
    if pattern is not None:
        try:
            if not re.match(str(pattern), str(value)):
                raise ValueError(f"param {name}: value {value!r} does not match pattern {pattern!r}")
        except re.error as exc:
            raise ValueError(f"param {name}: invalid regex pattern {pattern!r}: {exc}") from exc

    return value


def _collect_param_tokens(
    value: Any,
    out: set[str],
    *,
    ignore_top_level_keys: set[str] | None = None,
    _depth: int = 0,
) -> None:
    if isinstance(value, str):
        for match in _PARAM_TOKEN_RE.finditer(value):
            out.add(match.group(1))
        return
    if isinstance(value, list):
        for item in value:
            _collect_param_tokens(
                item,
                out,
                ignore_top_level_keys=ignore_top_level_keys,
                _depth=_depth + 1,
            )
        return
    if isinstance(value, dict):
        ignored = ignore_top_level_keys or set()
        for key, item in value.items():
            if _depth == 0 and str(key) in ignored:
                continue
            _collect_param_tokens(
                item,
                out,
                ignore_top_level_keys=ignore_top_level_keys,
                _depth=_depth + 1,
            )
        return


def resolve_case_params(
    case_data: dict[str, Any] | None,
    overrides: dict[str, Any] | None = None,
    *,
    allow_unresolved_top_level_keys: set[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    data = case_data or {}
    overrides = overrides or {}

    if not isinstance(overrides, dict):
        raise ValueError("param_overrides must be an object")
    for key, value in overrides.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("param_overrides keys must be non-empty strings")
        if not _is_scalar(value):
            raise ValueError(f"param_overrides.{key} must be a scalar value")

    params_block = data.get("params")
    definitions = {}
    if params_block is None:
        definitions = {}
    elif isinstance(params_block, dict):
        raw_defs = params_block.get("definitions") or {}
        if not isinstance(raw_defs, dict):
            raise ValueError("params.definitions must be an object")
        for name, spec in raw_defs.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("params.definitions keys must be non-empty strings")
            if isinstance(spec, dict):
                definitions[name] = deepcopy(spec)
            else:
                definitions[name] = {"type": "string", "default": spec}
    else:
        raise ValueError("params must be an object")

    unknown = sorted(set(overrides.keys()) - set(definitions.keys()))
    if unknown:
        raise ValueError(f"unknown param_overrides keys: {', '.join(unknown)}")

    resolved: dict[str, Any] = {}
    for name, spec in definitions.items():
        if "default" in spec:
            resolved[name] = _coerce_value(name, spec, spec.get("default"))

    for name, value in overrides.items():
        spec = definitions.get(name) or {}
        resolved[name] = _coerce_value(name, spec, value)

    for name, spec in definitions.items():
        required = bool(spec.get("required"))
        if required and name not in resolved:
            raise ValueError(f"required param missing: {name}")
        if name in resolved:
            resolved[name] = _coerce_value(name, spec, resolved[name])

    tokens: set[str] = set()
    _collect_param_tokens(
        data,
        tokens,
        ignore_top_level_keys=allow_unresolved_top_level_keys,
    )
    undefined_tokens = sorted(token for token in tokens if token not in definitions)
    if undefined_tokens:
        raise ValueError(f"undefined params referenced in templates: {', '.join(undefined_tokens)}")

    warnings: list[str] = []
    unused = sorted(set(definitions.keys()) - tokens)
    for name in unused:
        warnings.append(f"param '{name}' is defined but not used in templates")

    return resolved, warnings


def _render_value(
    value: Any,
    params: dict[str, Any],
    *,
    allow_unresolved_top_level_keys: set[str] | None = None,
    _top_level_key: str | None = None,
) -> Any:
    if isinstance(value, str):
        allow_unresolved = (
            _top_level_key is not None
            and _top_level_key in (allow_unresolved_top_level_keys or set())
        )
        match = _FULL_PARAM_TOKEN_RE.match(value)
        if match:
            key = match.group(1)
            if key not in params:
                if allow_unresolved:
                    return value
                raise ValueError(f"missing param value for token: {key}")
            return params[key]

        def repl(token_match: re.Match[str]) -> str:
            key = token_match.group(1)
            if key not in params:
                if allow_unresolved:
                    return token_match.group(0)
                raise ValueError(f"missing param value for token: {key}")
            return str(params[key])

        return _PARAM_TOKEN_RE.sub(repl, value)

    if isinstance(value, list):
        return [
            _render_value(
                item,
                params,
                allow_unresolved_top_level_keys=allow_unresolved_top_level_keys,
                _top_level_key=_top_level_key,
            )
            for item in value
        ]

    if isinstance(value, dict):
        return {
            key: _render_value(
                item,
                params,
                allow_unresolved_top_level_keys=allow_unresolved_top_level_keys,
                _top_level_key=str(key) if _top_level_key is None else _top_level_key,
            )
            for key, item in value.items()
        }

    return value


def render_case_data_with_params(
    case_data: dict[str, Any] | None,
    params: dict[str, Any],
    *,
    allow_unresolved_top_level_keys: set[str] | None = None,
) -> dict[str, Any]:
    rendered = _render_value(
        deepcopy(case_data or {}),
        params,
        allow_unresolved_top_level_keys=allow_unresolved_top_level_keys,
    )
    unresolved: set[str] = set()
    _collect_param_tokens(
        rendered,
        unresolved,
        ignore_top_level_keys=allow_unresolved_top_level_keys,
    )
    if unresolved:
        raise ValueError(f"unresolved params tokens remain after render: {', '.join(sorted(unresolved))}")
    return rendered
