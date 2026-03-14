import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.runner_core import judge_jobs as judge_jobs_core
from app.settings import ROOT, RUNS_DIR


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


class _FakeJudgePopen:
    lines = []
    exit_code = 0
    raise_exc = None

    def __init__(self, *_args, **_kwargs):
        if self.__class__.raise_exc is not None:
            raise self.__class__.raise_exc
        self.pid = 5150
        self.stdout = _FakeStdout(self.__class__.lines)

    def wait(self):
        return int(self.__class__.exit_code)


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _judge_job(job_id):
    return {
        "id": job_id,
        "status": "running",
        "target_type": "run",
        "target_path": "runs/fake",
        "dry_run": True,
        "judge_env_file": "",
        "tokens": ["python3", "scripts/judge.py", "run", "--run-dir", "runs/fake"],
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": None,
        "exit_code": None,
        "error": None,
        "log_lines": [],
        "progress": [],
        "_progress_map": {},
    }


def test_resolve_judge_target_validation_branches():
    run_dir = RUNS_DIR / "unit_r3_judge_run"
    batch_dir = RUNS_DIR / "batch_2099-01-01T00-00-00Z"
    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(batch_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    batch_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text("{}", encoding="utf-8")
    (batch_dir / "batch_index.json").write_text("[]", encoding="utf-8")
    try:
        path, error = judge_jobs_core.resolve_judge_target("run", str(run_dir.relative_to(ROOT)))
        assert error is None
        assert path == run_dir.resolve()

        path, error = judge_jobs_core.resolve_judge_target("batch", str(batch_dir.relative_to(ROOT)))
        assert error is None
        assert path == batch_dir.resolve()

        path, error = judge_jobs_core.resolve_judge_target("run", "")
        assert path is None
        assert "target_path is required" in error

        path, error = judge_jobs_core.resolve_judge_target("invalid", str(run_dir.relative_to(ROOT)))
        assert path is None
        assert "target_type must be run or batch" in error

        with tempfile.TemporaryDirectory() as outside:
            outside_run = Path(outside)
            (outside_run / "meta.json").write_text("{}", encoding="utf-8")
            path, error = judge_jobs_core.resolve_judge_target("run", str(outside_run))
            assert path is None
            assert "inside repository" in error
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
        shutil.rmtree(batch_dir, ignore_errors=True)


def test_judge_snapshot_truncates_logs_and_hides_internal_progress_map():
    job = {
        "id": "judge_snap",
        "log_lines": [f"line-{i}" for i in range(250)],
        "_progress_map": {"svc/case": 0},
        "progress": [{"label": "svc/case", "status": "ok", "score": 4.2}],
    }
    snap = judge_jobs_core.judge_job_snapshot(job)
    assert "_progress_map" not in snap
    assert len(snap["log_lines"]) == 200
    assert snap["log_truncated"] == 50


def test_judge_event_cursor_edges_and_reset_behavior():
    app = _make_app()
    with app.judge_lock:
        app.judge_event_limit = 2
        judge_jobs_core.push_judge_event_locked(app, "job_upsert", {"job": {"id": "judge-1"}})
        judge_jobs_core.push_judge_event_locked(app, "heartbeat", {"seq": 1})
        judge_jobs_core.push_judge_event_locked(app, "heartbeat", {"seq": 2})

    invalid_cursor = app.get_judge_events_since("bad", timeout_sec=0.0)
    assert invalid_cursor["reset"] is True

    negative_cursor = app.get_judge_events_since(-5, timeout_sec=0.0)
    assert negative_cursor["reset"] is True

    boundary = app.get_judge_events_since(1, timeout_sec=0.0)
    assert boundary["reset"] is False
    assert [ev["seq"] for ev in boundary["events"]] == [2, 3]


def test_run_judge_job_progress_overwrite_and_completion():
    app = _make_app()
    job_id = "judge_success"
    with app.judge_lock:
        app.judge_jobs[job_id] = _judge_job(job_id)
        app.judge_job_order.append(job_id)

    _FakeJudgePopen.lines = [
        "[judge] svc/case_a status=ok score=4.2\n",
        "[judge] svc/case_a status=ok score=4.0\n",
        "plain output\n",
    ]
    _FakeJudgePopen.exit_code = 0
    _FakeJudgePopen.raise_exc = None
    with patch("app.runner_core.judge_jobs.Popen", _FakeJudgePopen):
        judge_jobs_core.run_judge_job(app, job_id, ["python3", "scripts/judge.py", "run", "--run-dir", "runs/fake"])

    with app.judge_lock:
        job = app.judge_jobs[job_id]
        events = list(app.judge_event_history)
    assert job["status"] == "completed"
    assert len(job["progress"]) == 1
    assert job["progress"][0]["label"] == "svc/case_a"
    assert job["progress"][0]["score"] == 4.0
    types = [ev.get("type") for ev in events]
    assert "job_progress" in types
    assert "job_upsert" in types
    assert "invalidate_runs_batches" in types


def test_run_judge_job_failure_and_exception_paths():
    app = _make_app()
    job_id = "judge_fail"
    with app.judge_lock:
        app.judge_jobs[job_id] = _judge_job(job_id)
        app.judge_job_order.append(job_id)

    _FakeJudgePopen.lines = ["[judge] svc/case_b status=fail score=None\n"]
    _FakeJudgePopen.exit_code = 2
    _FakeJudgePopen.raise_exc = None
    with patch("app.runner_core.judge_jobs.Popen", _FakeJudgePopen):
        judge_jobs_core.run_judge_job(app, job_id, ["python3", "scripts/judge.py", "run", "--run-dir", "runs/fake"])
    with app.judge_lock:
        failed = app.judge_jobs[job_id]
    assert failed["status"] == "failed"
    assert failed["error"] == "judge command failed"

    app2 = _make_app()
    job2 = "judge_exception"
    with app2.judge_lock:
        app2.judge_jobs[job2] = _judge_job(job2)
        app2.judge_job_order.append(job2)
    _FakeJudgePopen.raise_exc = RuntimeError("judge boom")
    with patch("app.runner_core.judge_jobs.Popen", _FakeJudgePopen):
        judge_jobs_core.run_judge_job(app2, job2, ["python3", "scripts/judge.py", "run", "--run-dir", "runs/fake"])
    with app2.judge_lock:
        crashed = app2.judge_jobs[job2]
        events = list(app2.judge_event_history)
    assert crashed["status"] == "failed"
    assert "judge boom" in (crashed.get("error") or "")
    assert any(
        ev.get("type") == "invalidate_runs_batches" and (ev.get("data") or {}).get("reason") == "job_failed"
        for ev in events
    )
