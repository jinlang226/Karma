import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_cleanup_async_marks_run_failed_when_cleanup_commands_fail():
    app = _make_app()
    run_root = RUNS_DIR / f"it_docker_cleanup_{uuid.uuid4().hex[:8]}"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)

    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "agent_failed",
            "run_dir": str(run_root.relative_to(ROOT)),
            "service": "rabbitmq-experiments",
            "case": "manual_monitoring",
            "cleanup_status": "running",
        }
    )
    cleanup_log = run_root / "cleanup.log"

    with patch.object(app, "_run_command_list_stateless", return_value=False), patch.object(
        app, "_post_cleanup_metrics_from_state", lambda: None
    ):
        app._run_cleanup_async(
            [{"command": ["bash", "-lc", "exit 1"], "timeout_sec": 1, "sleep": 0}],
            cleanup_log,
        )

    try:
        assert app.run_state["status"] == "agent_failed"
        assert app.run_state["cleanup_status"] == "failed"
        assert app.run_state.get("cleanup_finished_at") is not None
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
