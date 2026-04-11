"""
Adversary scenario loading, validation, and per-stage operation collection.

An adversary scenario introduces an intentional fault into the cluster
during a workflow run to evaluate how well the agent diagnoses and
recovers from unexpected environmental conditions.

Scenarios live at::

    resources/{service}/adversarial/{scenario}/scenario.yaml

The service is derived from ``inject_at_stage`` via the stage service map,
enforcing that a scenario references resources from within the same test
suite that owns the inject stage.

No runtime imports. Consumed by ``definitions.workflows`` during row
resolution and by ``adversary.runtime`` during the lifecycle sweep.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

_SCENARIO_FILE_NAME = "scenario.yaml"
_ADVERSARIAL_DIR_NAME = "adversarial"
_VALID_ON_PROBE_FAIL = {"error", "skip"}


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


def _normalize_commands(raw: Any) -> list[dict[str, Any]]:
    """Return a list of canonical command dicts from *raw*.

    Accepts a command string, a command dict, or a list of either.
    Invalid entries are skipped silently.
    """
    if raw is None:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if isinstance(item, str):
            item = item.strip()
            if item:
                result.append({"command": item, "sleep": 0})
        elif isinstance(item, dict) and item.get("command") is not None:
            entry: dict[str, Any] = {
                "command": item["command"],
                "sleep": _coerce_int(item.get("sleep", 0), default=0),
            }
            ns = item.get("namespace_role") or item.get("namespaceRole")
            if ns is not None:
                entry["namespace_role"] = str(ns).strip()
            ts = item.get("timeout_sec") or item.get("timeoutSec")
            if ts is not None:
                entry["timeout_sec"] = _coerce_int(ts, default=60)
            result.append(entry)
    return result


def _normalize_operation_block(
    raw: Any,
    label: str,
    scenario_id: str,
    *,
    default_on_probe_fail: str = "error",
) -> dict[str, Any]:
    """Return a canonical operation unit dict for an adversary deploy or lift block.

    Parameters
    ----------
    raw:
        Raw dict from the scenario YAML deploy or lift section.
    label:
        ``"deploy"`` or ``"lift"``, used in error messages.
    scenario_id:
        Scenario name used in error messages.
    default_on_probe_fail:
        Fallback ``on_probe_fail`` value when not specified in *raw*.

    Raises
    ------
    ValueError
        When *raw* is not a dict, when any sub-block lacks commands, or
        when ``on_probe_fail`` is invalid.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"adversary scenario '{scenario_id}' {label} block must be an object"
        )

    probe_commands = _normalize_commands(raw.get("probe"))
    apply_commands = _normalize_commands(raw.get("apply"))
    verify_commands = _normalize_commands(raw.get("verify"))

    if not probe_commands:
        raise ValueError(
            f"adversary scenario '{scenario_id}' {label}.probe command(s) are required"
        )
    if not apply_commands:
        raise ValueError(
            f"adversary scenario '{scenario_id}' {label}.apply command(s) are required"
        )
    if not verify_commands:
        raise ValueError(
            f"adversary scenario '{scenario_id}' {label}.verify command(s) are required"
        )

    probe_raw = raw.get("probe")
    on_probe_fail = default_on_probe_fail
    if isinstance(probe_raw, dict):
        v = probe_raw.get("on_probe_fail")
        if v is not None:
            on_probe_fail = str(v).strip().lower()
    if on_probe_fail not in _VALID_ON_PROBE_FAIL:
        raise ValueError(
            f"adversary scenario '{scenario_id}' {label}.probe.on_probe_fail "
            f"must be one of: {', '.join(sorted(_VALID_ON_PROBE_FAIL))}"
        )

    vd = raw.get("verify") if isinstance(raw.get("verify"), dict) else {}
    retries = _coerce_int(
        vd.get("retries") if isinstance(vd, dict) else None, default=1
    )
    interval_sec = _coerce_float(
        (vd.get("interval_sec") or vd.get("intervalSec")) if isinstance(vd, dict) else None,
        default=0.0,
    )

    return {
        "id": f"{scenario_id}:{label}",
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

def validate_adversary_workflow_block(
    spec: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Validate the ``spec.adversary`` list in a workflow YAML and return raw entries.

    Performs field-presence and type checks only. Does not access the
    filesystem. Cross-field checks (e.g. verifying that ``inject_at_stage``
    and ``lift_at_stage`` reference declared stage IDs) are deferred to
    workflow loading, which has access to the full stage list.

    Parameters
    ----------
    spec:
        The ``spec`` dict from the workflow YAML, or ``None``.

    Raises
    ------
    ValueError
        When any entry is structurally invalid.

    Returns
    -------
    list[dict]
        Each entry has keys ``scenario`` (str), ``inject_at_stage`` (str),
        ``lift_at_stage`` (str or ``None``), ``param_overrides`` (dict).
    """
    ...


def resolve_adversary_scenario(
    entry: dict[str, Any],
    stage_service_map: dict[str, str],
    *,
    resources_dir: Path,
) -> dict[str, Any]:
    """Load and resolve one adversary entry into a fully normalized injection dict.

    The service is derived from
    ``stage_service_map[entry["inject_at_stage"]]``, then the scenario
    file is loaded from
    ``resources/{service}/adversarial/{scenario}/scenario.yaml``.
    Parameter substitution uses the same ``{{params.foo}}`` syntax as
    ``test.yaml`` files.

    Parameters
    ----------
    entry:
        Raw adversary entry from :func:`validate_adversary_workflow_block`.
    stage_service_map:
        Map of stage ID to service name for the current workflow.
    resources_dir:
        Root resources directory.

    Raises
    ------
    RuntimeError
        When the scenario file is missing, unparseable, or structurally
        invalid.

    Returns
    -------
    dict
        Keys: ``id`` (str), ``inject_at_stage`` (str),
        ``lift_at_stage`` (str or ``None``), ``deploy_unit`` (dict),
        ``lift_unit`` (dict or ``None``),
        ``prompt_hints`` (``{"deploy": str|None, "active": str|None}``).
    """
    ...


def collect_stage_operations(
    injections: list[dict[str, Any]],
    stage_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(deploy_units, lift_units)`` for injections targeting *stage_id*.

    An injection contributes a deploy unit when
    ``inject_at_stage == stage_id`` and a lift unit when
    ``lift_at_stage == stage_id``. Both can match on the same stage.

    Parameters
    ----------
    injections:
        Resolved injection dicts from :func:`resolve_adversary_scenario`.
    stage_id:
        ID of the stage being prepared.
    """
    deploy_units: list[dict[str, Any]] = []
    lift_units: list[dict[str, Any]] = []
    for inj in injections or []:
        if inj.get("inject_at_stage") == stage_id:
            deploy_units.append(deepcopy(inj["deploy_unit"]))
        if inj.get("lift_at_stage") == stage_id and inj.get("lift_unit") is not None:
            lift_units.append(deepcopy(inj["lift_unit"]))
    return deploy_units, lift_units


def collect_pending_lift_units(
    injections: list[dict[str, Any]],
    deployed_scenario_ids: set[str],
    completed_stage_ids: set[str],
) -> list[dict[str, Any]]:
    """Return lift units for injections whose lift stage never ran.

    Used by the final cleanup sweep in ``adversary.runtime`` to ensure no
    adversarial conditions are left in the cluster after an early workflow
    exit.

    An injection is considered pending when its ID is in
    *deployed_scenario_ids* and its ``lift_at_stage`` is not in
    *completed_stage_ids*.

    Parameters
    ----------
    injections:
        All resolved injection dicts for the workflow.
    deployed_scenario_ids:
        IDs of scenarios whose deploy unit executed successfully.
    completed_stage_ids:
        IDs of stages that ran to completion.
    """
    ...


def collect_stage_hint(
    injections: list[dict[str, Any]],
    stage_id: str,
    all_stage_ids: list[str],
) -> str | None:
    """Return the adversary prompt hint for *stage_id*, or ``None``.

    Returns the ``deploy`` hint for the ``inject_at_stage`` and the
    ``active`` hint for all intermediate stages strictly between
    ``inject_at_stage`` and ``lift_at_stage``. When multiple injections
    are active at the same stage their hints are joined with two newlines.

    Parameters
    ----------
    injections:
        Resolved injection dicts for the workflow.
    stage_id:
        Stage for which to collect hints.
    all_stage_ids:
        Ordered list of all stage IDs in the workflow.
    """
    ...
