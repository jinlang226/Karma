import importlib.util
import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "agent_single_stage_reliability.py"
    spec = importlib.util.spec_from_file_location("agent_single_stage_reliability", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to import {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_workflow(path: Path, stage_ids: list[str]) -> None:
    payload = {
        "apiVersion": "benchmark/v1alpha1",
        "kind": "Workflow",
        "metadata": {"name": path.stem},
        "spec": {
            "stages": [{"id": sid, "service": "demo", "case": "demo-case"} for sid in stage_ids],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _args(tmp_path: Path, *, resume: bool, runs: int) -> Namespace:
    workflow = tmp_path / "single.yaml"
    _write_workflow(workflow, ["stage_1"])
    return Namespace(
        workflow=str(workflow),
        runs=runs,
        work_dir=str(tmp_path / "work"),
        python_bin=sys.executable,
        orchestrator="orchestrator.py",
        sandbox="docker",
        max_attempts=1,
        stage_failure_mode="terminate",
        final_sweep_mode="off",
        run_timeout_sec=0,
        orchestrator_arg=[],
        resume=resume,
        dry_run=True,
    )


def _outcome_dict(mod, *, attempt_index: int) -> dict:
    return mod.RunOutcome(
        attempt_index=attempt_index,
        stage_count=1,
        workflow_path="/tmp/single.yaml",
        command=["python", "orchestrator.py", "workflow-run"],
        returncode=0,
        status="passed",
        passed=True,
        cleanup_status="done",
        terminal_reason="workflow_complete",
        failed_stage_id=None,
        failed_stage_status=None,
        failed_stage_reason=None,
        failed_stage_source=None,
        active_stage_index=1,
        active_stage_id="stage_1",
        failure_stage_index=1,
        parse_error=None,
        log_path=f"/tmp/run_{attempt_index:04d}.log",
        result_payload={"status": "passed"},
        classification="passed",
        dry_run=False,
    ).to_dict()


def test_parse_trailing_json_array_accepts_trailing_non_json_output():
    mod = _load_module()
    text = """
noise before
[
  {
    "workflow": "/tmp/workflow.yaml",
    "result": {
      "status": "passed"
    }
  }
]
# trailing docker output
"""
    parsed = mod._parse_trailing_json_array(text)
    assert parsed is not None
    assert parsed[0]["workflow"] == "/tmp/workflow.yaml"
    assert parsed[0]["result"]["status"] == "passed"


def test_runner_requires_single_stage_workflow():
    mod = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        workflow = tmp_path / "two-stage.yaml"
        _write_workflow(workflow, ["stage_1", "stage_2"])
        args = Namespace(
            workflow=str(workflow),
            runs=10,
            work_dir=str(tmp_path / "work"),
            python_bin=sys.executable,
            orchestrator="orchestrator.py",
            sandbox="docker",
            max_attempts=1,
            stage_failure_mode="terminate",
            final_sweep_mode="off",
            run_timeout_sec=0,
            orchestrator_arg=[],
            resume=False,
            dry_run=True,
        )
        try:
            mod.SingleStageReliabilityRunner(args)
            assert False, "expected ValueError for multi-stage workflow"
        except ValueError as exc:
            assert "exactly one stage" in str(exc)


def test_resume_continues_from_existing_attempts():
    mod = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        args = _args(tmp_path, resume=True, runs=5)
        work_dir = Path(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        history_path = work_dir / "history.jsonl"
        existing = [_outcome_dict(mod, attempt_index=1), _outcome_dict(mod, attempt_index=2), _outcome_dict(mod, attempt_index=3)]
        history_path.write_text(
            "\n".join(json.dumps(item, sort_keys=True) for item in existing) + "\n",
            encoding="utf-8",
        )

        runner = mod.SingleStageReliabilityRunner(args)
        attempts_called: list[int] = []

        def _fake_run_once(*, attempt_index: int):
            attempts_called.append(attempt_index)
            outcome = mod.RunOutcome.from_dict(_outcome_dict(mod, attempt_index=attempt_index))
            runner._record(outcome)
            return outcome

        runner._run_once = _fake_run_once
        summary = runner.run()
        assert attempts_called == [4, 5]
        assert summary["summary"]["total_runs"] == 5

        entries = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(entries) == 5
        assert [entry["attempt_index"] for entry in entries][-2:] == [4, 5]


def test_non_resume_starts_fresh_and_rewrites_history():
    mod = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        args = _args(tmp_path, resume=False, runs=1)
        work_dir = Path(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        history_path = work_dir / "history.jsonl"
        history_path.write_text(json.dumps(_outcome_dict(mod, attempt_index=9)) + "\n", encoding="utf-8")

        runner = mod.SingleStageReliabilityRunner(args)
        summary = runner.run()

        assert summary["summary"]["total_runs"] == 1
        entries = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(entries) == 1
        assert entries[0]["attempt_index"] == 1
