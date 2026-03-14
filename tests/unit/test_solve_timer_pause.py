from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.runner import BenchmarkApp


BASE_TIME = datetime(2026, 2, 9, 0, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, base_time):
        self.base_time = base_time
        self.offset = 0

    def now(self):
        return self.base_time + timedelta(seconds=self.offset)


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _setup_solve_state(app, start_ts):
    app.run_state = app._empty_run_state()
    app.run_state["solve_started_at_ts"] = start_ts
    app.run_state["solve_pause_total_sec"] = 0
    app.run_state["solve_pause_started_at_ts"] = None
    app.run_state["solve_paused"] = False


def test_solve_elapsed_no_pause():
    clock = FakeClock(BASE_TIME)
    with patch("app.runner_core.post_run.utc_now", clock.now):
        app = _make_app()
        _setup_solve_state(app, int(BASE_TIME.timestamp()))
        clock.offset = 120
        assert app._solve_elapsed_seconds() == 120


def test_pause_and_resume_excludes_verification_time():
    clock = FakeClock(BASE_TIME)
    with patch("app.runner_core.post_run.utc_now", clock.now):
        app = _make_app()
        _setup_solve_state(app, int(BASE_TIME.timestamp()))

        clock.offset = 60
        app._pause_solve_timer()
        assert app.run_state["solve_paused"] is True

        clock.offset = 360
        assert app._solve_elapsed_seconds() == 60

        app._resume_solve_timer()
        assert app.run_state["solve_paused"] is False
        assert app.run_state["solve_pause_total_sec"] == 300

        clock.offset = 420
        assert app._solve_elapsed_seconds() == 120


def test_multiple_pause_cycles_accumulate():
    clock = FakeClock(BASE_TIME)
    with patch("app.runner_core.post_run.utc_now", clock.now):
        app = _make_app()
        _setup_solve_state(app, int(BASE_TIME.timestamp()))

        clock.offset = 30
        app._pause_solve_timer()
        clock.offset = 90
        app._resume_solve_timer()

        clock.offset = 120
        app._pause_solve_timer()
        clock.offset = 180
        app._resume_solve_timer()

        clock.offset = 210
        assert app.run_state["solve_pause_total_sec"] == 120
        assert app._solve_elapsed_seconds() == 90


def test_pause_without_start_is_noop():
    clock = FakeClock(BASE_TIME)
    with patch("app.runner_core.post_run.utc_now", clock.now):
        app = _make_app()
        app.run_state = app._empty_run_state()
        app._pause_solve_timer()
        assert app.run_state["solve_paused"] is False
        assert app.run_state["solve_pause_started_at_ts"] is None


def test_resume_without_pause_is_noop():
    clock = FakeClock(BASE_TIME)
    with patch("app.runner_core.post_run.utc_now", clock.now):
        app = _make_app()
        _setup_solve_state(app, int(BASE_TIME.timestamp()))
        app._resume_solve_timer()
        assert app.run_state["solve_paused"] is False
        assert app.run_state["solve_pause_total_sec"] == 0


def test_time_limit_uses_solve_time_only():
    clock = FakeClock(BASE_TIME)
    with patch("app.runner_core.post_run.utc_now", clock.now), patch("app.runner.MAX_TIME_MINUTES", 1):
        app = _make_app()
        _setup_solve_state(app, int(BASE_TIME.timestamp()))
        app.run_state["max_attempts"] = 10
        app.run_state["attempts"] = 0
        app.run_state["status"] = "ready"

        clock.offset = 10
        app._pause_solve_timer()
        clock.offset = 300
        app._resume_solve_timer()
        clock.offset = 349

        with patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
            app, "_maybe_compute_metrics", lambda: None
        ), patch.object(app, "_maybe_start_cleanup", lambda: None):
            assert app._auto_fail_if_limits_exceeded() is False

        clock.offset = 351
        with patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
            app, "_maybe_compute_metrics", lambda: None
        ), patch.object(app, "_maybe_start_cleanup", lambda: None):
            assert app._auto_fail_if_limits_exceeded() is True
            assert app.run_state["status"] == "auto_failed"


def test_max_attempts_across_values():
    clock = FakeClock(BASE_TIME)
    with patch("app.runner_core.post_run.utc_now", clock.now):
        for max_attempts in (1, 2, 10):
            app = _make_app()
            _setup_solve_state(app, int(BASE_TIME.timestamp()))
            app.run_state["max_attempts"] = max_attempts
            app.run_state["attempts"] = max_attempts
            app.run_state["status"] = "ready"

            with patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
                app, "_maybe_compute_metrics", lambda: None
            ), patch.object(app, "_maybe_start_cleanup", lambda: None):
                assert app._auto_fail_if_limits_exceeded() is True
                assert app.run_state["status"] == "auto_failed"
