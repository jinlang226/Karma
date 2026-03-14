#!/usr/bin/env python3
import argparse
import json
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


BUFFER_SIZE = 65536
LOG_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
LOG_STATE = {
    "path": None,
    "disabled": False,
    "run_id": None,
}


def utc_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_host_port(value, default_port):
    if value.startswith("["):
        host, _, port_str = value.rpartition("]:")
        if port_str:
            return host.lstrip("["), int(port_str)
        return value.strip("[]"), default_port
    if ":" in value:
        host, port_str = value.rsplit(":", 1)
        return host, int(port_str)
    return value, default_port


def _set_log_state(path=None, run_id=None, disabled=False):
    with STATE_LOCK:
        LOG_STATE["path"] = path
        LOG_STATE["run_id"] = run_id
        LOG_STATE["disabled"] = disabled


def _get_log_state():
    with STATE_LOCK:
        return LOG_STATE["path"], LOG_STATE["run_id"], LOG_STATE["disabled"]


def _maybe_attach_run_id(record):
    path, run_id, disabled = _get_log_state()
    if disabled or not run_id:
        return record
    if "run_id" not in record:
        record["run_id"] = run_id
    return record


def append_log(log_path, record):
    record = _maybe_attach_run_id(record)
    line = json.dumps(record, sort_keys=True)
    with LOG_LOCK:
        if log_path is None:
            path, _, disabled = _get_log_state()
            if disabled:
                return
            log_path = path
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        else:
            print(line, flush=True)


def pump(src, dst, counter):
    try:
        while True:
            data = src.recv(BUFFER_SIZE)
            if not data:
                break
            dst.sendall(data)
            counter[0] += len(data)
    except Exception:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle_client(client_sock, client_addr, upstream):
    started = time.time()
    bytes_to_upstream = [0]
    bytes_to_client = [0]
    status = "ok"
    error = None

    append_log(
        None,
        {
            "ts": utc_ts(),
            "event": "connection_start",
            "client": f"{client_addr[0]}:{client_addr[1]}",
            "upstream": f"{upstream[0]}:{upstream[1]}",
        },
    )

    try:
        upstream_sock = socket.create_connection(upstream)
    except Exception as exc:
        status = "upstream_error"
        error = str(exc)
        client_sock.close()
        append_log(
            None,
            {
                "ts": utc_ts(),
                "client": f"{client_addr[0]}:{client_addr[1]}",
                "upstream": f"{upstream[0]}:{upstream[1]}",
                "status": status,
                "error": error,
                "duration_ms": 0,
                "bytes_to_upstream": 0,
                "bytes_to_client": 0,
            },
        )
        return

    t1 = threading.Thread(target=pump, args=(client_sock, upstream_sock, bytes_to_upstream))
    t2 = threading.Thread(target=pump, args=(upstream_sock, client_sock, bytes_to_client))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    try:
        client_sock.close()
    except OSError:
        pass
    try:
        upstream_sock.close()
    except OSError:
        pass

    duration_ms = int((time.time() - started) * 1000)
    append_log(
        None,
        {
            "ts": utc_ts(),
            "event": "connection_end",
            "client": f"{client_addr[0]}:{client_addr[1]}",
            "upstream": f"{upstream[0]}:{upstream[1]}",
            "status": status,
            "error": error,
            "duration_ms": duration_ms,
            "bytes_to_upstream": bytes_to_upstream[0],
            "bytes_to_client": bytes_to_client[0],
        },
    )


class ControlHandler(BaseHTTPRequestHandler):
    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        payload = self.rfile.read(length)
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path != "/status":
            self._send_json(404, {"error": "not_found"})
            return
        path, run_id, disabled = _get_log_state()
        self._send_json(
            200,
            {
                "status": "ok",
                "log_path": path,
                "run_id": run_id,
                "disabled": disabled,
            },
        )

    def do_POST(self):
        if self.path not in ("/start", "/stop"):
            self._send_json(404, {"error": "not_found"})
            return

        payload = self._read_body()
        if self.path == "/start":
            log_path = payload.get("log_path")
            run_id = payload.get("run_id")
            if not log_path:
                self._send_json(400, {"error": "log_path required"})
                return
            _set_log_state(path=log_path, run_id=run_id, disabled=False)
            append_log(
                None,
                {
                    "ts": utc_ts(),
                    "event": "log_start",
                    "run_id": run_id,
                    "log_path": log_path,
                },
            )
            self._send_json(200, {"status": "started", "log_path": log_path, "run_id": run_id})
            return

        append_log(
            None,
            {
                "ts": utc_ts(),
                "event": "log_stop",
            },
        )
        _set_log_state(path=None, run_id=None, disabled=True)
        self._send_json(200, {"status": "stopped"})


def _run_control_server(listen):
    listen_host, listen_port = parse_host_port(listen, 9090)
    server = HTTPServer((listen_host, listen_port), ControlHandler)
    server.serve_forever()


def run_proxy(listen, upstream, log_path):
    listen_host, listen_port = parse_host_port(listen, 8081)
    upstream_host, upstream_port = parse_host_port(upstream, 6443)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((listen_host, listen_port))
    server.listen(128)

    append_log(
        None,
        {
            "ts": utc_ts(),
            "event": "proxy_start",
            "listen": f"{listen_host}:{listen_port}",
            "upstream": f"{upstream_host}:{upstream_port}",
        },
    )

    try:
        while True:
            client_sock, client_addr = server.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(client_sock, client_addr, (upstream_host, upstream_port)),
                daemon=True,
            )
            thread.start()
    except KeyboardInterrupt:
        append_log(None, {"ts": utc_ts(), "event": "proxy_stop"})
    finally:
        server.close()


def main():
    parser = argparse.ArgumentParser(description="Simple TCP proxy for Kubernetes API traffic.")
    parser.add_argument(
        "--listen",
        default="127.0.0.1:8081",
        help="Host:port to listen on (default: 127.0.0.1:8081).",
    )
    parser.add_argument(
        "--upstream",
        required=True,
        help="Upstream host:port for the API server.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional JSONL log file path (defaults to stdout).",
    )
    parser.add_argument(
        "--control-listen",
        default=None,
        help="Optional host:port for control API (e.g. 127.0.0.1:8082).",
    )
    args = parser.parse_args()
    _set_log_state(path=args.log_file, run_id=None, disabled=False)
    if args.control_listen:
        thread = threading.Thread(
            target=_run_control_server, args=(args.control_listen,), daemon=True
        )
        thread.start()
    run_proxy(args.listen, args.upstream, args.log_file)


if __name__ == "__main__":
    main()
