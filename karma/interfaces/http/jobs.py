"""
UI job state management, single-case workflow translation, and job orchestration.

This is the critical translation layer between the HTTP interface and the
runtime. Single-case UI form submissions are converted into 1-stage
workflow dicts here, ensuring that both the UI and CLI paths execute
through an identical ``runtime.service`` stack with no further branching.
"""

from __future__ import annotations

import threading
import queue
from pathlib import Path
from typing import Any

from ...definitions.workflows import single_case_to_workflow, normalize_workflow
from ...runtime.service import submit_run, get_run_status, cleanup_run


_active_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _register_job(run_id: str, meta: dict[str, Any]) -> None:
    """Register *meta* under *run_id* in the active jobs table."""
    with _jobs_lock:
        _active_jobs[run_id] = meta


def _update_job(run_id: str, updates: dict[str, Any]) -> None:
    """Apply *updates* to the entry for *run_id*. No-op when not found."""
    with _jobs_lock:
        if run_id in _active_jobs:
            _active_jobs[run_id].update(updates)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def translate_ui_request(
    payload: dict[str, Any],
    *,
    resources_dir: Path,
) -> dict[str, Any]:
    """Return a normalized workflow dict from a raw UI form submission.

    Accepts two payload shapes:

    *Single-case* (``service`` and ``case_name`` keys present)
        Calls :func:`~karma.definitions.workflows.single_case_to_workflow`
        to produce a 1-stage workflow.

    *Inline workflow* (``workflow_yaml`` key present)
        Parses and normalizes the YAML string via
        :func:`~karma.definitions.workflows.normalize_workflow`.

    In both cases the returned dict passes directly to
    ``runtime.service.run_workflow`` or ``runtime.service.submit_run``.

    Parameters
    ----------
    payload:
        Raw dict from the HTTP request body.
    resources_dir:
        Root resources directory forwarded to ``normalize_workflow``.

    Raises
    ------
    ValueError
        When required payload fields are absent or the workflow YAML is
        unparseable.
    """
    if "workflow_yaml" in payload:
        import yaml
        try:
            raw = yaml.safe_load(payload["workflow_yaml"]) or {}
        except Exception as exc:
            raise ValueError(f"failed to parse workflow YAML: {exc}") from exc
        return normalize_workflow(raw, resources_dir=resources_dir)

    service = str(payload.get("service") or "").strip()
    case_name = str(payload.get("case_name") or "").strip()

    if not service:
        raise ValueError("service is required for single-case runs")
    if not case_name:
        raise ValueError("case_name is required for single-case runs")

    param_overrides = payload.get("params") or {}
    if not isinstance(param_overrides, dict):
        param_overrides = {}

    return single_case_to_workflow(
        service,
        case_name,
        param_overrides,
        prompt_mode=str(payload.get("prompt_mode") or "progressive").strip(),
        agent_timeout_sec=int(payload.get("agent_timeout_sec") or 900),
        namespace_roles=payload.get("namespace_roles") or None,
    )


def submit_job(
    payload: dict[str, Any],
    *,
    runs_dir: Path,
    resources_dir: Path,
    on_stage_complete: Any | None = None,
) -> str:
    """Translate a UI payload, submit it as a run, and return the run ID.

    Calls :func:`translate_ui_request` to normalize the payload, then
    calls ``runtime.service.submit_run`` to start the run asynchronously.
    Registers the resulting job in the active jobs table.

    Parameters
    ----------
    payload:
        Raw dict from the HTTP request body.
    runs_dir:
        Root runs directory.
    resources_dir:
        Root resources directory.
    on_stage_complete:
        Optional callback forwarded to ``runtime.service.submit_run``.

    Raises
    ------
    ValueError
        When the payload is invalid.

    Returns
    -------
    str
        Run ID that can be passed to :func:`get_job_status` and
        :func:`cancel_job`.
    """
    workflow = translate_ui_request(payload, resources_dir=resources_dir)
    event_queue: queue.Queue = queue.Queue(maxsize=100)

    run_id = submit_run(
        workflow,
        runs_dir=runs_dir,
        resources_dir=resources_dir,
        agent_name=payload.get("agent"),
        sandbox_mode=str(payload.get("sandbox") or "local"),
        on_stage_complete=on_stage_complete,
    )

    _register_job(run_id, {
        "run_id": run_id,
        "status": "running",
        "event_queue": event_queue,
    })

    return run_id


def get_job_status(run_id: str) -> dict[str, Any] | None:
    """Return the current status dict for *run_id*, or ``None`` when not found.

    Merges the local job entry with the runtime status from
    ``runtime.service.get_run_status``. The ``event_queue`` key is
    excluded from the returned dict.
    """
    with _jobs_lock:
        job = _active_jobs.get(run_id)
    if job is None:
        return None

    runtime_status = get_run_status(run_id)
    merged = dict(job)
    if runtime_status:
        merged.update(runtime_status)
    merged.pop("event_queue", None)
    return merged


def cancel_job(run_id: str) -> bool:
    """Request cancellation of a running job.

    Marks the job as cancelled in the active jobs table and pushes a
    cancel sentinel to the event queue. The runtime loop checks for
    cancellation between stages and exits early when detected.

    Returns
    -------
    bool
        ``True`` when the job was found and cancellation was requested,
        ``False`` when *run_id* is not registered.
    """
    ...


def list_jobs(*, status_filter: str | None = None) -> list[dict[str, Any]]:
    """Return a list of all active job status dicts.

    When *status_filter* is provided, only jobs whose ``status`` matches
    are included. The ``event_queue`` key is excluded from every entry.
    """
    with _jobs_lock:
        jobs = list(_active_jobs.values())
    return [
        {k: v for k, v in job.items() if k != "event_queue"}
        for job in jobs
        if status_filter is None or job.get("status") == status_filter
    ]
