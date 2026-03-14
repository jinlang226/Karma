import shutil
import time
from pathlib import Path
from unittest.mock import patch

from app.oracle import resolve_oracle_verify
from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _prepare_verification_run(app, name):
    run_root = RUNS_DIR / name
    run_dir = str(run_root.relative_to(ROOT))
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "verifying",
            "run_dir": run_dir,
            "verification_warnings": [],
            "verification_logs": [f"{run_dir}/verification_1.log"],
            "attempts": 1,
            "max_attempts": 3,
            "solve_started_at_ts": int(time.time()),
        }
    )
    return run_root, Path(run_dir) / "verification_1.log"


def test_resolve_oracle_verify_hooks_defaults():
    cfg = resolve_oracle_verify({})
    assert cfg["before_commands"] == []
    assert cfg["after_commands"] == []
    assert cfg["after_failure_mode"] == "warn"


def test_verification_after_hook_warn_records_warning_and_passes():
    app = _make_app()
    run_root, log_rel = _prepare_verification_run(app, "unit_verification_hook_warn")

    with patch.object(app, "_run_command_list", return_value=True), patch.object(
        app, "_run_command_list_stateless", return_value=False
    ), patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(
        app, "_maybe_start_cleanup", lambda: None
    ):
        app._run_verification(
            wait_cmds=[],
            before_cmds=[{"command": ["bash", "-lc", "true"], "sleep": 0}],
            verify_cmds=[{"command": ["bash", "-lc", "true"], "sleep": 0}],
            after_cmds=[{"command": ["bash", "-lc", "false"], "sleep": 0}],
            after_failure_mode="warn",
            log_path=log_rel,
            attempt=1,
        )

    assert app.run_state["status"] == "passed"
    assert "verification after-hook commands failed" in (app.run_state.get("verification_warnings") or [])
    status = app.run_status()
    assert "verification after-hook commands failed" in status.get("verification_warnings", [])
    shutil.rmtree(run_root, ignore_errors=True)


def test_verification_after_hook_fail_marks_attempt_failed():
    app = _make_app()
    run_root, log_rel = _prepare_verification_run(app, "unit_verification_hook_fail")

    with patch.object(app, "_run_command_list", return_value=True), patch.object(
        app, "_run_command_list_stateless", return_value=False
    ), patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(
        app, "_maybe_start_cleanup", lambda: None
    ):
        app._run_verification(
            wait_cmds=[],
            before_cmds=[],
            verify_cmds=[{"command": ["bash", "-lc", "true"], "sleep": 0}],
            after_cmds=[{"command": ["bash", "-lc", "false"], "sleep": 0}],
            after_failure_mode="fail",
            log_path=log_rel,
            attempt=1,
        )

    assert app.run_state["status"] == "failed"
    assert app.run_state["last_error"] == "verification after-hook commands failed"
    status = app.run_status()
    assert status["status"] == "failed"
    shutil.rmtree(run_root, ignore_errors=True)
