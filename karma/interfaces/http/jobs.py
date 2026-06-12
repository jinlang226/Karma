"""
UI job state management, single-case workflow translation, and job orchestration.

This is the critical translation layer between the HTTP interface and the
runtime. Single-case UI form submissions are converted into 1-stage
workflow dicts here, ensuring that both the UI and CLI paths execute
through an identical ``runtime.service`` stack with no further branching.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ...definitions.workflows import (
    single_case_to_workflow,
    normalize_workflow,
    load_workflow_file,
)
from ...runtime.service import run_workflow, get_run_status
from ...protocol import generate_run_id, run_config_path
from .events import hub


_active_jobs: dict[str, dict[str, Any]] = {}
_cancel_requested: set[str] = set()
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
    workflows_dir: Path | None = None,
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

    if "workflow_path" in payload:
        from pathlib import Path as _Path
        candidate = _Path(str(payload["workflow_path"]))
        load_path = candidate
        # When a workflows_dir is supplied (the HTTP boundary), confine the path
        # to that tree -- reject absolute paths and ".." traversal so the
        # endpoint can't be driven to read arbitrary files. Direct callers
        # (omit workflows_dir) are unrestricted.
        if workflows_dir is not None:
            root = _Path(workflows_dir).resolve()
            load_path = (candidate.resolve() if candidate.is_absolute()
                         else (_Path.cwd() / candidate).resolve())
            if load_path != root and root not in load_path.parents:
                raise ValueError("workflow_path must be under the workflows/ directory")
        try:
            raw = load_workflow_file(load_path)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
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
    workflows_dir: Path | None = None,
    on_stage_complete: Any | None = None,
) -> str:
    """Translate a UI payload, run it in the background, and return the run ID.

    Normalizes the payload via :func:`translate_ui_request`, then runs
    ``runtime.service.run_workflow`` on a daemon thread owned here. Running
    the workflow synchronously inside our own thread (rather than delegating
    to ``submit_run``'s thread) gives a definite completion point, so we can
    publish a terminal ``run_complete`` event and close the event stream --
    something the fire-and-forget path could not signal.

    Every stage completion is published to the shared :data:`events.hub`
    keyed by run ID; the SSE endpoint subscribes to that. An optional
    *on_stage_complete* callback is still invoked for non-HTTP callers.

    Raises
    ------
    ValueError
        When the payload is invalid.

    Returns
    -------
    str
        Run ID for :func:`get_job_status`, :func:`cancel_job`, and the SSE
        stream.
    """
    workflow = translate_ui_request(
        payload, resources_dir=resources_dir, workflows_dir=workflows_dir
    )
    run_id = generate_run_id(str(workflow.get("id") or "workflow"))
    _register_job(run_id, {"run_id": run_id, "status": "running", "kind": "run"})

    # Persist the submitted config so the Results view can show what was run.
    try:
        run_dir = Path(runs_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        run_config_path(run_dir).write_text(json.dumps({
            "agent": payload.get("agent"),
            "sandbox": str(payload.get("sandbox") or "local"),
            "params": payload.get("params") or {},
            "service": payload.get("service"),
            "case_name": payload.get("case_name"),
            "workflow_path": payload.get("workflow_path"),
            "workflow_id": workflow.get("id"),
            "prompt_mode": payload.get("prompt_mode") or workflow.get("prompt_mode"),
            "agent_timeout_sec": payload.get("agent_timeout_sec"),
            "max_attempts": payload.get("max_attempts"),
            "stage_total": len(workflow.get("stages") or []),
            # Persist the resolved stage + adversary definitions so the Results
            # detail can show every stage even when the workflow was run inline
            # (no saved file) or the file later changes.
            "stages": [
                {
                    "id": s.get("id"),
                    "service": s.get("service"),
                    "case_name": s.get("case_name"),
                    "param_overrides": s.get("param_overrides") or {},
                }
                for s in (workflow.get("stages") or [])
            ],
            "adversary": [
                {
                    "scenario": a.get("scenario"),
                    "inject_at_stage": a.get("inject_at_stage"),
                    "lift_at_stage": a.get("lift_at_stage"),
                    "param_overrides": a.get("param_overrides") or {},
                }
                for a in (workflow.get("adversary") or [])
            ],
        }, indent=2))
    except Exception:
        pass

    def _stage_cb(stage_result: dict[str, Any]) -> None:
        hub.publish(
            run_id,
            {"type": "stage_complete", "run_id": run_id, "stage": stage_result},
        )
        if on_stage_complete is not None:
            try:
                on_stage_complete(stage_result)
            except Exception:
                pass

    def _progress_cb(stage_id: str, message: str) -> None:
        hub.publish(run_id, {
            "type": "progress", "run_id": run_id,
            "stage_id": stage_id, "message": message,
        })

    def _run() -> None:
        try:
            result = run_workflow(
                workflow,
                runs_dir=runs_dir,
                resources_dir=resources_dir,
                agent_name=payload.get("agent"),
                sandbox_mode=str(payload.get("sandbox") or "local"),
                on_stage_complete=_stage_cb,
                on_progress=_progress_cb,
                should_cancel=lambda: run_id in _cancel_requested,
                max_attempts=(int(payload["max_attempts"]) if payload.get("max_attempts") else None),
                run_id=run_id,
            )
            _update_job(run_id, {"status": result.get("status", "complete")})
            hub.publish(run_id, {
                "type": "run_complete",
                "run_id": run_id,
                "status": result.get("status"),
                "summary": result.get("summary"),
            })
        except Exception as exc:
            _update_job(run_id, {"status": "error", "error": str(exc)})
            hub.publish(run_id, {
                "type": "run_complete",
                "run_id": run_id,
                "status": "error",
                "error": str(exc),
            })
        finally:
            with _jobs_lock:
                _cancel_requested.discard(run_id)
            hub.close(run_id)

    threading.Thread(target=_run, daemon=True).start()
    return run_id


def get_job_status(run_id: str) -> dict[str, Any] | None:
    """Return the current status dict for *run_id*, or ``None`` when not found.

    Merges the local job entry with the runtime status from
    ``runtime.service.get_run_status``.
    """
    with _jobs_lock:
        job = _active_jobs.get(run_id)
    if job is None:
        return None

    runtime_status = get_run_status(run_id)
    merged = dict(job)
    if runtime_status:
        merged.update(runtime_status)
    return merged


def cancel_job(run_id: str) -> bool:
    """Request cancellation of a running job and end its event stream.

    Marks the job cancelled in the active jobs table, publishes a
    ``cancelled`` event, and closes the hub stream so any attached SSE
    client terminates. The background workflow may still finish its
    current stage; cancellation is best effort at stage boundaries.

    Returns
    -------
    bool
        ``True`` when the job was found, ``False`` when *run_id* is not
        registered.
    """
    with _jobs_lock:
        job = _active_jobs.get(run_id)
    if job is None:
        return False
    # Signal the running workflow thread to stop: it polls this between stages
    # and during the agent wait, terminates the agent, and ends with a
    # cancelled run_complete (which closes the stream). We do not close the hub
    # here so that final cancelled event still reaches attached clients.
    with _jobs_lock:
        _cancel_requested.add(run_id)
    _update_job(run_id, {"status": "cancelled"})
    hub.publish(run_id, {"type": "cancelled", "run_id": run_id})
    return True


def list_jobs(*, status_filter: str | None = None) -> list[dict[str, Any]]:
    """Return a list of all active job status dicts.

    When *status_filter* is provided, only jobs whose ``status`` matches
    are included.
    """
    with _jobs_lock:
        jobs = list(_active_jobs.values())
    return [
        dict(job)
        for job in jobs
        if status_filter is None or job.get("status") == status_filter
    ]


def reconcile_stale_runs(runs_dir: Path) -> int:
    """Mark on-disk runs still flagged ``running`` as ``interrupted``.

    Called at server startup, when the in-memory job table is empty -- so any
    run whose state file still says ``running`` was orphaned by a previous
    process (e.g. a restart killed its background thread). Without this they
    show as ``running`` forever in the Results list. Returns the count fixed.
    """
    fixed = 0
    if not runs_dir.exists():
        return 0
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        for name in ("workflow_state.json", "run.json"):
            path = run_dir / name
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            if data.get("status") == "running":
                data["status"] = "interrupted"
                try:
                    path.write_text(json.dumps(data, indent=2))
                    fixed += 1
                except Exception:
                    pass
    return fixed
