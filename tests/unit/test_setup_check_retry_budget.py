import shutil
from pathlib import Path
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _prepare_run(app, name):
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


def test_run_setup_check_loop_required_timeout_sets_setup_failed():
    app = _make_app()
    run_root = _prepare_run(app, "unit_setup_budget_required")
    records = []
    cfg = {
        "mode": "required",
        "budget_sec": 1,
        "poll_sec": 1,
        "commands": [{"command": ["bash", "-lc", "exit 1"], "timeout_sec": 1, "sleep": 0}],
    }
    with patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(app, "_maybe_start_cleanup", lambda: None):
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
        assert records
        assert records[-1]["result"] == "failed"
        assert records[-1]["attempts"] >= 1
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_run_setup_check_loop_warn_timeout_records_warning_and_continues():
    app = _make_app()
    run_root = _prepare_run(app, "unit_setup_budget_warn")
    records = []
    cfg = {
        "mode": "warn",
        "budget_sec": 1,
        "poll_sec": 1,
        "commands": [{"command": ["bash", "-lc", "exit 1"], "timeout_sec": 1, "sleep": 0}],
    }
    with patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(app, "_maybe_start_cleanup", lambda: None):
        ok = app._run_setup_check_loop(
            "check:dummy",
            cfg,
            run_root / "setup_check_warn.log",
            stage="setup_check",
            records=records,
        )

    try:
        assert ok is True
        assert app.run_state["status"] == "setup_running"
        assert records
        assert records[-1]["result"] == "warn"
        assert app.run_state.get("setup_warnings")
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_run_setup_check_loop_retries_until_success_within_budget():
    app = _make_app()
    run_root = _prepare_run(app, "unit_setup_retry_success")
    records = []
    cfg = {
        "mode": "required",
        "budget_sec": 5,
        "poll_sec": 1,
        "commands": [{"command": ["bash", "-lc", "echo ignored"], "timeout_sec": 1, "sleep": 0}],
        "consecutive_passes": 1,
    }

    sequence = [False, False, True]

    def _next(*_args, **_kwargs):
        if sequence:
            return sequence.pop(0)
        return True

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
        assert app.run_state["status"] == "setup_running"
        assert records[-1]["result"] == "passed"
        assert records[-1]["attempts"] == 3
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
