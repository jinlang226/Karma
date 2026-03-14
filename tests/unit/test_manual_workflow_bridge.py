import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.runner_core import manual_workflow_bridge as bridge
from app.settings import ROOT
from app.util import encode_case_id, read_yaml


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _manual_case_id():
    return encode_case_id("rabbitmq-experiments", "manual_monitoring", "test.yaml")


def test_bridge_flag_defaults_off_and_truthy_values():
    assert bridge.manual_workflow_bridge_enabled(environ={}) is False
    assert bridge.manual_workflow_bridge_enabled(environ={bridge.BRIDGE_FLAG_ENV: "0"}) is False
    assert bridge.manual_workflow_bridge_enabled(environ={bridge.BRIDGE_FLAG_ENV: "false"}) is False
    assert bridge.manual_workflow_bridge_enabled(environ={bridge.BRIDGE_FLAG_ENV: "1"}) is True
    assert bridge.manual_workflow_bridge_enabled(environ={bridge.BRIDGE_FLAG_ENV: "true"}) is True


def test_workflow_job_mapping_running_and_submit_gate():
    running_wait = {"status": "running", "phase": "agent_waiting", "phase_message": "waiting submit"}
    payload = bridge.map_workflow_job_to_run_status(running_wait, workflow_state={})
    assert payload["status"] == "ready"
    assert payload["can_submit"] is True
    assert payload["current_step"] == "waiting submit"
    assert payload["namespace_lifecycle_owner"] == "orchestrator"

    running_setup = {"status": "running", "phase": "stage_setup"}
    payload = bridge.map_workflow_job_to_run_status(running_setup, workflow_state={})
    assert payload["status"] == "setup_running"
    assert payload["can_submit"] is False
    assert payload["setup_phase"] == "precondition_apply"


def test_workflow_job_mapping_terminal_states_from_workflow_state():
    completed = {"status": "completed", "phase": "done"}

    payload = bridge.map_workflow_job_to_run_status(
        completed,
        workflow_state={"terminal": True, "terminal_reason": "workflow_complete", "solve_status": "passed"},
    )
    assert payload["status"] == "passed"

    payload = bridge.map_workflow_job_to_run_status(
        completed,
        workflow_state={"terminal": True, "terminal_reason": "workflow_complete", "solve_status": "failed"},
    )
    assert payload["status"] == "failed"

    payload = bridge.map_workflow_job_to_run_status(
        completed,
        workflow_state={"terminal": True, "terminal_reason": "next_stage_setup_failed", "solve_status": "passed"},
    )
    assert payload["status"] == "setup_failed"

    payload = bridge.map_workflow_job_to_run_status(
        completed,
        workflow_state={"terminal": True, "terminal_reason": "agent_exited", "solve_status": "passed"},
    )
    assert payload["status"] == "auto_failed"

    payload = bridge.map_workflow_job_to_run_status(
        completed,
        workflow_state={"terminal": True, "terminal_reason": "stage_failed_terminate", "solve_status": "failed"},
    )
    assert payload["status"] == "failed"


def test_map_manual_session_to_run_status_handles_missing_job():
    session = bridge.empty_manual_workflow_session()
    session["active_job_id"] = "missing-job"
    out = bridge.map_manual_session_to_run_status(session, None)
    assert out["status"] == "auto_failed"
    assert out["last_error"] == "manual workflow job not found"
    assert out["can_submit"] is False
    assert out["namespace_lifecycle_owner"] == "orchestrator"


def test_runner_manual_workflow_bridge_path_is_feature_flagged():
    app = _make_app()
    app.manual_workflow_session = {
        "active_job_id": "wf_bridge_1",
        "case_id": None,
        "service": None,
        "case": None,
        "test_file": "test.yaml",
        "source": "manual_runner",
    }
    app.workflow_jobs["wf_bridge_1"] = {
        "id": "wf_bridge_1",
        "origin": "manual_runner",
        "status": "running",
        "phase": "agent_waiting",
        "phase_message": "waiting",
        "run_dir": "runs/manual_bridge",
        "max_attempts": 3,
        "active_attempt": 1,
        "solve_elapsed_sec": 12,
        "solve_limit_sec": 120,
    }

    # Default remains old run_state behavior when flag is not enabled.
    out = app.run_status()
    assert out["status"] == "idle"

    with patch.dict("os.environ", {bridge.BRIDGE_FLAG_ENV: "1"}):
        bridged = app.run_status()
    assert bridged["status"] == "ready"
    assert bridged["can_submit"] is True
    assert bridged["run_dir"] == "runs/manual_bridge"


def test_bridge_start_eligibility_rejects_internal_workflow_start_args():
    assert bridge.manual_bridge_start_eligible() is True
    assert bridge.manual_bridge_start_eligible(defer_cleanup=True) is False
    assert bridge.manual_bridge_start_eligible(skip_precondition_unit_ids=["u1"]) is False
    assert bridge.manual_bridge_start_eligible(case_data_override={}) is False
    assert bridge.manual_bridge_start_eligible(resolved_params={"a": 1}) is False
    assert bridge.manual_bridge_start_eligible(namespace_context={"roles": {"default": "ns"}}) is False


def test_runner_start_run_switches_to_synthetic_workflow_when_flag_enabled():
    app = _make_app()
    case_id = _manual_case_id()
    captured = {}

    def _fake_start_workflow(payload):
        captured["payload"] = payload
        return {"ok": True, "job": {"id": "wf_manual_u3", "origin": "manual_runner"}}

    with patch.dict("os.environ", {bridge.BRIDGE_FLAG_ENV: "1"}):
        with patch.object(app, "start_workflow", side_effect=_fake_start_workflow):
            out = app.start_run(case_id, max_attempts_override=2)
    assert out == {"status": "started"}

    payload = captured.get("payload") or {}
    assert payload.get("origin") == "manual_runner"
    assert payload.get("initial_phase") == "stage_setup"
    assert payload.get("action") == "run"
    flags = payload.get("flags") or {}
    assert flags.get("sandbox") == "local"
    assert flags.get("agent_cmd") == "sleep 86400"
    assert int(flags.get("submit_timeout")) == 24 * 60 * 60

    session = app.manual_workflow_session
    assert session.get("active_job_id") == "wf_manual_u3"
    assert session.get("case_id") == case_id
    assert session.get("service") == "rabbitmq-experiments"
    assert session.get("case") == "manual_monitoring"

    workflow_rel = str(payload.get("workflow_path") or "")
    workflow_file = (ROOT / workflow_rel).resolve()
    doc = read_yaml(workflow_file) or {}
    stage = ((doc.get("spec") or {}).get("stages") or [{}])[0]
    assert doc.get("kind") == "Workflow"
    assert (doc.get("metadata") or {}).get("name", "").startswith("manual-rabbitmq-experiments-manual_monitoring-")
    assert stage.get("service") == "rabbitmq-experiments"
    assert stage.get("case") == "manual_monitoring"
    assert stage.get("max_attempts") == 2
    workflow_file.unlink(missing_ok=True)


def test_start_manual_run_uses_synthetic_workflow_without_feature_flag():
    app = _make_app()
    case_id = _manual_case_id()
    captured = {}

    def _fake_start_workflow(payload):
        captured["payload"] = payload
        return {"ok": True, "job": {"id": "wf_manual_u5", "origin": "manual_runner"}}

    with patch.object(app, "start_workflow", side_effect=_fake_start_workflow):
        out = app.start_manual_run(case_id, max_attempts_override=3)
    assert out == {"status": "started"}
    payload = captured.get("payload") or {}
    assert payload.get("origin") == "manual_runner"
    assert payload.get("action") == "run"
    assert (payload.get("flags") or {}).get("sandbox") == "local"

    workflow_rel = str(payload.get("workflow_path") or "")
    workflow_file = (ROOT / workflow_rel).resolve()
    try:
        assert workflow_file.is_file()
    finally:
        workflow_file.unlink(missing_ok=True)


def test_runner_start_run_keeps_legacy_path_when_bridge_not_eligible():
    app = _make_app()
    case_id = _manual_case_id()

    with patch.dict("os.environ", {bridge.BRIDGE_FLAG_ENV: "1"}):
        with patch.object(app, "start_workflow", side_effect=AssertionError("bridge path should not be used")):
            with patch("threading.Thread.start", lambda self: None):
                out = app.start_run(case_id, case_data_override={"detailedInstructions": "x"})

    assert out.get("status") == "started"
    assert app.run_state.get("status") == "setup_running"
    assert app.run_state.get("case_id") == case_id


def test_manual_workflow_run_dir_resolution_prefers_job_and_falls_back_to_workflow_name():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        by_job = root / "runs" / "x_workflow_run_job"
        by_job.mkdir(parents=True, exist_ok=True)
        out = bridge.resolve_manual_workflow_run_dir(
            {"workflow_name": "ignored"},
            {"run_dir": str(by_job.relative_to(root))},
            root=root,
        )
        assert out == by_job

        fallback = root / "runs" / "2026-01-01T00-00-00Z_workflow_run_wf-demo"
        fallback.mkdir(parents=True, exist_ok=True)
        out = bridge.resolve_manual_workflow_run_dir(
            {"workflow_name": "wf-demo"},
            {"run_dir": None},
            root=root,
        )
        assert out == fallback


def test_runner_submit_bridge_writes_submit_signal():
    app = _make_app()
    run_dir = ROOT / "runs" / "unit_u4_submit_bridge"
    bundle_dir = run_dir / "agent_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    try:
        app.manual_workflow_session = {
            "active_job_id": "wf_submit_1",
            "workflow_name": "wf-submit",
            "workflow_path": "workflows/wf-submit.yaml",
        }
        app.workflow_jobs["wf_submit_1"] = {
            "id": "wf_submit_1",
            "origin": "manual_runner",
            "status": "running",
            "phase": "agent_waiting",
            "run_dir": str(run_dir.relative_to(ROOT)),
        }
        with patch.dict("os.environ", {bridge.BRIDGE_FLAG_ENV: "1"}):
            out = app.submit_run()
        assert out == {"status": "verifying"}
        signal_path = bundle_dir / "submit.signal"
        assert signal_path.is_file()
        assert signal_path.read_text(encoding="utf-8") == ""
    finally:
        (bundle_dir / "submit.signal").unlink(missing_ok=True)
        try:
            bundle_dir.rmdir()
        except Exception:
            pass
        try:
            run_dir.rmdir()
        except Exception:
            pass


def test_submit_manual_run_uses_bridge_without_feature_flag():
    app = _make_app()
    run_dir = ROOT / "runs" / "unit_u5_submit_manual_route"
    bundle_dir = run_dir / "agent_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    try:
        app.manual_workflow_session = {
            "active_job_id": "wf_submit_route_1",
            "workflow_name": "wf-submit-route",
            "workflow_path": "workflows/wf-submit-route.yaml",
        }
        app.workflow_jobs["wf_submit_route_1"] = {
            "id": "wf_submit_route_1",
            "origin": "manual_runner",
            "status": "running",
            "phase": "agent_waiting",
            "run_dir": str(run_dir.relative_to(ROOT)),
        }
        out = app.submit_manual_run()
        assert out == {"status": "verifying"}
        assert (bundle_dir / "submit.signal").is_file()
    finally:
        (bundle_dir / "submit.signal").unlink(missing_ok=True)
        try:
            bundle_dir.rmdir()
        except Exception:
            pass
        try:
            run_dir.rmdir()
        except Exception:
            pass


def test_manual_run_status_bridges_without_feature_flag():
    app = _make_app()
    app.manual_workflow_session = {
        "active_job_id": "wf_status_u5",
        "service": "rabbitmq-experiments",
        "case": "manual_monitoring",
        "test_file": "test.yaml",
        "case_id": _manual_case_id(),
    }
    app.workflow_jobs["wf_status_u5"] = {
        "id": "wf_status_u5",
        "origin": "manual_runner",
        "status": "running",
        "phase": "agent_waiting",
        "run_dir": "runs/wf_status_u5",
    }
    out = app.manual_run_status()
    assert out["status"] == "ready"
    assert out["can_submit"] is True


def test_runner_cleanup_bridge_writes_manual_cleanup_control_signal():
    app = _make_app()
    run_dir = ROOT / "runs" / "unit_u4_cleanup_bridge"
    bundle_dir = run_dir / "agent_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    try:
        app.manual_workflow_session = {
            "active_job_id": "wf_cleanup_1",
            "workflow_name": "wf-cleanup",
            "workflow_path": "workflows/wf-cleanup.yaml",
        }
        app.workflow_jobs["wf_cleanup_1"] = {
            "id": "wf_cleanup_1",
            "origin": "manual_runner",
            "status": "running",
            "phase": "agent_waiting",
            "run_dir": str(run_dir.relative_to(ROOT)),
        }
        with patch.dict("os.environ", {bridge.BRIDGE_FLAG_ENV: "1"}):
            out = app.cleanup_run()
        assert out.get("status") == "cleaning"
        assert out.get("log") == str((run_dir / "workflow_cleanup.log").relative_to(ROOT))
        signal_path = bundle_dir / "submit.signal"
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        assert payload.get("action") == "cleanup"
        assert payload.get("reason") == "manual_cleanup"
    finally:
        (bundle_dir / "submit.signal").unlink(missing_ok=True)
        try:
            bundle_dir.rmdir()
        except Exception:
            pass
        try:
            run_dir.rmdir()
        except Exception:
            pass


def test_runner_cleanup_bridge_clears_session_after_terminal_job():
    app = _make_app()
    wf_path = ROOT / "workflows" / "unit_u4_manual_cleanup.yaml"
    wf_path.parent.mkdir(parents=True, exist_ok=True)
    wf_path.write_text("kind: Workflow\n", encoding="utf-8")
    try:
        app.manual_workflow_session = {
            "active_job_id": "wf_done_1",
            "workflow_name": "wf-done",
            "workflow_path": str(wf_path.relative_to(ROOT)),
        }
        app.workflow_jobs["wf_done_1"] = {
            "id": "wf_done_1",
            "origin": "manual_runner",
            "status": "completed",
            "phase": "done",
        }
        with patch.dict("os.environ", {bridge.BRIDGE_FLAG_ENV: "1"}):
            out = app.cleanup_run()
        assert out == {"status": "already_cleaned"}
        assert app.manual_workflow_session.get("active_job_id") is None
        assert wf_path.exists() is False
    finally:
        wf_path.unlink(missing_ok=True)


def test_cleanup_manual_run_returns_skipped_without_active_manual_session():
    app = _make_app()
    out = app.cleanup_manual_run()
    assert out == {"status": "skipped"}
