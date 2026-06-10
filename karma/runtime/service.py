"""
Public runtime API for CLI and HTTP adapters.

This is the single entrypoint into the execution core. Neither adapter
implements orchestration logic; both call functions from this module.

Dependency rules:

- This module imports from ``runtime.case``, ``runtime.workflow``,
  ``definitions``, ``environments``, ``agents``, ``sandbox``, and
  ``protocol``.
- ``interfaces.*`` imports from this module; this module never imports
  from ``interfaces.*``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..definitions.workflows import resolve_workflow_rows, single_case_to_workflow
from ..environments.registry import get_environment
from ..agents.registry import resolve_agent
from ..protocol import generate_run_id
from .workflow import run_workflow_loop
from .case import run_stage


# ---------------------------------------------------------------------------
# Run status registry
# ---------------------------------------------------------------------------

_active_runs: dict[str, dict[str, Any]] = {}
_runs_lock = threading.Lock()


def _register_run(run_id: str, meta: dict[str, Any]) -> None:
    """Register *meta* under *run_id* in the active runs table."""
    with _runs_lock:
        _active_runs[run_id] = meta


def _update_run(run_id: str, updates: dict[str, Any]) -> None:
    """Apply *updates* to the entry for *run_id*. No-op when not found."""
    with _runs_lock:
        if run_id in _active_runs:
            _active_runs[run_id].update(updates)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_workflow(
    workflow: dict[str, Any],
    *,
    runs_dir: Path,
    resources_dir: Path,
    agent_name: str | None = None,
    sandbox_mode: str = "local",
    environment_provider: str | None = None,
    environment_config: dict[str, Any] | None = None,
    on_stage_complete: Any | None = None,
    on_progress: Any | None = None,
    run_id: str | None = None,
    stage_failure_mode: str = "terminate",
    final_sweep_mode: str = "auto",
    sandbox_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a workflow synchronously and return the final run result.

    Resolves workflow rows, initializes the environment, and drives the
    workflow loop via ``runtime.workflow``. Blocks until completion.

    Parameters
    ----------
    workflow:
        Normalized workflow dict from ``definitions.workflows``.
    runs_dir:
        Directory under which the run artifact directory is created.
    resources_dir:
        Root resources directory forwarded to row resolution.
    agent_name:
        Registered agent name, or ``None`` for solver/local runs.
    sandbox_mode:
        ``"local"`` or ``"docker"``.
    environment_provider:
        Provider name passed to ``environments.registry.get_environment``.
    environment_config:
        Provider-specific config dict.
    on_stage_complete:
        Optional callable invoked with the stage result dict after each
        stage. Used by the HTTP SSE path to stream progress.
    run_id:
        Explicit run ID. A timestamped ID is generated when ``None``.

    Returns
    -------
    dict
        Keys: ``run_id``, ``status`` (``"complete"``, ``"failed"``, or
        ``"error"``), ``stages`` (list[dict]), ``summary`` (dict).
    """
    from ..definitions.workflows import resolve_workflow_rows

    effective_run_id = run_id or generate_run_id(str(workflow.get("id") or "workflow"))
    run_dir = runs_dir / effective_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _register_run(effective_run_id, {"run_id": effective_run_id, "status": "running", "stages": []})

    try:
        rows = resolve_workflow_rows(workflow, resources_dir=resources_dir)
        env = get_environment(environment_provider, config=environment_config)
        agent_meta = resolve_agent(agent_name, sandbox_mode=sandbox_mode)
        # Docker image provisioning: override the tag and/or build the image on
        # demand (old --agent-tag / --docker-image / --agent-build).
        opts = sandbox_options or {}
        image_override = opts.get("image_tag")
        if image_override:
            agent_meta["image_tag"] = image_override
        if opts.get("build_image") and sandbox_mode == "docker" and agent_meta.get("folder"):
            from ..sandbox import build_agent_image
            build_agent_image(agent_meta, agent_meta.get("image_tag") or "karma-agent:latest", run_dir)
        prompt_mode = str(workflow.get("prompt_mode") or "progressive")

        result = run_workflow_loop(
            rows,
            run_id=effective_run_id,
            run_dir=run_dir,
            resources_dir=resources_dir,
            agent_meta=agent_meta,
            sandbox_mode=sandbox_mode,
            environment=env,
            prompt_mode=prompt_mode,
            on_stage_complete=on_stage_complete,
            on_progress=on_progress,
            stage_failure_mode=stage_failure_mode,
            final_sweep_mode=final_sweep_mode,
            sandbox_options=sandbox_options,
        )
        result["summary"] = {
            "total_stages": len(rows),
            "passed": sum(1 for s in result.get("stages", []) if s.get("status") == "pass"),
            "failed": sum(1 for s in result.get("stages", []) if s.get("status") in ("fail", "error", "timeout")),
        }
        _update_run(effective_run_id, result)
        return result

    except Exception as exc:
        error_result: dict[str, Any] = {
            "run_id": effective_run_id,
            "status": "error",
            "stages": [],
            "summary": {},
            "error": str(exc),
        }
        _update_run(effective_run_id, error_result)
        return error_result


def run_case(
    service: str,
    case_name: str,
    *,
    runs_dir: Path,
    resources_dir: Path,
    param_overrides: dict[str, Any] | None = None,
    agent_name: str | None = None,
    sandbox_mode: str = "local",
    environment_provider: str | None = None,
    environment_config: dict[str, Any] | None = None,
    namespace_roles: list[str] | None = None,
    agent_timeout_sec: int = 900,
    max_attempts: int | None = None,
    stage_failure_mode: str = "terminate",
    final_sweep_mode: str = "auto",
    sandbox_options: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute a single case as a 1-stage workflow and return the run result.

    Converts the request into a workflow via
    ``definitions.workflows.single_case_to_workflow`` and delegates to
    :func:`run_workflow`, ensuring both the CLI and HTTP paths use
    identical runtime code.

    Returns the same result dict as :func:`run_workflow`.
    """
    # --max-attempts is a total attempt cap; the loop runs retries + 1 attempts.
    retries = max(0, max_attempts - 1) if max_attempts else 0
    workflow = single_case_to_workflow(
        service,
        case_name,
        param_overrides,
        agent_timeout_sec=agent_timeout_sec,
        namespace_roles=namespace_roles,
        retries=retries,
    )
    return run_workflow(
        workflow,
        runs_dir=runs_dir,
        resources_dir=resources_dir,
        agent_name=agent_name,
        sandbox_mode=sandbox_mode,
        environment_provider=environment_provider,
        environment_config=environment_config,
        stage_failure_mode=stage_failure_mode,
        final_sweep_mode=final_sweep_mode,
        sandbox_options=sandbox_options,
        run_id=run_id,
    )


def submit_run(
    workflow: dict[str, Any],
    *,
    runs_dir: Path,
    resources_dir: Path,
    agent_name: str | None = None,
    sandbox_mode: str = "local",
    environment_provider: str | None = None,
    on_stage_complete: Any | None = None,
) -> str:
    """Submit a workflow run asynchronously and return the run ID immediately.

    Starts :func:`run_workflow` in a background thread. Progress can be
    polled via :func:`get_run_status`.

    Returns
    -------
    str
        Run ID that can be passed to :func:`get_run_status` and
        :func:`cleanup_run`.
    """
    effective_run_id = generate_run_id(str(workflow.get("id") or "workflow"))
    _register_run(effective_run_id, {"run_id": effective_run_id, "status": "running", "stages": []})

    def _run() -> None:
        run_workflow(
            workflow,
            runs_dir=runs_dir,
            resources_dir=resources_dir,
            agent_name=agent_name,
            sandbox_mode=sandbox_mode,
            environment_provider=environment_provider,
            on_stage_complete=on_stage_complete,
            run_id=effective_run_id,
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return effective_run_id


def get_run_status(run_id: str) -> dict[str, Any] | None:
    """Return the current status dict for *run_id*, or ``None`` when not found.

    The returned dict has the same shape as the result from
    :func:`run_workflow`, with ``status`` set to ``"running"`` while the
    run is still in progress.
    """
    with _runs_lock:
        entry = _active_runs.get(run_id)
    return dict(entry) if entry else None


def cleanup_run(run_id: str, *, runs_dir: Path) -> None:
    """Remove *run_id* from the active runs table.

    Safe to call on a run that has already been cleaned up or never
    registered.
    """
    with _runs_lock:
        _active_runs.pop(run_id, None)
