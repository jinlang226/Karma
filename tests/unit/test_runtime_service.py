"""Unit tests for karma.runtime.service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from karma.runtime.service import run_workflow


class TestRunWorkflowAgentSessionDefaults:
    def _workflow(self, **overrides):
        workflow = {
            "id": "wf",
            "label": "wf",
            "prompt_mode": "progressive",
            "stages": [
                {
                    "id": "stage_1",
                    "service": "svc",
                    "case_name": "case",
                    "param_overrides": {},
                    "namespaces": None,
                    "_warnings": [],
                }
            ],
            "adversary": [],
        }
        workflow.update(overrides)
        return workflow

    def test_defaults_to_persistent_session_when_unspecified(self, tmp_path: Path):
        workflow = self._workflow()
        loop_result = {"run_id": "r1", "status": "complete", "stages": []}

        with patch("karma.runtime.service.resolve_workflow_rows", return_value=[]), \
             patch("karma.runtime.service.get_environment", return_value=MagicMock()), \
             patch("karma.runtime.service.resolve_agent", return_value={}), \
             patch("karma.runtime.service.run_workflow_loop", return_value=loop_result) as mock_loop:
            run_workflow(workflow, runs_dir=tmp_path, resources_dir=tmp_path)

        kwargs = mock_loop.call_args.kwargs
        assert kwargs["agent_session"] == "persistent"
        assert kwargs["session_id"] is not None

    def test_explicit_per_stage_session_disables_persistence(self, tmp_path: Path):
        workflow = self._workflow(agent_session="per_stage")
        loop_result = {"run_id": "r1", "status": "complete", "stages": []}

        with patch("karma.runtime.service.resolve_workflow_rows", return_value=[]), \
             patch("karma.runtime.service.get_environment", return_value=MagicMock()), \
             patch("karma.runtime.service.resolve_agent", return_value={}), \
             patch("karma.runtime.service.run_workflow_loop", return_value=loop_result) as mock_loop:
            run_workflow(workflow, runs_dir=tmp_path, resources_dir=tmp_path)

        kwargs = mock_loop.call_args.kwargs
        assert kwargs["agent_session"] == "per_stage"
        assert kwargs["session_id"] is None
