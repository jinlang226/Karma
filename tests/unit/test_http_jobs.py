"""Unit tests for karma.interfaces.http.jobs."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from karma.interfaces.http.jobs import (
    translate_ui_request,
    get_job_status,
    list_jobs,
    cancel_job,
)


class TestTranslateUiRequest:
    def test_single_case_produces_one_stage(self, tmp_path):
        payload = {"service": "svc", "case_name": "my-case"}
        wf = translate_ui_request(payload, resources_dir=tmp_path)
        assert len(wf["stages"]) == 1

    def test_single_case_stage_references_service_and_case(self, tmp_path):
        payload = {"service": "rabbitmq-experiments", "case_name": "failover"}
        wf = translate_ui_request(payload, resources_dir=tmp_path)
        stage = wf["stages"][0]
        assert stage["service"] == "rabbitmq-experiments"
        assert stage["case_name"] == "failover"

    def test_raises_when_service_missing(self, tmp_path):
        with pytest.raises(ValueError, match="service"):
            translate_ui_request({"case_name": "x"}, resources_dir=tmp_path)

    def test_raises_when_case_name_missing(self, tmp_path):
        with pytest.raises(ValueError, match="case_name"):
            translate_ui_request({"service": "svc"}, resources_dir=tmp_path)

    def test_inline_workflow_yaml_parsed(self, tmp_path):
        yaml_str = (
            "metadata:\n  id: inline-wf\n"
            "spec:\n  stages:\n    - id: s1\n      service: svc\n      case: c\n"
        )
        with patch("karma.interfaces.http.jobs.normalize_workflow") as mock_norm:
            mock_norm.return_value = {"stages": [{"id": "s1"}], "adversary": []}
            wf = translate_ui_request(
                {"workflow_yaml": yaml_str}, resources_dir=tmp_path
            )
        mock_norm.assert_called_once()

    def test_raises_on_invalid_yaml(self, tmp_path):
        with pytest.raises(ValueError, match="YAML"):
            translate_ui_request(
                {"workflow_yaml": ":\n  bad: [unclosed"}, resources_dir=tmp_path
            )

    def test_single_case_normalized_semantics_stable(self, tmp_path):
        """Two identical payloads must produce structurally identical workflows."""
        payload = {"service": "svc", "case_name": "case"}
        wf1 = translate_ui_request(payload, resources_dir=tmp_path)
        wf2 = translate_ui_request(payload, resources_dir=tmp_path)
        assert wf1["stages"][0]["service"] == wf2["stages"][0]["service"]
        assert wf1["stages"][0]["case_name"] == wf2["stages"][0]["case_name"]


class TestGetJobStatus:
    def test_returns_none_for_unknown_run(self):
        assert get_job_status("no-such-run-id") is None

    def test_merges_local_and_runtime_status(self, tmp_path):
        from karma.interfaces.http import jobs
        jobs._active_jobs["test-run"] = {
            "run_id": "test-run",
            "status": "running",
            "kind": "run",
        }
        result = get_job_status("test-run")
        assert result["run_id"] == "test-run"
        assert result["kind"] == "run"
        del jobs._active_jobs["test-run"]


class TestListJobs:
    def test_returns_registered_jobs(self):
        from karma.interfaces.http import jobs
        jobs._active_jobs["j1"] = {"run_id": "j1", "status": "complete"}
        entries = list_jobs()
        assert any(e["run_id"] == "j1" for e in entries)
        del jobs._active_jobs["j1"]

    def test_status_filter_applied(self):
        from karma.interfaces.http import jobs
        jobs._active_jobs["j2"] = {"run_id": "j2", "status": "running"}
        jobs._active_jobs["j3"] = {"run_id": "j3", "status": "complete"}
        running = list_jobs(status_filter="running")
        assert all(j["status"] == "running" for j in running)
        del jobs._active_jobs["j2"]
        del jobs._active_jobs["j3"]


class TestCancelJob:
    def test_returns_false_for_unknown_run_id(self):
        assert cancel_job("does-not-exist") is False

    def test_returns_true_and_marks_cancelled(self):
        from karma.interfaces.http import jobs
        jobs._active_jobs["cj1"] = {"run_id": "cj1", "status": "running"}
        result = cancel_job("cj1")
        assert result is True
        assert jobs._active_jobs["cj1"]["status"] == "cancelled"
        del jobs._active_jobs["cj1"]

    def test_publishes_cancel_and_ends_stream_on_hub(self):
        from karma.interfaces.http import jobs
        from karma.interfaces.http.events import hub
        jobs._active_jobs["cj2"] = {"run_id": "cj2", "status": "running"}
        sub = hub.subscribe("cj2")
        cancel_job("cj2")
        first = sub.get_nowait()
        assert first["type"] == "cancelled"
        # stream is closed: terminal sentinel follows
        assert sub.get_nowait() is None
        hub.unsubscribe("cj2", sub)
        hub.forget("cj2")
        del jobs._active_jobs["cj2"]
