import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from app.settings import ROOT, RUNS_DIR


def _make_run(run_root: Path, service: str, case: str):
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "meta.json").write_text(
        json.dumps(
            {
                "service": service,
                "case": case,
                "status": "passed",
                "attempts": 1,
                "max_attempts": 3,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_root / "agent.log").write_text("[agent] thinking\n[agent] exec\n", encoding="utf-8")


def _parse_json_payload(stdout: str):
    marker = "{\n"
    pos = stdout.rfind(marker)
    if pos < 0:
        pos = stdout.rfind("{")
    if pos < 0:
        raise AssertionError(stdout)
    return json.loads(stdout[pos:])


def test_judge_post_batch_dry_run_writes_batch_artifacts():
    token = uuid.uuid4().hex[:8]
    batch_root = RUNS_DIR / f"batch_2099-01-01T00-00-00Z_it_judge_post_batch_{token}"
    run_a = RUNS_DIR / f"it_judge_post_batch_{token}_a"
    run_b = RUNS_DIR / f"it_judge_post_batch_{token}_b"
    shutil.rmtree(batch_root, ignore_errors=True)
    shutil.rmtree(run_a, ignore_errors=True)
    shutil.rmtree(run_b, ignore_errors=True)
    batch_root.mkdir(parents=True, exist_ok=True)
    _make_run(run_a, "rabbitmq-experiments", "manual_monitoring")
    _make_run(run_b, "rabbitmq-experiments", "manual_policy_sync")
    (batch_root / "batch_index.json").write_text(
        json.dumps(
            [
                {"run_dir": str(run_a.relative_to(ROOT)), "service": "rabbitmq-experiments", "case": "manual_monitoring"},
                {"run_dir": str(run_b.relative_to(ROOT)), "service": "rabbitmq-experiments", "case": "manual_policy_sync"},
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    cmd = ["python3", "scripts/judge.py", "batch", "--batch-dir", str(batch_root), "--dry-run"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=dict(os.environ),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout
        payload = _parse_json_payload(proc.stdout)
        assert payload.get("runs_judged") == 2

        index_path = ROOT / payload["judge_index_path"]
        summary_path = ROOT / payload["judge_summary_path"]
        csv_path = ROOT / payload["judge_leaderboard_path"]
        assert index_path.exists()
        assert summary_path.exists()
        assert csv_path.exists()

        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(index) == 2
        assert all(item.get("judge_status") == "dry_run" for item in index)
    finally:
        shutil.rmtree(batch_root, ignore_errors=True)
        shutil.rmtree(run_a, ignore_errors=True)
        shutil.rmtree(run_b, ignore_errors=True)
