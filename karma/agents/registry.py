"""
Agent registry and launch metadata resolution.

Contributors add a new agent by creating a subfolder under ``karma/agents/``
with a ``Dockerfile``, ``entrypoint.sh``, and ``README.md``, then
registering the folder name in ``_REGISTRY`` below.

The only shared agent code is this registry file. There is no base class,
shared manifest type, or shared helper layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_AGENTS_DIR = Path(__file__).parent

_REGISTRY: dict[str, dict[str, Any]] = {
    "react": {
        "folder": _AGENTS_DIR / "react",
        "dockerfile": _AGENTS_DIR / "react" / "Dockerfile",
        "entrypoint": "entrypoint.sh",
        "description": "ReAct-style agent that alternates reasoning and action steps.",
    },
    "cli_runner": {
        "folder": _AGENTS_DIR / "cli_runner",
        "dockerfile": _AGENTS_DIR / "cli_runner" / "Dockerfile",
        "entrypoint": "entrypoint.sh",
        "description": "CLI-driven agent for scripted or solver-based runs.",
    },
}


def get_agent_meta(name: str) -> dict[str, Any]:
    """Return the launch metadata dict for the named agent.

    Raises
    ------
    ValueError
        When *name* is not registered.

    Returns
    -------
    dict
        Keys: ``folder`` (Path), ``dockerfile`` (Path), ``entrypoint``
        (str), ``description`` (str).
    """
    meta = _REGISTRY.get(name)
    if meta is None:
        raise ValueError(
            f"unknown agent: {name!r}. "
            f"Available: {', '.join(sorted(_REGISTRY))}"
        )
    return dict(meta)


def get_agent_folder(name: str) -> Path:
    """Return the absolute path to the named agent's directory.

    Raises
    ------
    ValueError
        When *name* is not registered.
    """
    return get_agent_meta(name)["folder"]


def list_agents() -> list[str]:
    """Return the sorted list of registered agent names."""
    return sorted(_REGISTRY)


def resolve_agent(
    name: str | None,
    *,
    sandbox_mode: str,
) -> dict[str, Any]:
    """Return fully resolved launch metadata for the given agent and sandbox mode.

    When *name* is ``None`` and *sandbox_mode* is ``"local"``, returns a
    minimal descriptor indicating that no Docker image is required. When
    *sandbox_mode* is ``"docker"``, *name* must be provided.

    Returns
    -------
    dict
        Superset of :func:`get_agent_meta` with additional keys
        ``sandbox_mode`` (str) and ``image_tag`` (str or ``None``).
    """
    if sandbox_mode == "local" and name is None:
        return {
            "folder": None,
            "dockerfile": None,
            "entrypoint": None,
            "description": "local process, no Docker image",
            "sandbox_mode": "local",
            "image_tag": None,
        }

    if sandbox_mode == "docker" and name is None:
        raise ValueError("agent name is required when sandbox_mode is 'docker'")

    meta = get_agent_meta(name)  # type: ignore[arg-type]
    image_tag = f"karma-agent-{name}:latest" if sandbox_mode == "docker" else None
    return {**meta, "sandbox_mode": sandbox_mode, "image_tag": image_tag}
