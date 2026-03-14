import shutil
import time
from pathlib import Path
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _prepare_ready_run(app, name, data=None):
    run_root = RUNS_DIR / name
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "ready",
            "run_dir": str(run_root.relative_to(ROOT)),
            "verification_logs": [],
            "verification_warnings": [],
            "attempts": 0,
            "max_attempts": 3,
            "data": data or {},
            "solve_started_at_ts": int(time.time()),
            "solve_pause_total_sec": 0,
            "solve_pause_started_at_ts": None,
            "solve_paused": False,
        }
    )
    return run_root


def test_submit_run_transitions_to_verifying_and_tracks_attempt_log():
    app = _make_app()
    run_root = _prepare_ready_run(
        app,
        "unit_runner_r5_submit_transition",
        data={
            "oracle": {
                "verify": {
                    "commands": [{"command": ["bash", "-lc", "echo ok"], "sleep": 0}],
                }
            }
        },
    )

    with patch.object(app, "_auto_fail_if_limits_exceeded", return_value=False), patch(
        "app.runner_core.run_flow.threading.Thread.start", lambda self: None
    ):
        result = app.submit_run()

    try:
        assert result == {"status": "verifying"}
        assert app.run_state["status"] == "verifying"
        assert app.run_state["attempts"] == 1
        assert app.run_state["solve_paused"] is True
        assert app.run_state["verification_logs"] == [f"{app.run_state['run_dir']}/verification_1.log"]
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_submit_run_without_oracle_commands_returns_warning():
    app = _make_app()
    run_root = _prepare_ready_run(app, "unit_runner_r5_submit_missing_verify", data={})

    with patch.object(app, "_auto_fail_if_limits_exceeded", return_value=False):
        result = app.submit_run()

    try:
        assert "warning" in result
        assert app.run_state["status"] == "ready"
        assert app.run_state["attempts"] == 0
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_run_verification_classifies_oracle_timeout():
    app = _make_app()
    run_root = _prepare_ready_run(app, "unit_runner_r5_oracle_timeout")
    app.run_state.update(
        {
            "status": "verifying",
            "attempts": 1,
            "verification_logs": [f"{app.run_state['run_dir']}/verification_1.log"],
            "last_error": "Command timed out after 12s",
        }
    )
    log_path = Path(app.run_state["run_dir"]) / "verification_1.log"

    with patch.object(app, "_run_command_list", return_value=False), patch.object(
        app, "_run_command_list_stateless", return_value=True
    ):
        app._run_verification(
            wait_cmds=[],
            before_cmds=[],
            verify_cmds=[{"command": ["bash", "-lc", "false"], "sleep": 0}],
            after_cmds=[],
            after_failure_mode="warn",
            log_path=log_path,
            attempt=1,
        )

    try:
        assert app.run_state["status"] == "failed"
        assert app.run_state["last_verification_kind"] == "oracle_timeout"
        assert app.run_state["last_verification_step"] == "oracle"
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_run_verification_before_hook_failure_marks_harness_error():
    app = _make_app()
    run_root = _prepare_ready_run(app, "unit_runner_r5_before_hook_fail")
    app.run_state.update(
        {
            "status": "verifying",
            "attempts": 1,
            "verification_logs": [f"{app.run_state['run_dir']}/verification_1.log"],
            "last_error": None,
        }
    )
    log_path = Path(app.run_state["run_dir"]) / "verification_1.log"

    with patch.object(app, "_run_command_list", return_value=False), patch.object(
        app, "_run_command_list_stateless", return_value=True
    ):
        app._run_verification(
            wait_cmds=[],
            before_cmds=[{"command": ["bash", "-lc", "false"], "sleep": 0}],
            verify_cmds=[{"command": ["bash", "-lc", "true"], "sleep": 0}],
            after_cmds=[],
            after_failure_mode="warn",
            log_path=log_path,
            attempt=1,
        )

    try:
        assert app.run_state["status"] == "failed"
        assert app.run_state["last_verification_kind"] == "oracle_harness_error"
        assert app.run_state["last_verification_step"] == "before_hook"
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_abort_and_finalize_paths_keep_status_contract():
    app = _make_app()
    run_root = _prepare_ready_run(app, "unit_runner_r5_abort_finalize")
    with patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(app, "_maybe_start_cleanup", lambda: None):
        aborted = app.abort_run(reason="manual stop", exit_code=9)
    try:
        assert aborted["status"] == "aborted"
        assert aborted["error"] == "manual stop (exit_code=9)"
        assert app.run_state["status"] == "auto_failed"
        assert app.run_state["last_error"] == "manual stop (exit_code=9)"
    finally:
        shutil.rmtree(run_root, ignore_errors=True)

    app2 = _make_app()
    run_root2 = _prepare_ready_run(app2, "unit_runner_r5_finalize_pass")
    with patch.object(app2, "_stop_proxy_trace", lambda: None), patch.object(
        app2, "_maybe_compute_metrics", lambda: None
    ), patch.object(app2, "_maybe_start_cleanup", lambda: None):
        final = app2.finalize_active_run_without_submit(status="passed")
    try:
        assert final["status"] == "passed"
        assert app2.run_state["status"] == "passed"
        assert app2.run_state["finished_at"] is not None
    finally:
        shutil.rmtree(run_root2, ignore_errors=True)
