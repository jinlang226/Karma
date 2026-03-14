import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "agent_fixed_stage_reliability.py"
    spec = importlib.util.spec_from_file_location("agent_fixed_stage_reliability", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to import {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_runner(
    tmp_path: Path,
    *,
    precondition_hard_stop: bool = True,
    max_reruns: int = 3,
    resume: bool = False,
    seed_history: list[dict] | None = None,
):
    mod = _load_module()
    workflow_path = tmp_path / "workflow.yaml"
    work_dir = tmp_path / "work"
    workflow_path.write_text(
        "apiVersion: benchmark/v1alpha1\n"
        "kind: Workflow\n"
        "metadata:\n"
        "  name: test-workflow\n"
        "spec:\n"
        "  stages:\n"
        "    - id: stage_1\n"
        "      service: demo\n"
        "      case: noop\n",
        encoding="utf-8",
    )
    if seed_history:
        work_dir.mkdir(parents=True, exist_ok=True)
        history_path = work_dir / "history.jsonl"
        history_lines = [json.dumps(entry, sort_keys=True) for entry in seed_history]
        history_path.write_text("\n".join(history_lines) + "\n", encoding="utf-8")

    args = mod.build_parser().parse_args(
        [
            "--base-workflow",
            str(workflow_path),
            "--work-dir",
            str(work_dir),
            "--target-stage-count",
            "5",
            "--max-reruns",
            str(max_reruns),
            *(["--precondition-hard-stop"] if precondition_hard_stop else ["--no-precondition-hard-stop"]),
            *(["--resume"] if resume else []),
        ]
    )
    return mod, mod.FixedStageReliabilityRunner(args)


def _precondition_outcome(mod, *, attempt_index: int, hard_stop: bool, retryable: bool):
    return mod.RunOutcome(
        attempt_index=attempt_index,
        stage_count=50,
        workflow_path=f"workflow_attempt_{attempt_index}.yaml",
        command=["python3", "orchestrator.py"],
        returncode=0,
        status="workflow_fatal",
        passed=False,
        cleanup_status="done",
        terminal_reason="next_stage_setup_failed",
        failed_stage_id="stage_1",
        failed_stage_status="fatal_error",
        failed_stage_reason="stage_setup_failed",
        failed_stage_source="workflow_stage_results",
        active_stage_index=1,
        active_stage_id="stage_1",
        parse_error=None,
        log_path=f"run_{attempt_index:04d}.log",
        result_payload={"terminal_base_status": "setup_failed"},
        classification="precondition_failure",
        retryable=retryable,
        hard_stop=hard_stop,
        failure_stage_index=1,
    )


def test_parser_default_and_negated_precondition_hard_stop():
    mod = _load_module()
    parser = mod.build_parser()
    defaults = parser.parse_args([])
    disabled = parser.parse_args(["--no-precondition-hard-stop"])
    resumed = parser.parse_args(["--resume"])
    assert defaults.precondition_hard_stop is True
    assert disabled.precondition_hard_stop is False
    assert defaults.resume is False
    assert resumed.resume is True


def test_classify_precondition_failure_toggle():
    mod = _load_module()
    common = dict(
        passed=False,
        status="workflow_fatal",
        parse_error=None,
        cleanup_status="done",
        terminal_reason="next_stage_setup_failed",
        failed_stage_reason="stage_setup_failed",
        result_payload={"terminal_base_status": "setup_failed"},
    )
    hard_stop_outcome = mod.FixedStageReliabilityRunner._classify_outcome(
        **common, precondition_hard_stop=True
    )
    retryable_outcome = mod.FixedStageReliabilityRunner._classify_outcome(
        **common, precondition_hard_stop=False
    )
    assert hard_stop_outcome == ("precondition_failure", False, True)
    assert retryable_outcome == ("precondition_failure", True, False)


def test_run_retries_precondition_failure_when_not_hard_stop():
    with tempfile.TemporaryDirectory() as tmp:
        mod, runner = _make_runner(Path(tmp), precondition_hard_stop=False, max_reruns=3)
        outcomes = [
            _precondition_outcome(mod, attempt_index=1, hard_stop=False, retryable=True),
            _precondition_outcome(mod, attempt_index=2, hard_stop=False, retryable=True),
            _precondition_outcome(mod, attempt_index=3, hard_stop=False, retryable=True),
        ]

        def _fake_run_once(*_args, **_kwargs):
            return outcomes.pop(0)

        runner._run_once = _fake_run_once
        summary = runner.run()
        assert summary["status"] == "failed_after_max_reruns"
        assert summary["stop_reason"] == "max_reruns_exhausted"
        assert summary["matrix_pause_required"] is False
        assert summary["pause_classification"] is None
        assert summary["attempts_used"] == 3


def test_run_pauses_on_precondition_failure_when_hard_stop():
    with tempfile.TemporaryDirectory() as tmp:
        mod, runner = _make_runner(Path(tmp), precondition_hard_stop=True, max_reruns=3)
        outcomes = [
            _precondition_outcome(mod, attempt_index=1, hard_stop=True, retryable=False),
        ]

        def _fake_run_once(*_args, **_kwargs):
            return outcomes.pop(0)

        runner._run_once = _fake_run_once
        summary = runner.run()
        assert summary["status"] == "matrix_pause_required"
        assert summary["stop_reason"] == "precondition_failure_abort"
        assert summary["matrix_pause_required"] is True
        assert summary["pause_classification"] == "precondition_failure"
        assert summary["attempts_used"] == 1


def test_resume_runs_only_remaining_attempts():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        mod = _load_module()
        seed_history = [
            _precondition_outcome(mod, attempt_index=1, hard_stop=False, retryable=True).to_dict(),
            _precondition_outcome(mod, attempt_index=2, hard_stop=False, retryable=True).to_dict(),
            _precondition_outcome(mod, attempt_index=3, hard_stop=False, retryable=True).to_dict(),
        ]
        mod, runner = _make_runner(
            tmp_path,
            precondition_hard_stop=False,
            max_reruns=5,
            resume=True,
            seed_history=seed_history,
        )
        attempts_called: list[int] = []
        outcomes = [
            _precondition_outcome(mod, attempt_index=4, hard_stop=False, retryable=True),
            _precondition_outcome(mod, attempt_index=5, hard_stop=False, retryable=True),
        ]

        def _fake_run_once(*_args, **kwargs):
            attempts_called.append(int(kwargs["attempt_index"]))
            return outcomes.pop(0)

        runner._run_once = _fake_run_once
        summary = runner.run()
        assert attempts_called == [4, 5]
        assert summary["attempts_used"] == 5
        assert summary["status"] == "failed_after_max_reruns"
        assert summary["stop_reason"] == "max_reruns_exhausted"


def test_resume_reclassifies_precondition_history_with_policy_toggle():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        mod = _load_module()
        seed_history = [
            _precondition_outcome(mod, attempt_index=1, hard_stop=True, retryable=False).to_dict(),
        ]
        mod, runner = _make_runner(
            tmp_path,
            precondition_hard_stop=False,
            max_reruns=3,
            resume=True,
            seed_history=seed_history,
        )
        # Existing history is re-classified under current policy on resume.
        assert runner.history[0].hard_stop is False
        assert runner.history[0].retryable is True
        outcomes = [
            _precondition_outcome(mod, attempt_index=2, hard_stop=False, retryable=True),
            _precondition_outcome(mod, attempt_index=3, hard_stop=False, retryable=True),
        ]

        def _fake_run_once(*_args, **_kwargs):
            return outcomes.pop(0)

        runner._run_once = _fake_run_once
        summary = runner.run()
        assert summary["matrix_pause_required"] is False
        assert summary["status"] == "failed_after_max_reruns"
        assert summary["attempts_used"] == 3
