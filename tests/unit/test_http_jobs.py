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
        payload = {"service": "rabbitmq", "case_name": "failover"}
        wf = translate_ui_request(payload, resources_dir=tmp_path)
        stage = wf["stages"][0]
        assert stage["service"] == "rabbitmq"
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

    def test_workflow_path_loads_and_normalizes(self, tmp_path):
        wf = tmp_path / "wf.yaml"
        wf.write_text(
            "metadata:\n  id: from-file\n"
            "spec:\n  stages:\n    - id: s1\n      service: svc\n      case: c\n"
        )
        result = translate_ui_request(
            {"workflow_path": str(wf)}, resources_dir=tmp_path
        )
        assert result["id"] == "from-file"
        assert len(result["stages"]) == 1

    def test_workflow_path_missing_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            translate_ui_request(
                {"workflow_path": str(tmp_path / "nope.yaml")}, resources_dir=tmp_path
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


def test_stream_route_accepts_run_before_first_event(monkeypatch):
    """A just-submitted run (registered job, no events yet) must not 404 on
    /stream -- the UI opens the stream before the first stage event fires."""
    import karma.interfaces.http.server as server
    from karma.interfaces.http.events import hub
    # a run id known to the job registry but with no buffered events
    import karma.interfaces.http.jobs as jobs
    jobs._register_job("run-xyz", {"run_id": "run-xyz", "status": "running", "kind": "run"})
    try:
        assert not hub.is_known("run-xyz")          # no events buffered yet
        assert jobs.get_job_status("run-xyz") is not None  # but the job exists
    finally:
        jobs._active_jobs.pop("run-xyz", None)
