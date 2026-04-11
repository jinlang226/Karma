"""
Public K8s transport API.

This is the only transport module imported by the rest of KARMA.
``runtime.case`` uses it to start and stop the kubectl proxy per stage.
``sandbox`` uses it to obtain the kubeconfig path when building the agent
container bundle.

``transport.k8s.proxy`` is never imported directly by ``runtime.*``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


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
        """
        self._proc = proc
        self._port = port
        self._run_dir = run_dir

    @property
    def port(self) -> int:
        """TCP port the proxy is listening on."""
        return self._port

    def is_ready(self) -> bool:
        """Return ``True`` when the proxy is running and accepting connections.

        Non-blocking. Checks the process status then probes the port.
        """
        ...

    def teardown(self) -> None:
        """Terminate the proxy subprocess and wait for it to exit.

        Sends SIGTERM then SIGKILL after a short grace period. Safe to call
        on an already-stopped proxy.
        """
        ...


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
    ...


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
    ...


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
    ...
