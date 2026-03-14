import csv
import json
import shutil
import uuid

from app.judge.engine import TrajectoryJudge
from app.settings import ROOT, RUNS_DIR


def test_write_batch_summary_excludes_null_scores_from_averages_and_preserves_error_rows():
    batch_root = RUNS_DIR / f"unit_judge_batch_{uuid.uuid4().hex[:8]}"
    shutil.rmtree(batch_root, ignore_errors=True)
    batch_root.mkdir(parents=True, exist_ok=True)

    judge = TrajectoryJudge(
        base_url="http://127.0.0.1:1/v1",
        api_key="dummy",
        model="dummy-model",
        fail_open=True,
    )
    rows = [
        {
            "run_dir": "runs/a",
            "service": "rabbitmq-experiments",
            "case": "manual_monitoring",
            "judge_status": "ok",
            "final_score": 4.0,
            "process_quality_score": 4.5,
            "efficiency_score": 3.0,
            "average_confidence": 0.8,
            "result_path": "runs/a/judge/result_v1.json",
            "error": None,
        },
        {
            "run_dir": "runs/b",
            "service": "rabbitmq-experiments",
            "case": "manual_monitoring",
            "judge_status": "ok",
            "final_score": None,
            "process_quality_score": 4.0,
            "efficiency_score": None,
            "average_confidence": 0.7,
            "result_path": "runs/b/judge/result_v1.json",
            "error": None,
        },
        {
            "run_dir": "runs/c",
            "service": "rabbitmq-experiments",
            "case": "manual_tls_rotation",
            "judge_status": "ok",
            "final_score": 2.0,
            "process_quality_score": 2.0,
            "efficiency_score": 2.0,
            "average_confidence": 0.6,
            "result_path": "runs/c/judge/result_v1.json",
            "error": None,
        },
        {
            "run_dir": "runs/d",
            "service": "rabbitmq-experiments",
            "case": "manual_tls_rotation",
            "judge_status": "error",
            "final_score": None,
            "process_quality_score": None,
            "efficiency_score": None,
            "average_confidence": None,
            "result_path": "runs/d/judge/result_v1.json",
            "error": "provider failure",
        },
    ]

    try:
        out = judge.write_batch_summary(batch_root, rows)
        index_path = ROOT / out["judge_index_path"]
        summary_path = ROOT / out["judge_summary_path"]
        csv_path = ROOT / out["judge_leaderboard_path"]

        assert index_path.exists()
        assert summary_path.exists()
        assert csv_path.exists()

        index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(index_payload) == 4
        assert sum(1 for row in index_payload if row.get("judge_status") == "error") == 1

        summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        # Only non-null final_score rows contribute: (4.0 + 2.0) / 2.
        assert summary_payload["average_final_score"] == 3.0
        assert summary_payload["ok_runs"] == 3
        assert summary_payload["error_runs"] == 1

        by_case = summary_payload["by_case"]
        assert by_case["rabbitmq-experiments/manual_monitoring"]["count"] == 2
        # One score is null; average should use only the valid 4.0 row.
        assert by_case["rabbitmq-experiments/manual_monitoring"]["average_final_score"] == 4.0

        with csv_path.open("r", encoding="utf-8") as handle:
            parsed = list(csv.reader(handle))
        # header + 4 result rows
        assert len(parsed) == 5
        assert parsed[0][0] == "run_dir"
    finally:
        shutil.rmtree(batch_root, ignore_errors=True)
