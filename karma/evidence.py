"""
Snapshot collection, usage normalization, trace facts, and metric dispatch.

Evidence is collected after the agent exits and before the oracle runs.
It captures what the agent did (kubectl calls, resource mutations, timing)
rather than whether the task was completed correctly.

This module does not import ``runtime.*``. It reads from and writes to the
run directory via ``protocol`` path helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .metrics import dispatch_metrics


def collect_kubectl_snapshot(kubectl_log_path: Path) -> list[dict[str, Any]]:
    """Parse the proxy kubectl log and return a structured call list.

    Each entry represents one intercepted kubectl call with keys
    ``timestamp``, ``verb``, ``resource``, ``namespace``, ``name``,
    ``status``, and ``duration_ms``.

    Returns an empty list when the log file is absent or empty.
    """
    ...


def normalize_token_usage(agent_log_path: Path) -> dict[str, Any]:
    """Extract token usage statistics from the agent log file.

    Scans the agent's stdout/stderr log for structured usage lines.

    Returns a dict with keys ``prompt_tokens``, ``completion_tokens``,
    ``total_tokens``, and ``turns``. All values are zero when no usage
    data is found.
    """
    ...


def compute_trace_facts(kubectl_snapshot: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive structured facts from a kubectl call snapshot.

    Returns a dict with keys ``total_calls``, ``mutation_calls``,
    ``read_calls``, ``unique_resources``, ``namespaces_touched``, and
    ``first_mutation_sec`` (``None`` when no mutations occurred).
    """
    ...


def collect_evidence(
    *,
    run_dir: Path,
    stage_id: str,
    case: dict[str, Any],
    role_bindings: dict[str, str],
    token_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the full evidence dict for one stage and write it to disk.

    Reads the kubectl log, normalizes token usage, computes trace facts,
    and dispatches to all enabled metric plugins. Writes the assembled
    dict to ``protocol.stage_evidence_path(run_dir, stage_id)``.

    This function never raises. Partial results are returned with an
    ``"error"`` key when any step fails.

    Returns
    -------
    dict
        Keys: ``kubectl_snapshot``, ``token_usage``, ``trace_facts``,
        ``metrics``.
    """
    ...
