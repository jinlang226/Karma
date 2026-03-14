from pathlib import Path
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR


class DummyThread:
    last = None

    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False
        DummyThread.last = self

    def start(self):
        self.started = True


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_cleanup_run_tracks_running_status():
    app = _make_app()
    app.run_state = app._empty_run_state()
    app.run_state["status"] = "failed"
    app.run_state["service"] = "smoke"
    app.run_state["case"] = "cleanup_tracking"
    app.run_state["test_file"] = "test.yaml"
    app.run_state["external_metrics"] = []
    app.run_state["attempts"] = 1

    run_dir_path = RUNS_DIR / "unit-cleanup-tracking"
    run_dir_path.mkdir(parents=True, exist_ok=True)
    app.run_state["run_dir"] = str(run_dir_path.relative_to(ROOT))
    app.run_state["data"] = {
        "cleanUpCommands": [
            {"command": ["bash", "-lc", "echo cleanup"], "sleep": 0},
        ]
    }

    with patch.object(app, "_stop_proxy_trace", lambda: None), patch(
        "app.runner_core.post_run.threading.Thread", DummyThread
    ):
        res = app.cleanup_run()

    assert res.get("status") == "cleaning"
    assert app.run_state.get("cleanup_status") == "running"
    assert app.run_state.get("cleanup_log")
    assert app.run_state.get("status") == "failed"
    assert DummyThread.last is not None
    assert DummyThread.last.started is True
    assert isinstance(DummyThread.last.args[0], list)
    assert isinstance(DummyThread.last.args[1], Path)
