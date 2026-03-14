import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib import request
from urllib.error import HTTPError

from app.server import create_handler


class _RouteApp:
    def __init__(self):
        self.cluster_ok = True
        self.cluster_error = None
        self.fail_submit = False
        self.fail_cleanup = False
        self.fail_judge_start = False
        self.fail_workflow_start = False
        self.fail_workflow_submit = False
        self.fail_workflow_cleanup = False
        self.fail_workflow_prompt = False

    def list_services(self):
        return ["svc-a", "svc-b"]

    def list_cases(self, service):
        return [f"{service}-case-1"]

    def get_case(self, case_id):
        return {"id": case_id}

    def run_status(self):
        return {"status": "idle"}

    def run_metrics(self):
        return {"status": "pending"}

    def proxy_status(self):
        return {"status": "disabled"}

    def orchestrator_options(self):
        return {"defaults": {"sandbox": "docker"}, "choices": {"sandbox": ["local", "docker"]}}

    def start_run(self, case_id, max_attempts_override=None):
        if case_id == "bad":
            return {"error": "bad case"}
        return {
            "status": "started",
            "case_id": case_id,
            "max_attempts_override": max_attempts_override,
        }

    def submit_run(self):
        if self.fail_submit:
            return {"error": "submit failed"}
        return {"status": "verifying"}

    def cleanup_run(self):
        if self.fail_cleanup:
            return {"error": "cleanup failed"}
        return {"status": "cleanup_started"}

    def orchestrator_preview(self, payload):
        if (payload or {}).get("force_error"):
            return {"ok": False, "errors": ["preview failed"]}
        return {"ok": True, "tokens": ["python3", "orchestrator.py", "run"]}

    def list_judge_runs(self):
        return [{"id": "judge_run_1"}]

    def list_judge_batches(self):
        return [{"id": "judge_batch_1"}]

    def list_judge_jobs(self):
        return [{"id": "judge_job_1"}]

    def get_judge_job(self, job_id):
        if job_id == "judge_job_1":
            return {"id": "judge_job_1", "status": "running"}
        return None

    def start_judge(self, _payload):
        if self.fail_judge_start:
            return {"error": "judge start failed"}
        return {"ok": True, "job": {"id": "judge_job_1"}}

    def judge_preview(self, payload):
        if not (payload or {}).get("ok", True):
            return {"ok": False, "error": "judge preview failed"}
        return {"ok": True, "tokens": ["python3", "scripts/judge.py", "run"]}

    def list_workflow_files(self):
        return [{"path": "workflows/demo.yaml"}]

    def list_workflow_jobs(self):
        return [{"id": "wf_job_1"}]

    def get_workflow_job(self, job_id):
        if job_id == "wf_job_1":
            return {"id": "wf_job_1", "status": "running"}
        return None

    def get_workflow_job_prompt(self, job_id, max_chars=None):
        if str(job_id) == "missing":
            return {"error": "Workflow job not found", "http_status": 404}
        if self.fail_workflow_prompt:
            return {"error": "workflow prompt failed", "http_status": 500}
        text = "workflow prompt text"
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
        if not (payload or {}).get("ok", True):
            return {"ok": False, "error": "workflow preview failed"}
        return {"ok": True, "run_tokens": ["python3", "orchestrator.py", "workflow-run"]}

    def start_workflow(self, _payload):
        if self.fail_workflow_start:
            return {"error": "workflow start failed"}
        return {"ok": True, "job": {"id": "wf_job_1"}}

    def submit_workflow_job(self, job_id):
        if str(job_id) == "missing":
            return {"error": "Workflow job not found", "http_status": 404}
        if self.fail_workflow_submit:
            return {"error": "workflow submit failed", "http_status": 409}
        return {"ok": True, "job_id": job_id, "status": "verifying"}

    def cleanup_workflow_job(self, job_id):
        if str(job_id) == "missing":
            return {"error": "Workflow job not found", "http_status": 404}
        if self.fail_workflow_cleanup:
            return {"error": "workflow cleanup failed", "http_status": 409}
        return {"ok": True, "job_id": job_id, "status": "cleaning"}

    # Stream endpoints are not under test in this file but required by handler.
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


class _RouteAppManual(_RouteApp):
    def __init__(self):
        super().__init__()
        self.manual_calls = {"status": 0, "start": 0, "submit": 0, "cleanup": 0}

    def manual_run_status(self):
        self.manual_calls["status"] += 1
        return {"status": "idle", "source": "manual"}

    def start_manual_run(self, case_id, max_attempts_override=None):
        self.manual_calls["start"] += 1
        return {"status": "started", "case_id": case_id, "max_attempts_override": max_attempts_override}

    def submit_manual_run(self):
        self.manual_calls["submit"] += 1
        return {"status": "verifying", "source": "manual"}

    def cleanup_manual_run(self):
        self.manual_calls["cleanup"] += 1
        return {"status": "cleaning", "source": "manual"}


@contextmanager
def _server(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        yield f"http://{host}:{int(port)}"
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
    req = request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=5) as resp:
        return resp.getcode(), json.loads(resp.read().decode("utf-8"))


def _post_json_allow_error(url, payload):
    try:
        return _post_json(url, payload)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post_raw(url, body):
    req = request.Request(url, data=body.encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=5) as resp:
            return resp.getcode(), resp.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_get_route_matrix_and_not_found_contract():
    app = _RouteApp()
    with _server(app) as base:
        status, payload = _get_json(f"{base}/api/services")
        assert status == 200
        assert payload["services"] == ["svc-a", "svc-b"]
        assert payload["cluster_ok"] is True

        status, payload = _get_json(f"{base}/api/services/svc-a/cases")
        assert status == 200
        assert payload["cases"] == ["svc-a-case-1"]

        status, payload = _get_json(f"{base}/api/cases/case-1")
        assert status == 200
        assert payload["id"] == "case-1"

        status, payload = _get_json(f"{base}/api/run/status")
        assert status == 200
        assert payload["status"] == "idle"

        status, payload = _get_json(f"{base}/api/run/metrics")
        assert status == 200
        assert payload["status"] == "pending"

        status, payload = _get_json(f"{base}/api/proxy/status")
        assert status == 200
        assert payload["status"] == "disabled"

        status, payload = _get_json(f"{base}/api/orchestrator/options")
        assert status == 200
        assert "defaults" in payload
        assert "choices" in payload

        status, payload = _get_json(f"{base}/api/judge/runs")
        assert status == 200
        assert payload["runs"][0]["id"] == "judge_run_1"

        status, payload = _get_json(f"{base}/api/judge/batches")
        assert status == 200
        assert payload["batches"][0]["id"] == "judge_batch_1"

        status, payload = _get_json(f"{base}/api/judge/jobs")
        assert status == 200
        assert payload["jobs"][0]["id"] == "judge_job_1"

        status, payload = _get_json(f"{base}/api/judge/jobs/judge_job_1")
        assert status == 200
        assert payload["id"] == "judge_job_1"

        status, payload = _get_json_allow_error(f"{base}/api/judge/jobs/missing")
        assert status == 404
        assert "error" in payload

        status, payload = _get_json(f"{base}/api/workflow/files")
        assert status == 200
        assert payload["workflows"][0]["path"] == "workflows/demo.yaml"

        status, payload = _get_json(f"{base}/api/workflow/jobs")
        assert status == 200
        assert payload["jobs"][0]["id"] == "wf_job_1"

        status, payload = _get_json(f"{base}/api/workflow/jobs/wf_job_1/prompt?max_chars=10")
        assert status == 200
        assert payload["ok"] is True
        assert payload["available"] is True
        assert payload["truncated"] is True

        status, payload = _get_json_allow_error(f"{base}/api/workflow/jobs/wf_job_1/prompt?max_chars=nope")
        assert status == 400
        assert payload["error"] == "max_chars must be an integer"

        status, payload = _get_json_allow_error(f"{base}/api/workflow/jobs/missing/prompt")
        assert status == 404
        assert payload["error"] == "Workflow job not found"

        status, payload = _get_json_allow_error(f"{base}/api/workflow/jobs/missing")
        assert status == 404
        assert "error" in payload

        conn = HTTPConnection("127.0.0.1", int(base.rsplit(":", 1)[1]), timeout=5)
        conn.request("GET", "/api/does/not/exist")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()
        assert resp.status == 404
        assert body == "Not found"


def test_post_run_routes_and_invalid_json_contract():
    app = _RouteApp()
    with _server(app) as base:
        status, payload_text = _post_raw(f"{base}/api/run/start", "{")
        assert status == 400
        payload = json.loads(payload_text)
        assert payload["error"] == "Invalid JSON"

        status, payload = _post_json_allow_error(f"{base}/api/run/start", {})
        assert status == 400
        assert payload["error"] == "case_id is required"

        status, payload = _post_json(
            f"{base}/api/run/start",
            {"case_id": "good-case", "max_attempts": 2},
        )
        assert status == 200
        assert payload["status"] == "started"
        assert payload["case_id"] == "good-case"
        assert payload["max_attempts_override"] == 2

        status, payload = _post_json_allow_error(f"{base}/api/run/start", {"case_id": "bad"})
        assert status == 400
        assert payload["error"] == "bad case"

        status, payload = _post_json(f"{base}/api/run/submit", {})
        assert status == 200
        assert payload["status"] == "verifying"
        app.fail_submit = True
        status, payload = _post_json_allow_error(f"{base}/api/run/submit", {})
        assert status == 400
        assert payload["error"] == "submit failed"

        status, payload = _post_json(f"{base}/api/run/cleanup", {})
        assert status == 200
        assert payload["status"] == "cleanup_started"
        app.fail_cleanup = True
        status, payload = _post_json_allow_error(f"{base}/api/run/cleanup", {})
        assert status == 400
        assert payload["error"] == "cleanup failed"

        status, payload = _post_json(f"{base}/api/orchestrator/preview", {"force_error": True})
        assert status == 200
        assert payload["ok"] is False


def test_post_judge_and_workflow_routes_status_mapping():
    app = _RouteApp()
    with _server(app) as base:
        status, payload = _post_json(f"{base}/api/judge/preview", {"ok": True})
        assert status == 200
        assert payload["ok"] is True

        status, payload = _post_json_allow_error(f"{base}/api/judge/preview", {"ok": False})
        assert status == 400
        assert payload["ok"] is False

        status, payload = _post_json(f"{base}/api/judge/start", {"target_type": "run"})
        assert status == 200
        assert payload["ok"] is True
        app.fail_judge_start = True
        status, payload = _post_json_allow_error(f"{base}/api/judge/start", {"target_type": "run"})
        assert status == 400
        assert payload["error"] == "judge start failed"

        status, payload = _post_json(f"{base}/api/workflow/preview", {"ok": True})
        assert status == 200
        assert payload["ok"] is True

        status, payload = _post_json_allow_error(f"{base}/api/workflow/preview", {"ok": False})
        assert status == 400
        assert payload["ok"] is False

        status, payload = _post_json(f"{base}/api/workflow/start", {"action": "run", "workflow_path": "x"})
        assert status == 200
        assert payload["ok"] is True
        app.fail_workflow_start = True
        status, payload = _post_json_allow_error(
            f"{base}/api/workflow/start",
            {"action": "run", "workflow_path": "x"},
        )
        assert status == 400
        assert payload["error"] == "workflow start failed"

        status, payload = _post_json(f"{base}/api/workflow/jobs/wf_job_1/submit", {})
        assert status == 200
        assert payload["status"] == "verifying"
        app.fail_workflow_submit = True
        status, payload = _post_json_allow_error(f"{base}/api/workflow/jobs/wf_job_1/submit", {})
        assert status == 409
        assert payload["error"] == "workflow submit failed"
        status, payload = _post_json_allow_error(f"{base}/api/workflow/jobs/missing/submit", {})
        assert status == 404
        assert payload["error"] == "Workflow job not found"

        status, payload = _post_json(f"{base}/api/workflow/jobs/wf_job_1/cleanup", {})
        assert status == 200
        assert payload["status"] == "cleaning"
        app.fail_workflow_cleanup = True
        status, payload = _post_json_allow_error(f"{base}/api/workflow/jobs/wf_job_1/cleanup", {})
        assert status == 409
        assert payload["error"] == "workflow cleanup failed"
        status, payload = _post_json_allow_error(f"{base}/api/workflow/jobs/missing/cleanup", {})
        assert status == 404
        assert payload["error"] == "Workflow job not found"


def test_run_routes_prefer_manual_route_methods_when_available():
    app = _RouteAppManual()
    with _server(app) as base:
        status, payload = _get_json(f"{base}/api/run/status")
        assert status == 200
        assert payload.get("source") == "manual"

        status, payload = _post_json(
            f"{base}/api/run/start",
            {"case_id": "good-case", "max_attempts": 4},
        )
        assert status == 200
        assert payload["status"] == "started"
        assert payload["max_attempts_override"] == 4

        status, payload = _post_json(f"{base}/api/run/submit", {})
        assert status == 200
        assert payload.get("source") == "manual"

        status, payload = _post_json(f"{base}/api/run/cleanup", {})
        assert status == 200
        assert payload.get("source") == "manual"

    assert app.manual_calls == {"status": 1, "start": 1, "submit": 1, "cleanup": 1}
