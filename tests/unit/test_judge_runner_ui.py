import json
import shutil
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.runner_core import judge_jobs as judge_jobs_core
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_list_judge_runs_and_batches():
    app = _make_app()

    run_dir = RUNS_DIR / "unit_judge_ui_run"
    batch_dir = RUNS_DIR / "batch_2099-01-01T00-00-00Z"
    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(batch_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    batch_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "service": "rabbitmq-experiments",
                "case": "manual_monitoring",
                "status": "passed",
                "setup_started_at": "2026-02-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    judge_dir = run_dir / "judge"
    judge_dir.mkdir(parents=True, exist_ok=True)
    (judge_dir / "result_v1.json").write_text(
        json.dumps(
            {
                "judge_status": "ok",
                "evaluated_at": "2026-02-18T00:05:00Z",
                "scores": {"final_score": 4.5},
            }
        ),
        encoding="utf-8",
    )
    (judge_dir / "summary.md").write_text("ok\n", encoding="utf-8")

    (batch_dir / "batch_index.json").write_text(
        json.dumps([{"run_dir": str(run_dir.relative_to(ROOT)), "service": "rabbitmq-experiments", "case": "manual_monitoring"}]),
        encoding="utf-8",
    )
    (batch_dir / "judge_index.json").write_text(
        json.dumps([{"run_dir": str(run_dir.relative_to(ROOT)), "judge_status": "ok", "final_score": 4.5}]),
        encoding="utf-8",
    )
    (batch_dir / "judge_summary.json").write_text(
        json.dumps({"average_final_score": 4.5, "generated_at": "2026-02-18T00:06:00Z"}),
        encoding="utf-8",
    )

    runs = app.list_judge_runs()
    batches = app.list_judge_batches()

    hit_run = next(item for item in runs if item["run_dir"].endswith("unit_judge_ui_run"))
    assert hit_run["service"] == "rabbitmq-experiments"
    assert hit_run["judge_status"] == "ok"
    assert hit_run["judge_score"] == 4.5

    hit_batch = next(item for item in batches if item["batch_dir"].endswith("batch_2099-01-01T00-00-00Z"))
    assert hit_batch["run_count"] == 1
    assert hit_batch["judged_count"] == 1
    assert hit_batch["average_final_score"] == 4.5

    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(batch_dir, ignore_errors=True)


def test_judge_preview_and_start_job():
    app = _make_app()
    run_dir = RUNS_DIR / "unit_judge_ui_start"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps({"service": "svc", "case": "case"}), encoding="utf-8")

    preview = app.judge_preview(
        {
            "target_type": "run",
            "target_path": str(run_dir.relative_to(ROOT)),
            "dry_run": True,
            "judge_env_file": "judge.env",
        }
    )
    assert preview["ok"] is True
    assert "--dry-run" in preview["command_one_line"]
    assert "--judge-env-file" in preview["command_one_line"]

    with patch("threading.Thread.start", lambda self: None):
        started = app.start_judge(
            {
                "target_type": "run",
                "target_path": str(run_dir.relative_to(ROOT)),
                "dry_run": True,
            }
        )
    assert started.get("ok") is True
    jobs = app.list_judge_jobs()
    assert jobs
    assert jobs[0]["status"] == "running"

    blocked = app.start_judge(
        {
            "target_type": "run",
            "target_path": str(run_dir.relative_to(ROOT)),
            "dry_run": True,
        }
    )
    assert "error" in blocked

    shutil.rmtree(run_dir, ignore_errors=True)


def test_judge_stream_snapshot_and_events():
    app = _make_app()
    job_id = "judge_stream_job_1"
    with app.judge_lock:
        job = {
            "id": job_id,
            "status": "running",
            "target_type": "run",
            "target_path": "runs/fake",
            "dry_run": True,
            "judge_env_file": "",
            "tokens": ["python3", "scripts/judge.py", "run", "--run-dir", "runs/fake", "--dry-run"],
            "started_at": "2026-02-18T00:00:00Z",
            "finished_at": None,
            "exit_code": None,
            "error": None,
            "log_lines": [],
            "progress": [],
            "_progress_map": {},
        }
        app.judge_jobs[job_id] = job
        app.judge_job_order.append(job_id)
        judge_jobs_core.push_judge_event_locked(
            app,
            "job_upsert",
            {"job": judge_jobs_core.judge_job_snapshot(job)},
        )

    snap = app.get_judge_stream_snapshot()
    assert snap["seq"] >= 1
    assert any(item.get("id") == job_id for item in snap.get("jobs", []))

    events = app.get_judge_events_since(0, timeout_sec=0.0)
    assert events["reset"] is False
    assert events["events"]
    assert any(ev.get("type") == "job_upsert" for ev in events["events"])

    with app.judge_lock:
        judge_jobs_core.push_judge_event_locked(app, "job_log", {"job_id": job_id, "line": "hello"})
    tail = app.get_judge_events_since(events["current_seq"], timeout_sec=0.0)
    assert any(ev.get("type") == "job_log" for ev in tail["events"])

    with app.judge_lock:
        app.judge_event_limit = 2
        judge_jobs_core.push_judge_event_locked(app, "heartbeat", {"seq": 1})
        judge_jobs_core.push_judge_event_locked(app, "heartbeat", {"seq": 2})
        judge_jobs_core.push_judge_event_locked(app, "heartbeat", {"seq": 3})
    old = app.get_judge_events_since(0, timeout_sec=0.0)
    assert old["reset"] is True
