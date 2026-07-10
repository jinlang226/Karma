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
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from ... import protocol
from ...settings import settings


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
        control_port: int | None = None,
    ) -> None:
        """Wrap a running proxy process.

        Parameters
        ----------
        proc:
            The underlying :class:`subprocess.Popen` for the proxy daemon.
        port:
            TCP port the proxy is listening on.
        control_port:
            Optional port for the control endpoint.
        """
        self._proc = proc
        self._port = port
        self._control_port = control_port
        # Host-only temp dir holding the upstream client cert/key (set by
        # launch_proxy). Kept OUT of run_dir so the agent's /workspace mount never
        # exposes the cluster credential; removed on teardown.
        self._creds_dir: Path | None = None

    @property
    def port(self) -> int:
        """TCP port the proxy is listening on."""
        return self._port

    def is_ready(self) -> bool:
        """Return ``True`` when the proxy is running and accepting connections.

        Non-blocking. Checks the process status, then probes the **data port**
        the agent actually uses -- NOT the control port. The control server runs
        in a daemon thread (see ``proxy.main``); if its bind loses a port race it
        dies while the data proxy keeps serving, so gating readiness on the
        control ``/health`` alone falsely reported "did not become ready". The
        data port is the only thing the agent needs.
        """
        if self._proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", self._port), timeout=1):
                return True
        except OSError:
            return False

    def is_alive(self) -> bool:
        """True while the proxy subprocess is still running (used by readiness to
        fail fast when the child exits early, e.g. on a port-bind race)."""
        return self._proc.poll() is None

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

        # Wipe the host-only cert/key temp dir now that the proxy is down.
        if self._creds_dir is not None:
            shutil.rmtree(self._creds_dir, ignore_errors=True)
            self._creds_dir = None


def launch_proxy(
    *,
    run_dir: Path,
    readiness_timeout_sec: int = 15,
    env: dict[str, str] | None = None,
    bind_host: str = "127.0.0.1",
    max_attempts: int = 4,
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
    bind_host:
        Address the proxy binds to (``0.0.0.0`` for docker-sandbox reach).
    max_attempts:
        How many times to retry with fresh ports on a transient bind race.

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
    # (stages/<id>/stages/<id>/...) and silently empties evidence.
    log_path = run_dir / "kubectl_log.jsonl"

    # Derive the upstream URL + auth once; only the ports change per attempt.
    upstream_url = _get_upstream_url(env)
    merged_env = {**os.environ, **(env or {})}
    auth = _get_upstream_auth(env)
    auth_args: list[str] = []
    creds_dir: Path | None = None
    if auth.get("client_cert_file") and auth.get("client_key_file"):
        auth_args += ["--client-cert", auth["client_cert_file"],
                      "--client-key", auth["client_key_file"]]
    elif auth.get("client_cert_data") and auth.get("client_key_data"):
        # Embedded cert data (kind's default) must be written to a file the proxy
        # can read. It must NOT go under run_dir: that dir is bind-mounted into the
        # agent as /workspace, so the agent could read the upstream cluster-admin
        # key and talk to the API server directly, bypassing the proxy (C2). Write
        # it to a host-only temp dir instead, removed on teardown.
        creds_dir = Path(tempfile.mkdtemp(prefix="karma-proxy-creds-"))
        try:
            os.chmod(creds_dir, 0o700)
        except Exception:
            pass
        cert_path = creds_dir / "client.crt"
        key_path = creds_dir / "client.key"
        cert_path.write_text(auth["client_cert_data"])
        key_path.write_text(auth["client_key_data"])
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass
        auth_args += ["--client-cert", str(cert_path), "--client-key", str(key_path)]
    if auth.get("token"):
        auth_args += ["--token", auth["token"]]

    # find_free_port() picks a free port but the child binds it a moment later;
    # if another process grabs it in that gap the child dies with
    # ``OSError: [Errno 48] Address already in use`` (data port) or its control
    # thread dies (control port). Both are transient port races, so retry with
    # freshly-picked ports rather than failing the whole stage.
    last_err: Exception | None = None
    for _attempt in range(max(1, max_attempts)):
        proxy_port = find_free_port()
        control_port = find_free_port()
        cmd = [
            sys.executable, "-m", "karma.transport.k8s.proxy",
            "--upstream-url", upstream_url,
            "--log-path", str(log_path),
            "--port", str(proxy_port),
            "--control-port", str(control_port),
            "--bind-host", bind_host,
            "--request-timeout", str(settings.command_timeout_sec),
            *auth_args,
        ]
        proc = subprocess.Popen(
            cmd, env=merged_env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
        handle = ProxyHandle(proc, proxy_port, control_port=control_port)
        try:
            wait_for_readiness(handle, timeout_sec=readiness_timeout_sec)
        except RuntimeError as exc:
            last_err = exc
            handle.teardown()  # reap the dead/failed child before retrying
            continue          # (this handle has no creds_dir, so it won't wipe them)
        # Hand the cert/key temp dir to the surviving handle so its teardown wipes it.
        handle._creds_dir = creds_dir
        try:
            (run_dir / "proxy.pid").write_text(str(proc.pid))
        except Exception:
            pass
        return handle
    # Every attempt failed: no handle owns the creds dir, so clean it up here.
    if creds_dir is not None:
        shutil.rmtree(creds_dir, ignore_errors=True)
    raise last_err or RuntimeError("kubectl proxy failed to start")


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
    running on the host. ``sandbox._launch_docker`` injects a Linux host
    alias for that name when Docker does not provide it automatically.

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
        # If the child already exited (e.g. data-port bind race -> EADDRINUSE),
        # stop waiting immediately so the caller can retry with a fresh port
        # instead of polling a dead process for the full timeout.
        if not proxy_handle.is_alive():
            raise RuntimeError(
                f"kubectl proxy exited before becoming ready "
                f"(port {proxy_handle.port})"
            )
        time.sleep(interval)
        interval = min(interval * 1.5, 1.0)
    raise RuntimeError(
        f"kubectl proxy did not become ready within {timeout_sec}s "
        f"(port {proxy_handle.port})"
    )
