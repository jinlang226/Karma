import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path
import tempfile


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "workflow_stage_comparison_sweep.py"
    spec = importlib.util.spec_from_file_location("workflow_stage_comparison_sweep", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to import {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
#0 building with "desktop-linux" instance using docker driver
#1 [internal] load build definition from Dockerfile
"""
    parsed = mod._parse_trailing_json_array(text)
    assert parsed is not None
    assert parsed[0]["workflow"] == "/tmp/workflow.yaml"
    assert parsed[0]["result"]["status"] == "passed"


def test_parse_trailing_json_array_skips_non_orchestrator_arrays():
    mod = _load_module()
    text = """
[{"foo": "bar"}]
more noise
[
  {
    "workflow": "/tmp/workflow.yaml",
    "result": {
      "status": "failed",
      "terminal_reason": "oracle_fail"
    }
  }
]
trailing output
"""
    parsed = mod._parse_trailing_json_array(text)
    assert parsed is not None
    assert parsed[0]["result"]["terminal_reason"] == "oracle_fail"


def _write_workflow(path: Path, stage_ids: list[str]) -> None:
    stages = []
    for stage_id in stage_ids:
        stages.append(
            {
                "id": stage_id,
                "service": "demo",
                "case": "demo-case",
            }
        )
    payload = {
        "apiVersion": "benchmark/v1alpha1",
        "kind": "Workflow",
        "metadata": {"name": path.stem},
        "spec": {"stages": stages},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _args(tmp_path: Path, *, resume: bool, runs_per_workflow: int) -> Namespace:
    single = tmp_path / "single.yaml"
    multi = tmp_path / "multi.yaml"
    _write_workflow(single, ["stage_single"])
    _write_workflow(multi, ["stage_1", "stage_2", "stage_3"])
    return Namespace(
        single_workflow=str(single),
        multi_workflow=str(multi),
        runs_per_workflow=runs_per_workflow,
        work_dir=str(tmp_path / "work"),
        python_bin=sys.executable,
        orchestrator="orchestrator.py",
        sandbox="docker",
        max_attempts=1,
        stage_failure_mode=None,
        final_sweep_mode="full",
        run_timeout_sec=0,
        orchestrator_arg=[],
        resume=resume,
        dry_run=True,
    )


def _outcome_dict(
    mod,
    *,
    workflow_kind: str,
    attempt_index: int,
    stage_count: int,
) -> dict:
    return mod.RunOutcome(
        workflow_kind=workflow_kind,
        attempt_index=attempt_index,
        stage_count=stage_count,
        workflow_path=f"/tmp/{workflow_kind}.yaml",
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
        active_stage_index=stage_count,
        active_stage_id=f"{workflow_kind}_stage",
        failure_stage_index=stage_count,
        parse_error=None,
        log_path=f"/tmp/{workflow_kind}_{attempt_index}.log",
        result_payload={"status": "passed"},
        classification="passed",
        dry_run=False,
    ).to_dict()


def test_resume_continues_from_existing_attempts_without_rewriting_prior_logs():
    mod = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        args = _args(tmp_path, resume=True, runs_per_workflow=5)

        work_dir = Path(args.work_dir)
        log_dir = work_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        sentinel_log = log_dir / "single_run_0001.log"
        sentinel_log.write_text("sentinel", encoding="utf-8")

        history_path = work_dir / "history.jsonl"
        existing = []
        for attempt in range(1, 6):
            existing.append(
                _outcome_dict(mod, workflow_kind="single", attempt_index=attempt, stage_count=1)
            )
        for attempt in range(1, 4):
            existing.append(
                _outcome_dict(mod, workflow_kind="three_stage", attempt_index=attempt, stage_count=3)
            )
        history_path.write_text(
            "\n".join(json.dumps(item, sort_keys=True) for item in existing) + "\n",
            encoding="utf-8",
        )

        runner = mod.SweepRunner(args)
        summary = runner.run()

        # Existing single attempts are complete; only three_stage 4..5 should be added.
        assert summary["single_workflow"]["summary"]["total_runs"] == 5
        assert summary["three_stage_workflow"]["summary"]["total_runs"] == 5
        assert sentinel_log.read_text(encoding="utf-8") == "sentinel"

        entries = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(entries) == 10
        assert [e["attempt_index"] for e in entries if e["workflow_kind"] == "three_stage"][-2:] == [4, 5]


def test_non_resume_starts_fresh_and_truncates_existing_history():
    mod = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        args = _args(tmp_path, resume=False, runs_per_workflow=1)

        work_dir = Path(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        history_path = work_dir / "history.jsonl"
        history_path.write_text(
            json.dumps(_outcome_dict(mod, workflow_kind="single", attempt_index=7, stage_count=1)) + "\n",
            encoding="utf-8",
        )

        runner = mod.SweepRunner(args)
        summary = runner.run()
        assert summary["single_workflow"]["summary"]["total_runs"] == 1
        assert summary["three_stage_workflow"]["summary"]["total_runs"] == 1

        entries = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(entries) == 2
        assert entries[0]["workflow_kind"] == "single"
        assert entries[0]["attempt_index"] == 1
        assert entries[1]["workflow_kind"] == "three_stage"
        assert entries[1]["attempt_index"] == 1
