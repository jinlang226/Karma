"""
Adversary injection lifecycle: deploy, lift, and report.

Owns the runtime-facing adversary API called by ``runtime.case`` at two
points in the stage execution lifecycle:

- After preconditions complete: :func:`deploy` runs the deploy unit to
  plant the fault condition in the cluster.
- During cleanup (before namespace teardown): :func:`lift` runs the lift
  unit to remove the fault condition.
- After lift: :func:`report` writes a structured result to disk.

Dependency rule: this module must not import from ``runtime.*``. It
accepts ``role_bindings``, ``log_path``, and ``run_dir`` as arguments so
that ``runtime.case`` retains full control over I/O paths and namespace
context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def deploy(
    units: list[dict[str, Any]],
    *,
    role_bindings: dict[str, str],
    log_path: Path,
    env_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute adversary deploy units and return a deploy result dict.

    Called by ``runtime.case.run_stage`` after preconditions complete and
    before the agent is launched. Each unit follows the canonical
    probe/apply/verify shape. When the probe passes the fault is already
    active and apply is skipped; when the probe fails apply is run to
    plant the fault, then verify confirms it is active.

    Parameters
    ----------
    units:
        Deploy operation units from
        ``adversary.definitions.collect_stage_operations``.
    role_bindings:
        Map of namespace role name to physical namespace name, forwarded
        to every kubectl command.
    log_path:
        Path to the adversary log file where all command output is appended.
    env_vars:
        Additional environment variables forwarded to commands.

    Returns
    -------
    dict
        Keys: ``ok`` (bool), ``deployed_ids`` (list[str] of unit IDs that
        activated successfully), ``output`` (str).
    """
    ...


def lift(
    units: list[dict[str, Any]],
    *,
    role_bindings: dict[str, str],
    log_path: Path,
    env_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute adversary lift units and return a lift result dict.

    Called by ``runtime.case.run_stage`` during cleanup, after evidence
    collection and oracle execution. When the probe confirms the fault is
    still active, apply removes it and verify confirms it is gone. When
    the probe shows the fault is already gone (``on_probe_fail: skip``),
    the lift is considered a no-op success.

    Parameters
    ----------
    units:
        Lift operation units from
        ``adversary.definitions.collect_stage_operations``.
    role_bindings:
        Map of namespace role name to physical namespace name.
    log_path:
        Path to the adversary log file where all command output is appended.
    env_vars:
        Additional environment variables forwarded to commands.

    Returns
    -------
    dict
        Keys: ``ok`` (bool), ``lifted_ids`` (list[str] of unit IDs that
        were successfully removed), ``output`` (str).
    """
    ...


def report(
    scenario_id: str,
    deploy_result: dict[str, Any],
    lift_result: dict[str, Any] | None,
    *,
    run_dir: Path,
    stage_id: str,
) -> dict[str, Any]:
    """Write a structured adversary result to disk and return it.

    Written to ``{run_dir}/stages/{stage_id}/adversary.json``. When
    *lift_result* is ``None`` the scenario had no lift stage or the lift
    was handled by the final cleanup sweep in ``runtime.workflow``.

    Parameters
    ----------
    scenario_id:
        Identifier of the adversary scenario.
    deploy_result:
        Result dict from :func:`deploy`.
    lift_result:
        Result dict from :func:`lift`, or ``None``.
    run_dir:
        Root directory of the current run.
    stage_id:
        ID of the stage this adversary was active in.

    Returns
    -------
    dict
        Keys: ``scenario_id`` (str), ``deploy`` (dict), ``lift`` (dict or
        ``None``), ``ok`` (bool).
    """
    ...
