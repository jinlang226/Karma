import shutil
import uuid
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _prepare_setup_run(app, name):
    run_root = RUNS_DIR / name
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "setup_running",
            "run_dir": str(run_root.relative_to(ROOT)),
            "setup_log": str((run_root / "preoperation.log").relative_to(ROOT)),
            "setup_warnings": [],
            "data": {},
        }
    )
    return run_root


def test_setup_check_requires_consecutive_pass_window_before_ready():
    app = _make_app()
    run_root = _prepare_setup_run(app, f"it_setup_window_{uuid.uuid4().hex[:8]}")
    cfg = {
        "mode": "required",
        "budget_sec": 10,
        "poll_sec": 1,
        "consecutive_passes": 3,
        "commands": [{"command": ["bash", "-lc", "echo probe"], "timeout_sec": 1, "sleep": 0}],
    }
    records = []
    sequence = [True, True, False, True, True, True]

    def _next(*_args, **_kwargs):
        return sequence.pop(0) if sequence else True

    with patch.object(app, "_run_command_list_stateless", side_effect=_next):
        ok = app._run_setup_check_loop(
            "precondition_check",
            cfg,
            run_root / "setup_precondition_check.log",
            stage="setup_check",
            records=records,
        )

    try:
        assert ok is True
        assert records[-1]["result"] == "passed"
        assert records[-1]["attempts"] == 6
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_setup_check_fails_when_window_not_stable_within_budget():
    app = _make_app()
    run_root = _prepare_setup_run(app, f"it_setup_window_fail_{uuid.uuid4().hex[:8]}")
    cfg = {
        "mode": "required",
        "budget_sec": 1,
        "poll_sec": 0,
        "consecutive_passes": 2,
        "commands": [{"command": ["bash", "-lc", "echo probe"], "timeout_sec": 1, "sleep": 0}],
    }
    records = []
    sequence = [True, False, True, False, True, False]

    def _next(*_args, **_kwargs):
        return sequence.pop(0) if sequence else False

    with patch.object(app, "_run_command_list_stateless", side_effect=_next), patch.object(
        app, "_stop_proxy_trace", lambda: None
    ), patch.object(app, "_maybe_compute_metrics", lambda: None), patch.object(
        app, "_maybe_start_cleanup", lambda: None
    ):
        ok = app._run_setup_check_loop(
            "precondition_check",
            cfg,
            run_root / "setup_precondition_check.log",
            stage="setup_check",
            records=records,
        )

    try:
        assert ok is False
        assert app.run_state["status"] == "setup_failed"
        assert records[-1]["result"] == "failed"
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
