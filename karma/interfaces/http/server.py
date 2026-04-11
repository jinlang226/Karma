"""
HTTP routes, SSE, static file serving, and the thin backend layer.

This module is intentionally thin. All execution is delegated to
``interfaces.http.jobs`` and ``runtime.service``. No orchestration logic
lives here.

Routes::

    GET  /                          serve index.html
    GET  /static/<path>             serve static files
    POST /api/run                   submit a case or workflow run
    GET  /api/run/<run_id>/status   poll run status
    GET  /api/run/<run_id>/stream   SSE stream of stage events
    POST /api/run/<run_id>/cancel   cancel a running job
    GET  /api/cases                 list available cases by service
    GET  /api/agents                list registered agents
    GET  /api/metrics               list registered metric plugins
    POST /api/judge                 trigger judge on a completed run
"""

from __future__ import annotations

import json
import queue
from pathlib import Path
from typing import Any

from .jobs import submit_job, get_job_status, cancel_job
from ...runtime.service import get_run_status
from ...agents.registry import list_agents
from ...metrics import list_metrics


def create_app(
    *,
    resources_dir: Path,
    runs_dir: Path,
    static_dir: Path | None = None,
) -> Any:
    """Create and return the WSGI application instance.

    Parameters
    ----------
    resources_dir:
        Root resources directory used for case discovery.
    runs_dir:
        Root runs directory used for artifact storage.
    static_dir:
        Directory from which static files are served. Defaults to
        ``karma/static/``.
    """
    ...


def _sse_stream_generator(
    run_id: str,
    event_queue: queue.Queue,
) -> Any:
    """Yield SSE-formatted event strings from *event_queue*.

    Reads from the queue until a ``None`` sentinel is pushed or the queue
    is empty for longer than the idle timeout. Each event is formatted as::

        data: {json}\\n\\n
    """
    ...


def _on_stage_complete_factory(
    run_id: str,
    event_queue: queue.Queue,
) -> Any:
    """Return a callback that pushes stage completion events to *event_queue*.

    The returned callable accepts a stage result dict and enqueues it as a
    JSON SSE event. It is passed to ``runtime.service.submit_run`` so that
    the runtime loop can push progress without importing HTTP code.
    """
    def on_stage_complete(stage_result: dict[str, Any]) -> None:
        try:
            event_queue.put_nowait({
                "type": "stage_complete",
                "run_id": run_id,
                "stage": stage_result,
            })
        except queue.Full:
            pass
    return on_stage_complete


def main(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    resources_dir: Path | None = None,
    runs_dir: Path | None = None,
) -> None:
    """Start the HTTP server.

    Called by ``main.py``. Reads ``KARMA_HOST``, ``KARMA_PORT``,
    ``KARMA_RESOURCES_DIR``, and ``KARMA_RUNS_DIR`` from the environment
    when the corresponding keyword argument is ``None``.
    """
    import os

    resolved_resources = resources_dir or Path(
        os.environ.get("KARMA_RESOURCES_DIR", "resources")
    )
    resolved_runs = runs_dir or Path(os.environ.get("KARMA_RUNS_DIR", "runs"))
    resolved_host = os.environ.get("KARMA_HOST", host)
    resolved_port = int(os.environ.get("KARMA_PORT", port))

    app = create_app(
        resources_dir=resolved_resources,
        runs_dir=resolved_runs,
    )
    app.run(host=resolved_host, port=resolved_port)
