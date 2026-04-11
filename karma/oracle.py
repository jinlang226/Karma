"""
Oracle execution and final regression sweep helpers.

The oracle is the authoritative pass/fail verdict for a stage. It runs
after the agent exits and evaluates the live cluster state against the
expected outcome defined in the case's ``oracle.verify`` block.

This module does not import ``runtime.*``. It accepts a ``run_dir`` and
``role_bindings`` argument so that it can be invoked independently of a
live run context, including from the judge pipeline and standalone scripts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def run_oracle(
    oracle_config: dict[str, Any],
    *,
    role_bindings: dict[str, str],
    run_dir: Path,
    stage_id: str,
    env_vars: dict[str, str] | None = None,
    timeout_sec: int = 300,
) -> dict[str, Any]:
    """Execute the oracle verify block for one stage and return the verdict.

    Runs ``before_commands``, then ``verify_commands``, then
    ``after_commands`` in order. A non-zero exit code from
    ``verify_commands`` sets the verdict to ``"fail"``. If
    ``after_commands`` fail and ``after_failure_mode`` is ``"fail"``, the
    verdict is additionally marked as degraded.

    When ``oracle_config`` contains a ``script_path``, the ``oracle.py``
    script is executed as a subprocess with ``role_bindings`` injected as
    environment variables, and its output is merged into the verdict.

    The verdict is written to ``protocol.stage_oracle_path(run_dir, stage_id)``.

    This function never raises. Unexpected errors are captured in the
    returned dict under the ``"error"`` key with a verdict of ``"error"``.

    Parameters
    ----------
    oracle_config:
        Normalized oracle config dict produced by ``definitions.cases.normalize_oracle_config``.
    role_bindings:
        Map of namespace role name to physical namespace name.
    run_dir:
        Root directory of the current run.
    stage_id:
        ID of the stage being evaluated.
    env_vars:
        Additional environment variables forwarded to oracle commands.
    timeout_sec:
        Maximum wall-clock seconds allowed for the full oracle execution.

    Returns
    -------
    dict
        Keys: ``verdict`` (``"pass"``, ``"fail"``, or ``"error"``),
        ``output``, ``before_output``, ``after_output``,
        ``script_output`` (``None`` if no script), ``error`` (``None``
        on success).
    """
    ...


def run_regression_sweep(
    stage_oracle_configs: list[tuple[str, dict[str, Any]]],
    *,
    role_bindings_map: dict[str, dict[str, str]],
    run_dir: Path,
    env_vars: dict[str, str] | None = None,
    timeout_sec: int = 300,
) -> dict[str, Any]:
    """Re-run the oracle for all completed stages to detect regressions.

    Called at the end of a successful multi-stage workflow. A regression is
    a stage that passed its own oracle but fails when re-evaluated after
    later stages have run and potentially altered cluster state.

    Parameters
    ----------
    stage_oracle_configs:
        Ordered list of ``(stage_id, oracle_config)`` pairs for every
        stage that completed without error.
    role_bindings_map:
        Map of ``stage_id`` to the role bindings that were active during
        that stage's execution.
    run_dir:
        Root directory of the current run.
    env_vars:
        Additional environment variables forwarded to oracle commands.
    timeout_sec:
        Per-stage timeout in seconds.

    Returns
    -------
    dict
        Map of ``stage_id`` to its regression verdict dict.
    """
    ...
