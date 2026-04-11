"""
Case loading, schema validation, parameter resolution, and config normalization.

A *case* is one benchmark task defined by a ``test.yaml`` file under
``resources/{service}/{case_name}/``. This module loads that file from
disk, validates its structure, resolves parameter overrides, and returns
a normalized case descriptor consumed by ``runtime.case``.

Only the contemporary case format is accepted. Files containing legacy
fields such as ``preOperationCommands`` or ``verificationCommands`` are
rejected at load time with a descriptive error. No dual-format branching
exists anywhere in this module or downstream in ``runtime.case``.

Schema validation is performed by pydantic models defined in this
module. ``load_case_file`` runs ``CaseSchema.model_validate`` before
returning, so no unvalidated dict ever reaches normalization code.

No runtime imports. All functions operate on plain dicts and ``Path``
objects.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ValidationError, field_validator, model_validator

_TEST_FILE_NAME = "test.yaml"
_VALID_ON_PROBE_FAIL = {"error", "skip"}


# ---------------------------------------------------------------------------
# Pydantic schema models
# ---------------------------------------------------------------------------

class _CommandItem(BaseModel):
    """A single command entry in a probe, apply, or verify block."""

    command: str
    sleep: int = 0
    namespace_role: str | None = None
    timeout_sec: int | None = None


class _OperationBlock(BaseModel):
    """A probe/apply/verify operation block within a precondition unit."""

    probe: list[_CommandItem] | _CommandItem | str
    apply: list[_CommandItem] | _CommandItem | str
    verify: list[_CommandItem] | _CommandItem | str
    on_probe_fail: Literal["error", "skip"] = "error"


class _PreconditionUnit(BaseModel):
    """One precondition unit with a name and an operation block."""

    name: str
    probe: list[_CommandItem] | _CommandItem | str
    apply: list[_CommandItem] | _CommandItem | str
    verify: list[_CommandItem] | _CommandItem | str
    on_probe_fail: Literal["error", "skip"] = "error"


class _OracleVerify(BaseModel):
    """The verify block inside the oracle config."""

    commands: list[_CommandItem] | _CommandItem | str = []
    retries: int = 1
    interval_sec: float = 0.0


class _OracleConfig(BaseModel):
    """The oracle block at the top level of a test.yaml."""

    verify: _OracleVerify = _OracleVerify()
    script: str | None = None


class _ParamDefinition(BaseModel):
    """A single parameter definition with a default value."""

    default: Any = None
    description: str = ""


class _NamespaceContract(BaseModel):
    """Namespace role requirements for a case."""

    required_roles: list[str] = []
    optional_roles: list[str] = []


class CaseSchema(BaseModel):
    """Top-level schema for a contemporary ``test.yaml`` file.

    Validation fails immediately when a required field is missing, a
    field has the wrong type, or an unrecognized top-level key is
    present that is not a known contemporary field. The error message
    from pydantic identifies the exact field path and reason.
    """

    prompt: str
    params: dict[str, _ParamDefinition] = {}
    preconditionUnits: list[_PreconditionUnit] = []
    oracle: _OracleConfig = _OracleConfig()
    namespace_contract: _NamespaceContract = _NamespaceContract()
    decoys: list[Any] = []
    setup_check: Any = None
    metrics: list[str] = []
    tags: list[str] = []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _coerce_int(value: Any, default: int) -> int:
    """Return *value* coerced to ``int``, or *default* on failure."""
    try:
        return int(value)
    except Exception:
        return default


def _coerce_float(value: Any, default: float) -> float:
    """Return *value* coerced to ``float``, or *default* on failure."""
    try:
        return float(value)
    except Exception:
        return default


def _normalize_command_item(raw: Any) -> dict[str, Any] | None:
    """Return a canonical command dict for *raw*, or ``None`` when invalid.

    Accepts a non-empty string or a dict containing at minimum a
    ``"command"`` key. Recognized dict keys: ``command``, ``sleep``,
    ``namespace_role`` / ``namespaceRole``, ``timeout_sec`` /
    ``timeoutSec``.
    """
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        return {"command": raw, "sleep": 0}
    if not isinstance(raw, dict):
        return None
    command = raw.get("command")
    if command is None:
        return None
    item: dict[str, Any] = {
        "command": command,
        "sleep": _coerce_int(raw.get("sleep", 0), default=0),
    }
    ns = raw.get("namespace_role") or raw.get("namespaceRole")
    if ns is not None:
        item["namespace_role"] = str(ns).strip()
    ts = raw.get("timeout_sec") or raw.get("timeoutSec")
    if ts is not None:
        item["timeout_sec"] = _coerce_int(ts, default=60)
    return item


def _normalize_commands(raw: Any) -> list[dict[str, Any]]:
    """Return a list of canonical command dicts from *raw*.

    Accepts a single command string, a single command dict, or a list of
    either. Invalid and ``None`` entries are skipped silently.
    """
    if raw is None:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        normalized = _normalize_command_item(item)
        if normalized is not None:
            result.append(normalized)
    return result


def _normalize_operation_block(
    raw: Any,
    label: str,
    case_id: str,
    *,
    default_on_probe_fail: str = "error",
) -> dict[str, Any]:
    """Return a canonical operation unit dict for *raw*.

    An operation unit has three sub-blocks: ``probe``, ``apply``, and
    ``verify``. Each must resolve to at least one command.

    Parameters
    ----------
    raw:
        Raw dict from the case YAML.
    label:
        Human-readable label used in error messages, e.g. ``"precondition[1]"``.
    case_id:
        Case identifier used in error messages.
    default_on_probe_fail:
        Default ``on_probe_fail`` value when not specified in *raw*.

    Raises
    ------
    ValueError
        When *raw* is not a dict, when any sub-block is missing commands,
        or when ``on_probe_fail`` is not a valid value.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"case '{case_id}' {label} block must be an object")

    probe_commands = _normalize_commands(raw.get("probe"))
    apply_commands = _normalize_commands(raw.get("apply"))
    verify_commands = _normalize_commands(raw.get("verify"))

    if not probe_commands:
        raise ValueError(f"case '{case_id}' {label}.probe command(s) are required")
    if not apply_commands:
        raise ValueError(f"case '{case_id}' {label}.apply command(s) are required")
    if not verify_commands:
        raise ValueError(f"case '{case_id}' {label}.verify command(s) are required")

    probe_raw = raw.get("probe")
    on_probe_fail = default_on_probe_fail
    if isinstance(probe_raw, dict):
        v = probe_raw.get("on_probe_fail")
        if v is not None:
            on_probe_fail = str(v).strip().lower()
    if on_probe_fail not in _VALID_ON_PROBE_FAIL:
        raise ValueError(
            f"case '{case_id}' {label}.probe.on_probe_fail must be one of: "
            f"{', '.join(sorted(_VALID_ON_PROBE_FAIL))}"
        )

    vd = raw.get("verify") if isinstance(raw.get("verify"), dict) else {}
    retries = _coerce_int(vd.get("retries") if isinstance(vd, dict) else None, default=1)
    interval_sec = _coerce_float(
        (vd.get("interval_sec") or vd.get("intervalSec")) if isinstance(vd, dict) else None,
        default=0.0,
    )

    return {
        "id": f"{case_id}:{label}",
        "probe_commands": probe_commands,
        "apply_commands": apply_commands,
        "verify_commands": verify_commands,
        "verify_retries": max(1, retries),
        "verify_interval_sec": max(0.0, interval_sec),
        "on_probe_fail": on_probe_fail,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def case_path(resources_dir: Path, service: str, case_name: str) -> Path:
    """Return the ``test.yaml`` path for *service*/*case_name*.

    Does not check whether the file exists.
    """
    return resources_dir / service / case_name / _TEST_FILE_NAME


_LEGACY_FIELDS: dict[str, str] = {
    "preOperationCommands": "preconditionUnits",
    "verificationCommands": "oracle.verify.commands",
}


def load_case_file(resources_dir: Path, service: str, case_name: str) -> dict[str, Any]:
    """Load and parse the ``test.yaml`` for *service*/*case_name*.

    Raises
    ------
    RuntimeError
        When the file is absent or cannot be parsed as a YAML object, or
        when the file contains a legacy field such as
        ``preOperationCommands`` or ``verificationCommands``, or when
        pydantic schema validation fails. The error message names the
        offending field path and the reason.
    """
    path = case_path(resources_dir, service, case_name)
    if not path.exists():
        raise RuntimeError(f"case '{case_name}': test.yaml not found at {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        raise RuntimeError(
            f"case '{case_name}': failed to parse {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"case '{case_name}': {path} must be a YAML object")
    for legacy_field, replacement in _LEGACY_FIELDS.items():
        if legacy_field in data:
            raise RuntimeError(
                f"case '{case_name}': legacy field '{legacy_field}' is not supported. "
                f"Use '{replacement}' instead."
            )
    try:
        CaseSchema.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        raise RuntimeError(
            f"case '{case_name}': schema validation failed: {details}"
        ) from exc
    return data


def resolve_case_params(
    case_data: dict[str, Any],
    param_overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Resolve the final parameter values for a case.

    Merges declared parameter defaults from *case_data* with
    *param_overrides*, with overrides taking precedence. Unrecognized
    override keys produce a warning entry rather than an error.

    Returns
    -------
    tuple[dict, list[str]]
        ``(resolved_params, warnings)`` where *warnings* is a list of
        non-fatal diagnostic strings.
    """
    ...


def normalize_namespace_contract(case_data: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized namespace contract from *case_data*.

    Returns
    -------
    dict
        Keys ``required_roles`` (list[str]) and ``optional_roles``
        (list[str]). Both lists are deduplicated with order preserved.
        An absent contract is represented as two empty lists.
    """
    ...


def normalize_precondition_units(case_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the list of normalized precondition operation units from *case_data*.

    Reads from the ``preconditionUnits`` key only. Any file containing a
    legacy field such as ``preOperationCommands`` is rejected upstream by
    :func:`load_case_file` before this function is ever reached, so no
    format detection is performed here.

    Each unit has the canonical probe/apply/verify shape produced by
    :func:`_normalize_operation_block`. Returns an empty list when the
    case declares no preconditions.

    Raises
    ------
    RuntimeError
        When any unit is structurally invalid.
    """
    ...


def normalize_oracle_config(case_data: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized oracle configuration from *case_data*.

    Returns
    -------
    dict
        Keys: ``verify_commands`` (list), ``before_commands`` (list),
        ``after_commands`` (list), ``after_failure_mode`` (``"warn"`` or
        ``"fail"``), ``script_path`` (str or ``None``).
    """
    ...


def normalize_decoy_config(case_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the list of normalized decoy descriptors from *case_data*.

    Each descriptor has keys ``path`` (str) and ``namespace`` (str).
    Returns an empty list when the case declares no decoys.
    """
    ...


def normalize_case(
    case_data: dict[str, Any],
    service: str,
    case_name: str,
    param_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the fully normalized case descriptor for *case_data*.

    Runs all sub-normalizations and returns a single flat dict that
    ``runtime.case`` consumes without further parsing.

    Raises
    ------
    RuntimeError
        When any structural error is detected during normalization.

    Returns
    -------
    dict
        Keys: ``service``, ``case_name``, ``params``,
        ``namespace_contract``, ``precondition_units``, ``oracle``,
        ``decoys``, ``setup_check``, ``metrics``, ``tags``,
        ``warnings``.
    """
    ...
