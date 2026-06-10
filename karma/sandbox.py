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

import os
import signal
import subprocess
import time
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
        try:
            self._exit_code = self._proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            raise
        return self._exit_code

    def terminate(self) -> None:
        """Forcibly stop the agent process.

        For Docker containers: runs ``docker kill`` then ``docker rm``.
        For local processes: sends SIGTERM, then SIGKILL after a short
        grace period. Safe to call on an already-terminated process.
        """
        if self._sandbox_mode == "docker" and self._container_id:
            try:
                subprocess.run(
                    ["docker", "kill", self._container_id],
                    capture_output=True, timeout=15,
                )
                subprocess.run(
                    ["docker", "rm", "-f", self._container_id],
                    capture_output=True, timeout=15,
                )
            except Exception:
                pass
        else:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5)
            except Exception:
                pass
        self._exit_code = self._proc.returncode

    def is_running(self) -> bool:
        """Return ``True`` when the agent process is still alive.

        Non-blocking. Uses ``poll()`` for local processes and
        ``docker inspect`` for Docker containers.
        """
        if self._sandbox_mode == "docker" and self._container_id:
            try:
                result = subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", self._container_id],
                    capture_output=True, text=True, timeout=5,
                )
                return result.stdout.strip() == "true"
            except Exception:
                return False
        return self._proc.poll() is None

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
    dockerfile = Path(agent_meta["dockerfile"])
    context_dir = Path(agent_meta["folder"])
    log_path = run_dir / "agent_build.log"
    run_dir.mkdir(parents=True, exist_ok=True)

    with log_path.open("w") as log_fh:
        try:
            result = subprocess.run(
                ["docker", "build", "-t", image_tag, "-f", str(dockerfile), str(context_dir)],
                stdout=log_fh, stderr=log_fh,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "docker binary not found on PATH; install Docker to use sandbox_mode=docker"
            )
    if result.returncode != 0:
        raise RuntimeError(
            f"Docker build failed for image {image_tag!r}; see {log_path}"
        )
    return image_tag


def launch_agent(
    agent_meta: dict[str, Any],
    *,
    sandbox_mode: str,
    env_vars: dict[str, str],
    run_dir: Path,
    agent_timeout_sec: int,
    kubeconfig_path: Path | None = None,
    extra_mounts: list[tuple[Path, str]] | None = None,
    command_override: str | None = None,
) -> AgentProcess:
    """Launch the agent process and return a handle to it.

    In ``"local"`` mode the agent entrypoint is spawned as a subprocess
    with *env_vars* injected and *run_dir* as the working directory.

    In ``"docker"`` mode the image is built or pulled as needed, then the
    container is started with *env_vars* forwarded, *run_dir* mounted as
    ``/workspace``, and *kubeconfig_path* mounted when provided.
    *extra_mounts* supplies additional ``(host_path, container_path)`` bind
    mounts.

    *command_override* (the old ``--agent-cmd``) supplies a per-run launch
    command: in local mode it is run via the shell in place of the registered
    entrypoint; in docker mode it is appended to ``docker run ... <image>`` as
    the container command, overriding the image's default.

    Returns without waiting for the agent to complete. The caller is
    responsible for calling :meth:`AgentProcess.wait` or
    :meth:`AgentProcess.terminate`.

    Raises
    ------
    RuntimeError
        When the process or container cannot be started.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    merged_env = {**os.environ, **env_vars}

    if sandbox_mode == "local":
        log_path = run_dir / "agent.log"
        if command_override:
            # Per-run launch command (old --agent-cmd): run the given command
            # line through the shell, replacing the registered entrypoint.
            with log_path.open("w") as log_fh:
                proc = subprocess.Popen(
                    command_override, shell=True, env=merged_env,
                    cwd=str(run_dir), stdout=log_fh, stderr=log_fh,
                )
            return AgentProcess(proc, sandbox_mode="local", run_dir=run_dir)
        entrypoint = agent_meta.get("entrypoint") or "entrypoint.sh"
        folder = agent_meta.get("folder")
        cmd = [str(Path(folder) / entrypoint)] if folder else [entrypoint]
        with log_path.open("w") as log_fh:
            proc = subprocess.Popen(
                cmd, env=merged_env, cwd=str(run_dir),
                stdout=log_fh, stderr=log_fh,
            )
        return AgentProcess(proc, sandbox_mode="local", run_dir=run_dir)

    # docker mode
    image_tag = agent_meta.get("image_tag") or f"karma-agent:latest"
    docker_cmd = ["docker", "run", "-d", "--rm"]
    for k, v in env_vars.items():
        docker_cmd += ["-e", f"{k}={v}"]
    # Forward agent auth from the host so the in-container CLI can authenticate
    # (Claude: CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY; Codex/OpenAI: keys).
    # File-based creds (e.g. ~/.codex/auth.json) are mounted via extra_mounts /
    # --agent-auth-path instead.
    for _k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
               "CODEX_MODEL", "KARMA_CLAUDE_AGENT_MODEL"):
        _v = os.environ.get(_k)
        if _v:
            docker_cmd += ["-e", f"{_k}={_v}"]
    docker_cmd += ["-v", f"{run_dir}:/workspace"]
    if kubeconfig_path:
        docker_cmd += ["-v", f"{kubeconfig_path}:/root/.kube/config:ro"]
    for host_path, container_path in (extra_mounts or []):
        docker_cmd += ["-v", f"{host_path}:{container_path}"]
    docker_cmd.append(image_tag)
    if command_override:
        # Override the image's default command (old --agent-cmd, docker mode).
        import shlex
        docker_cmd += shlex.split(command_override)

    # Fail with a clear message if the image is not built locally, rather than
    # letting `docker run` emit a cryptic registry-pull error.
    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image_tag], capture_output=True, text=True
        )
    except FileNotFoundError:
        raise RuntimeError(
            "docker binary not found on PATH; install Docker to use sandbox_mode=docker"
        )
    if inspect.returncode != 0:
        df, folder = agent_meta.get("dockerfile"), agent_meta.get("folder")
        hint = (f"docker build -t {image_tag} -f {df} {folder}"
                if df and folder else "build the agent image")
        raise RuntimeError(
            f"Docker image '{image_tag}' not found locally. Build it first "
            f"(run with --agent-build, or: {hint}), or use --sandbox local."
        )

    try:
        result = subprocess.run(docker_cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(
            "docker binary not found on PATH; install Docker to use sandbox_mode=docker"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to start Docker container: {result.stderr.strip()}"
        )
    container_id = result.stdout.strip()
    # Attach log streaming in background by running docker logs -f as a subprocess.
    log_path = run_dir / "agent.log"
    log_fh = log_path.open("w")
    logs_proc = subprocess.Popen(
        ["docker", "logs", "-f", container_id],
        stdout=log_fh, stderr=log_fh,
    )
    # We return the logs proc as the handle's proc so .wait() tracks container termination.
    return AgentProcess(logs_proc, sandbox_mode="docker", container_id=container_id, run_dir=run_dir)


def cleanup_agent(process: AgentProcess) -> None:
    """Ensure the agent process is terminated and container resources removed.

    Safe to call on an already-exited process. Errors during cleanup are
    logged but not re-raised.
    """
    try:
        process.terminate()
    except Exception:
        pass
