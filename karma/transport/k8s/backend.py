"""
Public K8s transport API.

This is the only transport module imported by the rest of KARMA.
``runtime.case`` uses it to start and stop the kubectl proxy per stage.
``sandbox`` uses it to obtain the kubeconfig path when building the agent
container bundle.

``transport.k8s.proxy`` is never imported directly by ``runtime.*``.
"""

from __future__ import annotations

import base64
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
                pass
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
    bind_host: str = "127.0.0.1",
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
    # ``run_dir`` here is the stage directory, so the kubectl log lives directly
    # in it -- the same path evidence reads via
    # ``protocol.stage_kubectl_log_path(run_root, stage_id)``. Computing it as
    # ``stage_kubectl_log_path(run_dir, run_dir.name)`` double-nests
    # (stages/<id>/stages/<id>/...) and silently empties evidence + metrics.
    log_path = run_dir / "kubectl_log.jsonl"

    # Derive the upstream URL from KUBECONFIG or default.
    upstream_url = _get_upstream_url(env)
    merged_env = {**os.environ, **(env or {})}
    auth = _get_upstream_auth(env)

    cert_args: list[str] = []
    if auth.get("client_cert_file") and auth.get("client_key_file"):
        cert_args += ["--client-cert", auth["client_cert_file"],
                      "--client-key", auth["client_key_file"]]
    elif auth.get("client_cert_data") and auth.get("client_key_data"):
        cert_path = run_dir / "proxy-client.crt"
        key_path = run_dir / "proxy-client.key"
        cert_path.write_text(auth["client_cert_data"])
        key_path.write_text(auth["client_key_data"])
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass
        cert_args += ["--client-cert", str(cert_path), "--client-key", str(key_path)]
    if auth.get("token"):
        cert_args += ["--token", auth["token"]]

    last_error: RuntimeError | None = None
    for attempt in range(4):
        proxy_port = find_free_port()
        control_port = find_free_port()
        cmd = [
            sys.executable, "-m", "karma.transport.k8s.proxy",
            "--upstream-url", upstream_url,
            "--log-path", str(log_path),
            "--port", str(proxy_port),
            "--control-port", str(control_port),
            "--bind-host", bind_host,
            *cert_args,
        ]
        proc = subprocess.Popen(
            cmd,
            env=merged_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            (run_dir / "proxy.pid").write_text(str(proc.pid))
        except Exception:
            pass

        handle = ProxyHandle(proc, proxy_port, run_dir=run_dir, control_port=control_port)
        try:
            wait_for_readiness(handle, timeout_sec=readiness_timeout_sec)
            return handle
        except RuntimeError as exc:
            last_error = exc
            detail = str(exc)
            try:
                handle.teardown()
            except Exception:
                pass
            retryable = "Address already in use" in detail
            if not retryable or attempt == 3:
                raise
            time.sleep(0.1 * (attempt + 1))

    assert last_error is not None
    raise last_error


def _startup_failure_detail(proc: subprocess.Popen[str]) -> str:
    """Return a concise startup-failure detail string for a dead proxy process."""
    stdout_text = ""
    stderr_text = ""
    try:
        stdout_text, stderr_text = proc.communicate(timeout=0.1)
    except Exception:
        try:
            if proc.stdout is not None:
                stdout_text = proc.stdout.read() or ""
        except Exception:
            pass
        try:
            if proc.stderr is not None:
                stderr_text = proc.stderr.read() or ""
        except Exception:
            pass
    detail = (stderr_text or stdout_text or "").strip()
    if not detail:
        code = proc.returncode if proc.returncode is not None else "unknown"
        detail = f"exit={code}"
    return detail


def _get_upstream_auth(env: dict[str, str] | None) -> dict[str, str]:
    """Extract upstream API-server auth from the caller's KUBECONFIG.

    Returns a dict that may contain ``client_cert_data``/``client_key_data``
    (PEM strings), ``client_cert_file``/``client_key_file`` (paths), and/or
    ``token``. Empty when no usable auth is found (e.g. an auth-less local
    cluster), in which case the proxy forwards unauthenticated as before.
    """
    auth: dict[str, str] = {}
    try:
        r = subprocess.run(
            ["kubectl", "config", "view", "--raw", "--minify", "-o", "json"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, **(env or {})},
        )
        cfg = json.loads(r.stdout or "{}")
        users = cfg.get("users") or []
        user = (users[0].get("user") if users else {}) or {}
        ccd, ckd = user.get("client-certificate-data"), user.get("client-key-data")
        if ccd and ckd:
            auth["client_cert_data"] = base64.b64decode(ccd).decode()
            auth["client_key_data"] = base64.b64decode(ckd).decode()
        else:
            cc, ck = user.get("client-certificate"), user.get("client-key")
            if cc and ck:
                auth["client_cert_file"] = cc
                auth["client_key_file"] = ck
        if user.get("token"):
            auth["token"] = str(user["token"])
    except Exception:
        pass
    return auth


def _get_upstream_url(env: dict[str, str] | None) -> str:
    """Return the Kubernetes API server URL from KUBECONFIG or a default."""
    try:
        result = subprocess.run(
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
    docker: bool = False,
) -> Path:
    """Write the agent credential bundle to the run directory.

    Generates a kubeconfig pointing kubectl at the proxy and writes it to
    ``protocol.bundle_kubeconfig_path(run_dir)``. Also writes
    *namespace_env_vars* to ``protocol.bundle_env_path(run_dir)`` as JSON.

    The kubeconfig carries no credentials: the proxy authenticates to the
    real API server on the agent's behalf (see ``launch_proxy``), so
    *source_kubeconfig* is accepted for API compatibility but not needed.

    When *docker* is True the kubeconfig points at ``host.docker.internal``
    instead of ``127.0.0.1`` so the agent container can reach the proxy
    running on the host.

    Returns
    -------
    Path
        Path to the written kubeconfig file.
    """
    bundle_dir = protocol.ensure_bundle_dir(run_dir)
    # A container cannot reach the host's loopback; Docker exposes the host as
    # host.docker.internal.
    proxy_host = "host.docker.internal" if docker else "127.0.0.1"
    proxy_url = f"http://{proxy_host}:{proxy_handle.port}"

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
    poll_interval_sec: float = 0.1,
) -> None:
    """Block until the proxy is ready or the timeout expires.

    Polls with exponential backoff starting at *poll_interval_sec* and
    capping at 1 second to reduce busy-waiting once the proxy is slow
    to start.

    Raises
    ------
    RuntimeError
        When the proxy is not ready within *timeout_sec* seconds.
    """
    deadline = time.monotonic() + timeout_sec
    interval = poll_interval_sec
    while time.monotonic() < deadline:
        if proxy_handle.is_ready():
            return
        if proxy_handle._proc.poll() is not None:
            detail = _startup_failure_detail(proxy_handle._proc)
            raise RuntimeError(f"kubectl proxy exited before readiness: {detail}")
        time.sleep(interval)
        interval = min(interval * 1.5, 1.0)
    raise RuntimeError(
        f"kubectl proxy did not become ready within {timeout_sec}s "
        f"(port {proxy_handle.port})"
    )
