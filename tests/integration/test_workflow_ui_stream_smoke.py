import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from app.server import create_handler
from app.settings import ROOT


class _WorkflowStreamApp:
    cluster_ok = True
    cluster_error = None

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

    def list_judge_runs(self):
        return []

    def list_judge_batches(self):
        return []

    def list_judge_jobs(self):
        return []

    def get_judge_job(self, _job_id):
        return None

    def list_workflow_files(self):
        return []

    def list_workflow_jobs(self):
        return []

    def get_workflow_job(self, _job_id):
        return None

    def start_judge(self, payload):
        _ = payload
        return {"ok": True}

    def judge_preview(self, payload):
        _ = payload
        return {"ok": True}

    def workflow_preview(self, payload):
        _ = payload
        return {"ok": True}

    def start_workflow(self, payload):
        _ = payload
        return {"ok": True}

    def get_judge_stream_snapshot(self):
        return {"seq": 0, "jobs": []}

    def get_judge_events_since(self, _since, timeout_sec=0.0):
        _ = timeout_sec
        return {"reset": False, "events": [], "current_seq": 0}

    def get_workflow_stream_snapshot(self):
        return {"schema": "workflow_stream.v2", "seq": 15, "jobs": [{"id": "wf_1", "status": "running"}]}

    def get_workflow_events_since(self, since_seq, timeout_sec=0.0):
        _ = timeout_sec
        seq = int(since_seq)
        if seq < 14:
            return {"reset": True, "events": [], "current_seq": 15}
        if seq == 14:
            return {
                "reset": False,
                "events": [{"seq": 15, "type": "job_upsert", "data": {"job": {"id": "wf_1", "status": "running"}}}],
                "current_seq": 15,
            }
        return {"reset": False, "events": [], "current_seq": 15}


@contextmanager
def _server(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        yield host, int(port)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=1.0)


def _read_first_sse_event(host, port, path):
    conn = HTTPConnection(host, port, timeout=3)
    conn.request("GET", path)
    resp = conn.getresponse()
    assert resp.status == 200
    assert "text/event-stream" in (resp.getheader("Content-Type") or "")

    event_name = None
    event_id = None
    data_lines = []
    while True:
        raw = resp.fp.readline()
        if not raw:
            break
        line = raw.decode("utf-8").rstrip("\n")
        if not line:
            break
        if line.startswith("id: "):
            event_id = line[4:].strip()
        elif line.startswith("event: "):
            event_name = line[7:].strip()
        elif line.startswith("data: "):
            data_lines.append(line[6:])
    conn.close()
    payload = json.loads("\n".join(data_lines) if data_lines else "{}")
    return event_name, event_id, payload


def test_workflow_stream_reconnect_replays_incremental_events():
    with _server(_WorkflowStreamApp()) as (host, port):
        event, event_id, payload = _read_first_sse_event(host, port, "/api/workflow/stream?since=14")
        assert event == "job_upsert"
        assert event_id == "15"
        assert (payload.get("job") or {}).get("id") == "wf_1"

        event, event_id, payload = _read_first_sse_event(host, port, "/api/workflow/stream?since=1")
        assert event == "hello"
        assert event_id == "15"
        assert payload.get("schema") == "workflow_stream.v2"


def test_workflow_ui_js_uses_incremental_stream_handlers():
    text = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'stream.addEventListener("job_upsert"' in text
    assert 'stream.addEventListener("log_append"' in text
    assert 'stream.addEventListener("job_phase"' in text
    assert "upsertWorkflowJob(payload.job);" in text
    assert "applyWorkflowLogEvent(payload);" in text
