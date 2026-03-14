import json
import shutil
import uuid
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


def test_setup_allows_intentional_degraded_topology_when_check_expects_it():
    app = _make_app()
    run_root = _prepare_run(app, f"it_setup_degraded_{uuid.uuid4().hex[:8]}")
    degraded_marker = run_root / "degraded_expected.ok"

    app.run_state["data"] = {
        "preconditionUnits": [
            {
                "id": "degraded_baseline",
                "probe": {"command": ["bash", "-lc", f"test -f {degraded_marker}"], "timeout_sec": 2},
                "apply": {
                    "command": ["bash", "-lc", f"mkdir -p {Path(degraded_marker).parent}; touch {degraded_marker}"],
                    "timeout_sec": 2,
                },
                "verify": {"command": ["bash", "-lc", f"test -f {degraded_marker}"], "timeout_sec": 2},
            }
        ],
        "setup_self_check": {
            "precondition_check": {
                "mode": "required",
                "budget_sec": 5,
                "poll_sec": 1,
                # This mimics a case-level check that intentionally expects degraded pre-fix state.
                "commands": [{"command": ["bash", "-lc", f"test -f {degraded_marker}"], "timeout_sec": 2, "sleep": 0}],
            }
        },
    }

    with patch.object(app, "_apply_decoys_if_needed", return_value=True), patch.object(
        app, "_stop_proxy_trace", lambda: None
    ), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(
        app, "_maybe_start_cleanup", lambda: None
    ):
        app._run_setup()

    try:
        assert app.run_state["status"] == "ready"
        checks_path = ROOT / app.run_state["setup_checks_path"]
        assert checks_path.exists()
        payload = json.loads(checks_path.read_text(encoding="utf-8"))
        pre = next(item for item in (payload.get("checks") or []) if item.get("id") == "precondition_check")
        assert pre["result"] == "passed"
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
