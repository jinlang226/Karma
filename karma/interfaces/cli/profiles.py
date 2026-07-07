"""
CLI profile loading and default merging.

A profile is a YAML file that persists common CLI settings so they do not
need to be repeated on every invocation. Profiles are looked up by name
from ``~/.karma/profiles/{name}.yaml`` or supplied as an explicit file path.

Example profile file::

    agent: react
    sandbox: docker
    runs_dir: /data/karma-runs
    resources_dir: /data/karma-resources
    judge_model: gpt-4o
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_PROFILES_DIR = Path.home() / ".karma" / "profiles"

_KNOWN_PROFILE_KEYS = {
    "agent",
    "sandbox",
    "runs_dir",
    "resources_dir",
    "judge_model",
    "environment_provider",
    "agent_timeout_sec",
    "prompt_mode",
}


def load_profile(name_or_path: str) -> dict[str, Any]:
    """Load and return a CLI profile by name or file path.

    When *name_or_path* ends with ``.yaml`` or ``.yml`` it is treated as a
    direct file path. Otherwise the profile is looked up as
    ``~/.karma/profiles/{name_or_path}.yaml``.

    Returns an empty dict when the profile file does not exist so that
    missing profiles degrade gracefully.

    Raises
    ------
    RuntimeError
        When the file exists but cannot be parsed.
    """
    if name_or_path.endswith((".yaml", ".yml")):
        path = Path(name_or_path)
    else:
        path = _PROFILES_DIR / f"{name_or_path}.yaml"

    if not path.exists():
        return {}

    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        raise RuntimeError(f"failed to parse profile {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"profile {path} must be a YAML object")

    unknown = set(data) - _KNOWN_PROFILE_KEYS
    if unknown:
        import warnings
        warnings.warn(
            f"profile {path} contains unknown keys: {', '.join(sorted(unknown))}",
            stacklevel=2,
        )

    return data


def merge_profile(
    explicit: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Return *explicit* CLI arguments merged with *profile* defaults.

    Explicit values take priority. Profile values fill in keys where the
    explicit value is ``None`` or absent.
    """
    merged = dict(profile)
    for key, value in explicit.items():
        if value is not None:
            merged[key] = value
    return merged
