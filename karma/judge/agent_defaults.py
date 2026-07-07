"""
Derive judge-LLM defaults from the agent that executed a run.

When a judge invocation does not name a model, KARMA mirrors the agent that
actually ran the tasks (recorded as ``agent`` in ``{run_dir}/config.json``)
rather than falling back to a fixed ``gpt-4o``. Each agent resolves its model
from its own environment variable -- the same variables the agent's
``entrypoint.sh`` reads at launch -- so the judge reproduces that choice here,
including the backend/base_url/api_key needed to actually reach that model.

Pure disk + env reads; this module must not import ``runtime.*``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _read_agent(run_dir: Path) -> str | None:
    """Return the executing agent name recorded in ``config.json``, or None."""
    try:
        cfg = json.loads((run_dir / "config.json").read_text())
    except Exception:
        return None
    agent = cfg.get("agent")
    return agent.strip() if isinstance(agent, str) and agent.strip() else None


def resolve_agent_judge_defaults(run_dir: Path) -> dict[str, Any]:
    """Return judge-LLM overrides mirroring the agent that ran *run_dir*.

    Reads the executing agent from ``config.json`` and reproduces the model
    (and, where needed, backend/base_url/api_key) that agent used, following
    each agent's own environment-variable conventions. Returns only the keys
    it can determine confidently; an empty dict means "no opinion -- fall back
    to the existing ``KARMA_JUDGE_*`` / ``gpt-4o`` defaults".
    """
    agent = _read_agent(run_dir)
    if not agent:
        return {}

    if agent == "claude_code":
        # Runs ``claude --model <m>``; the judge mirrors it via the same claude
        # CLI backend (ambient Claude auth, no API key) -- see judge.client.
        return {
            "backend": "claude_cli",
            "model": os.environ.get("KARMA_CLAUDE_AGENT_MODEL") or "sonnet",
        }
    if agent == "api":
        # Self-contained OpenAI-compatible loop (DeepSeek by default); mirror
        # its endpoint exactly so the judge hits the same model.
        out: dict[str, Any] = {
            "backend": "openai",
            "model": os.environ.get("KARMA_API_MODEL") or "deepseek-v4-flash",
            "base_url": os.environ.get("KARMA_API_BASE_URL") or "https://api.deepseek.com",
        }
        api_key = (
            os.environ.get("KARMA_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if api_key:
            out["api_key"] = api_key
        return out
    if agent == "codex":
        # Codex CLI (OpenAI-backed); only the model name is knowable from env.
        model = os.environ.get("CODEX_MODEL")
        return {"model": model} if model else {}
    if agent == "copilot":
        # GitHub Copilot CLI; only the model name is knowable from env.
        model = os.environ.get("KARMA_COPILOT_AGENT_MODEL")
        return {"model": model} if model else {}
    return {}
