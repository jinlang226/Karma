import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from app.server import create_handler


class _SSEEdgeApp:
    cluster_ok = True
    cluster_error = None

    def __init__(self):
        self.workflow_since_calls = []

    # Non-SSE handler requirements.
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

    # Judge stream (unused by tests, but required).
    def get_judge_stream_snapshot(self):
        return {"seq": 0, "jobs": []}

    def get_judge_events_since(self, _since, timeout_sec=0.0):
        _ = timeout_sec
        return {"reset": False, "events": [], "current_seq": 0}

    # Workflow stream under test.
    def get_workflow_stream_snapshot(self):
        return {"schema": "workflow_stream.v2", "seq": 11, "jobs": [{"id": "wf_1"}]}

    def get_workflow_events_since(self, since_seq, timeout_sec=0.0):
        since = int(since_seq)
        self.workflow_since_calls.append((since, float(timeout_sec)))
        if float(timeout_sec) == 0.0:
            if since == 10:
                return {
                    "reset": False,
                    "events": [{"seq": 11, "type": "job_upsert", "data": {"job": {"id": "wf_1"}}}],
                    "current_seq": 11,
                }
            return {"reset": True, "events": [], "current_seq": 11}
        return {"reset": False, "events": [], "current_seq": 11}


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


def _read_sse_events(host, port, path, *, count=1, headers=None):
    conn = HTTPConnection(host, port, timeout=3)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    assert resp.status == 200
    assert "text/event-stream" in (resp.getheader("Content-Type") or "")

    events = []
    event_name = None
    event_id = None
    data_lines = []
    while len(events) < count:
        raw = resp.fp.readline()
        if not raw:
            break
        line = raw.decode("utf-8").rstrip("\n")
        if not line:
            payload = json.loads("\n".join(data_lines) if data_lines else "{}")
            events.append((event_name, event_id, payload))
            event_name = None
            event_id = None
            data_lines = []
            continue
        if line.startswith("id: "):
            event_id = line[4:].strip()
        elif line.startswith("event: "):
            event_name = line[7:].strip()
        elif line.startswith("data: "):
            data_lines.append(line[6:])
    conn.close()
    return events


def test_workflow_stream_last_event_id_overrides_query_cursor():
    app = _SSEEdgeApp()
    with _server(app) as (host, port):
        events = _read_sse_events(
            host,
            port,
            "/api/workflow/stream?since=1",
            count=1,
            headers={"Last-Event-ID": "10"},
        )
    assert events
    event_name, event_id, payload = events[0]
    assert event_name == "job_upsert"
    assert event_id == "11"
    assert (payload.get("job") or {}).get("id") == "wf_1"
    assert app.workflow_since_calls
    assert app.workflow_since_calls[0] == (10, 0.0)


def test_workflow_stream_heartbeat_includes_server_epoch_ms():
    app = _SSEEdgeApp()
    with _server(app) as (host, port):
        events = _read_sse_events(host, port, "/api/workflow/stream?since=10", count=2)
    assert len(events) >= 2
    first_name, first_id, first_payload = events[0]
    assert first_name == "job_upsert"
    assert first_id == "11"
    assert (first_payload.get("job") or {}).get("id") == "wf_1"

    event_name, event_id, payload = events[1]
    assert event_name == "heartbeat"
    assert event_id == "11"
    assert payload.get("seq") == 11
    assert isinstance(payload.get("server_epoch_ms"), int)
    assert payload.get("server_epoch_ms") > 0
