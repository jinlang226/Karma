from pathlib import Path
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.runner_core import workflow_jobs as workflow_jobs_core
from app.settings import ROOT


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _workflow_fixture_path():
    wf_dir = ROOT / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    return wf_dir / "unit_ui_workflow.yaml"


def _write_workflow_fixture(path: Path):
    path.write_text(
        "\n".join(
            [
                "apiVersion: benchmark/v1alpha1",
                "kind: Workflow",
                "metadata:",
                "  name: unit-ui-workflow",
                "spec:",
                "  prompt_mode: progressive",
                "  stages:",
                "  - id: s1",
                "    service: rabbitmq-experiments",
                "    case: manual_monitoring",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_workflow_files_preview_and_start():
    app = _make_app()
    wf_path = _workflow_fixture_path()
    _write_workflow_fixture(wf_path)

    files = app.list_workflow_files()
    row = next((it for it in files if it.get("path") == str(wf_path.relative_to(ROOT))), None)
    assert row is not None
    assert row["status"] == "ok"
    assert row["stage_count"] == 1

    preview = app.workflow_preview(
        {
            "workflow_path": str(wf_path.relative_to(ROOT)),
            "flags": {"sandbox": "docker", "agent": "react"},
        }
    )
    assert preview["ok"] is True
    assert "workflow-run" in preview["run_one_line"]

    with patch("threading.Thread.start", lambda self: None):
        started = app.start_workflow(
            {
                "action": "run",
                "workflow_path": str(wf_path.relative_to(ROOT)),
                "flags": {"setup_timeout_mode": "auto"},
            }
        )
    assert started.get("ok") is True
    assert (started.get("job") or {}).get("origin") == "workflow_runner"
    jobs = app.list_workflow_jobs()
    assert jobs
    assert jobs[0]["status"] == "running"
    assert jobs[0]["kind"] == "run"
    assert jobs[0]["origin"] == "workflow_runner"

    blocked = app.start_workflow(
        {
            "action": "run",
            "workflow_path": str(wf_path.relative_to(ROOT)),
        }
    )
    assert "error" in blocked

    wf_path.unlink(missing_ok=True)


def test_start_workflow_manual_origin_uses_custom_phase_and_remains_hidden():
    app = _make_app()
    wf_path = _workflow_fixture_path()
    _write_workflow_fixture(wf_path)
    rel_path = str(wf_path.relative_to(ROOT))
    try:
        with patch("threading.Thread.start", lambda self: None):
            started = app.start_workflow(
                {
                    "action": "run",
                    "workflow_path": rel_path,
                    "origin": "manual_runner",
                    "initial_phase": "stage_setup",
                    "phase_message": "manual setup",
                }
            )
        assert started.get("ok") is True
        job = started.get("job") or {}
        assert job.get("origin") == "manual_runner"
        assert job.get("phase") == "stage_setup"
        assert job.get("phase_message") == "manual setup"

        listed = app.list_workflow_jobs()
        assert listed == []

        stored = app.get_workflow_job(job.get("id"))
        assert stored is not None
        assert stored.get("origin") == "manual_runner"
    finally:
        wf_path.unlink(missing_ok=True)


def test_start_workflow_ui_run_uses_debug_local_profile_defaults():
    app = _make_app()
    wf_path = _workflow_fixture_path()
    _write_workflow_fixture(wf_path)
    rel_path = str(wf_path.relative_to(ROOT))
    try:
        with patch("threading.Thread.start", lambda self: None):
            started = app.start_workflow(
                {
                    "action": "run",
                    "workflow_path": rel_path,
                    "source": "ui",
                }
            )
        assert started.get("ok") is True
        job = started.get("job") or {}
        assert job.get("origin") == "workflow_runner"
        assert job.get("request_source") == "ui"
        assert job.get("execution_profile") == "ui_debug_local"
        assert job.get("sandbox_mode") == "local"
        assert job.get("interactive_controls") is True
        assert job.get("can_submit") is True
        assert isinstance(job.get("prompt"), dict)
        assert job.get("prompt", {}).get("available") is False
        tokens = list(job.get("tokens") or [])
        assert "--sandbox" in tokens
        assert "local" in tokens
        assert "--agent-cmd" in tokens
        assert "sleep 86400" in tokens
    finally:
        wf_path.unlink(missing_ok=True)


def test_start_workflow_ui_run_docker_mode_keeps_non_interactive_profile():
    app = _make_app()
    wf_path = _workflow_fixture_path()
    _write_workflow_fixture(wf_path)
    rel_path = str(wf_path.relative_to(ROOT))
    try:
        with patch("threading.Thread.start", lambda self: None):
            started = app.start_workflow(
                {
                    "action": "run",
                    "workflow_path": rel_path,
                    "source": "ui",
                    "execution_mode": "docker",
                }
            )
        assert started.get("ok") is True
        job = started.get("job") or {}
        assert job.get("request_source") == "ui"
        assert job.get("execution_profile") == "default"
        assert job.get("sandbox_mode") == "docker"
        assert job.get("interactive_controls") is False
        assert job.get("can_submit") is False
    finally:
        wf_path.unlink(missing_ok=True)


def test_workflow_stream_snapshot_and_events():
    app = _make_app()
    job_id = "workflow_stream_job_1"
    with app.workflow_lock:
        job = {
            "id": job_id,
            "kind": "run",
            "status": "running",
            "workflow_name": "wf",
            "workflow_path": "workflows/test.yaml",
            "prompt_mode": "progressive",
            "phase": "stage_setup",
            "phase_message": "starting",
            "logs": {
                "orchestrator": {"lines": [], "truncated": 0, "total_lines": 0},
                "agent": {"lines": [], "truncated": 0, "total_lines": 0},
                "submit": {"lines": [], "truncated": 0, "total_lines": 0},
                "transition": {"lines": [], "truncated": 0, "total_lines": 0},
            },
            "rev": 1,
        }
        app.workflow_jobs[job_id] = job
        app.workflow_job_order.append(job_id)
        workflow_jobs_core.push_workflow_event_locked(
            app,
            "job_upsert",
            {"job": workflow_jobs_core.workflow_job_snapshot(job)},
        )

    snap = app.get_workflow_stream_snapshot()
    assert snap["seq"] >= 1
    assert snap["schema"] == "workflow_stream.v2"
    assert any(item.get("id") == job_id for item in snap.get("jobs", []))

    events = app.get_workflow_events_since(0, timeout_sec=0.0)
    assert events["reset"] is False
    assert events["events"]
    assert any(ev.get("type") == "job_upsert" for ev in events["events"])

    with app.workflow_lock:
        workflow_jobs_core.push_workflow_event_locked(
            app,
            "log_append",
            {"job_id": job_id, "stream": "orchestrator", "lines": ["hello"], "from_line": 1},
        )
    tail = app.get_workflow_events_since(events["current_seq"], timeout_sec=0.0)
    assert any(ev.get("type") == "log_append" for ev in tail["events"])

    with app.workflow_lock:
        app.workflow_event_limit = 2
        workflow_jobs_core.push_workflow_event_locked(app, "heartbeat", {"seq": 1})
        workflow_jobs_core.push_workflow_event_locked(app, "heartbeat", {"seq": 2})
        workflow_jobs_core.push_workflow_event_locked(app, "heartbeat", {"seq": 3})
    old = app.get_workflow_events_since(0, timeout_sec=0.0)
    assert old["reset"] is True


def test_workflow_job_snapshot_exposes_phase_progress_and_artifacts():
    app = _make_app()
    job_id = "workflow_snapshot_job_2"
    with app.workflow_lock:
        job = {
            "id": job_id,
            "kind": "run",
            "status": "completed",
            "workflow_name": "wf-demo",
            "workflow_path": "workflows/demo.yaml",
            "prompt_mode": "concat_stateful",
            "phase": "done",
            "phase_message": "workflow complete",
            "progress_pct": 100,
            "active_stage_id": "stage_2",
            "active_stage_index": 2,
            "stage_total": 2,
            "run_dir": "runs/demo",
            "workflow_state_path": "runs/demo/workflow_state.json",
            "workflow_stage_results_path": "runs/demo/workflow_stage_results.jsonl",
            "workflow_transition_log": "runs/demo/workflow_transition.log",
            "workflow_final_sweep_path": "runs/demo/workflow_final_sweep.json",
            "logs": {
                "orchestrator": {"lines": ["line-1"], "truncated": 0, "total_lines": 1},
                "agent": {"lines": [], "truncated": 0, "total_lines": 0},
                "submit": {"lines": [], "truncated": 0, "total_lines": 0},
                "transition": {"lines": [], "truncated": 0, "total_lines": 0},
            },
            "rev": 1,
        }
        app.workflow_jobs[job_id] = job
        app.workflow_job_order.append(job_id)

    snap = app.get_workflow_job(job_id)
    assert snap is not None
    for key in (
        "phase",
        "phase_message",
        "progress_pct",
        "prompt",
        "run_dir",
        "workflow_state_path",
        "workflow_stage_results_path",
        "workflow_transition_log",
        "workflow_final_sweep_path",
    ):
        assert key in snap
    assert snap["phase"] == "done"
    assert snap["progress_pct"] == 100
    assert snap["workflow_final_sweep_path"] == "runs/demo/workflow_final_sweep.json"


def test_workflow_import_parses_yaml_into_builder_draft():
    app = _make_app()
    result = app.workflow_import(
        {
            "workflow_path": "workflows/import_ui.yaml",
            "yaml_text": "\n".join(
                [
                    "apiVersion: benchmark/v1alpha1",
                    "kind: Workflow",
                    "metadata:",
                    "  name: imported-demo",
                    "spec:",
                    "  prompt_mode: concat_stateful",
                    "  namespaces:",
                    "  - cluster_a",
                    "  - cluster_b",
                    "  stages:",
                    "  - id: s1",
                    "    service: demo",
                    "    case: configmap-update",
                    "    namespaces:",
                    "    - cluster_a",
                    "    max_attempts: 2",
                    "    param_overrides:",
                    "      target_value: left",
                    "  - id: s2",
                    "    service: demo",
                    "    case: configmap-update-two-ns",
                    "    namespaces:",
                    "    - cluster_a",
                    "    - cluster_b",
                    "    namespace_binding:",
                    "      source: cluster_a",
                    "      target: cluster_b",
                    "    param_overrides:",
                    "      source_value: ${stages.s1.params.target_value}",
                    "      target_value: right",
                ]
            ) + "\n",
        }
    )
    assert result["ok"] is True
    assert result["workflow_name"] == "imported-demo"
    assert result["prompt_mode"] == "concat_stateful"
    assert result["stage_count"] == 2
    draft = result["draft"]
    assert draft["metadata"]["name"] == "imported-demo"
    assert draft["spec"]["namespaces"] == ["cluster_a", "cluster_b"]
    assert draft["spec"]["stages"][0]["max_attempts"] == 2
    assert draft["spec"]["stages"][1]["namespace_bindings"] == {
        "source": "cluster_a",
        "target": "cluster_b",
    }
    assert draft["spec"]["stages"][1]["param_overrides"]["target_value"] == "right"
    assert draft["spec"]["stages"][1]["param_overrides"]["source_value"] == "${stages.s1.params.target_value}"


def test_workflow_import_invalid_yaml_returns_error_and_no_draft():
    app = _make_app()
    result = app.workflow_import({"yaml_text": "kind: nope"})
    assert result["ok"] is False
    assert "error" in result
    assert "draft" not in result


def teardown_module(module):
    wf_path = _workflow_fixture_path()
    wf_path.unlink(missing_ok=True)
    # keep the workflows directory if it already existed.
    try:
        (ROOT / "workflows").rmdir()
    except Exception:
        pass
