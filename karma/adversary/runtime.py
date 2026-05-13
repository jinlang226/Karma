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

import json
import subprocess
from pathlib import Path
from typing import Any


def _exec_command(
    cmd: str,
    *,
    env_vars: dict[str, str] | None,
    timeout: int,
    log_path: Path,
) -> tuple[bool, str]:
    """Run *cmd* in a shell, appending output to *log_path*.

    Returns ``(success, combined_output)``.
    """
    import os
    import time

    env = {**os.environ, **(env_vars or {})}
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, env=env, timeout=timeout
        )
        out = proc.stdout + proc.stderr
        with log_path.open("a") as fh:
            fh.write(f"$ {cmd}\n{out}\n")
        return proc.returncode == 0, out
    except subprocess.TimeoutExpired:
        msg = f"command timed out after {timeout}s: {cmd}\n"
        with log_path.open("a") as fh:
            fh.write(msg)
        return False, msg
    except Exception as exc:
        msg = f"command error: {exc}\n"
        with log_path.open("a") as fh:
            fh.write(msg)
        return False, msg


def _run_units(
    units: list[dict[str, Any]],
    *,
    role_bindings: dict[str, str],
    log_path: Path,
    env_vars: dict[str, str] | None,
    result_id_key: str,
) -> dict[str, Any]:
    """Run probe/apply/verify for each unit, respecting on_probe_fail.

    Returns a dict with ``ok``, *result_id_key* (list of IDs that ran), and
    ``output`` (concatenated log lines).
    """
    import time

    log_path.parent.mkdir(parents=True, exist_ok=True)
    all_output: list[str] = []
    success_ids: list[str] = []
    overall_ok = True
    env = {**role_bindings, **(env_vars or {})}

    for unit in units or []:
        unit_id = unit.get("id", "unknown")
        on_fail = unit.get("on_probe_fail", "error")

        # Probe
        probe_ok = True
        for cmd_entry in unit.get("probe_commands") or []:
            cmd = cmd_entry["command"]
            to = cmd_entry.get("timeout_sec") or 30
            ok, out = _exec_command(cmd, env_vars=env, timeout=to, log_path=log_path)
            all_output.append(out)
            if cmd_entry.get("sleep"):
                time.sleep(cmd_entry["sleep"])
            if not ok:
                probe_ok = False
                break

        if probe_ok:
            # Fault already active; skip apply
            success_ids.append(unit_id)
            continue
        if not probe_ok and on_fail == "skip":
            # Not active and we should skip — nothing to do
            continue
        if not probe_ok and on_fail == "error":
            # Run apply to plant/remove the fault
            for cmd_entry in unit.get("apply_commands") or []:
                cmd = cmd_entry["command"]
                to = cmd_entry.get("timeout_sec") or 120
                ok, out = _exec_command(cmd, env_vars=env, timeout=to, log_path=log_path)
                all_output.append(out)
                if cmd_entry.get("sleep"):
                    time.sleep(cmd_entry["sleep"])
                if not ok:
                    overall_ok = False
                    break
            # Verify
            verify_ok = True
            for cmd_entry in unit.get("verify_commands") or []:
                cmd = cmd_entry["command"]
                to = cmd_entry.get("timeout_sec") or 30
                ok, out = _exec_command(cmd, env_vars=env, timeout=to, log_path=log_path)
                all_output.append(out)
                if not ok:
                    verify_ok = False
                    break
            if verify_ok:
                success_ids.append(unit_id)
            else:
                overall_ok = False

    return {
        "ok": overall_ok,
        result_id_key: success_ids,
        "output": "".join(all_output),
    }


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
    return _run_units(units, role_bindings=role_bindings, log_path=log_path, env_vars=env_vars,
                      result_id_key="deployed_ids")


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
    return _run_units(units, role_bindings=role_bindings, log_path=log_path, env_vars=env_vars,
                      result_id_key="lifted_ids")


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
    result: dict[str, Any] = {
        "scenario_id": scenario_id,
        "deploy": deploy_result,
        "lift": lift_result,
        "ok": deploy_result.get("ok", False) and (lift_result is None or lift_result.get("ok", False)),
    }
    out_dir = run_dir / "stages" / stage_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "adversary.json").write_text(json.dumps(result, indent=2))
    return result
