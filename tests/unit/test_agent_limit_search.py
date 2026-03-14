import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "agent_limit_search.py"
    spec = importlib.util.spec_from_file_location("agent_limit_search", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to import {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_runner(tmp_path):
    mod = _load_module()
    workflow_path = tmp_path / "workflow.yaml"
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
    args = argparse.Namespace(
        base_workflow=str(workflow_path),
        work_dir=str(tmp_path / "work"),
        initial_factor=1,
        max_factor=8,
        max_stage_count=0,
        max_search_steps=8,
        run_timeout_sec=0,
        python_bin="python3",
        orchestrator_bin="orchestrator.py",
        sandbox="docker",
        stage_failure_mode="terminate",
        final_sweep_mode="off",
        max_attempts=1,
        orchestrator_arg=[],
        env=[],
        dry_run=False,
    )
    return mod, mod.AgentLimitSearch(args)


def _outcome(
    mod,
    *,
    run_index,
    classification,
    passed,
    status="failed",
    terminal_reason=None,
    result_payload=None,
):
    return mod.RunOutcome(
        run_index=run_index,
        factor=1,
        stage_count=1,
        attempt_kind="primary",
        workflow_path="workflow.yaml",
        command=["python3", "orchestrator.py"],
        returncode=0,
        status=status,
        passed=passed,
        cleanup_status="done",
        terminal_reason=terminal_reason,
        failed_stage_id="stage_1",
        failed_stage_status="failed_exhausted",
        failed_stage_reason="oracle_fail",
        failed_stage_source="workflow_stage_results",
        active_stage_index=1,
        active_stage_id="stage_1",
        parse_error=None,
        log_path="run.log",
        result_payload=result_payload if result_payload is not None else {},
        classification=classification,
    )


def test_classify_setup_failure_has_priority():
    with tempfile.TemporaryDirectory() as tmp:
        mod, _runner = _make_runner(Path(tmp))
        classification = mod.AgentLimitSearch._classify_outcome(
            passed=False,
            status="failed",
            parse_error=None,
            cleanup_status="done",
            terminal_reason="agent_exited",
            failed_stage_reason="stage_setup_failed",
            result_payload={"terminal_base_status": "setup_failed"},
        )
        assert classification == "precondition_failure"


def test_classify_agent_exited_by_exit_code():
    with tempfile.TemporaryDirectory() as tmp:
        mod, _runner = _make_runner(Path(tmp))
        give_up = mod.AgentLimitSearch._classify_outcome(
            passed=False,
            status="workflow_fatal",
            parse_error=None,
            cleanup_status="done",
            terminal_reason="agent_exited",
            failed_stage_reason=None,
            result_payload={"agent_exit_code": 0},
        )
        runtime = mod.AgentLimitSearch._classify_outcome(
            passed=False,
            status="workflow_fatal",
            parse_error=None,
            cleanup_status="done",
            terminal_reason="agent_exited",
            failed_stage_reason=None,
            result_payload={"agent_exit_code": 127},
        )
        assert give_up == "agent_give_up"
        assert runtime == "agent_runtime_error"


def test_run_search_aborts_on_precondition_failure():
    with tempfile.TemporaryDirectory() as tmp:
        mod, runner = _make_runner(Path(tmp))
        outcomes = [
            _outcome(mod, run_index=1, classification="precondition_failure", passed=False, status="failed"),
        ]

        def _fake_run_once(*_args, **_kwargs):
            return outcomes.pop(0)

        runner._run_once = _fake_run_once
        summary = runner.run_search()
        assert summary["stop_reason"] == "precondition_failed_abort"
        assert summary["limit_valid"] is False
        assert summary["limit_factor"] is None
        assert summary["counted_run_count"] == 1
        assert summary["uncounted_run_count"] == 0


def test_run_search_agent_exit_once_is_uncounted():
    with tempfile.TemporaryDirectory() as tmp:
        mod, runner = _make_runner(Path(tmp))
        outcomes = [
            _outcome(
                mod,
                run_index=1,
                classification="agent_runtime_error",
                passed=False,
                status="workflow_fatal",
                terminal_reason="agent_exited",
            ),
            _outcome(mod, run_index=2, classification="countable_failure", passed=False, status="failed"),
            _outcome(mod, run_index=3, classification="countable_failure", passed=False, status="failed"),
        ]

        def _fake_run_once(*_args, **_kwargs):
            return outcomes.pop(0)

        runner._run_once = _fake_run_once
        summary = runner.run_search()
        assert summary["stop_reason"] == "failed_twice_same_factor"
        assert summary["limit_valid"] is True
        assert summary["limit_factor"] == 1
        assert summary["counted_run_count"] == 2
        assert summary["uncounted_run_count"] == 1
        assert runner.history[0].counted_for_limit is False
        assert runner.history[1].counted_for_limit is True
        assert runner.history[2].counted_for_limit is True


def test_run_search_aborts_when_agent_exits_twice():
    with tempfile.TemporaryDirectory() as tmp:
        mod, runner = _make_runner(Path(tmp))
        outcomes = [
            _outcome(
                mod,
                run_index=1,
                classification="agent_runtime_error",
                passed=False,
                status="workflow_fatal",
                terminal_reason="agent_exited",
            ),
            _outcome(
                mod,
                run_index=2,
                classification="agent_runtime_error",
                passed=False,
                status="workflow_fatal",
                terminal_reason="agent_exited",
            ),
        ]

        def _fake_run_once(*_args, **_kwargs):
            return outcomes.pop(0)

        runner._run_once = _fake_run_once
        summary = runner.run_search()
        assert summary["stop_reason"] == "agent_exited_twice_abort"
        assert summary["limit_valid"] is False
        assert summary["limit_factor"] is None
        assert summary["counted_run_count"] == 0
        assert summary["uncounted_run_count"] == 2


def test_classify_infra_abort_conditions():
    with tempfile.TemporaryDirectory() as tmp:
        mod, _runner = _make_runner(Path(tmp))
        c1 = mod.AgentLimitSearch._classify_outcome(
            passed=False,
            status="parse_error",
            parse_error=None,
            cleanup_status="done",
            terminal_reason=None,
            failed_stage_reason=None,
            result_payload={},
        )
        c2 = mod.AgentLimitSearch._classify_outcome(
            passed=False,
            status="workflow_fatal",
            parse_error="unable to parse workflow result JSON",
            cleanup_status="done",
            terminal_reason=None,
            failed_stage_reason=None,
            result_payload={},
        )
        c3 = mod.AgentLimitSearch._classify_outcome(
            passed=False,
            status="failed",
            parse_error=None,
            cleanup_status="partial",
            terminal_reason=None,
            failed_stage_reason=None,
            result_payload={},
        )
        assert c1 == "infra_abort"
        assert c2 == "infra_abort"
        assert c3 == "infra_abort"


def test_run_search_aborts_on_infra_failure():
    with tempfile.TemporaryDirectory() as tmp:
        mod, runner = _make_runner(Path(tmp))
        outcomes = [
            _outcome(mod, run_index=1, classification="infra_abort", passed=False, status="process_error"),
        ]

        def _fake_run_once(*_args, **_kwargs):
            return outcomes.pop(0)

        runner._run_once = _fake_run_once
        summary = runner.run_search()
        assert summary["stop_reason"] == "infra_abort"
        assert summary["limit_valid"] is False
        assert summary["limit_factor"] is None
