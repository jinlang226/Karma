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

from .jobs import submit_job, get_job_status, cancel_job, list_jobs
from .events import hub
from . import catalog
from ...runtime.service import get_run_status
from ...agents.registry import list_agents
from ...metrics import list_metrics


def create_app(
    *,
    resources_dir: Path,
    runs_dir: Path,
    workflows_dir: Path | None = None,
    static_dir: Path | None = None,
) -> Any:
    """Create and return the WSGI application instance.

    Registers all REST and SSE routes. Static files are served from
    *static_dir* (defaults to ``karma/static/`` relative to this package).
    Each POST /api/run call creates a per-request event queue that is
    kept in a closure-local dict for the matching SSE stream endpoint.

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
    try:
        from flask import Flask, jsonify, request, Response, send_from_directory
    except ImportError:
        raise RuntimeError(
            "Flask is required for the HTTP interface. "
            "Install it with: pip install flask"
        )

    if static_dir is None:
        static_dir = Path(__file__).parent.parent.parent / "static"
    if workflows_dir is None:
        workflows_dir = Path("workflows")

    app = Flask(__name__, static_folder=None)

    @app.route("/")
    def index():  # type: ignore[return]
        idx = Path(static_dir) / "index.html"
        if idx.exists():
            return idx.read_text(), 200, {"Content-Type": "text/html"}
        return "<h1>KARMA</h1>", 200

    @app.route("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(str(static_dir), filename)

    @app.route("/api/run", methods=["POST"])
    def api_run():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            run_id = submit_job(
                payload,
                runs_dir=runs_dir,
                resources_dir=resources_dir,
            )
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"run_id": run_id}), 201

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/api/run/<run_id>/status")
    def api_run_status(run_id):
        status = get_job_status(run_id)
        if status is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(status)

    @app.route("/api/run/<run_id>/stream")
    def api_run_stream(run_id):
        if not hub.is_known(run_id):
            return jsonify({"error": "not found"}), 404
        return Response(
            _sse_stream(run_id),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
            },
        )

    @app.route("/api/run/<run_id>/cancel", methods=["POST"])
    def api_run_cancel(run_id):
        if not cancel_job(run_id):
            return jsonify({"error": "not found"}), 404
        return jsonify({"run_id": run_id, "status": "cancelled"})

    @app.route("/api/cases")
    def api_cases():
        return jsonify(catalog.list_cases_by_service(Path(resources_dir)))

    @app.route("/api/services")
    def api_services():
        return jsonify({
            "services": catalog.list_services(Path(resources_dir)),
            "cluster": catalog.cluster_status(),
        })

    @app.route("/api/cases/<service>/<case_name>")
    def api_case_detail(service, case_name):
        try:
            detail = catalog.get_case_detail(Path(resources_dir), service, case_name)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify(detail)

    @app.route("/api/runs")
    def api_runs():
        return jsonify(catalog.list_runs(Path(runs_dir)))

    @app.route("/api/workflows")
    def api_workflows():
        return jsonify(
            catalog.list_workflow_files(Path(workflows_dir), Path(resources_dir))
        )

    @app.route("/api/jobs")
    def api_jobs():
        return jsonify(list_jobs())

    @app.route("/api/agents")
    def api_agents():
        return jsonify(list_agents())

    @app.route("/api/metrics")
    def api_metrics():
        return jsonify(list_metrics())

    @app.route("/api/judge", methods=["POST"])
    def api_judge():
        from ...judge.engine import run_judge, run_judge_batch
        payload = request.get_json(force=True, silent=True) or {}
        run_dir_str = str(payload.get("run_dir") or "")
        if not run_dir_str:
            return jsonify({"error": "run_dir is required"}), 400
        run_dir_path = Path(run_dir_str)
        if not run_dir_path.exists():
            return jsonify({"error": "run_dir not found"}), 404
        stage_id = payload.get("stage_id")
        try:
            if stage_id:
                result = run_judge(
                    run_dir_path, str(stage_id),
                    judge_model=payload.get("model"),
                )
            else:
                result = run_judge_batch(
                    run_dir_path, judge_model=payload.get("model")
                )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify(result)

    return app


_HEARTBEAT_INTERVAL = 15.0


def _sse_stream(stream_id: str) -> Any:
    """Yield SSE-formatted strings for *stream_id* from the shared hub.

    Subscribes to :data:`events.hub`, which first replays the buffered
    history (so a late or reconnecting client sees prior events) and then
    delivers live events. A comment heartbeat is emitted whenever the
    stream is idle for ``_HEARTBEAT_INTERVAL`` seconds so intermediaries do
    not drop the connection. The ``None`` sentinel ends the stream with a
    terminal ``done`` event. The subscription is always released on exit.
    """
    q = hub.subscribe(stream_id)
    try:
        while True:
            try:
                event = q.get(timeout=_HEARTBEAT_INTERVAL)
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue
            if event is None:
                yield f"data: {json.dumps({'type': 'done', 'run_id': stream_id})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        hub.unsubscribe(stream_id, q)


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
    resolved_workflows = Path(os.environ.get("KARMA_WORKFLOWS_DIR", "workflows"))
    resolved_host = os.environ.get("KARMA_HOST", host)
    resolved_port = int(os.environ.get("KARMA_PORT", port))

    app = create_app(
        resources_dir=resolved_resources,
        runs_dir=resolved_runs,
        workflows_dir=resolved_workflows,
    )
    app.run(host=resolved_host, port=resolved_port)
