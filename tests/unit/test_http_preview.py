"""Route tests for the HTTP preview endpoints (judge + workflow)."""

import pytest
from pathlib import Path
from karma.interfaces.http.server import create_app


@pytest.fixture()
def client(tmp_path):
    # Minimal demo case so workflow preview can resolve a real stage.
    case = tmp_path / "cases" / "demo" / "cm" / "test.yaml"
    case.parent.mkdir(parents=True)
    case.write_text(
        "prompt: do it\n"
        "namespace_contract:\n  required_roles: [default]\n"
        "oracle:\n  verify:\n    commands:\n      - command: 'true'\n"
    )
    app = create_app(resources_dir=tmp_path / "cases", runs_dir=tmp_path / "runs")
    app.config.update(TESTING=True)
    return app.test_client()


class TestWorkflowPreview:
    def test_resolves_and_summarizes_stages(self, client):
        wf = (
            "metadata:\n  id: wf1\n"
            "spec:\n  stages:\n"
            "    - id: stage_1\n      service: demo\n      case: cm\n"
        )
        resp = client.post("/api/workflow/preview", json={"yaml_text": wf})
        data = resp.get_json()
        assert data["ok"] is True
        assert data["stage_count"] == 1
        assert data["stages"][0]["service"] == "demo"
        assert data["stages"][0]["case_name"] == "cm"

    def test_reports_errors_without_executing(self, client):
        resp = client.post("/api/workflow/preview", json={"yaml_text": "not: a workflow"})
        data = resp.get_json()
        assert data["ok"] is False and data["errors"]


class TestJudgePreview:
    def test_missing_run_dir_is_400(self, client):
        resp = client.post("/api/judge/preview", json={})
        assert resp.status_code == 400

    def test_unknown_run_dir_is_404(self, client, tmp_path):
        # A path INSIDE the runs dir that doesn't exist -> 404.
        resp = client.post(
            "/api/judge/preview", json={"run_dir": str(tmp_path / "runs" / "nope")})
        assert resp.status_code == 404

    def test_run_dir_outside_runs_is_400(self, client, tmp_path):
        # A path OUTSIDE the runs dir is rejected before any filesystem access (C5).
        resp = client.post(
            "/api/judge/preview", json={"run_dir": str(tmp_path / "etc" / "passwd")})
        assert resp.status_code == 400


class TestConfigEndpoint:
    def test_reports_a_boolean(self, client):
        body = client.get("/api/config").get_json()
        assert isinstance(body["default_system_prompt_available"], bool)

    def test_flags_missing_default_system_prompt(self, client):
        import karma.runtime.service as svc
        from unittest.mock import patch
        from pathlib import Path
        with patch.object(svc, "_DEFAULT_SYSTEM_PROMPT_PATH", Path("/nonexistent/x.md")):
            body = client.get("/api/config").get_json()
        assert body["default_system_prompt_available"] is False
