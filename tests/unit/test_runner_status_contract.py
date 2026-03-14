from unittest.mock import patch

from app.runner import BenchmarkApp


REQUIRED_RUN_STATUS_KEYS = {
    "status",
    "case",
    "attempts",
    "max_attempts",
    "elapsed_seconds",
    "time_limit_seconds",
    "run_dir",
    "setup_log",
    "cleanup_log",
    "cleanup_status",
    "verification_logs",
    "current_step",
    "last_error",
    "metrics_path",
    "cluster_ok",
    "cluster_error",
    "has_verification",
    "can_submit",
    "verification_warnings",
    "resolved_params",
    "setup_timeout_auto_sec",
    "setup_timeout_auto_breakdown",
    "setup_phase",
    "setup_warnings",
    "setup_checks_path",
    "defer_cleanup",
    "skip_precondition_unit_ids",
    "last_verification_kind",
    "last_verification_step",
    "namespace_context",
    "namespace_lifecycle_owner",
}


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _seed_run_state(app, status):
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": status,
            "attempts": 1,
            "max_attempts": 3,
            "verification_warnings": [],
            "resolved_params": {},
            "setup_warnings": [],
            "skip_precondition_unit_ids": [],
        }
    )
    if status in ("ready", "failed"):
        # Avoid auto-limit transitions inside run_status().
        app.run_state["attempts"] = 0
        app.run_state["solve_started_at"] = None
        app.run_state["solve_started_at_ts"] = None
    return app.run_status()


def test_run_status_idle_contract_keys():
    app = _make_app()
    status = app.run_status()

    assert REQUIRED_RUN_STATUS_KEYS.issubset(set(status.keys()))
    assert status["status"] == "idle"
    assert status["can_submit"] is False
    assert status["verification_warnings"] == []
    assert status["setup_warnings"] == []
    assert isinstance(status["namespace_context"], dict)
    assert isinstance(status["namespace_lifecycle_owner"], str)


def test_run_status_contract_keys_across_phases():
    app = _make_app()
    for phase in ("setup_running", "ready", "verifying", "failed", "passed", "auto_failed", "setup_failed"):
        status = _seed_run_state(app, phase)
        assert REQUIRED_RUN_STATUS_KEYS.issubset(set(status.keys()))
        assert status["status"] == phase
        expected_can_submit = phase in ("ready", "failed")
        assert status["can_submit"] is expected_can_submit
        assert isinstance(status["verification_warnings"], list)
        assert isinstance(status["setup_warnings"], list)
        assert isinstance(status["namespace_context"], dict)
        assert isinstance(status["namespace_lifecycle_owner"], str)


def test_runner_stream_snapshot_minimal_schema_contract():
    app = _make_app()

    workflow = app.get_workflow_stream_snapshot()
    assert workflow.get("schema") == "workflow_stream.v2"
    assert isinstance(workflow.get("seq"), int)
    assert isinstance(workflow.get("server_epoch_ms"), int)
    assert isinstance(workflow.get("jobs"), list)

    judge = app.get_judge_stream_snapshot()
    assert isinstance(judge.get("seq"), int)
    assert isinstance(judge.get("jobs"), list)
