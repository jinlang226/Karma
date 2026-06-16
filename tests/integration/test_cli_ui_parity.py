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
        service = "rabbitmq"
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
        assert ui_stage["case_name"] == cli_stage["case_name"]
        assert ui_stage["param_overrides"] == cli_stage["param_overrides"]

    def test_both_paths_call_run_workflow(self, tmp_path):
        """Confirm the HTTP adapter ultimately calls runtime.service.run_workflow.

        submit_job runs the workflow on its own daemon thread (so it can
        publish a terminal event), so we wait for the threaded call.
        """
        import threading

        called = threading.Event()

        def _fake_run_workflow(workflow, **kwargs):
            called.set()
            return {"status": "complete", "summary": {}}

        with patch("karma.interfaces.http.jobs.run_workflow",
                   side_effect=_fake_run_workflow) as mock_run, \
             patch("karma.interfaces.http.jobs.translate_ui_request",
                   return_value={"id": "wf", "stages": [{"id": "s1"}], "adversary": []}):
            from karma.interfaces.http.jobs import submit_job
            run_id = submit_job(
                {"service": "svc", "case_name": "case"},
                runs_dir=tmp_path,
                resources_dir=tmp_path,
            )
            assert called.wait(timeout=5), "run_workflow was never invoked"
            mock_run.assert_called_once()
            assert isinstance(run_id, str) and run_id
