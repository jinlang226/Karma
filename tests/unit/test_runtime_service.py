"""Unit tests for karma.runtime.service run metadata."""

import json
from unittest.mock import patch

from karma.runtime import service


class TestConfigPersistsPromptMode:
    """#3: the run's config.json must record prompt_mode so the run self-documents
    its mode and the UI can surface + reproduce it. config.json is written before
    the heavy setup, so a run that fails right after still persists it."""

    def test_cli_config_records_prompt_mode(self, tmp_path):
        wf = {
            "id": "wf-x",
            "prompt_mode": "concat_blind",
            "stages": [{"id": "s1", "service": "svc", "case_name": "c"}],
        }
        # Stop right after the config.json write (before any cluster/agent work).
        with patch.object(service, "resolve_workflow_rows",
                          side_effect=RuntimeError("stop after config")):
            res = service.run_workflow(
                wf, runs_dir=tmp_path, resources_dir=tmp_path,
                agent_name="claude_code", run_id="test-run",
            )
        assert res["status"] == "error"      # failed as intended, after the write
        cfg = json.loads((tmp_path / "test-run" / "config.json").read_text())
        assert cfg["prompt_mode"] == "concat_blind"

    def test_defaults_to_progressive_when_unset(self, tmp_path):
        wf = {"id": "wf-y", "stages": [{"id": "s1", "service": "svc", "case_name": "c"}]}
        with patch.object(service, "resolve_workflow_rows",
                          side_effect=RuntimeError("stop")):
            service.run_workflow(
                wf, runs_dir=tmp_path, resources_dir=tmp_path,
                agent_name="claude_code", run_id="test-run-2",
            )
        cfg = json.loads((tmp_path / "test-run-2" / "config.json").read_text())
        assert cfg["prompt_mode"] == "progressive"
