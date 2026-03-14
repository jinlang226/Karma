import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from app.server import create_handler


class _SSEFakeApp:
    cluster_ok = True
    cluster_error = None

    # Unused non-SSE routes; kept to satisfy handler attribute access.
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

    def workflow_preview(self, payload):
        _ = payload
        return {"ok": True}

    def start_workflow(self, payload):
        _ = payload
        return {"ok": True}

    def start_judge(self, payload):
        _ = payload
        return {"ok": True}

    def judge_preview(self, payload):
        _ = payload
        return {"ok": True}

    # Workflow SSE.
    def get_workflow_stream_snapshot(self):
        return {"schema": "workflow_stream.v2", "seq": 10, "jobs": []}

    def get_workflow_events_since(self, since_seq, timeout_sec=15.0):
        _ = timeout_sec
        if int(since_seq) < 8:
            return {"reset": True, "events": [], "current_seq": 10}
        if int(since_seq) == 9:
            return {
                "reset": False,
                "events": [{"seq": 10, "type": "job_phase", "data": {"job_id": "wf_job_1"}}],
                "current_seq": 10,
            }
        return {"reset": False, "events": [], "current_seq": 10}

    # Judge SSE.
    def get_judge_stream_snapshot(self):
        return {"seq": 7, "jobs": [{"id": "judge_1"}]}

    def get_judge_events_since(self, since_seq, timeout_sec=15.0):
        _ = timeout_sec
        if int(since_seq) < 5:
            return {"reset": True, "events": [], "current_seq": 7}
        if int(since_seq) == 6:
            return {
                "reset": False,
                "events": [{"seq": 7, "type": "job_upsert", "data": {"job": {"id": "judge_1"}}}],
                "current_seq": 7,
            }
        return {"reset": False, "events": [], "current_seq": 7}


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


def _read_first_sse_event(host, port, path, headers=None):
    conn = HTTPConnection(host, port, timeout=3)
    conn.request("GET", path, headers=headers or {})
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


def test_workflow_stream_hello_and_replay_contract():
    app = _SSEFakeApp()
    with _server(app) as (host, port):
        event, event_id, payload = _read_first_sse_event(host, port, "/api/workflow/stream")
        assert event == "hello"
        assert event_id == "10"
        assert payload["schema"] == "workflow_stream.v2"
        assert "jobs" in payload

        event, event_id, payload = _read_first_sse_event(host, port, "/api/workflow/stream?since=9")
        assert event == "job_phase"
        assert event_id == "10"
        assert payload["job_id"] == "wf_job_1"

        event, event_id, payload = _read_first_sse_event(host, port, "/api/workflow/stream?since=1")
        assert event == "hello"
        assert event_id == "10"
        assert payload["schema"] == "workflow_stream.v2"


def test_judge_stream_hello_and_replay_contract():
    app = _SSEFakeApp()
    with _server(app) as (host, port):
        event, event_id, payload = _read_first_sse_event(host, port, "/api/judge/stream")
        assert event == "hello"
        assert event_id == "7"
        assert payload["seq"] == 7
        assert isinstance(payload.get("jobs"), list)

        event, event_id, payload = _read_first_sse_event(host, port, "/api/judge/stream?since=6")
        assert event == "job_upsert"
        assert event_id == "7"
        assert (payload.get("job") or {}).get("id") == "judge_1"

        event, event_id, payload = _read_first_sse_event(host, port, "/api/judge/stream?since=2")
        assert event == "hello"
        assert event_id == "7"
        assert payload["seq"] == 7
