from unittest.mock import patch

from app.runner import BenchmarkApp
from app.runner_core import workflow_jobs as workflow_jobs_core


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_workflow_phase_parser_wrapper_parity():
    cases = [
        ("[orchestrator] stage=setup_start", ("stage_setup", "orchestrator stage=setup_start")),
        ("[orchestrator] stage=agent_start", ("agent_running", "orchestrator stage=agent_start")),
        ("[orchestrator] stage=waiting_start", ("agent_waiting", "orchestrator stage=waiting_start")),
        ("[orchestrator] stage=waiting_submit", ("agent_waiting", "orchestrator stage=waiting_submit")),
        ("[orchestrator] stage=start_received", ("agent_running", "orchestrator stage=start_received")),
        ("[orchestrator] stage=final_sweep", ("final_sweep", "orchestrator stage=final_sweep")),
        ("[orchestrator] stage=workflow_cleanup", ("cleanup", "orchestrator stage=workflow_cleanup")),
        ("workflow transition stage_a->stage_b", ("transition", "workflow transition stage_a->stage_b")),
        ("final sweep complete", ("final_sweep", "final sweep complete")),
        ("cleanup started", ("cleanup", "cleanup started")),
    ]
    for line, expected in cases:
        assert workflow_jobs_core.parse_workflow_phase_line(line) == expected


def test_workflow_artifact_parser_wrapper_parity():
    line = (
        '{"run_dir":"runs/demo","workflow_state_path":"runs/demo/workflow_state.json"}'
    )
    payload = {}
    workflow_jobs_core.parse_workflow_artifact_line(payload, line)
    assert payload["run_dir"] == "runs/demo"
    assert payload["workflow_state_path"] == "runs/demo/workflow_state.json"
    assert "compiled_artifact_path" not in payload


def test_workflow_stream_snapshot_wrapper_shape():
    app = _make_app()
    with app.workflow_lock:
        job = {
            "id": "wf_shape_1",
            "kind": "run",
            "status": "running",
            "workflow_name": "wf",
            "workflow_path": "workflows/test.yaml",
            "prompt_mode": "progressive",
            "phase": "stage_setup",
            "phase_message": "starting",
            "logs": {"orchestrator": {"lines": ["x"], "truncated": 0, "total_lines": 1}},
            "rev": 1,
        }
        app.workflow_jobs[job["id"]] = job
        app.workflow_job_order.append(job["id"])
        workflow_jobs_core.push_workflow_event_locked(
            app,
            "job_upsert",
            {"job": workflow_jobs_core.workflow_job_snapshot(job)},
        )
    snap = app.get_workflow_stream_snapshot()
    assert snap["schema"] == "workflow_stream.v2"
    assert isinstance(snap["seq"], int)
    assert isinstance(snap["server_epoch_ms"], int)
    assert isinstance(snap["jobs"], list) and snap["jobs"]
