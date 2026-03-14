import time
from unittest.mock import patch

from app.runner import BenchmarkApp


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _ready_state(app):
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "ready",
            "attempts": 0,
            "max_attempts": 3,
            "verification_warnings": [],
            "setup_warnings": [],
        }
    )


def test_run_status_auto_fails_when_attempt_budget_exhausted():
    app = _make_app()
    _ready_state(app)
    app.run_state["attempts"] = 3

    with patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(app, "_maybe_start_cleanup", lambda: None):
        status = app.run_status()

    assert status["status"] == "auto_failed"
    assert status["last_error"] == "Maximum attempts reached"
    assert status["can_submit"] is False


def test_run_status_auto_fails_when_time_limit_exceeded():
    app = _make_app()
    _ready_state(app)
    app.run_state["solve_started_at_ts"] = int(time.time()) - 2

    with patch("app.runner.MAX_TIME_MINUTES", 0), patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(app, "_maybe_start_cleanup", lambda: None):
        status = app.run_status()

    assert status["status"] == "auto_failed"
    assert status["last_error"] == "Time limit exceeded"
    assert status["can_submit"] is False

