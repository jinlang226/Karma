"""
karma/settings.py — runtime configuration loaded from environment variables.

All KARMA_ prefixed variables are read at import time via ``Settings.from_env()``.
Callers import the module-level ``settings`` singleton; tests can replace it with
a fresh ``Settings.from_env()`` call against a patched environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    """Flat bag of every tuneable runtime parameter.

    All fields have sensible defaults so a local dev environment works
    without setting any environment variable.
    """

    # Filesystem roots
    resources_dir: Path = field(default_factory=lambda: Path("cases"))
    runs_dir: Path = field(default_factory=lambda: Path("runs"))

    # HTTP server
    host: str = "127.0.0.1"
    port: int = 8080

    # Judge LLM
    judge_api_key: str | None = None
    judge_base_url: str | None = None
    judge_model: str = "gpt-4o"

    # Agent model used when launching containerised agents
    agent_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str | None = None

    # Execution limits
    command_timeout_sec: int = 120
    precondition_timeout_sec: int = 600
    # How precondition_timeout_sec is applied: "fixed" uses it as a hard cap;
    # "auto" uses max(precondition_timeout_sec, per-case computed budget) so a
    # legitimately slow precondition is not killed by a too-small literal.
    setup_timeout_mode: str = "auto"
    oracle_timeout_sec: int = 120
    # Absolute upper bound (seconds) on a single agent attempt. The per-stage
    # agent_timeout_sec is treated as an IDLE budget that resets while the agent
    # keeps producing output, so a still-working agent is never cut off by the
    # clock; this cap only stops a runaway agent that loops while still emitting.
    agent_hard_cap_sec: int = 3600

    # Logging
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        """Construct a Settings instance by reading KARMA_* environment variables.

        Each field maps to an env var with the KARMA_ prefix and the field
        name uppercased (e.g. ``runs_dir`` → ``KARMA_RUNS_DIR``).  Judge API
        key also falls back to OPENAI_API_KEY for compatibility.
        """
        def _path(key: str, default: str) -> Path:
            return Path(os.environ.get(key, default))

        def _int(key: str, default: int) -> int:
            try:
                return int(os.environ[key])
            except (KeyError, ValueError):
                return default

        def _str(key: str, default: str) -> str:
            return os.environ.get(key, default)

        def _opt(key: str, fallback: str | None = None) -> str | None:
            return os.environ.get(key) or (os.environ.get(fallback) if fallback else None)

        return cls(
            resources_dir=_path("KARMA_RESOURCES_DIR", "cases"),
            runs_dir=_path("KARMA_RUNS_DIR", "runs"),
            host=_str("KARMA_HOST", "127.0.0.1"),
            port=_int("KARMA_PORT", 8080),
            judge_api_key=_opt("KARMA_JUDGE_API_KEY", "OPENAI_API_KEY"),
            judge_base_url=_opt("KARMA_JUDGE_BASE_URL", "OPENAI_BASE_URL"),
            judge_model=_str("KARMA_JUDGE_MODEL", "gpt-4o"),
            agent_model=_str("KARMA_AGENT_MODEL", "claude-sonnet-4-6"),
            anthropic_api_key=_opt("ANTHROPIC_API_KEY"),
            command_timeout_sec=_int("KARMA_COMMAND_TIMEOUT_SEC", 120),
            precondition_timeout_sec=_int("KARMA_PRECONDITION_TIMEOUT_SEC", 600),
            setup_timeout_mode=_str("KARMA_SETUP_TIMEOUT_MODE", "auto"),
            oracle_timeout_sec=_int("KARMA_ORACLE_TIMEOUT_SEC", 120),
            agent_hard_cap_sec=_int("KARMA_AGENT_HARD_CAP_SEC", 3600),
            log_level=_str("KARMA_LOG_LEVEL", "INFO"),
        )


# Module-level singleton used by production code.
settings: Settings = Settings.from_env()
