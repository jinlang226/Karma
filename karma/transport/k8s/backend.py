"""
Public K8s transport API.

This is the only transport module imported by the rest of KARMA.
``runtime.case`` uses it to start and stop the kubectl proxy per stage.
``sandbox`` uses it to obtain the kubeconfig path when building the agent
container bundle.

``transport.k8s.proxy`` is never imported directly by ``runtime.*``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from ... import protocol


class ProxyHandle:
    """Handle for a running kubectl proxy subprocess.

    Returned by :func:`launch_proxy`. Provides port discovery, readiness
    polling, bundle writing, and teardown.
    """

    def __init__(
        self,
        proc: subprocess.Popen,
        port: int,
        *,
        run_dir: Path,
        control_port: int | None = None,
    ) -> None:
        """Wrap a running proxy process.

        Parameters
        ----------
        proc:
            The underlying :class:`subprocess.Popen` for the proxy daemon.
        port:
            TCP port the proxy is listening on.
        run_dir:
            Stage run directory used for logging.
        control_port:
            Optional port for the control endpoint.
        """
        self._proc = proc
        self._port = port
        self._run_dir = run_dir
        self._control_port = control_port

    @property
    def port(self) -> int:
        """TCP port the proxy is listening on."""
        return self._port

    def is_ready(self) -> bool:
        """Return ``True`` when the proxy is running and accepting connections.

        Non-blocking. Checks the process status then probes the port.
        """
        if self._proc.poll() is not None:
            return False
        if self._control_port:
            try:
                url = f"http://127.0.0.1:{self._control_port}/health"
                with urllib.request.urlopen(url, timeout=1) as resp:
                    return resp.status == 200
            except Exception:
                return False
        # Fall back to checking if the proxy port is open
        try:
            with socket.create_connection(("127.0.0.1", self._port), timeout=1):
                return True
        except OSError:
            return False

    def teardown(self) -> None:
        """Terminate the proxy subprocess and wait for it to exit.

        Sends SIGTERM then SIGKILL after a short grace period. Safe to call
        on an already-stopped proxy.
        """
        if self._control_port:
            try:
                url = f"http://127.0.0.1:{self._control_port}/shutdown"
                urllib.request.urlopen(
                    urllib.request.Request(url, method="POST"), timeout=2
                )
            except Exception:
                pass

        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)


def launch_proxy(
    *,
    run_dir: Path,
    readiness_timeout_sec: int = 15,
    env: dict[str, str] | None = None,
) -> ProxyHandle:
    """Launch the kubectl proxy daemon on a random available port.

    Spawns ``transport.k8s.proxy`` as a subprocess and polls for
    readiness.

    Parameters
    ----------
    run_dir:
        Stage run directory used for the proxy log file.
    readiness_timeout_sec:
        Maximum seconds to wait for the proxy to accept connections.
    env:
        Additional environment variables forwarded to the proxy process.

    Raises
    ------
    RuntimeError
        When the proxy does not become ready within *readiness_timeout_sec*.
    """
    from .proxy import find_free_port

    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = protocol.stage_kubectl_log_path(run_dir, run_dir.name)

    proxy_port = find_free_port()
    control_port = find_free_port()

    # Derive the upstream URL from KUBECONFIG or default.
    upstream_url = _get_upstream_url(env)

    merged_env = {**os.environ, **(env or {})}
    cmd = [
        sys.executable, "-m", "karma.transport.k8s.proxy",
        "--upstream-url", upstream_url,
        "--log-path", str(log_path),
        "--port", str(proxy_port),
        "--control-port", str(control_port),
    ]
    proc = subprocess.Popen(
        cmd, env=merged_env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )

    handle = ProxyHandle(proc, proxy_port, run_dir=run_dir, control_port=control_port)
    wait_for_readiness(handle, timeout_sec=readiness_timeout_sec)
    return handle


def _get_upstream_url(env: dict[str, str] | None) -> str:
    """Return the Kubernetes API server URL from KUBECONFIG or a default."""
    import subprocess as sp
    try:
        result = sp.run(
            ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"],
            capture_output=True, text=True, timeout=5, env={**os.environ, **(env or {})}
        )
        url = result.stdout.strip()
        if url:
            return url
    except Exception:
        pass
    return "https://127.0.0.1:6443"


def write_agent_bundle(
    proxy_handle: ProxyHandle,
    *,
    run_dir: Path,
    namespace_env_vars: dict[str, str],
    source_kubeconfig: Path | None = None,
) -> Path:
    """Write the agent credential bundle to the run directory.

    Generates a kubeconfig pointing kubectl at the proxy and writes it to
    ``protocol.bundle_kubeconfig_path(run_dir)``. Also writes
    *namespace_env_vars* to ``protocol.bundle_env_path(run_dir)`` as JSON.

    When *source_kubeconfig* is provided its cluster and auth sections
    are used as the base; otherwise a minimal localhost kubeconfig is
    generated.

    Returns
    -------
    Path
        Path to the written kubeconfig file.
    """
    bundle_dir = protocol.ensure_bundle_dir(run_dir)
    proxy_url = f"http://127.0.0.1:{proxy_handle.port}"

    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "karma-proxy", "cluster": {"server": proxy_url}}],
        "users": [{"name": "karma-agent", "user": {}}],
        "contexts": [{"name": "karma", "context": {"cluster": "karma-proxy", "user": "karma-agent"}}],
        "current-context": "karma",
    }

    import yaml as _yaml
    kc_path = protocol.bundle_kubeconfig_path(run_dir)
    kc_path.write_text(_yaml.dump(kubeconfig))

    env_path = protocol.bundle_env_path(run_dir)
    env_path.write_text(json.dumps(namespace_env_vars, indent=2))

    return kc_path


def wait_for_readiness(
    proxy_handle: ProxyHandle,
    *,
    timeout_sec: int = 15,
    poll_interval_sec: float = 0.25,
) -> None:
    """Block until the proxy is ready or the timeout expires.

    Raises
    ------
    RuntimeError
        When the proxy is not ready within *timeout_sec* seconds.
    """
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if proxy_handle.is_ready():
            return
        time.sleep(poll_interval_sec)
    raise RuntimeError(
        f"kubectl proxy did not become ready within {timeout_sec}s "
        f"(port {proxy_handle.port})"
    )
