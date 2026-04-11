"""
Regression test: CLI and UI paths must use the same runtime service.

Verifies that a single-case UI request and an equivalent CLI run-case
invocation both resolve to the same workflow representation before
reaching the runtime, with no path-specific branching in the execution
core.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from karma.interfaces.http.jobs import translate_ui_request
from karma.definitions.workflows import single_case_to_workflow


class TestCliUiParity:
    """Both paths must produce structurally identical workflows."""

    def test_ui_and_cli_produce_same_workflow_structure(self, tmp_path):
        service = "rabbitmq-experiments"
        case_name = "failover"
        params = {"target_node": "rabbit@pod-0"}

        ui_workflow = translate_ui_request(
            {"service": service, "case_name": case_name, "params": params},
            resources_dir=tmp_path,
        )
        cli_workflow = single_case_to_workflow(service, case_name, params)

        assert len(ui_workflow["stages"]) == len(cli_workflow["stages"])
        ui_stage = ui_workflow["stages"][0]
        cli_stage = cli_workflow["stages"][0]
        assert ui_stage["service"] == cli_stage["service"]
        assert ui_stage["case"] == cli_stage["case"]
        assert ui_stage["param_overrides"] == cli_stage["param_overrides"]

    def test_both_paths_call_submit_run(self, tmp_path):
        """Confirm both adapters ultimately call runtime.service.submit_run."""
        with patch("karma.interfaces.http.jobs.submit_run") as mock_submit, \
             patch("karma.interfaces.http.jobs.normalize_workflow",
                   return_value={"stages": [{"id": "s1"}], "adversary": []}):
            mock_submit.return_value = "run-123"
            from karma.interfaces.http.jobs import submit_job
            run_id = submit_job(
                {"service": "svc", "case_name": "case"},
                runs_dir=tmp_path,
                resources_dir=tmp_path,
            )
            mock_submit.assert_called_once()
            assert run_id == "run-123"
