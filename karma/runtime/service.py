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
    run_id: str | None = None,
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
    ...


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
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute a single case as a 1-stage workflow and return the run result.

    Converts the request into a workflow via
    ``definitions.workflows.single_case_to_workflow`` and delegates to
    :func:`run_workflow`, ensuring both the CLI and HTTP paths use
    identical runtime code.

    Returns the same result dict as :func:`run_workflow`.
    """
    workflow = single_case_to_workflow(
        service,
        case_name,
        param_overrides,
        agent_timeout_sec=agent_timeout_sec,
        namespace_roles=namespace_roles,
    )
    return run_workflow(
        workflow,
        runs_dir=runs_dir,
        resources_dir=resources_dir,
        agent_name=agent_name,
        sandbox_mode=sandbox_mode,
        environment_provider=environment_provider,
        environment_config=environment_config,
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
    ...


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
