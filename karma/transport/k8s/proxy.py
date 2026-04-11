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

import json
import socket
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Port utilities
# ---------------------------------------------------------------------------

def find_free_port() -> int:
    """Return an available TCP port on localhost.

    Binds briefly to port 0 to let the OS assign a free port, then releases
    it before returning. There is a small TOCTOU window between release and
    use, which is acceptable for local and CI environments.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
        """
        self._upstream_url = upstream_url
        self._log_path = log_path
        self._port = port

    def start(self) -> None:
        """Start the proxy and block until :meth:`shutdown` is called.

        Writes ``{"port": N}`` to stdout so that ``backend.launch_proxy``
        can discover the allocated port before entering the request loop.
        """
        ...

    def _log_call(self, entry: dict[str, Any]) -> None:
        """Append one call log entry to the JSONL log file.

        Parameters
        ----------
        entry:
            Dict with keys ``timestamp``, ``verb``, ``path``, ``status``,
            and ``duration_ms``.
        """
        ...

    def shutdown(self) -> None:
        """Signal the server to stop accepting new connections and exit."""
        ...


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

    Used by ``backend.wait_for_readiness`` to poll for proxy startup
    without issuing real kubectl calls.
    """
    ...


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
    ...


if __name__ == "__main__":
    main(sys.argv[1:])
