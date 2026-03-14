import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib import request

from app.server import create_handler


class _FakeApp:
    def __init__(self):
        self.cluster_ok = True
        self.cluster_error = None
        self.workflow_start_payloads = []
        self.workflow_control_calls = []
        self.workflow_prompt_calls = []
        self.workflow_import_payloads = []
        self._jobs = {
            "wf_job_1": {
                "id": "wf_job_1",
                "status": "running",
                "kind": "run",
                "workflow_path": "workflows/demo.yaml",
                "prompt_mode": "progressive",
            },
            "wf_job_2": {
                "id": "wf_job_2",
                "status": "failed",
                "kind": "run",
                "workflow_path": "workflows/demo.yaml",
                "prompt_mode": "concat_stateful",
                "phase": "cleanup",
                "error": "setup failed",
                "workflow_stage_results_path": "runs/wf_job_2/workflow_stage_results.jsonl",
            },
        }

    # Generic endpoints.
    def list_services(self):
        return []

    def list_cases(self, _service):
        return []

    def get_case(self, _case_id):
        return {"error": "not found"}

    def run_status(self):
        return {"status": "idle"}

    def run_metrics(self):
        return {"status": "pending"}

    def proxy_status(self):
        return {"status": "disabled"}

    def orchestrator_options(self):
        return {"defaults": {}, "choices": {}}

    # Judge endpoints (unused in this test, but required by handler surface).
    def list_judge_runs(self):
        return []

    def list_judge_batches(self):
        return []

    def list_judge_jobs(self):
        return []

    def get_judge_job(self, _job_id):
        return None

    # Workflow endpoints.
    def list_workflow_files(self):
        return [
            {
                "path": "workflows/demo.yaml",
                "name": "demo",
                "prompt_mode": "progressive",
                "stage_count": 2,
                "status": "ok",
            }
        ]

    def list_workflow_jobs(self):
        return list(self._jobs.values())

    def get_workflow_job(self, job_id):
        return self._jobs.get(job_id)

    def get_workflow_job_prompt(self, job_id, max_chars=None):
        self.workflow_prompt_calls.append((str(job_id), max_chars))
        if str(job_id) == "missing":
            return {"error": "Workflow job not found", "http_status": 404}
        text = "# workflow/demo\n\nActive Stage: 1/1"
        limit = 24_000 if max_chars in (None, "") else int(max_chars)
        if limit <= 0:
            limit = 24_000
        truncated = len(text) > limit
        return {
            "ok": True,
            "job_id": str(job_id),
            "available": True,
            "prompt": text[:limit],
            "truncated": bool(truncated),
            "path": "runs/wf_job_1/agent_bundle/PROMPT.md",
            "updated_at": "2026-02-23T18:40:12Z",
            "size_bytes": len(text),
            "phase": "agent_waiting",
        }

    def workflow_preview(self, payload):
        path = str((payload or {}).get("workflow_path") or "")
        if not path:
            return {"ok": False, "error": "workflow_path is required"}
        return {
            "ok": True,
            "workflow_path": path,
            "run_one_line": "python3 orchestrator.py workflow-run --workflow workflows/demo.yaml",
            "run_tokens": ["python3", "orchestrator.py", "workflow-run"],
        }

    def workflow_import(self, payload):
        self.workflow_import_payloads.append(dict(payload or {}))
        yaml_text = str((payload or {}).get("yaml_text") or "")
        if "kind: Workflow" not in yaml_text:
            return {"ok": False, "error": "invalid yaml"}
        return {
            "ok": True,
            "draft": {
                "metadata": {"name": "demo"},
                "spec": {
                    "prompt_mode": "progressive",
                    "namespaces": ["cluster_a"],
                    "stages": [
                        {
                            "id": "s1",
                            "service": "rabbitmq-experiments",
                            "case": "manual_monitoring",
                            "max_attempts": None,
                            "namespaces": ["cluster_a"],
                            "namespace_bindings": {},
                            "param_overrides": {},
                        }
                    ],
                },
            },
            "workflow_name": "demo",
            "prompt_mode": "progressive",
            "stage_count": 1,
        }

    def start_workflow(self, payload):
        self.workflow_start_payloads.append(dict(payload or {}))
        if str((payload or {}).get("workflow_path") or "") == "":
            return {"error": "workflow_path is required"}
        return {"ok": True, "job": self._jobs["wf_job_1"]}

    def submit_workflow_job(self, job_id):
        self.workflow_control_calls.append(("submit", str(job_id)))
        if str(job_id) == "missing":
            return {"error": "Workflow job not found", "http_status": 404}
        return {"ok": True, "job_id": str(job_id), "status": "verifying"}

    def cleanup_workflow_job(self, job_id):
        self.workflow_control_calls.append(("cleanup", str(job_id)))
        if str(job_id) == "missing":
            return {"error": "Workflow job not found", "http_status": 404}
        return {"ok": True, "job_id": str(job_id), "status": "cleaning"}

    # SSE endpoints (unused in this test).
    def get_judge_stream_snapshot(self):
        return {"seq": 0, "jobs": []}

    def get_judge_events_since(self, _since, timeout_sec=0.0):
        _ = timeout_sec
        return {"reset": False, "events": [], "current_seq": 0}

    def get_workflow_stream_snapshot(self):
        return {"schema": "workflow_stream.v2", "seq": 0, "jobs": []}

    def get_workflow_events_since(self, _since, timeout_sec=0.0):
        _ = timeout_sec
        return {"reset": False, "events": [], "current_seq": 0}


@contextmanager
def _server(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=1.0)


def _get_json(url):
    with request.urlopen(url, timeout=5) as resp:
        return resp.getcode(), json.loads(resp.read().decode("utf-8"))


def _get_json_allow_error(url):
    try:
        return _get_json(url)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=5) as resp:
        return resp.getcode(), json.loads(resp.read().decode("utf-8"))


def _post_json_allow_error(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=5) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_workflow_api_contract_routes():
    app = _FakeApp()
    with _server(app) as base:
        status, payload = _get_json(f"{base}/api/workflow/files")
        assert status == 200
        workflows = payload.get("workflows")
        assert isinstance(workflows, list) and workflows
        first = workflows[0]
        for key in ("path", "name", "prompt_mode", "stage_count", "status"):
            assert key in first

        status, payload = _get_json(f"{base}/api/workflow/jobs")
        assert status == 200
        jobs = payload.get("jobs")
        assert isinstance(jobs, list) and jobs
        assert jobs[0]["id"] == "wf_job_1"
        assert any(item.get("id") == "wf_job_2" for item in jobs)

        status, payload = _get_json(f"{base}/api/workflow/jobs/wf_job_1")
        assert status == 200
        assert payload["id"] == "wf_job_1"
        assert payload["kind"] == "run"

        status, payload = _get_json(f"{base}/api/workflow/jobs/wf_job_2")
        assert status == 200
        assert payload["id"] == "wf_job_2"
        assert payload["status"] == "failed"
        assert "workflow_stage_results_path" in payload

        status, payload = _get_json(f"{base}/api/workflow/jobs/wf_job_1/prompt?max_chars=12")
        assert status == 200
        assert payload["ok"] is True
        assert payload["available"] is True
        assert payload["truncated"] is True
        assert payload["job_id"] == "wf_job_1"
        assert payload["phase"] == "agent_waiting"

        status, payload = _get_json_allow_error(f"{base}/api/workflow/jobs/wf_job_1/prompt?max_chars=bad")
        assert status == 400
        assert payload["error"] == "max_chars must be an integer"

        status, payload = _get_json_allow_error(f"{base}/api/workflow/jobs/missing/prompt")
        assert status == 404
        assert payload["error"] == "Workflow job not found"

        status, payload = _get_json_allow_error(f"{base}/api/workflow/jobs/missing")
        assert status == 404
        assert "error" in payload

        status, payload = _post_json(
            f"{base}/api/workflow/preview",
            {"workflow_path": "workflows/demo.yaml", "flags": {"sandbox": "docker"}},
        )
        assert status == 200
        assert payload["ok"] is True
        assert "workflow-run" in payload["run_one_line"]

        status, payload = _post_json_allow_error(
            f"{base}/api/workflow/preview",
            {"workflow_path": ""},
        )
        assert status == 400
        assert payload["ok"] is False
        assert "error" in payload

        status, payload = _post_json(
            f"{base}/api/workflow/import",
            {
                "yaml_text": "apiVersion: benchmark/v1alpha1\\nkind: Workflow\\nmetadata:\\n  name: demo\\nspec:\\n  stages: []\\n",
                "workflow_path": "workflows/demo.yaml",
            },
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["stage_count"] == 1
        assert payload["draft"]["metadata"]["name"] == "demo"
        assert app.workflow_import_payloads[-1]["workflow_path"] == "workflows/demo.yaml"

        status, payload = _post_json_allow_error(
            f"{base}/api/workflow/import",
            {"yaml_text": "not a workflow"},
        )
        assert status == 400
        assert payload["ok"] is False
        assert payload["error"] == "invalid yaml"

        status, payload = _post_json(
            f"{base}/api/workflow/start",
            {"action": "run", "workflow_path": "workflows/demo.yaml", "source": "ui"},
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["job"]["id"] == "wf_job_1"
        assert app.workflow_start_payloads[-1].get("source") == "ui"

        status, payload = _post_json_allow_error(
            f"{base}/api/workflow/start",
            {"action": "run", "workflow_path": ""},
        )
        assert status == 400
        assert "error" in payload

        status, payload = _post_json(f"{base}/api/workflow/jobs/wf_job_1/submit", {})
        assert status == 200
        assert payload["ok"] is True
        assert payload["status"] == "verifying"

        status, payload = _post_json(f"{base}/api/workflow/jobs/wf_job_1/cleanup", {})
        assert status == 200
        assert payload["ok"] is True
        assert payload["status"] == "cleaning"

        status, payload = _post_json_allow_error(f"{base}/api/workflow/jobs/missing/submit", {})
        assert status == 404
        assert payload["error"] == "Workflow job not found"

        status, payload = _post_json_allow_error(f"{base}/api/workflow/jobs/missing/cleanup", {})
        assert status == 404
        assert payload["error"] == "Workflow job not found"

        assert ("submit", "wf_job_1") in app.workflow_control_calls
        assert ("cleanup", "wf_job_1") in app.workflow_control_calls
        assert ("wf_job_1", 12) in app.workflow_prompt_calls
