"""
Standalone kubectl proxy daemon.

Intercepts kubectl API calls from the agent, forwards them to the real
Kubernetes API server, and logs every call to a JSONL file in the stage
run directory.

This module is not imported by the rest of KARMA. It is always launched
as a subprocess by ``transport.k8s.backend``. Import it directly only in
tests that exercise proxy behavior in isolation.

Canonical manual debug command::

    python -m karma.transport.k8s.proxy [args...]
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Port utilities
# ---------------------------------------------------------------------------

def _is_streaming_request(path: str) -> bool:
    """True for watch/follow requests (``kubectl wait``, ``rollout status``,
    ``logs -f``), whose upstream response is an unbounded stream that must be
    forwarded incrementally rather than buffered with ``resp.read()``."""
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(path).query)

    def _truthy(key: str) -> bool:
        v = q.get(key)
        return bool(v) and str(v[0]).lower() in ("true", "1")

    return _truthy("watch") or _truthy("follow")


def find_free_port() -> int:
    """Return an available TCP port on localhost.

    Binds briefly to port 0 to let the OS assign a free port, then releases
    it before returning. There is a small TOCTOU window between release and
    use, which is acceptable for local and CI environments.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _read_upstream_status(sock: Any, cap: int = 65536) -> tuple[int | None, bytes]:
    """Peek an upgrade tunnel's upstream HTTP status line.

    Reads from *sock* only up to the end of the first line (the HTTP status
    line) so the call log can record the real outcome of an exec/attach/
    port-forward: 101 on a successful protocol switch, or the 4xx/5xx the API
    server returned when it rejected the request. Returns
    ``(status_code_or_None, bytes_read)``; the caller MUST forward *bytes_read*
    to the client unchanged, since reading here consumes them from the upstream.
    """
    buf = b""
    while b"\r\n" not in buf and len(buf) < cap:
        try:
            chunk = sock.recv(4096)
        except Exception:
            break
        if not chunk:
            break
        buf += chunk
    line = buf.split(b"\r\n", 1)[0]
    parts = line.split(None, 2)
    if len(parts) >= 2 and parts[0].upper().startswith(b"HTTP/"):
        try:
            return int(parts[1]), buf
        except ValueError:
            pass
    return None, buf


# ---------------------------------------------------------------------------
# Proxy server
# ---------------------------------------------------------------------------

class KubectlProxyServer:
    """Minimal HTTP proxy that intercepts and logs kubectl API calls.

    Listens on *port* and forwards all requests to *upstream_url*. Every
    request and response pair is appended to *log_path* as a JSONL entry.
    """

    def __init__(
        self,
        *,
        upstream_url: str,
        log_path: Path,
        port: int,
        client_cert: str | None = None,
        client_key: str | None = None,
        token: str | None = None,
        bind_host: str = "127.0.0.1",
        request_timeout: int = 120,
    ) -> None:
        """Initialize the proxy.

        Parameters
        ----------
        upstream_url:
            Real Kubernetes API server URL, e.g. ``https://127.0.0.1:6443``.
        log_path:
            Path to the JSONL call log file.
        port:
            TCP port to listen on.
        client_cert, client_key:
            PEM file paths for client-certificate auth to the upstream API
            server. The agent talks to this proxy over plain HTTP and so
            cannot present a client cert itself, so the proxy presents it on
            the agent's behalf (e.g. kind/kubeadm clusters).
        token:
            Bearer token for upstream auth (token-based clusters), added as an
            ``Authorization`` header on each forwarded request.
        request_timeout:
            Read timeout (seconds) for a non-streaming upstream request. A slow
            admission webhook can push a legitimate apply/create past a short
            cap and surface a spurious 502; default 120 (KARMA's command
            timeout). Watch/follow streams use their own long window. (SS-4)
        """
        self._upstream_url = upstream_url.rstrip("/")
        self._log_path = log_path
        self._port = port
        self._bind_host = bind_host
        self._client_cert = client_cert
        self._client_key = client_key
        self._token = token
        self._request_timeout = request_timeout
        self._server: HTTPServer | None = None
        self._stop_event = threading.Event()
        # ThreadingHTTPServer handles each request on its own thread, so the
        # call-log append must be serialized to avoid interleaved JSONL lines.
        self._log_lock = threading.Lock()

    def start(self) -> None:
        """Start the proxy and block until :meth:`shutdown` is called.

        Writes ``{"port": N}`` to stdout as a startup marker, then serves
        requests. The listening port is fixed by ``--port`` (chosen by
        ``backend.launch_proxy``), not discovered from this line.
        """
        upstream = self._upstream_url
        log_path = self._log_path
        stop_event = self._stop_event
        client_cert = self._client_cert
        client_key = self._client_key
        token = self._token
        request_timeout = self._request_timeout
        log_path.parent.mkdir(parents=True, exist_ok=True)

        proxy_self = self

        class _Handler(BaseHTTPRequestHandler):
            def _handle_upgrade(self, start_ts: float) -> None:
                """Tunnel a connection-upgrade request (kubectl exec / attach /
                port-forward) raw to the API server. These switch protocols
                (SPDY or WebSocket) via HTTP 101, which urllib cannot negotiate,
                so we open a TLS socket to the upstream, replay the request line
                + headers (presenting the agent's auth), and shuttle bytes both
                ways until either side closes."""
                import socket as _socket
                import ssl as _ssl
                from urllib.parse import urlparse
                up = urlparse(upstream)
                host = up.hostname
                port = up.port or (443 if up.scheme == "https" else 80)
                status = 101
                usock = None
                try:
                    lines = [f"{self.command} {self.path} HTTP/1.1", f"Host: {host}:{port}"]
                    for hk, hv in self.headers.items():
                        if hk.lower() == "host":
                            continue
                        lines.append(f"{hk}: {hv}")
                    if token:
                        lines.append(f"Authorization: Bearer {token}")
                    raw = ("\r\n".join(lines) + "\r\n\r\n").encode()

                    sock = _socket.create_connection((host, port), timeout=30)
                    if up.scheme == "https":
                        ctx = _ssl.create_default_context()
                        ctx.check_hostname = False
                        ctx.verify_mode = _ssl.CERT_NONE
                        if client_cert and client_key:
                            ctx.load_cert_chain(certfile=client_cert, keyfile=client_key)
                        usock = ctx.wrap_socket(sock, server_hostname=host)
                    else:
                        usock = sock
                    usock.sendall(raw)

                    client = self.connection

                    # Peek the upstream's HTTP status line so the call log records
                    # the real outcome: a rejected exec/attach/port-forward returns
                    # 4xx/5xx here, not the 101 we optimistically assumed above. The
                    # peeked bytes are forwarded to the client unchanged -- we read
                    # only to log, not to consume the response.
                    code, head = _read_upstream_status(usock)
                    if code is not None:
                        status = code
                    if head:
                        client.sendall(head)

                    def _pump(src, dst):
                        try:
                            while True:
                                data = src.recv(65536)
                                if not data:
                                    break
                                dst.sendall(data)
                        except Exception:
                            pass
                        finally:
                            for s in (src, dst):
                                try:
                                    s.shutdown(_socket.SHUT_RDWR)
                                except Exception:
                                    pass

                    t_up = threading.Thread(target=_pump, args=(client, usock), daemon=True)
                    t_down = threading.Thread(target=_pump, args=(usock, client), daemon=True)
                    t_up.start(); t_down.start()
                    t_up.join(); t_down.join()
                except Exception as exc:
                    status = 502
                    try:
                        self.send_response(502)
                        self.end_headers()
                        self.wfile.write(str(exc).encode())
                    except Exception:
                        pass
                finally:
                    if usock is not None:
                        try:
                            usock.close()
                        except Exception:
                            pass
                    self.close_connection = True
                proxy_self._log_call({
                    "timestamp": start_ts, "verb": self.command.lower(),
                    "path": self.path, "status": status,
                    "duration_ms": int((time.time() - start_ts) * 1000),
                    "streamed": True, "upgraded": True,
                })

            def do_command(self) -> None:
                start_ts = time.time()
                # kubectl exec/attach/port-forward upgrade the connection (SPDY or
                # WebSocket); tunnel those raw since urllib can't switch protocols.
                if self.headers.get("Upgrade"):
                    self._handle_upgrade(start_ts)
                    return
                target_url = upstream + self.path
                content_length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(content_length) if content_length > 0 else None

                headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ("host", "transfer-encoding")}
                # The agent kubeconfig points here over plain HTTP with no
                # credentials; authenticate to the real API server on its behalf.
                if token:
                    headers["Authorization"] = f"Bearer {token}"

                streaming = _is_streaming_request(self.path)
                req = urllib.request.Request(
                    target_url, data=body, method=self.command, headers=headers
                )
                resp_headers: list = []
                resp = None
                try:
                    import ssl
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    if client_cert and client_key:
                        ctx.load_cert_chain(certfile=client_cert, keyfile=client_key)
                    # Watch/follow streams stay open; give them a long read
                    # window (kubectl applies its own --timeout and closes). A
                    # non-stream op gets request_timeout (SS-4: a hardcoded 60s
                    # tripped a spurious 502 on slow admission webhooks).
                    resp = urllib.request.urlopen(
                        req, context=ctx, timeout=600 if streaming else request_timeout
                    )
                    status = resp.status
                    resp_headers = list(resp.headers.items())
                    resp_data = None if streaming else resp.read()
                except urllib.error.HTTPError as exc:
                    status = exc.code
                    resp_data = exc.read()
                    resp_headers = list(exc.headers.items()) if exc.headers else []
                    streaming = False
                except Exception as exc:
                    status = 502
                    resp_data = str(exc).encode()
                    streaming = False

                def _fwd_headers() -> None:
                    # Forward Content-Type / Content-Encoding so kubectl can
                    # decode; drop hop-by-hop + length/encoding we re-set.
                    for hk, hv in resp_headers:
                        if hk.lower() in ("transfer-encoding", "connection",
                                          "keep-alive", "content-length"):
                            continue
                        self.send_header(hk, hv)

                if streaming and resp is not None:
                    # Stream the watch incrementally: HTTP/1.0 connection-close
                    # framing (no Content-Length), flushing each chunk so the
                    # agent's `kubectl wait`/`rollout status` see events as they
                    # arrive instead of blocking on a buffered read.
                    read1 = getattr(resp, "read1", None)
                    # SS-5b: the header writes live inside this try, so an agent
                    # that has already disconnected (a header write then throws)
                    # still reaches the finally and closes the upstream response
                    # instead of leaking it.
                    try:
                        self.send_response(status)
                        _fwd_headers()
                        self.send_header("Connection", "close")
                        self.end_headers()
                        # Use read1(): it returns as soon as ONE socket read yields
                        # data, so each watch event is forwarded the instant it
                        # arrives. Plain read(n) on a chunked stream blocks trying
                        # to accumulate n bytes across multiple events -- a single
                        # small event (e.g. a Job flipping to Complete) would sit
                        # buffered until kubectl's own --timeout fired, making
                        # `kubectl wait`/`rollout status` falsely time out even
                        # though the condition was already met.
                        while True:
                            chunk = read1(65536) if read1 is not None else resp.read(4096)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    except Exception:
                        pass  # client closed (watch satisfied) or upstream ended
                    finally:
                        try:
                            resp.close()
                        except Exception:
                            pass
                else:
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception:
                            pass
                    self.send_response(status)
                    _fwd_headers()
                    self.send_header("Content-Length", str(len(resp_data)))
                    self.end_headers()
                    self.wfile.write(resp_data)

                duration_ms = int((time.time() - start_ts) * 1000)
                proxy_self._log_call({
                    "timestamp": start_ts,
                    "verb": self.command.lower(),
                    "path": self.path,
                    "status": status,
                    "duration_ms": duration_ms,
                    "streamed": bool(streaming),
                })

            def log_message(self, fmt: str, *args: Any) -> None:
                pass  # suppress default access log

            do_GET = do_command
            do_POST = do_command
            do_PUT = do_command
            do_PATCH = do_command
            do_DELETE = do_command
            do_HEAD = do_command

        # Threaded so a long-poll (kubectl wait / rollout status / --wait
        # delete) handled in one thread does not block other agent requests.
        self._server = ThreadingHTTPServer((self._bind_host, self._port), _Handler)
        sys.stdout.write(json.dumps({"port": self._port}) + "\n")
        sys.stdout.flush()

        self._server.timeout = 1.0
        while not stop_event.is_set():
            self._server.handle_request()
        self._server.server_close()

    def _log_call(self, entry: dict[str, Any]) -> None:
        """Append one call log entry to the JSONL log file.

        Parameters
        ----------
        entry:
            Dict with keys ``timestamp``, ``verb``, ``path``, ``status``,
            ``duration_ms``, and ``streamed`` (plus ``upgraded`` for
            exec/attach/port-forward tunnels).
        """
        with self._log_lock, self._log_path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def shutdown(self) -> None:
        """Signal the server to stop accepting new connections and exit."""
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Control endpoint
# ---------------------------------------------------------------------------

def start_control_server(proxy: KubectlProxyServer, *, control_port: int) -> None:
    """Start a minimal HTTP control server on *control_port*.

    Exposes two endpoints:

    ``GET /health``
        Returns ``200 {"status": "ok"}`` when the proxy is ready.
    ``POST /shutdown``
        Signals the proxy to shut down.

    Only ``/shutdown`` is currently used (by ``ProxyHandle.teardown``).
    Readiness is gated on the data port (see ``ProxyHandle.is_ready``),
    not on this ``/health`` endpoint.
    """
    class _ControlHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                body = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if self.path == "/shutdown":
                proxy.shutdown()
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    ctrl_server = HTTPServer(("127.0.0.1", control_port), _ControlHandler)
    sys.stdout.write(json.dumps({"control_port": control_port}) + "\n")
    sys.stdout.flush()
    ctrl_server.timeout = 1.0
    while not proxy._stop_event.is_set():
        ctrl_server.handle_request()
    ctrl_server.server_close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """Parse arguments and start the proxy daemon.

    Arguments
    ---------
    --upstream-url
        URL of the real Kubernetes API server.
    --log-path
        Path to the JSONL call log file.
    --port
        Port to listen on (default: random available port).
    --control-port
        Port for the control endpoint (default: random available port).
    """
    parser = argparse.ArgumentParser(description="KARMA kubectl proxy daemon")
    parser.add_argument("--upstream-url", required=True, help="Real Kubernetes API server URL")
    parser.add_argument("--log-path", required=True, help="Path to the JSONL call log file")
    parser.add_argument("--port", type=int, default=0, help="Port to listen on (0=random)")
    parser.add_argument("--control-port", type=int, default=0, help="Control endpoint port (0=random)")
    parser.add_argument("--client-cert", default=None, help="Client cert PEM for upstream auth")
    parser.add_argument("--client-key", default=None, help="Client key PEM for upstream auth")
    parser.add_argument("--token", default=None, help="Bearer token for upstream auth")
    parser.add_argument("--bind-host", default="127.0.0.1",
                        help="Address to bind (0.0.0.0 for docker-sandbox reachability)")
    parser.add_argument("--request-timeout", type=int, default=120,
                        help="Read timeout (s) for a non-streaming upstream request")
    args = parser.parse_args(argv)

    port = args.port or find_free_port()
    control_port = args.control_port or find_free_port()

    proxy = KubectlProxyServer(
        upstream_url=args.upstream_url,
        log_path=Path(args.log_path),
        port=port,
        client_cert=args.client_cert,
        client_key=args.client_key,
        token=args.token,
        bind_host=args.bind_host,
        request_timeout=args.request_timeout,
    )

    ctrl_thread = threading.Thread(
        target=start_control_server, args=(proxy,), kwargs={"control_port": control_port}, daemon=True
    )
    ctrl_thread.start()
    proxy.start()


if __name__ == "__main__":
    main(sys.argv[1:])
