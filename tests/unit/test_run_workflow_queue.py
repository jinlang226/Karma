"""Unit tests for scripts/remote-agents/run_workflow_queue.py."""

import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts/remote-agents/run_workflow_queue.py"
MODULE_SPEC = importlib.util.spec_from_file_location("run_workflow_queue", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
run_workflow_queue = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(run_workflow_queue)


def _attempt_record(*, outcome="nonpass", workflow_passed=False, stages=None, error="", run_id="run-1"):
    return {
        "workflow": "pass/example.yaml",
        "worker_kubeconfig": "/tmp/kc",
        "heavy": False,
        "mode": "orchestrator",
        "started_at": "2026-07-07T00:00:00+00:00",
        "finished_at": "2026-07-07T00:00:10+00:00",
        "duration_sec": 10.0,
        "returncode": 0,
        "outcome": outcome,
        "run_id": run_id,
        "run_status": "complete" if workflow_passed else "failed",
        "run_dir": f"/tmp/runs/{run_id}",
        "stages": list(stages or []),
        "stage_total": len(stages or []),
        "stage_passed": len([stage for stage in (stages or []) if stage.get("status") == "pass"]),
        "stage_failed": len([stage for stage in (stages or []) if stage.get("status") in {"fail", "error", "timeout"}]),
        "workflow_passed": workflow_passed,
        "stdout_log": "logs/example.stdout.log",
        "stderr_log": "logs/example.stderr.log",
        "stdout_tail": "",
        "stderr_tail": "",
        "error": error,
        "preflight": {"ok": True, "reason": ""},
        "post_cleanup": {"before": [], "deleted": [], "remaining": [], "timed_out": False},
    }


class TestClassifyRetryableFailure:
    def test_accepts_precondition_failure_without_oracle_verdict(self):
        record = _attempt_record(
            stages=[
                {
                    "stage_id": "stage_1",
                    "status": "error",
                    "oracle_verdict": None,
                    "submitted": False,
                    "error": "precondition units failed",
                }
            ]
        )

        assert run_workflow_queue.classify_retryable_failure(record) == "stage_1: precondition units failed"

    def test_rejects_oracle_failures(self):
        record = _attempt_record(
            stages=[
                {
                    "stage_id": "stage_1",
                    "status": "fail",
                    "oracle_verdict": "fail",
                    "submitted": True,
                    "error": None,
                }
            ]
        )

        assert run_workflow_queue.classify_retryable_failure(record) == ""


class TestRunOneWorkflow:
    def test_retries_transient_failure_until_pass(self, tmp_path: Path):
        args = Namespace(transient_retries=3)
        first = _attempt_record(
            run_id="run-1",
            stages=[
                {
                    "stage_id": "stage_1",
                    "status": "error",
                    "oracle_verdict": None,
                    "submitted": False,
                    "error": "precondition units failed",
                }
            ],
        )
        second = _attempt_record(
            outcome="pass",
            workflow_passed=True,
            run_id="run-2",
            stages=[
                {
                    "stage_id": "stage_1",
                    "status": "pass",
                    "oracle_verdict": "pass",
                    "submitted": True,
                    "error": None,
                }
            ],
        )

        with patch.object(run_workflow_queue, "run_workflow_attempt", side_effect=[first, second]) as mock_attempt:
            record = run_workflow_queue.run_one_workflow(
                args,
                "pass/example.yaml",
                "/tmp/kc",
                heavy=False,
                logs_dir=tmp_path,
            )

        assert mock_attempt.call_count == 2
        assert record["outcome"] == "pass"
        assert record["attempt_count"] == 2
        assert record["transient_retry_count"] == 1
        assert record["retry_exhausted"] is False
        assert [attempt["attempt"] for attempt in record["attempts"]] == [1, 2]
        assert record["attempts"][0]["retryable_failure"] is True
        assert record["attempts"][1]["retryable_failure"] is False

    def test_stops_after_retry_budget(self, tmp_path: Path):
        args = Namespace(transient_retries=2)
        attempts = [
            _attempt_record(
                run_id=f"run-{index}",
                stages=[
                    {
                        "stage_id": "stage_1",
                        "status": "error",
                        "oracle_verdict": None,
                        "submitted": False,
                        "error": "precondition units failed",
                    }
                ],
            )
            for index in range(1, 4)
        ]

        with patch.object(run_workflow_queue, "run_workflow_attempt", side_effect=attempts) as mock_attempt:
            record = run_workflow_queue.run_one_workflow(
                args,
                "pass/example.yaml",
                "/tmp/kc",
                heavy=False,
                logs_dir=tmp_path,
            )

        assert mock_attempt.call_count == 3
        assert record["attempt_count"] == 3
        assert record["retry_exhausted"] is True
        assert record["outcome"] == "nonpass"
        assert record["retryable_failure"] is True


class TestMainLedger:
    def test_writes_one_jsonl_record_for_final_workflow_result(self, tmp_path: Path, monkeypatch):
        workflow_list = tmp_path / "workflows.txt"
        workflow_list.write_text("pass/example.yaml\n")
        kubeconfig = tmp_path / "kc"
        kubeconfig.write_text("apiVersion: v1\n")
        batch_dir = tmp_path / "batch"
        final_record = {
            "workflow": "pass/example.yaml",
            "worker_kubeconfig": str(kubeconfig),
            "heavy": False,
            "mode": "orchestrator",
            "started_at": "2026-07-07T00:00:00+00:00",
            "finished_at": "2026-07-07T00:01:00+00:00",
            "duration_sec": 60.0,
            "returncode": 0,
            "outcome": "pass",
            "run_id": "run-2",
            "run_status": "complete",
            "run_dir": "/tmp/runs/run-2",
            "stages": [],
            "stage_total": 1,
            "stage_passed": 1,
            "stage_failed": 0,
            "workflow_passed": True,
            "stdout_log": "logs/example.attempt-02.stdout.log",
            "stderr_log": "logs/example.attempt-02.stderr.log",
            "stdout_tail": "",
            "stderr_tail": "",
            "error": "",
            "preflight": {"ok": True, "reason": ""},
            "post_cleanup": {"before": [], "deleted": [], "remaining": [], "timed_out": False},
            "attempt": 2,
            "attempt_count": 2,
            "transient_retry_limit": 3,
            "transient_retry_count": 1,
            "retry_exhausted": False,
            "retryable_failure": False,
            "retry_reason": "",
            "attempts": [
                {"attempt": 1, "outcome": "nonpass", "retryable_failure": True, "retry_reason": "stage_1: precondition units failed"},
                {"attempt": 2, "outcome": "pass", "retryable_failure": False, "retry_reason": ""},
            ],
        }

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_workflow_queue.py",
                "--workflow-list",
                str(workflow_list),
                "--kubeconfigs",
                str(kubeconfig),
                "--batch-dir",
                str(batch_dir),
            ],
        )

        with patch.object(run_workflow_queue, "run_one_workflow", return_value=final_record):
            assert run_workflow_queue.main() == 0

        results_lines = (batch_dir / "results.jsonl").read_text().splitlines()
        assert len(results_lines) == 1
        payload = json.loads(results_lines[0])
        assert payload["workflow"] == "pass/example.yaml"
        assert payload["attempt_count"] == 2
        assert len(payload["attempts"]) == 2
