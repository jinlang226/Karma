"""
Single-stage lifecycle execution.

:func:`run_stage` is the innermost execution unit called by
``runtime.workflow`` for each stage in the workflow loop.

Stage execution steps:

1. Create the stage directory via ``protocol``.
2. Launch the kubectl proxy via ``transport.k8s.backend``.
3. Bind namespace roles and create namespaces via the environment provider.
4. Run precondition units.
5. Plant decoys.
6. Run adversary deploy units.
7. Write the agent credential bundle (kubeconfig, env vars).
8. Render and write the stage prompt.
9. Launch the agent via ``sandbox``.
10. Poll for ``submit.txt`` or wait for the agent timeout.
11. Terminate the agent process.
12. Collect evidence via ``evidence``.
13. Run the oracle via ``oracle``.
14. Run adversary lift units.
15. Write stage metadata and outcome.
16. Tear down the proxy and clean up namespaces.

:func:`run_stage` never raises. All errors are captured in the returned
stage result dict under the ``"error"`` key so that ``runtime.workflow``
can decide whether to retry or advance.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..transport.k8s.backend import launch_proxy, write_agent_bundle
from ..environments.registry import get_environment
from ..sandbox import launch_agent, cleanup_agent
from ..oracle import run_oracle
from ..evidence import collect_evidence
from .. import protocol


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_operation_units(
    units: list[dict[str, Any]],
    *,
    role_bindings: dict[str, str],
    log_path: Path,
    env_vars: dict[str, str] | None = None,
    label: str = "operation",
) -> dict[str, Any]:
    """Execute a list of probe/apply/verify operation units in order.

    For each unit: runs probe commands first; when the probe passes, runs
    apply then verify. Respects the ``on_probe_fail`` policy (``"error"``
    or ``"skip"``). Retries verify up to ``verify_retries`` times with
    ``verify_interval_sec`` between attempts. All command output is
    appended to *log_path*.

    Returns
    -------
    dict
        Keys: ``ok`` (bool), ``units`` (list[dict] of per-unit outcomes),
        ``output`` (str).
    """
    ...


def _wait_for_submit(
    submit_path: Path,
    *,
    agent_timeout_sec: int,
    poll_interval_sec: float = 1.0,
) -> tuple[bool, str | None]:
    """Poll for the agent's submit file until it appears or the timeout expires.

    Returns
    -------
    tuple[bool, str | None]
        ``(submitted, content)`` where *submitted* is ``True`` when the
        file appeared and *content* is its text. Returns
        ``(False, None)`` on timeout.
    """
    deadline = time.monotonic() + agent_timeout_sec
    while time.monotonic() < deadline:
        if submit_path.exists():
            try:
                return True, submit_path.read_text()
            except Exception:
                pass
        time.sleep(poll_interval_sec)
    return False, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_stage(
    row: dict[str, Any],
    *,
    run_dir: Path,
    resources_dir: Path,
    agent_meta: dict[str, Any],
    sandbox_mode: str,
    environment: Any,
    prior_stage_ids: list[str],
    stage_prompts: list[str],
    prompt_mode: str,
) -> dict[str, Any]:
    """Execute one workflow stage and return its result dict.

    Parameters
    ----------
    row:
        Workflow row dict from ``definitions.workflows.resolve_workflow_rows``.
    run_dir:
        Root directory of the current run.
    resources_dir:
        Root resources directory.
    agent_meta:
        Agent launch metadata from ``agents.registry.resolve_agent``.
    sandbox_mode:
        ``"local"`` or ``"docker"``.
    environment:
        Initialized environment provider from ``environments.registry``.
    prior_stage_ids:
        IDs of stages that completed successfully before this one.
    stage_prompts:
        Rendered prompt strings for all stages up to and including this one.
    prompt_mode:
        One of the prompt modes defined in ``definitions.prompts``.

    Returns
    -------
    dict
        Keys: ``stage_id``, ``status`` (``"pass"``, ``"fail"``,
        ``"timeout"``, or ``"error"``), ``oracle_verdict``
        (``"pass"``, ``"fail"``, ``"error"``, or ``None``),
        ``submitted`` (bool), ``duration_sec`` (float),
        ``error`` (str or ``None``), ``evidence_path`` (str),
        ``oracle_path`` (str).
    """
    stage_id = row["stage_id"]
    stage_dir = protocol.ensure_stage_dir(run_dir, stage_id)
    start_time = time.monotonic()

    proxy_handle = None
    agent_process = None
    role_bindings: dict[str, str] = {}

    try:
        ...
    except Exception as exc:
        return {
            "stage_id": stage_id,
            "status": "error",
            "oracle_verdict": None,
            "submitted": False,
            "duration_sec": time.monotonic() - start_time,
            "error": str(exc),
            "evidence_path": str(protocol.stage_evidence_path(run_dir, stage_id)),
            "oracle_path": str(protocol.stage_oracle_path(run_dir, stage_id)),
        }
    finally:
        if agent_process is not None:
            cleanup_agent(agent_process)
        if proxy_handle is not None:
            proxy_handle.teardown()
        if role_bindings and environment is not None:
            try:
                environment.cleanup_namespaces(role_bindings, run_dir=stage_dir)
            except Exception:
                pass
