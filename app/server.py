import json
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .settings import STATIC_DIR


def create_handler(app):
    class RequestHandler(BaseHTTPRequestHandler):
        def _send_json(self, payload, status=200):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, content, status=200, content_type="text/plain"):
            data = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_sse_headers(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

        def _send_sse_event(self, event_name, payload, event_id=None):
            lines = []
            if event_id is not None:
                lines.append(f"id: {int(event_id)}")
            if event_name:
                lines.append(f"event: {event_name}")
            body = json.dumps(payload, separators=(",", ":"))
            if "\n" in body:
                for part in body.splitlines():
                    lines.append(f"data: {part}")
            else:
                lines.append(f"data: {body}")
            lines.append("")
            chunk = ("\n".join(lines) + "\n").encode("utf-8")
            self.wfile.write(chunk)
            self.wfile.flush()

        def _serve_static(self, rel_path):
            path = STATIC_DIR / rel_path
            if not path.exists() or not path.is_file():
                self._send_text("Not found", status=404)
                return
            content_type = "text/plain"
            if path.suffix == ".html":
                content_type = "text/html"
            elif path.suffix == ".js":
                content_type = "application/javascript"
            elif path.suffix == ".css":
                content_type = "text/css"
            self._send_text(path.read_text(), content_type=content_type)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._serve_static("index.html")
                return
            if parsed.path.startswith("/static/"):
                rel = parsed.path[len("/static/") :]
                self._serve_static(rel)
                return
            if parsed.path == "/api/services":
                payload = {
                    "services": app.list_services(),
                    "cluster_ok": app.cluster_ok,
                    "cluster_error": app.cluster_error,
                }
                self._send_json(payload)
                return
            if parsed.path.startswith("/api/services/") and parsed.path.endswith("/cases"):
                parts = parsed.path.split("/")
                if len(parts) >= 4:
                    service = parts[3]
                    self._send_json({"cases": app.list_cases(service)})
                    return
            if parsed.path.startswith("/api/cases/"):
                case_id = parsed.path[len("/api/cases/") :]
                self._send_json(app.get_case(case_id))
                return
            if parsed.path == "/api/run/status":
                status_fn = getattr(app, "manual_run_status", None)
                if not callable(status_fn):
                    status_fn = app.run_status
                self._send_json(status_fn())
                return
            if parsed.path == "/api/run/metrics":
                self._send_json(app.run_metrics())
                return
            if parsed.path == "/api/proxy/status":
                self._send_json(app.proxy_status())
                return
            if parsed.path == "/api/orchestrator/options":
                self._send_json(app.orchestrator_options())
                return
            if parsed.path == "/api/judge/stream":
                query = parse_qs(parsed.query or "")
                since = 0
                try:
                    if query.get("since"):
                        since = int(query.get("since")[0])
                except Exception:
                    since = 0
                try:
                    last_event_id = self.headers.get("Last-Event-ID")
                    if last_event_id:
                        since = max(since, int(last_event_id))
                except Exception:
                    pass

                try:
                    self._send_sse_headers()
                    cursor = 0
                    if since > 0:
                        replay = app.get_judge_events_since(since, timeout_sec=0.0)
                        events = replay.get("events") or []
                        if replay.get("reset") or not events:
                            snap = app.get_judge_stream_snapshot()
                            self._send_sse_event("hello", snap, event_id=snap.get("seq"))
                            cursor = int(snap.get("seq") or 0)
                        else:
                            for event in events:
                                event_id = int(event.get("seq") or cursor)
                                self._send_sse_event(
                                    event.get("type") or "message",
                                    event.get("data") or {},
                                    event_id=event_id,
                                )
                                cursor = max(cursor, event_id)
                    else:
                        snap = app.get_judge_stream_snapshot()
                        self._send_sse_event("hello", snap, event_id=snap.get("seq"))
                        cursor = int(snap.get("seq") or 0)

                    while True:
                        delta = app.get_judge_events_since(cursor, timeout_sec=15.0)
                        if delta.get("reset"):
                            snap = app.get_judge_stream_snapshot()
                            self._send_sse_event("hello", snap, event_id=snap.get("seq"))
                            cursor = int(snap.get("seq") or cursor)
                            continue

                        events = delta.get("events") or []
                        if events:
                            for event in events:
                                event_id = int(event.get("seq") or cursor)
                                self._send_sse_event(
                                    event.get("type") or "message",
                                    event.get("data") or {},
                                    event_id=event_id,
                                )
                                cursor = max(cursor, event_id)
                            continue

                        current_seq = int(delta.get("current_seq") or cursor)
                        self._send_sse_event("heartbeat", {"seq": current_seq}, event_id=current_seq)
                        cursor = max(cursor, current_seq)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    return
                except Exception:
                    return
            if parsed.path == "/api/workflow/stream":
                query = parse_qs(parsed.query or "")
                since = 0
                try:
                    if query.get("since"):
                        since = int(query.get("since")[0])
                except Exception:
                    since = 0
                try:
                    last_event_id = self.headers.get("Last-Event-ID")
                    if last_event_id:
                        since = max(since, int(last_event_id))
                except Exception:
                    pass

                try:
                    self._send_sse_headers()
                    cursor = 0
                    if since > 0:
                        replay = app.get_workflow_events_since(since, timeout_sec=0.0)
                        events = replay.get("events") or []
                        if replay.get("reset") or not events:
                            snap = app.get_workflow_stream_snapshot()
                            self._send_sse_event("hello", snap, event_id=snap.get("seq"))
                            cursor = int(snap.get("seq") or 0)
                        else:
                            for event in events:
                                event_id = int(event.get("seq") or cursor)
                                self._send_sse_event(
                                    event.get("type") or "message",
                                    event.get("data") or {},
                                    event_id=event_id,
                                )
                                cursor = max(cursor, event_id)
                    else:
                        snap = app.get_workflow_stream_snapshot()
                        self._send_sse_event("hello", snap, event_id=snap.get("seq"))
                        cursor = int(snap.get("seq") or 0)

                    while True:
                        delta = app.get_workflow_events_since(cursor, timeout_sec=15.0)
                        if delta.get("reset"):
                            snap = app.get_workflow_stream_snapshot()
                            self._send_sse_event("hello", snap, event_id=snap.get("seq"))
                            cursor = int(snap.get("seq") or cursor)
                            continue

                        events = delta.get("events") or []
                        if events:
                            for event in events:
                                event_id = int(event.get("seq") or cursor)
                                self._send_sse_event(
                                    event.get("type") or "message",
                                    event.get("data") or {},
                                    event_id=event_id,
                                )
                                cursor = max(cursor, event_id)
                            continue

                        current_seq = int(delta.get("current_seq") or cursor)
                        self._send_sse_event(
                            "heartbeat",
                            {"seq": current_seq, "server_epoch_ms": int(time.time() * 1000)},
                            event_id=current_seq,
                        )
                        cursor = max(cursor, current_seq)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    return
                except Exception:
                    return
            if parsed.path == "/api/judge/runs":
                self._send_json({"runs": app.list_judge_runs()})
                return
            if parsed.path == "/api/judge/batches":
                self._send_json({"batches": app.list_judge_batches()})
                return
            if parsed.path == "/api/judge/jobs":
                self._send_json({"jobs": app.list_judge_jobs()})
                return
            if parsed.path.startswith("/api/judge/jobs/"):
                job_id = parsed.path[len("/api/judge/jobs/") :]
                payload = app.get_judge_job(job_id)
                if payload is None:
                    self._send_json({"error": "Judge job not found"}, status=404)
                else:
                    self._send_json(payload)
                return
            if parsed.path == "/api/workflow/files":
                self._send_json({"workflows": app.list_workflow_files()})
                return
            if parsed.path == "/api/workflow/jobs":
                self._send_json({"jobs": app.list_workflow_jobs()})
                return
            if parsed.path.startswith("/api/workflow/jobs/"):
                suffix = parsed.path[len("/api/workflow/jobs/") :]
                if suffix.endswith("/prompt"):
                    job_id = str(suffix[: -len("/prompt")] or "").strip().strip("/")
                    if job_id and "/" not in job_id:
                        raw_max = parse_qs(parsed.query or "").get("max_chars", [None])[0]
                        max_chars = None
                        if raw_max not in (None, ""):
                            try:
                                max_chars = int(raw_max)
                            except Exception:
                                self._send_json({"error": "max_chars must be an integer"}, status=400)
                                return
                        fn = getattr(app, "get_workflow_job_prompt", None)
                        if not callable(fn):
                            result = {"error": "Workflow prompt endpoint is unavailable", "http_status": 501}
                        else:
                            result = fn(job_id, max_chars=max_chars)
                        status = int((result or {}).get("http_status") or (200 if "error" not in (result or {}) else 400))
                        response = dict(result or {})
                        response.pop("http_status", None)
                        self._send_json(response, status=status)
                        return
                job_id = str(suffix or "").strip().strip("/")
                payload = app.get_workflow_job(job_id)
                if payload is None:
                    self._send_json({"error": "Workflow job not found"}, status=404)
                else:
                    self._send_json(payload)
                return
            self._send_text("Not found", status=404)

        def do_POST(self):
            parsed = urlparse(self.path)
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8") if content_len else ""
            if body:
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    self._send_json({"error": "Invalid JSON"}, status=400)
                    return
            else:
                payload = {}

            if parsed.path == "/api/run/start":
                case_id = payload.get("case_id")
                if not case_id:
                    self._send_json({"error": "case_id is required"}, status=400)
                    return
                start_fn = getattr(app, "start_manual_run", None)
                if not callable(start_fn):
                    start_fn = app.start_run
                result = start_fn(case_id, max_attempts_override=payload.get("max_attempts"))
                status = 200 if "error" not in result else 400
                self._send_json(result, status=status)
                return
            if parsed.path == "/api/run/submit":
                submit_fn = getattr(app, "submit_manual_run", None)
                if not callable(submit_fn):
                    submit_fn = app.submit_run
                result = submit_fn()
                status = 200 if "error" not in result else 400
                self._send_json(result, status=status)
                return
            if parsed.path == "/api/run/cleanup":
                cleanup_fn = getattr(app, "cleanup_manual_run", None)
                if not callable(cleanup_fn):
                    cleanup_fn = app.cleanup_run
                result = cleanup_fn()
                status = 200 if "error" not in result else 400
                self._send_json(result, status=status)
                return
            if parsed.path == "/api/orchestrator/preview":
                result = app.orchestrator_preview(payload)
                self._send_json(result, status=200)
                return
            if parsed.path == "/api/judge/start":
                result = app.start_judge(payload)
                status = 200 if "error" not in result else 400
                self._send_json(result, status=status)
                return
            if parsed.path == "/api/judge/preview":
                result = app.judge_preview(payload)
                status = 200 if result.get("ok", False) else 400
                self._send_json(result, status=status)
                return
            if parsed.path == "/api/workflow/preview":
                result = app.workflow_preview(payload)
                status = 200 if result.get("ok", False) else 400
                self._send_json(result, status=status)
                return
            if parsed.path == "/api/workflow/import":
                result = app.workflow_import(payload)
                status = 200 if result.get("ok", False) else 400
                self._send_json(result, status=status)
                return
            if parsed.path.startswith("/api/workflow/jobs/"):
                suffix = parsed.path[len("/api/workflow/jobs/") :]
                job_action = None
                if suffix.endswith("/submit"):
                    job_action = "submit"
                    job_id = suffix[: -len("/submit")]
                elif suffix.endswith("/cleanup"):
                    job_action = "cleanup"
                    job_id = suffix[: -len("/cleanup")]
                else:
                    job_id = suffix
                job_id = str(job_id or "").strip().strip("/")
                if job_action and job_id and "/" not in job_id:
                    if job_action == "submit":
                        fn = getattr(app, "submit_workflow_job", None)
                        if not callable(fn):
                            result = {"error": "Workflow submit control is unavailable", "http_status": 501}
                        else:
                            result = fn(job_id)
                    else:
                        fn = getattr(app, "cleanup_workflow_job", None)
                        if not callable(fn):
                            result = {"error": "Workflow cleanup control is unavailable", "http_status": 501}
                        else:
                            result = fn(job_id)
                    status = int((result or {}).get("http_status") or (200 if "error" not in (result or {}) else 400))
                    response = dict(result or {})
                    response.pop("http_status", None)
                    self._send_json(response, status=status)
                    return
            if parsed.path == "/api/workflow/start":
                result = app.start_workflow(payload)
                status = 200 if "error" not in result else 400
                self._send_json(result, status=status)
                return

            self._send_text("Not found", status=404)

    return RequestHandler
