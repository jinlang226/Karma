from types import SimpleNamespace
from unittest.mock import patch

from app.orchestrator_core import runtime_glue


def test_runtime_glue_run_workflow_stage_forwards_stage_run_dir():
    captured = {}

    def _fake_run_workflow_stage(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"status": "ready", "run_dir": "runs/stage"}

    with patch.object(
        runtime_glue,
        "_workflow_run_run_workflow_stage",
        side_effect=_fake_run_workflow_stage,
    ):
        out = runtime_glue._run_workflow_stage(
            app=object(),
            row={"stage": {"id": "s1", "case_id": "demo"}},
            args=SimpleNamespace(),
            skip_unit_ids=["u1"],
            defer_cleanup=False,
            stage_run_dir="runs/workflow/stage_runs/01_s1",
        )

    assert out["status"] == "ready"
    assert captured["kwargs"]["skip_unit_ids"] == ["u1"]
    assert captured["kwargs"]["defer_cleanup"] is False
    assert captured["kwargs"]["stage_run_dir"] == "runs/workflow/stage_runs/01_s1"
    assert captured["kwargs"]["wait_for_status_fn"] is runtime_glue._wait_for_status
    assert captured["kwargs"]["stage_setup_timeout_fn"] is runtime_glue._stage_setup_timeout


def test_stage_oracle_stateless_runs_after_hooks_when_oracle_fails():
    calls = []

    def _fake_run(commands, *_args, **_kwargs):
        calls.append(list(commands))
        idx = len(calls)
        if idx == 1:
            return True, "ok", None
        if idx == 2:
            return False, "nonzero", "exit=1"
        return True, "ok", None

    verify_cfg = {
        "commands": [{"command": ["bash", "-lc", "verify"]}],
        "before_commands": [{"command": ["bash", "-lc", "before"]}],
        "after_commands": [{"command": ["bash", "-lc", "after"]}],
        "after_failure_mode": "warn",
    }
    with patch.object(runtime_glue, "resolve_oracle_verify", return_value=verify_cfg), patch.object(
        runtime_glue, "_run_command_list_logged", side_effect=_fake_run
    ):
        out = runtime_glue._run_stage_oracle_stateless(case_data={}, log_path="runs/test.log")

    assert out["status"] == "fail"
    assert out["reason"] == "oracle exit=1"
    assert len(calls) == 3
    assert calls[2] == verify_cfg["after_commands"]


def test_stage_oracle_stateless_runs_after_hooks_when_before_fails():
    calls = []

    def _fake_run(commands, *_args, **_kwargs):
        calls.append(list(commands))
        idx = len(calls)
        if idx == 1:
            return False, "nonzero", "exit=2"
        return True, "ok", None

    verify_cfg = {
        "commands": [{"command": ["bash", "-lc", "verify"]}],
        "before_commands": [{"command": ["bash", "-lc", "before"]}],
        "after_commands": [{"command": ["bash", "-lc", "after"]}],
        "after_failure_mode": "warn",
    }
    with patch.object(runtime_glue, "resolve_oracle_verify", return_value=verify_cfg), patch.object(
        runtime_glue, "_run_command_list_logged", side_effect=_fake_run
    ):
        out = runtime_glue._run_stage_oracle_stateless(case_data={}, log_path="runs/test.log")

    assert out["status"] == "error"
    assert out["reason"] == "before-hook exit=2"
    assert len(calls) == 2
    assert calls[1] == verify_cfg["after_commands"]


def test_build_single_case_workflow_plan_expands_multi_role_namespace_contract():
    captured = {}

    class _FakeApp:
        def get_case(self, _case_id):
            return {
                "service": "spark",
                "case": "spark_multi_tenant_job_execution",
            }

    case_data = {
        "namespace_contract": {
            "base_namespace": "spark-team",
            "default_role": "team_a",
            "required_roles": ["team_a", "team_b"],
        }
    }

    def _fake_load_stage_case_row(_app, _stage):
        return {
            "case_data": case_data,
            "resolved_params": {},
            "param_warnings": [],
        }

    def _fake_build_single_stage_plan(case_id, _case_data, _args, **kwargs):
        captured["namespace_context"] = kwargs.get("namespace_context")
        return {
            "stages": [
                {
                    "id": "stage_single",
                    "case_id": case_id,
                    "service": "spark",
                    "case": "spark_multi_tenant_job_execution",
                    "max_attempts": 1,
                }
            ]
        }

    with patch.object(runtime_glue, "_load_stage_case_row", side_effect=_fake_load_stage_case_row), patch.object(
        runtime_glue,
        "_execution_plan_build_single_stage_plan",
        side_effect=_fake_build_single_stage_plan,
    ):
        runtime_glue._build_single_case_workflow_plan(
            _FakeApp(),
            "spark|spark_multi_tenant_job_execution|test.yaml",
            SimpleNamespace(max_attempts=1),
        )

    assert captured["namespace_context"] == {
        "default_role": "team_a",
        "roles": {
            "team_a": "spark-team-team-a",
            "team_b": "spark-team-team-b",
        },
    }
