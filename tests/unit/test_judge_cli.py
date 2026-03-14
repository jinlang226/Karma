import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app.judge.cli as judge_cli


class _FakeJudge:
    last_args = None
    instance = None

    def __init__(self):
        self.evaluated = []
        self.batch_payload = None

    @classmethod
    def from_args(cls, args):
        cls.last_args = args
        cls.instance = cls()
        return cls.instance

    def evaluate_run(self, run_dir, service=None, case=None):
        row = {
            "run_dir": str(run_dir),
            "service": service or "svc",
            "case": case or "case",
            "judge_status": "ok",
            "final_score": 4.2,
            "process_quality_score": 4.3,
            "efficiency_score": 4.1,
            "average_confidence": 0.88,
            "result_path": "runs/x/judge/result_v1.json",
            "error": None,
        }
        self.evaluated.append(row)
        return row

    def write_batch_summary(self, batch_dir, run_results):
        self.batch_payload = (str(batch_dir), list(run_results))
        return {
            "judge_index_path": str(Path(batch_dir) / "judge_index.json"),
            "judge_summary_path": str(Path(batch_dir) / "judge_summary.json"),
            "judge_leaderboard_path": str(Path(batch_dir) / "judge_leaderboard.csv"),
        }


def test_judge_cli_run_loads_llm_env_file():
    with TemporaryDirectory() as temp_dir:
        env_path = Path(temp_dir) / "judge.env"
        env_path.write_text(
            "LLM_MODEL=file-model\nLLM_API_KEY=file-key\nLLM_BASE_URL=https://example.com/v1\n",
            encoding="utf-8",
        )

        with patch.object(judge_cli, "TrajectoryJudge", _FakeJudge):
            rc = judge_cli.main(
                [
                    "run",
                    "--run-dir",
                    "runs/fake-run",
                    "--judge-env-file",
                    str(env_path),
                ]
            )

        assert rc == 0
        llm_env = getattr(_FakeJudge.last_args, "_llm_env", {})
        assert llm_env.get("LLM_MODEL") == "file-model"
        assert llm_env.get("LLM_API_KEY") == "file-key"
        assert llm_env.get("LLM_BASE_URL") == "https://example.com/v1"
        assert bool(getattr(_FakeJudge.last_args, "dry_run", False)) is False
        assert len(_FakeJudge.instance.evaluated) == 1


def test_judge_cli_run_auto_loads_repo_judge_env():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        (root / "judge.env").write_text(
            "JUDGE_MODEL=judge-model\nJUDGE_API_KEY=judge-key\nJUDGE_BASE_URL=https://judge.example/v1\n",
            encoding="utf-8",
        )

        with patch.object(judge_cli, "ROOT", root):
            with patch.object(judge_cli, "TrajectoryJudge", _FakeJudge):
                rc = judge_cli.main(["run", "--run-dir", "runs/fake-run"])

        assert rc == 0
        llm_env = getattr(_FakeJudge.last_args, "_llm_env", {})
        assert llm_env.get("JUDGE_MODEL") == "judge-model"
        assert llm_env.get("JUDGE_API_KEY") == "judge-key"


def test_judge_cli_run_legacy_agent_env_fallback():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        legacy = root / "agent_tests" / "react" / "config.env"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("LLM_MODEL=legacy-model\nLLM_API_KEY=legacy-key\n", encoding="utf-8")

        with patch.object(judge_cli, "ROOT", root):
            with patch.object(judge_cli, "TrajectoryJudge", _FakeJudge):
                rc = judge_cli.main(["run", "--run-dir", "runs/fake-run"])

        assert rc == 0
        llm_env = getattr(_FakeJudge.last_args, "_llm_env", {})
        assert llm_env.get("LLM_MODEL") == "legacy-model"


def test_judge_cli_run_accepts_dry_run_flag():
    with patch.object(judge_cli, "TrajectoryJudge", _FakeJudge):
        rc = judge_cli.main(
            [
                "run",
                "--run-dir",
                "runs/fake-run",
                "--dry-run",
            ]
        )
    assert rc == 0
    assert bool(getattr(_FakeJudge.last_args, "dry_run", False)) is True


def test_judge_cli_batch_reads_batch_index_and_judges_rows():
    with TemporaryDirectory() as temp_dir:
        batch_dir = Path(temp_dir) / "batch_1"
        batch_dir.mkdir(parents=True, exist_ok=True)
        index_payload = [
            {"run_dir": "runs/a", "service": "svc", "case": "case_a"},
            {"run_dir": "runs/b", "service": "svc", "case": "case_b"},
        ]
        (batch_dir / "batch_index.json").write_text(
            json.dumps(index_payload, indent=2), encoding="utf-8"
        )

        with patch.object(judge_cli, "TrajectoryJudge", _FakeJudge):
            rc = judge_cli.main(["batch", "--batch-dir", str(batch_dir)])

        assert rc == 0
        assert len(_FakeJudge.instance.evaluated) == 2
        batch_dir_arg, run_results = _FakeJudge.instance.batch_payload
        assert Path(batch_dir_arg).resolve() == batch_dir.resolve()
        assert len(run_results) == 2


def test_judge_cli_batch_expands_nested_rows():
    with TemporaryDirectory() as temp_dir:
        batch_dir = Path(temp_dir) / "batch_1"
        batch_dir.mkdir(parents=True, exist_ok=True)
        index_payload = [
            {
                "service": "svc",
                "case": "case_a",
                "status": "sweep_completed",
                "runs": [
                    {"run_dir": "runs/a"},
                    {"run_dir": "runs/b"},
                ],
            },
            {
                "service": "svc",
                "case": "case_b",
                "result": {
                    "status": "passed",
                    "run_dir": "runs/c",
                },
            },
        ]
        (batch_dir / "batch_index.json").write_text(
            json.dumps(index_payload, indent=2), encoding="utf-8"
        )

        with patch.object(judge_cli, "TrajectoryJudge", _FakeJudge):
            rc = judge_cli.main(["batch", "--batch-dir", str(batch_dir)])

        assert rc == 0
        assert len(_FakeJudge.instance.evaluated) == 3


def test_judge_cli_batch_discovers_runs_when_index_has_no_run_dir():
    with TemporaryDirectory() as temp_dir:
        runs_root = Path(temp_dir) / "runs"
        runs_root.mkdir(parents=True, exist_ok=True)
        batch_dir = runs_root / "batch_2026-02-17T07-33-11Z"
        batch_dir.mkdir(parents=True, exist_ok=True)
        (runs_root / "batch_2026-02-17T08-00-00Z").mkdir(parents=True, exist_ok=True)
        index_payload = [
            {
                "service": "rabbitmq-experiments",
                "case": "classic_queue",
                "status": "sweep_completed",
                "run_dir": None,
            }
        ]
        (batch_dir / "batch_index.json").write_text(
            json.dumps(index_payload, indent=2), encoding="utf-8"
        )

        start_ts = int(
            datetime(2026, 2, 17, 7, 33, 11, tzinfo=timezone.utc).timestamp()
        )
        in_window = runs_root / "2026-02-17T07-40-00Z_rabbitmq-experiments_classic_queue_test"
        in_window.mkdir(parents=True, exist_ok=True)
        (in_window / "meta.json").write_text(
            json.dumps(
                {
                    "service": "rabbitmq-experiments",
                    "case": "classic_queue",
                    "setup_started_at_ts": start_ts + 10,
                }
            ),
            encoding="utf-8",
        )

        wrong_case = runs_root / "2026-02-17T07-41-00Z_rabbitmq-experiments_failover_test"
        wrong_case.mkdir(parents=True, exist_ok=True)
        (wrong_case / "meta.json").write_text(
            json.dumps(
                {
                    "service": "rabbitmq-experiments",
                    "case": "failover",
                    "setup_started_at_ts": start_ts + 20,
                }
            ),
            encoding="utf-8",
        )

        out_of_window = runs_root / "2026-02-17T08-10-00Z_rabbitmq-experiments_classic_queue_test"
        out_of_window.mkdir(parents=True, exist_ok=True)
        (out_of_window / "meta.json").write_text(
            json.dumps(
                {
                    "service": "rabbitmq-experiments",
                    "case": "classic_queue",
                    "setup_started_at_ts": start_ts + 3600,
                }
            ),
            encoding="utf-8",
        )

        with patch.object(judge_cli, "TrajectoryJudge", _FakeJudge):
            rc = judge_cli.main(["batch", "--batch-dir", str(batch_dir)])

        assert rc == 0
        assert len(_FakeJudge.instance.evaluated) == 1
        judged = _FakeJudge.instance.evaluated[0]
        assert Path(judged["run_dir"]).resolve() == in_window.resolve()
        assert judged["case"] == "classic_queue"
