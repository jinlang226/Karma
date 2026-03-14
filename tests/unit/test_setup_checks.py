import json
import shutil
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _prepare_setup_run(app, name):
    run_root = RUNS_DIR / name
    run_dir = str(run_root.relative_to(ROOT))
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "setup_running",
            "run_dir": run_dir,
            "setup_log": f"{run_dir}/preoperation.log",
            "setup_phase": "precondition_apply",
            "setup_warnings": [],
            "data": {},
        }
    )
    return run_root


def test_precondition_required_failure_sets_setup_failed():
    app = _make_app()
    run_root = _prepare_setup_run(app, "unit_precondition_required_fail")
    app.run_state["data"] = {
        "setup_self_check": {
            "precondition_check": {
                "mode": "required",
                "budget_sec": 1,
                "poll_sec": 1,
                "commands": [{"command": ["bash", "-lc", "exit 1"], "timeout_sec": 1, "sleep": 0}],
            }
        }
    }
    records = []
    with patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(app, "_maybe_start_cleanup", lambda: None):
        ok = app._run_precondition_check(records)
    assert ok is False
    assert app.run_state["status"] == "setup_failed"
    assert records[-1]["result"] == "failed"
    shutil.rmtree(run_root, ignore_errors=True)


def test_precondition_warn_failure_allows_setup_to_continue():
    app = _make_app()
    run_root = _prepare_setup_run(app, "unit_precondition_warn_fail")
    app.run_state["data"] = {
        "setup_self_check": {
            "precondition_check": {
                "mode": "warn",
                "budget_sec": 1,
                "poll_sec": 1,
                "commands": [{"command": ["bash", "-lc", "exit 1"], "timeout_sec": 1, "sleep": 0}],
            }
        }
    }
    records = []
    with patch.object(app, "_stop_proxy_trace", lambda: None), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(app, "_maybe_start_cleanup", lambda: None):
        ok = app._run_precondition_check(records)
    assert ok is True
    assert app.run_state["status"] == "setup_running"
    assert records[-1]["result"] == "warn"
    assert app.run_state["setup_warnings"]
    shutil.rmtree(run_root, ignore_errors=True)


def test_run_setup_records_phase_and_setup_checks_summary():
    app = _make_app()
    run_root = _prepare_setup_run(app, "unit_run_setup_success")
    app.run_state["data"] = {
        "preOperationCommands": [{"command": ["bash", "-lc", "echo setup"], "timeout_sec": 3, "sleep": 0}],
        "setup_self_check": {
            "precondition_check": {
                "mode": "required",
                "budget_sec": 10,
                "poll_sec": 1,
                "commands": [{"command": ["bash", "-lc", "echo ok"], "timeout_sec": 3, "sleep": 0}],
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

    assert app.run_state["status"] == "ready"
    assert app.run_state["setup_phase"] == "ready"
    assert app.run_state["current_step"] is None
    summary_path = ROOT / app.run_state["setup_checks_path"]
    assert summary_path.exists()
    payload = json.loads(summary_path.read_text())
    ids = [item.get("id") for item in payload.get("checks") or []]
    assert "precondition_check" in ids
    shutil.rmtree(run_root, ignore_errors=True)


def test_set_setup_phase_updates_current_step_label():
    app = _make_app()
    run_root = _prepare_setup_run(app, "unit_setup_phase_label")
    app._set_setup_phase("precondition_check")
    assert app.run_state["setup_phase"] == "precondition_check"
    assert app.run_state["current_step"] == "phase:Precondition Check"
    shutil.rmtree(run_root, ignore_errors=True)


def test_run_setup_uses_precondition_units_and_ignores_preoperation_commands():
    app = _make_app()
    run_root = _prepare_setup_run(app, "unit_run_setup_precondition_units")
    marker = run_root / "unit_marker"
    app.run_state["data"] = {
        # This would fail if executed; preconditionUnits should take precedence.
        "preOperationCommands": [{"command": ["bash", "-lc", "exit 1"], "timeout_sec": 1, "sleep": 0}],
        "preconditionUnits": [
            {
                "id": "p1",
                "probe": {"command": ["bash", "-lc", f"test -f {marker}"], "timeout_sec": 1},
                "apply": {"command": ["bash", "-lc", f"touch {marker}"], "timeout_sec": 1},
                "verify": {"command": ["bash", "-lc", f"test -f {marker}"], "timeout_sec": 1},
            }
        ],
    }
    with patch.object(app, "_apply_decoys_if_needed", return_value=True), patch.object(
        app, "_stop_proxy_trace", lambda: None
    ), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(
        app, "_maybe_start_cleanup", lambda: None
    ):
        app._run_setup()

    assert app.run_state["status"] == "ready"
    assert marker.exists()
    warnings = app.run_state.get("setup_warnings") or []
    assert any("preconditionUnits detected" in text for text in warnings)
    summary_path = ROOT / app.run_state["setup_checks_path"]
    payload = json.loads(summary_path.read_text())
    ids = [item.get("id") for item in payload.get("checks") or []]
    assert "precondition_check" in ids
    try:
        marker.unlink()
    except Exception:
        pass
    shutil.rmtree(run_root, ignore_errors=True)


def test_precondition_check_derives_from_precondition_unit_probe():
    app = _make_app()
    run_root = _prepare_setup_run(app, "unit_precondition_check_from_units")
    marker = run_root / "check_marker"
    app.run_state["data"] = {
        "preconditionUnits": [
            {
                "id": "p1",
                "probe": {"command": ["bash", "-lc", f"test -f {marker}"], "timeout_sec": 1},
                "apply": {"command": ["bash", "-lc", f"touch {marker}"], "timeout_sec": 1},
                "verify": {"command": ["bash", "-lc", f"test -f {marker}"], "timeout_sec": 1},
            }
        ],
    }
    with patch.object(app, "_apply_decoys_if_needed", return_value=True), patch.object(
        app, "_stop_proxy_trace", lambda: None
    ), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(
        app, "_maybe_start_cleanup", lambda: None
    ):
        app._run_setup()

    assert app.run_state["status"] == "ready"
    warnings = app.run_state.get("setup_warnings") or []
    assert not any("skipping precondition check" in text for text in warnings)
    summary_path = ROOT / app.run_state["setup_checks_path"]
    payload = json.loads(summary_path.read_text())
    pre = next(item for item in (payload.get("checks") or []) if item.get("id") == "precondition_check")
    assert pre["result"] == "passed"
    try:
        marker.unlink()
    except Exception:
        pass
    shutil.rmtree(run_root, ignore_errors=True)


def test_run_setup_does_not_perform_namespace_bootstrap():
    app = _make_app()
    run_root = _prepare_setup_run(app, "unit_setup_no_namespace_bootstrap")
    app.run_state["data"] = {
        "preOperationCommands": [{"command": ["bash", "-lc", "echo setup"], "timeout_sec": 1, "sleep": 0}],
    }

    with patch.object(
        app,
        "_run_command_list",
        side_effect=lambda cmds, *_args, **_kwargs: len(cmds or []) > 0
        and all("kubectl get namespace" not in str(item.get("command")) for item in cmds or []),
    ), patch.object(
        app, "_run_precondition_check", return_value=True
    ), patch.object(
        app, "_apply_decoys_if_needed", return_value=True
    ), patch.object(
        app, "_stop_proxy_trace", lambda: None
    ), patch.object(
        app, "_maybe_compute_metrics", lambda: None
    ), patch.object(
        app, "_maybe_start_cleanup", lambda: None
    ):
        app._run_setup()

    assert app.run_state["status"] == "ready"
    shutil.rmtree(run_root, ignore_errors=True)
