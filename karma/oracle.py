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

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from . import protocol


def _run_commands(
    commands: list[dict[str, Any]],
    *,
    env: dict[str, str],
    timeout_sec: int,
) -> tuple[bool, str]:
    """Run a list of command dicts, returning ``(ok, combined_output)``.

    Stops on the first non-zero exit unless the command list is empty.
    """
    output_parts: list[str] = []
    deadline = time.monotonic() + timeout_sec

    for entry in commands:
        cmd = entry.get("command", "")
        if not cmd:
            continue
        remaining = max(1, int(deadline - time.monotonic()))
        cmd_timeout = min(entry.get("timeout_sec") or 120, remaining)
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                env=env, timeout=cmd_timeout,
            )
            out = proc.stdout + proc.stderr
            output_parts.append(f"$ {cmd}\n{out}")
            if entry.get("sleep"):
                time.sleep(entry["sleep"])
            if proc.returncode != 0:
                return False, "".join(output_parts)
        except subprocess.TimeoutExpired:
            output_parts.append(f"$ {cmd}\n[timed out after {cmd_timeout}s]\n")
            return False, "".join(output_parts)
        except Exception as exc:
            output_parts.append(f"$ {cmd}\n[error: {exc}]\n")
            return False, "".join(output_parts)

    return True, "".join(output_parts)


# Signatures of a transient kubectl/apiserver connectivity failure — a brief
# proxy or apiserver blip (common when several stages run concurrently) rather
# than a real assessment of cluster state. A FAIL carrying one of these is
# re-run instead of being recorded as the verdict.
_TRANSIENT_SIGNATURES = (
    "Unable to connect to the server",
    "TLS handshake timeout",
    "i/o timeout",
    "connection refused",
    "dial tcp",
    "Client.Timeout exceeded",
    "unexpected EOF",
    "the server is currently unable to handle the request",
    "etcdserver: request timed out",
)


def _is_transient_failure(result: dict[str, Any]) -> bool:
    """True if a FAIL verdict looks caused by a transient API/proxy blip.

    Such failures (kubectl momentarily cannot reach the apiserver, so the
    oracle cannot list pods or read secrets) do not reflect cluster state, so
    the caller re-runs the evaluation rather than trusting the verdict.
    """
    blob = " ".join(
        str(result.get(key) or "")
        for key in ("output", "before_output", "after_output", "script_output")
    )
    return any(sig in blob for sig in _TRANSIENT_SIGNATURES)


def _evaluate_oracle(
    oracle_config: dict[str, Any],
    *,
    env: dict[str, str],
    effective_timeout: int,
) -> dict[str, Any]:
    """Run before/verify/script/after commands once and return a verdict dict.

    Pure evaluation with no disk writes; ``run_oracle`` wraps this in a
    transient-failure retry loop and persists the final result.
    """
    result: dict[str, Any] = {
        "verdict": "error",
        "output": "",
        "before_output": "",
        "after_output": "",
        "script_output": None,
        "error": None,
    }
    try:
        after_failure_mode = oracle_config.get("after_failure_mode") or "warn"

        before_ok, before_out = _run_commands(
            oracle_config.get("before_commands") or [],
            env=env, timeout_sec=effective_timeout,
        )
        result["before_output"] = before_out

        verify_ok, verify_out = _run_commands(
            oracle_config.get("verify_commands") or [],
            env=env, timeout_sec=effective_timeout,
        )
        result["output"] = verify_out

        script_output: str | None = None
        script_path = oracle_config.get("script_path")
        if script_path:
            try:
                proc = subprocess.run(
                    ["python3", script_path],
                    capture_output=True, text=True, env=env, timeout=effective_timeout,
                )
                script_output = proc.stdout + proc.stderr
                if proc.returncode != 0:
                    verify_ok = False
            except Exception as exc:
                script_output = f"[script error: {exc}]"
                verify_ok = False
        result["script_output"] = script_output

        after_ok, after_out = _run_commands(
            oracle_config.get("after_commands") or [],
            env=env, timeout_sec=effective_timeout,
        )
        result["after_output"] = after_out

        if not verify_ok:
            result["verdict"] = "fail"
        elif not after_ok and after_failure_mode == "fail":
            result["verdict"] = "fail"
        else:
            result["verdict"] = "pass"
    except Exception as exc:
        result["verdict"] = "error"
        result["error"] = str(exc)
    return result


def run_oracle(
    oracle_config: dict[str, Any],
    *,
    role_bindings: dict[str, str],
    run_dir: Path,
    stage_id: str,
    env_vars: dict[str, str] | None = None,
    timeout_sec: int | None = None,
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
    from .settings import settings as _settings
    if timeout_sec is not None:
        effective_timeout = timeout_sec
    else:
        # Honor per-command timeout_sec declared in the oracle config: the outer
        # wall-clock deadline must be at least the sum of the declared per-command
        # budgets across before/verify/after, otherwise a command's own
        # timeout_sec is silently clamped by the default oracle_timeout_sec (e.g.
        # a 300s pod-restart-persistence check capped at 120s). Defaults to
        # oracle_timeout_sec when no command declares a longer timeout.
        declared = 0
        for _key in ("before_commands", "verify_commands", "after_commands"):
            for _entry in oracle_config.get(_key) or []:
                if _entry.get("command"):
                    declared += int(_entry.get("timeout_sec") or 0) + int(_entry.get("sleep") or 0)
        effective_timeout = max(_settings.oracle_timeout_sec, declared)

    env = {**os.environ, **role_bindings, **(env_vars or {})}

    # A transient apiserver/proxy blip must not be recorded as a real FAIL: when
    # a fail carries a connectivity signature, re-run the whole evaluation a few
    # times (short backoff) before trusting it. A genuine fail (real assertion
    # mismatch) has no such signature and is returned on the first pass.
    result: dict[str, Any] = {"verdict": "error", "output": "", "error": None}
    for _attempt in range(3):
        result = _evaluate_oracle(
            oracle_config, env=env, effective_timeout=effective_timeout,
        )
        if result["verdict"] != "fail" or not _is_transient_failure(result):
            break
        time.sleep(min(5 * (_attempt + 1), 15))

    protocol.ensure_stage_dir(run_dir, stage_id)
    oracle_path = protocol.stage_oracle_path(run_dir, stage_id)
    oracle_path.write_text(json.dumps(result, indent=2))
    return result


def run_regression_sweep(
    stage_oracle_configs: list[tuple[str, dict[str, Any]]],
    *,
    role_bindings_map: dict[str, dict[str, str]],
    run_dir: Path,
    env_vars_map: dict[str, dict[str, str]] | None = None,
    timeout_sec: int | None = None,
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
    env_vars_map:
        Map of ``stage_id`` to the namespace/param environment variables
        (``BENCH_NAMESPACE``, ``BENCH_NS_<ROLE>``, ``BENCH_PARAM_<KEY>``,
        ...) that the stage's oracle commands reference. Each stage has its
        own namespaces, so the env is per-stage rather than shared.
    timeout_sec:
        Per-stage timeout in seconds.

    Returns
    -------
    dict
        Map of ``stage_id`` to its regression verdict dict.
    """
    results: dict[str, Any] = {}
    for stage_id, oracle_config in stage_oracle_configs:
        role_bindings = role_bindings_map.get(stage_id) or {}
        env_vars = (env_vars_map or {}).get(stage_id)
        verdict = run_oracle(
            oracle_config,
            role_bindings=role_bindings,
            run_dir=run_dir,
            stage_id=f"{stage_id}__regression",
            env_vars=env_vars,
            timeout_sec=timeout_sec,
        )
        results[stage_id] = verdict
    return results
