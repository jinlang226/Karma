import json
import tempfile
from pathlib import Path

from app.orchestrator_core import artifacts


class _FakeTime:
    @staticmethod
    def gmtime():
        return (2026, 2, 22, 3, 4, 5, 0, 0, 0)

    @staticmethod
    def strftime(_fmt, _value):
        return "2026-02-22T03:04:05Z"


def test_write_submit_result_and_append_log():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        submit_result = run_dir / "submit_result.json"
        payload = {"status": "failed", "can_retry": True}

        ok_write = artifacts.write_submit_result(submit_result, payload)
        ok_append = artifacts.append_submit_result_log(run_dir, payload)

        assert ok_write is True
        assert ok_append is True
        assert json.loads(submit_result.read_text(encoding="utf-8"))["status"] == "failed"
        lines = (run_dir / "submit_results.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["can_retry"] is True


def test_write_stage_writes_orchestrator_stage_file():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        logs = []

        ok = artifacts.write_stage(
            run_dir,
            "verifying",
            detail="attempt=2",
            time_module=_FakeTime,
            print_fn=lambda msg, flush=True: logs.append((msg, flush)),
        )

        assert ok is True
        payload = json.loads((run_dir / "orchestrator_stage.json").read_text(encoding="utf-8"))
        assert payload["stage"] == "verifying"
        assert payload["detail"] == "attempt=2"
        assert payload["ts"] == "2026-02-22T03:04:05Z"
        assert logs and logs[0][0] == "[orchestrator] stage=verifying"


def test_attach_agent_usage_fields_merges_usage_payload():
    outcome = {"status": "passed", "run_dir": "runs/demo"}

    def _fake_ingest(run_path, root):
        assert str(run_path).endswith("runs/demo")
        assert root == Path("/repo")
        return {"token_usage_total_tokens": 42, "agent_usage_path": "runs/demo/agent_usage.json"}

    merged = artifacts.attach_agent_usage_fields(
        outcome,
        root=Path("/repo"),
        ingest_agent_usage_fn=_fake_ingest,
    )

    assert merged["status"] == "passed"
    assert merged["token_usage_total_tokens"] == 42
