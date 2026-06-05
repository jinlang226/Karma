"""Unit tests for karma.runtime.case."""

import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from karma.runtime.case import _wait_for_submit, _run_operation_units


class TestWaitForSubmit:
    def test_returns_true_when_file_appears(self, tmp_path):
        submit = tmp_path / "submit.txt"
        submit.write_text("done")
        found, content = _wait_for_submit(submit, agent_timeout_sec=5)
        assert found is True
        assert content == "done"

    def test_returns_false_on_timeout(self, tmp_path):
        submit = tmp_path / "submit.txt"
        found, content = _wait_for_submit(
            submit, agent_timeout_sec=0, poll_interval_sec=0.01
        )
        assert found is False
        assert content is None

    def test_reads_content_correctly(self, tmp_path):
        submit = tmp_path / "submit.txt"
        submit.write_text("agent answer here")
        _, content = _wait_for_submit(submit, agent_timeout_sec=5)
        assert content == "agent answer here"


class TestRunOperationUnits:
    def test_returns_ok_false_when_apply_fails(self, tmp_path):
        # Probe fails (precondition not yet satisfied), so the apply path
        # runs; when apply itself fails the unit is reported as not ok.
        units = [
            {
                "id": "unit:precondition",
                "probe_commands": [{"command": "false", "sleep": 0}],
                "apply_commands": [{"command": "false", "sleep": 0}],
                "verify_commands": [{"command": "echo verify", "sleep": 0}],
                "verify_retries": 1,
                "verify_interval_sec": 0.0,
                "on_probe_fail": "error",
            }
        ]
        log = tmp_path / "ops.log"
        result = _run_operation_units(
            units, role_bindings={}, log_path=log
        )
        assert result["ok"] is False

    def test_returns_ok_true_on_success(self, tmp_path):
        units = [
            {
                "id": "unit:precondition",
                "probe_commands": [{"command": "true", "sleep": 0}],
                "apply_commands": [{"command": "true", "sleep": 0}],
                "verify_commands": [{"command": "true", "sleep": 0}],
                "verify_retries": 1,
                "verify_interval_sec": 0.0,
                "on_probe_fail": "error",
            }
        ]
        log = tmp_path / "ops.log"
        result = _run_operation_units(
            units, role_bindings={}, log_path=log
        )
        assert result["ok"] is True

    def test_skip_on_probe_fail_does_not_error(self, tmp_path):
        units = [
            {
                "id": "unit:precondition",
                "probe_commands": [{"command": "false", "sleep": 0}],
                "apply_commands": [{"command": "true", "sleep": 0}],
                "verify_commands": [{"command": "true", "sleep": 0}],
                "verify_retries": 1,
                "verify_interval_sec": 0.0,
                "on_probe_fail": "skip",
            }
        ]
        log = tmp_path / "ops.log"
        result = _run_operation_units(
            units, role_bindings={}, log_path=log
        )
        assert result["ok"] is True

    def test_empty_units_returns_ok(self, tmp_path):
        log = tmp_path / "ops.log"
        result = _run_operation_units([], role_bindings={}, log_path=log)
        assert result["ok"] is True

    def test_result_contains_output_key(self, tmp_path):
        log = tmp_path / "ops.log"
        result = _run_operation_units([], role_bindings={}, log_path=log)
        assert "output" in result


class TestRunStage:
    """Smoke tests for run_stage error capture behavior."""

    def test_returns_error_status_on_setup_failure(self, tmp_path):
        from karma.runtime.case import run_stage

        row = {
            "stage_id": "stage_1",
            "service": "svc",
            "case_name": "case",
            "case": {"oracle": {}, "precondition_units": [], "decoys": []},
            "namespace_roles": ["default"],
            "adversary_deploy": [],
            "adversary_lift": [],
            "adversary_hint": None,
            "prompt_mode": "progressive",
            "agent_timeout_sec": 1,
            "retries": 0,
        }
        environment = MagicMock()
        environment.bind_namespace_roles.side_effect = RuntimeError("cluster unreachable")

        result = run_stage(
            row,
            run_dir=tmp_path,
            resources_dir=tmp_path,
            agent_meta={"sandbox_mode": "local"},
            sandbox_mode="local",
            environment=environment,
            prior_stage_ids=[],
            stage_prompts=["do the thing"],
            prompt_mode="progressive",
        )
        assert result["status"] == "error"
        assert result["stage_id"] == "stage_1"
        assert "cluster unreachable" in (result.get("error") or "")

    def test_no_agent_run_skips_launch_and_uses_oracle_verdict(self, tmp_path):
        # `resolve_agent(None, sandbox_mode="local")` yields a descriptor with no
        # folder/entrypoint. A no-agent run must NOT try to launch an agent
        # (which previously crashed with FileNotFoundError on "entrypoint.sh");
        # it stands the scenario up and the oracle verdict drives the status.
        from karma.runtime.case import run_stage

        row = {
            "stage_id": "stage_1",
            "service": "demo",
            "case_name": "configmap-update",
            "case": {"prompt": "patch the configmap", "oracle": {}, "precondition_units": [], "decoys": []},
            "namespace_roles": ["default"],
            "adversary_deploy": [],
            "adversary_lift": [],
            "adversary_hint": None,
            "prompt_mode": "progressive",
            "agent_timeout_sec": 1,
            "retries": 0,
        }
        no_agent_meta = {
            "folder": None,
            "dockerfile": None,
            "entrypoint": None,
            "sandbox_mode": "local",
            "image_tag": None,
        }
        environment = MagicMock()
        environment.bind_namespace_roles.return_value = {"default": "karma-ns"}
        environment.build_env_vars.return_value = {}

        proxy = MagicMock()
        proxy.port = 0

        with patch("karma.runtime.case.launch_proxy", return_value=proxy), \
             patch("karma.runtime.case.write_agent_bundle", return_value=tmp_path / "kc"), \
             patch("karma.runtime.case.collect_evidence", return_value={}), \
             patch("karma.runtime.case.run_oracle", return_value={"verdict": "pass"}), \
             patch("karma.runtime.case.launch_agent") as mock_launch:
            result = run_stage(
                row,
                run_dir=tmp_path,
                resources_dir=tmp_path,
                agent_meta=no_agent_meta,
                sandbox_mode="local",
                environment=environment,
                prior_stage_ids=[],
                stage_prompts=["do the thing"],
                prompt_mode="progressive",
            )

        mock_launch.assert_not_called()
        assert result["status"] == "pass"
        assert result["submitted"] is False
        assert result["oracle_verdict"] == "pass"
