import tempfile
from pathlib import Path
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.runner_core import workflow_jobs as workflow_jobs_core
from app.settings import ROOT


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


class _FakePopen:
    lines = []
    exit_code = 0
    raise_exc = None
    last_kwargs = {}

    def __init__(self, *_args, **_kwargs):
        if self.__class__.raise_exc is not None:
            raise self.__class__.raise_exc
        self.__class__.last_kwargs = dict(_kwargs)
        self.pid = 4242
        self.stdout = _FakeStdout(self.__class__.lines)

    def wait(self):
        return int(self.__class__.exit_code)


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _workflow_job(job_id):
    return {
        "id": job_id,
        "kind": "run",
        "status": "running",
        "workflow_name": "wf",
        "workflow_path": "workflows/demo.yaml",
        "prompt_mode": "progressive",
        "request_source": "cli",
        "execution_profile": "default",
        "sandbox_mode": "docker",
        "interactive_controls": False,
        "phase": "agent_waiting",
        "phase_message": "starting",
        "active_stage_id": None,
        "active_stage_index": None,
        "stage_total": None,
        "active_attempt": None,
        "max_attempts": None,
        "run_dir": None,
        "compiled_artifact_path": None,
        "workflow_state_path": None,
        "workflow_stage_results_path": None,
        "workflow_transition_log": None,
        "workflow_final_sweep_path": None,
        "solve_elapsed_sec": None,
        "solve_limit_sec": None,
        "solve_paused": False,
        "pause_reason": None,
        "server_epoch_ms": 0,
        "progress_pct": None,
        "error": None,
        "exit_code": None,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": None,
        "stages": {},
        "stage_order": [],
        "logs": {
            "orchestrator": {"lines": [], "truncated": 0, "total_lines": 0},
            "agent": {"lines": [], "truncated": 0, "total_lines": 0},
            "submit": {"lines": [], "truncated": 0, "total_lines": 0},
            "transition": {"lines": [], "truncated": 0, "total_lines": 0},
        },
        "tokens": ["python3", "orchestrator.py", "workflow-run"],
        "dry_run": False,
        "rev": 1,
    }


def test_resolve_workflow_target_validation_branches():
    workflows_dir = ROOT / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    valid_yaml = workflows_dir / "unit_r2_valid.yaml"
    invalid_yaml = workflows_dir / "unit_r2_invalid.yaml"
    non_yaml = workflows_dir / "unit_r2_invalid.txt"
    try:
        valid_yaml.write_text(
            "\n".join(
                [
                    "apiVersion: benchmark/v1alpha1",
                    "kind: Workflow",
                    "metadata:",
                    "  name: unit-r2-valid",
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
        non_yaml.write_text("not yaml workflow", encoding="utf-8")
        invalid_yaml.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n", encoding="utf-8")

        path, error = workflow_jobs_core.resolve_workflow_target(str(valid_yaml.relative_to(ROOT)))
        assert error is None
        assert path == valid_yaml.resolve()

        path, error = workflow_jobs_core.resolve_workflow_target(str(non_yaml.relative_to(ROOT)))
        assert path is None
        assert "must point to .yaml/.yml" in error

        path, error = workflow_jobs_core.resolve_workflow_target("workflows/missing.yaml")
        assert path is None
        assert "workflow file not found" in error

        path, error = workflow_jobs_core.resolve_workflow_target(str(invalid_yaml.relative_to(ROOT)))
        assert path is None
        assert "invalid workflow spec" in error

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as outside:
            outside.write(valid_yaml.read_text(encoding="utf-8"))
            outside_path = outside.name
        try:
            path, error = workflow_jobs_core.resolve_workflow_target(outside_path)
            assert path is None
            assert "inside repository" in error
        finally:
            Path(outside_path).unlink(missing_ok=True)
    finally:
        valid_yaml.unlink(missing_ok=True)
        invalid_yaml.unlink(missing_ok=True)
        non_yaml.unlink(missing_ok=True)
        try:
            workflows_dir.rmdir()
        except Exception:
            pass


def test_workflow_job_snapshot_truncates_large_logs():
    lines = [f"line-{idx}" for idx in range(350)]
    snap = workflow_jobs_core.workflow_job_snapshot({"logs": {"orchestrator": {"lines": lines}}})
    out = (snap.get("logs") or {}).get("orchestrator") or {}
    assert snap.get("origin") == "workflow_runner"
    assert len(out.get("lines") or []) == 300
    assert out.get("truncated") == 50
    assert out.get("total_lines") == 350
    assert out.get("lines")[0] == "line-50"
    assert out.get("lines")[-1] == "line-349"


def test_workflow_event_cursor_edges_and_reset_behavior():
    app = _make_app()
    with app.workflow_lock:
        app.workflow_event_limit = 2
        workflow_jobs_core.push_workflow_event_locked(app, "job_upsert", {"job": {"id": "wf-1"}})
        workflow_jobs_core.push_workflow_event_locked(app, "heartbeat", {"seq": 1})
        workflow_jobs_core.push_workflow_event_locked(app, "heartbeat", {"seq": 2})

    invalid_cursor = app.get_workflow_events_since("bad", timeout_sec=0.0)
    assert invalid_cursor["reset"] is True

    negative_cursor = app.get_workflow_events_since(-5, timeout_sec=0.0)
    assert negative_cursor["reset"] is True

    boundary = app.get_workflow_events_since(1, timeout_sec=0.0)
    assert boundary["reset"] is False
    assert [ev["seq"] for ev in boundary["events"]] == [2, 3]


def test_workflow_visibility_filters_manual_origin_from_list_stream_and_events():
    app = _make_app()
    visible_job_id = "wf_visible_origin"
    hidden_job_id = "wf_hidden_origin"

    with app.workflow_lock:
        visible = _workflow_job(visible_job_id)
        visible["origin"] = "workflow_runner"
        hidden = _workflow_job(hidden_job_id)
        hidden["origin"] = "manual_runner"
        app.workflow_jobs[visible_job_id] = visible
        app.workflow_jobs[hidden_job_id] = hidden
        app.workflow_job_order.extend([visible_job_id, hidden_job_id])

        workflow_jobs_core.push_workflow_event_locked(
            app,
            "job_upsert",
            {"job": workflow_jobs_core.workflow_job_snapshot(visible)},
        )
        workflow_jobs_core.push_workflow_event_locked(
            app,
            "job_upsert",
            {"job": workflow_jobs_core.workflow_job_snapshot(hidden)},
        )
        workflow_jobs_core.push_workflow_event_locked(
            app,
            "log_append",
            {"job_id": visible_job_id, "stream": "orchestrator", "lines": ["visible"], "from_line": 1},
        )
        workflow_jobs_core.push_workflow_event_locked(
            app,
            "log_append",
            {"job_id": hidden_job_id, "stream": "orchestrator", "lines": ["hidden"], "from_line": 1},
        )

    jobs = app.list_workflow_jobs()
    assert [job.get("id") for job in jobs] == [visible_job_id]

    snap = app.get_workflow_stream_snapshot()
    assert [job.get("id") for job in (snap.get("jobs") or [])] == [visible_job_id]

    events = app.get_workflow_events_since(0, timeout_sec=0.0).get("events") or []
    hidden_seen = False
    visible_seen = False
    for event in events:
        data = event.get("data") or {}
        job = data.get("job") if isinstance(data.get("job"), dict) else {}
        job_id = str(data.get("job_id") or "")
        if str(job.get("id") or "") == hidden_job_id or job_id == hidden_job_id:
            hidden_seen = True
        if str(job.get("id") or "") == visible_job_id or job_id == visible_job_id:
            visible_seen = True
    assert hidden_seen is False
    assert visible_seen is True


def test_workflow_execution_profile_resolution_ui_debug_and_cli_defaults():
    profile = workflow_jobs_core.resolve_workflow_execution_profile(
        "run",
        {"source": "ui"},
        {"sandbox": "docker"},
        environ={workflow_jobs_core.UI_WORKFLOW_DEBUG_LOCAL_ENV: "1"},
    )
    assert profile["source"] == "ui"
    assert profile["profile"] == "ui_debug_local"
    assert profile["flags"]["sandbox"] == "local"
    assert profile["flags"]["agent_cmd"] == workflow_jobs_core.UI_WORKFLOW_DEBUG_HOLD_CMD
    assert int(profile["flags"]["submit_timeout"]) == workflow_jobs_core.UI_WORKFLOW_DEBUG_SUBMIT_TIMEOUT_SEC

    profile = workflow_jobs_core.resolve_workflow_execution_profile(
        "run",
        {"source": "ui", "execution_mode": "docker"},
        {},
        environ={workflow_jobs_core.UI_WORKFLOW_DEBUG_LOCAL_ENV: "1"},
    )
    assert profile["source"] == "ui"
    assert profile["profile"] == "default"
    assert profile["flags"]["sandbox"] == "docker"

    profile = workflow_jobs_core.resolve_workflow_execution_profile(
        "run",
        {"source": "ui", "execution_mode": "debug"},
        {},
        environ={workflow_jobs_core.UI_WORKFLOW_DEBUG_LOCAL_ENV: "0"},
    )
    assert profile["profile"] == "default"
    assert "ui_debug_local_disabled" in (profile.get("warnings") or [])

    profile = workflow_jobs_core.resolve_workflow_execution_profile(
        "run",
        {},
        {"sandbox": "docker"},
        environ={workflow_jobs_core.UI_WORKFLOW_DEBUG_LOCAL_ENV: "1"},
    )
    assert profile["source"] == "cli"
    assert profile["profile"] == "default"
    assert profile["flags"]["sandbox"] == "docker"


def test_workflow_job_control_submit_and_cleanup_paths():
    app = _make_app()
    run_dir = ROOT / "runs" / "unit_u6_workflow_control"
    bundle = run_dir / "agent_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    job_id = "wf_control_u6"
    try:
        with app.workflow_lock:
            job = _workflow_job(job_id)
            job["kind"] = "run"
            job["status"] = "running"
            job["phase"] = "agent_waiting"
            job["interactive_controls"] = True
            job["execution_profile"] = "ui_debug_local"
            job["sandbox_mode"] = "local"
            job["request_source"] = "ui"
            job["run_dir"] = str(run_dir.relative_to(ROOT))
            app.workflow_jobs[job_id] = job
            app.workflow_job_order.append(job_id)

        out = workflow_jobs_core.submit_workflow_job(app, job_id)
        assert out["ok"] is True
        assert out["status"] == "verifying"
        signal_path = bundle / "submit.signal"
        assert signal_path.is_file()
        assert signal_path.read_text(encoding="utf-8") == ""

        out = workflow_jobs_core.cleanup_workflow_job(app, job_id)
        assert out["ok"] is True
        assert out["status"] == "cleaning"
        payload = signal_path.read_text(encoding="utf-8")
        assert '"action": "cleanup"' in payload

        with app.workflow_lock:
            app.workflow_jobs[job_id]["phase"] = "agent_running"
        blocked = workflow_jobs_core.submit_workflow_job(app, job_id)
        assert blocked["http_status"] == 409
        assert "not waiting for submit" in blocked["error"]
        with patch("app.runner_core.workflow_jobs._interrupt_workflow_job", return_value=(True, None)) as mock_interrupt:
            out = workflow_jobs_core.cleanup_workflow_job(app, job_id)
        assert out["ok"] is True
        assert out["status"] == "cleaning"
        mock_interrupt.assert_called_once()

        missing = workflow_jobs_core.submit_workflow_job(app, "missing")
        assert missing["http_status"] == 404
        assert missing["error"] == "Workflow job not found"
    finally:
        (bundle / "submit.signal").unlink(missing_ok=True)
        try:
            bundle.rmdir()
        except Exception:
            pass
        try:
            run_dir.rmdir()
        except Exception:
            pass


def test_workflow_job_snapshot_includes_prompt_metadata():
    run_dir = ROOT / "runs" / "unit_u7_prompt_snapshot"
    bundle = run_dir / "agent_bundle"
    prompt_path = bundle / "PROMPT.md"
    bundle.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("# prompt\n\nhello", encoding="utf-8")
    try:
        job = _workflow_job("wf_prompt_snapshot")
        job["kind"] = "run"
        job["run_dir"] = str(run_dir.relative_to(ROOT))
        snap = workflow_jobs_core.workflow_job_snapshot(job)
        prompt = snap.get("prompt") or {}
        assert prompt.get("available") is True
        assert prompt.get("path") == str(prompt_path.relative_to(ROOT))
        assert int(prompt.get("size_bytes") or 0) > 0
        assert isinstance(prompt.get("updated_at"), str) and prompt.get("updated_at")
    finally:
        prompt_path.unlink(missing_ok=True)
        try:
            bundle.rmdir()
        except Exception:
            pass
        try:
            run_dir.rmdir()
        except Exception:
            pass


def test_get_workflow_job_prompt_returns_text_and_pending_states():
    app = _make_app()
    run_dir = ROOT / "runs" / "unit_u7_prompt_read"
    bundle = run_dir / "agent_bundle"
    prompt_path = bundle / "PROMPT.md"
    bundle.mkdir(parents=True, exist_ok=True)
    prompt_text = "# prompt\n\nline-1\nline-2\nline-3"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    job_id = "wf_prompt_read"
    try:
        with app.workflow_lock:
            job = _workflow_job(job_id)
            job["kind"] = "run"
            job["status"] = "running"
            job["phase"] = "agent_waiting"
            job["workflow_name"] = "wf_prompt_read_unique_u7"
            job["run_dir"] = str(run_dir.relative_to(ROOT))
            app.workflow_jobs[job_id] = job
            app.workflow_job_order.append(job_id)

        out = workflow_jobs_core.get_workflow_job_prompt(app, job_id, max_chars=12)
        assert out.get("ok") is True
        assert out.get("available") is True
        assert out.get("truncated") is True
        assert len(out.get("prompt") or "") == 12
        assert out.get("phase") == "agent_waiting"
        assert out.get("path") == str(prompt_path.relative_to(ROOT))

        prompt_path.unlink(missing_ok=True)
        pending = workflow_jobs_core.get_workflow_job_prompt(app, job_id, max_chars=12)
        assert pending.get("ok") is True
        assert pending.get("available") is False
        assert pending.get("reason") == "prompt_not_ready"
        assert pending.get("path") == str(prompt_path.relative_to(ROOT))

        with app.workflow_lock:
            app.workflow_jobs[job_id]["run_dir"] = None
        no_run_dir = workflow_jobs_core.get_workflow_job_prompt(app, job_id, max_chars=12)
        assert no_run_dir.get("ok") is True
        assert no_run_dir.get("available") is False
        assert no_run_dir.get("reason") == "run_dir_not_ready"

        missing = workflow_jobs_core.get_workflow_job_prompt(app, "missing")
        assert missing.get("http_status") == 404
        assert missing.get("error") == "Workflow job not found"
    finally:
        prompt_path.unlink(missing_ok=True)
        try:
            bundle.rmdir()
        except Exception:
            pass
        try:
            run_dir.rmdir()
        except Exception:
            pass


def test_run_workflow_job_success_emits_expected_events_and_artifacts():
    app = _make_app()
    job_id = "wf_success"
    with app.workflow_lock:
        app.workflow_jobs[job_id] = _workflow_job(job_id)
        app.workflow_job_order.append(job_id)

    _FakePopen.lines = [
        '[orchestrator] stage=setup_start\n',
        '{"workflow_state_path":"runs/wf/workflow_state.json"}\n',
        "[orchestrator] stage=done\n",
    ]
    _FakePopen.exit_code = 0
    _FakePopen.raise_exc = None
    with patch("app.runner_core.workflow_jobs.Popen", _FakePopen):
        workflow_jobs_core.run_workflow_job(app, job_id, ["python3", "orchestrator.py", "workflow-run"])

    with app.workflow_lock:
        job = app.workflow_jobs[job_id]
        events = list(app.workflow_event_history)
    assert job["status"] == "completed"
    assert job["phase"] == "done"
    assert job["workflow_state_path"] == "runs/wf/workflow_state.json"
    assert _FakePopen.last_kwargs.get("start_new_session") is True
    event_types = [ev.get("type") for ev in events]
    for required in ("log_append", "job_phase", "job_upsert", "job_complete", "invalidate_workflow_files"):
        assert required in event_types


def test_run_workflow_job_failure_and_exception_paths():
    app = _make_app()
    job_id = "wf_fail"
    with app.workflow_lock:
        app.workflow_jobs[job_id] = _workflow_job(job_id)
        app.workflow_job_order.append(job_id)

    _FakePopen.lines = ["[orchestrator] stage=setup_start\n"]
    _FakePopen.exit_code = 2
    _FakePopen.raise_exc = None
    with patch("app.runner_core.workflow_jobs.Popen", _FakePopen):
        workflow_jobs_core.run_workflow_job(app, job_id, ["python3", "orchestrator.py", "workflow-run"])
    with app.workflow_lock:
        failed_job = app.workflow_jobs[job_id]
    assert failed_job["status"] == "failed"
    assert failed_job["error"] == "workflow command failed"

    app2 = _make_app()
    job2 = "wf_exception"
    with app2.workflow_lock:
        app2.workflow_jobs[job2] = _workflow_job(job2)
        app2.workflow_job_order.append(job2)
    _FakePopen.raise_exc = RuntimeError("boom")
    with patch("app.runner_core.workflow_jobs.Popen", _FakePopen):
        workflow_jobs_core.run_workflow_job(app2, job2, ["python3", "orchestrator.py", "workflow-run"])
    with app2.workflow_lock:
        crashed = app2.workflow_jobs[job2]
        events = list(app2.workflow_event_history)
    assert crashed["status"] == "failed"
    assert "boom" in (crashed.get("error") or "")
    assert any(ev.get("type") == "error" for ev in events)
