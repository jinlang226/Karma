import json
import shutil
from pathlib import Path
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _prepare_run_dir(name):
    run_root = RUNS_DIR / name
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


def test_run_cleanup_async_updates_status_done_and_triggers_post_metrics():
    app = _make_app()
    run_root = _prepare_run_dir("unit_runner_r6_cleanup_done")
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "failed",
            "run_dir": str(run_root.relative_to(ROOT)),
            "cleanup_status": "running",
        }
    )
    calls = []

    with patch.object(app, "_run_command_list_stateless", return_value=True), patch.object(
        app, "_post_cleanup_metrics_from_state", side_effect=lambda: calls.append("state")
    ):
        app._run_cleanup_async([{"command": ["bash", "-lc", "echo cleanup"], "sleep": 0}], run_root / "cleanup.log")

    try:
        assert app.run_state["cleanup_status"] == "done"
        assert app.run_state["cleanup_finished_at"] is not None
        assert app.run_state["cleanup_finished_at_ts"] is not None
        assert calls == ["state"]
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_run_cleanup_async_updates_status_failed_when_any_cleanup_check_fails():
    app = _make_app()
    run_root = _prepare_run_dir("unit_runner_r6_cleanup_failed")
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "failed",
            "run_dir": str(run_root.relative_to(ROOT)),
            "cleanup_status": "running",
        }
    )

    with patch.object(app, "_run_command_list_stateless", return_value=False), patch.object(
        app, "_post_cleanup_metrics_from_state", lambda: None
    ):
        app._run_cleanup_async([{"command": ["bash", "-lc", "echo cleanup"], "sleep": 0}], run_root / "cleanup.log")

    try:
        assert app.run_state["cleanup_status"] == "failed"
        assert app.run_state["cleanup_finished_at"] is not None
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_run_cleanup_async_with_context_uses_context_metric_path_only():
    app = _make_app()
    run_root = _prepare_run_dir("unit_runner_r6_cleanup_context")
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "failed",
            "run_dir": str(run_root.relative_to(ROOT)),
            "cleanup_status": "running",
        }
    )
    calls = []

    with patch.object(app, "_run_command_list_stateless", return_value=True), patch.object(
        app, "_post_cleanup_metrics_from_state", side_effect=lambda: calls.append("state")
    ), patch.object(
        app, "_post_cleanup_metrics_from_context", side_effect=lambda ctx: calls.append(("context", ctx.get("run_dir")))
    ):
        app._run_cleanup_async(
            [{"command": ["bash", "-lc", "echo cleanup"], "sleep": 0}],
            run_root / "cleanup.log",
            context={
                "external_metrics": ["residual_drift"],
                "run_dir": str(run_root.relative_to(ROOT)),
                "service": "svc",
                "case": "case",
            },
        )

    try:
        assert calls == [("context", str(run_root.relative_to(ROOT)))]
        # Context mode should not mutate the active run lifecycle.
        assert app.run_state["cleanup_status"] == "running"
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_write_metrics_merges_existing_payload_and_updates_run_state_path():
    app = _make_app()
    run_root = _prepare_run_dir("unit_runner_r6_metrics_merge")
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "passed",
            "run_dir": str(run_root.relative_to(ROOT)),
        }
    )
    metrics_path = run_root / "external_metrics.json"
    metrics_path.write_text(json.dumps({"existing_metric": {"score": 1.0}}), encoding="utf-8")

    try:
        app._write_metrics({"new_metric": {"score": 2.0}})
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        assert payload["existing_metric"]["score"] == 1.0
        assert payload["new_metric"]["score"] == 2.0
        assert app.run_state["metrics_path"] == str(metrics_path.relative_to(ROOT))
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_cleanup_run_returns_no_cleanup_and_resets_state_when_no_commands():
    app = _make_app()
    run_root = _prepare_run_dir("unit_runner_r6_no_cleanup")
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "passed",
            "run_dir": str(run_root.relative_to(ROOT)),
            "data": {},
        }
    )

    try:
        result = app.cleanup_run()
        assert result == {"status": "no_cleanup"}
        assert app.run_state["status"] == "idle"
        assert app.run_state["run_dir"] is None
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_cleanup_run_done_status_returns_already_cleaned_and_resets_state():
    app = _make_app()
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "failed",
            "cleanup_status": "done",
        }
    )

    result = app.cleanup_run()
    assert result == {"status": "already_cleaned"}
    assert app.run_state["status"] == "idle"
    assert app.run_state["cleanup_status"] is None
