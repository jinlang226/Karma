"""
Local and Docker agent launch and container lifecycle management.

Sandbox modes:

``local``
    The agent runs as a plain subprocess on the host. No Docker required.
    Used for solver-based runs and development iteration.

``docker``
    The agent runs inside an isolated Docker container, providing a clean
    environment and reproducible results across machines.

The public interface is :func:`launch_agent`, which returns an
:class:`AgentProcess` handle regardless of sandbox mode.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class AgentProcess:
    """Handle for a running agent process, local or Docker.

    Returned by :func:`launch_agent`. Callers use this handle to wait for
    the agent, terminate it early, or inspect its exit code.
    """

    def __init__(
        self,
        proc: subprocess.Popen,
        *,
        sandbox_mode: str,
        container_id: str | None = None,
        run_dir: Path,
    ) -> None:
        """Wrap a running subprocess or Docker container process.

        Parameters
        ----------
        proc:
            The underlying :class:`subprocess.Popen` object.
        sandbox_mode:
            ``"local"`` or ``"docker"``.
        container_id:
            Docker container ID when *sandbox_mode* is ``"docker"``,
            otherwise ``None``.
        run_dir:
            Stage run directory used for logging.
        """
        self._proc = proc
        self._sandbox_mode = sandbox_mode
        self._container_id = container_id
        self._run_dir = run_dir
        self._exit_code: int | None = None

    def wait(self, timeout_sec: int | None = None) -> int:
        """Block until the agent finishes and return its exit code.

        Raises :class:`subprocess.TimeoutExpired` when *timeout_sec* is
        given and the agent does not finish in time. The caller is
        responsible for calling :meth:`terminate` after catching the error.
        """
        ...

    def terminate(self) -> None:
        """Forcibly stop the agent process.

        For Docker containers: runs ``docker kill`` then ``docker rm``.
        For local processes: sends SIGTERM, then SIGKILL after a short
        grace period. Safe to call on an already-terminated process.
        """
        ...

    def is_running(self) -> bool:
        """Return ``True`` when the agent process is still alive.

        Non-blocking. Uses ``poll()`` for local processes and
        ``docker inspect`` for Docker containers.
        """
        ...

    @property
    def exit_code(self) -> int | None:
        """Exit code of the process, or ``None`` while still running."""
        return self._exit_code


def build_agent_image(
    agent_meta: dict[str, Any],
    *,
    image_tag: str,
    run_dir: Path,
) -> str:
    """Build a Docker image from the agent's Dockerfile.

    Streams build output to ``{run_dir}/agent_build.log``.

    Parameters
    ----------
    agent_meta:
        Agent descriptor from ``agents.registry.get_agent_meta``.
    image_tag:
        Docker image tag to apply to the built image.
    run_dir:
        Stage run directory used for log output.

    Returns
    -------
    str
        The image tag on success.

    Raises
    ------
    RuntimeError
        When the Docker build exits with a non-zero status.
    """
    ...


def launch_agent(
    agent_meta: dict[str, Any],
    *,
    sandbox_mode: str,
    env_vars: dict[str, str],
    run_dir: Path,
    agent_timeout_sec: int,
    kubeconfig_path: Path | None = None,
    extra_mounts: list[tuple[Path, str]] | None = None,
) -> AgentProcess:
    """Launch the agent process and return a handle to it.

    In ``"local"`` mode the agent entrypoint is spawned as a subprocess
    with *env_vars* injected and *run_dir* as the working directory.

    In ``"docker"`` mode the image is built or pulled as needed, then the
    container is started with *env_vars* forwarded, *run_dir* mounted as
    ``/workspace``, and *kubeconfig_path* mounted when provided.
    *extra_mounts* supplies additional ``(host_path, container_path)`` bind
    mounts.

    Returns without waiting for the agent to complete. The caller is
    responsible for calling :meth:`AgentProcess.wait` or
    :meth:`AgentProcess.terminate`.

    Raises
    ------
    RuntimeError
        When the process or container cannot be started.
    """
    ...


def cleanup_agent(process: AgentProcess) -> None:
    """Ensure the agent process is terminated and container resources removed.

    Safe to call on an already-exited process. Errors during cleanup are
    logged but not re-raised.
    """
    ...
