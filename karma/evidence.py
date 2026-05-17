"""
Snapshot collection, usage normalization, trace facts, and metric dispatch.

Evidence is collected after the agent exits and before the oracle runs.
It captures what the agent did (kubectl calls, resource mutations, timing)
rather than whether the task was completed correctly.

This module does not import ``runtime.*``. It reads from and writes to the
run directory via ``protocol`` path helpers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .metrics import dispatch_metrics


_MUTATION_VERBS = frozenset(
    {"apply", "create", "patch", "replace", "delete", "edit", "scale", "rollout"}
)


def collect_kubectl_snapshot(kubectl_log_path: Path) -> list[dict[str, Any]]:
    """Parse the proxy kubectl log and return a structured call list.

    Each entry represents one intercepted kubectl call with keys
    ``timestamp``, ``verb``, ``resource``, ``namespace``, ``name``,
    ``status``, and ``duration_ms``.

    Returns an empty list when the log file is absent or empty.
    """
    if not kubectl_log_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    try:
        raw_text = kubectl_log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        entries.append({
            "timestamp": record.get("timestamp") or record.get("ts"),
            "verb": str(record.get("verb") or "").lower(),
            "resource": str(record.get("resource") or ""),
            "namespace": str(record.get("namespace") or ""),
            "name": str(record.get("name") or ""),
            "status": record.get("status") or record.get("statusCode"),
            "duration_ms": record.get("duration_ms") or record.get("durationMs"),
        })
    return entries


def normalize_token_usage(agent_log_path: Path) -> dict[str, Any]:
    """Extract token usage statistics from the agent log file.

    Scans the agent's stdout/stderr log for structured usage lines.

    Returns a dict with keys ``prompt_tokens``, ``completion_tokens``,
    ``total_tokens``, and ``turns``. All values are zero when no usage
    data is found.
    """
    result = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "turns": 0}
    if not agent_log_path.exists():
        return result

    for line in agent_log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        usage = record.get("usage") or record.get("token_usage") or {}
        if isinstance(usage, dict):
            result["prompt_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            result["completion_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            result["total_tokens"] += int(usage.get("total_tokens") or 0)
            result["turns"] += 1

    if result["total_tokens"] == 0 and result["prompt_tokens"] > 0:
        result["total_tokens"] = result["prompt_tokens"] + result["completion_tokens"]

    return result


def compute_trace_facts(kubectl_snapshot: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive structured facts from a kubectl call snapshot.

    Returns a dict with keys ``total_calls``, ``mutation_calls``,
    ``read_calls``, ``unique_resources``, ``namespaces_touched``, and
    ``first_mutation_sec`` (``None`` when no mutations occurred).
    """
    total_calls = len(kubectl_snapshot)
    mutation_calls = 0
    read_calls = 0
    unique_resources: set[str] = set()
    namespaces_touched: set[str] = set()
    first_mutation_sec: float | None = None

    for entry in kubectl_snapshot:
        verb = str(entry.get("verb") or "").lower()
        resource = str(entry.get("resource") or "")
        namespace = str(entry.get("namespace") or "")

        if resource:
            unique_resources.add(resource)
        if namespace:
            namespaces_touched.add(namespace)

        if verb in _MUTATION_VERBS:
            mutation_calls += 1
            ts = entry.get("timestamp")
            if ts is not None and first_mutation_sec is None:
                try:
                    first_mutation_sec = float(ts)
                except (TypeError, ValueError):
                    pass
        else:
            read_calls += 1

    return {
        "total_calls": total_calls,
        "mutation_calls": mutation_calls,
        "read_calls": read_calls,
        "unique_resources": sorted(unique_resources),
        "namespaces_touched": sorted(namespaces_touched),
        "first_mutation_sec": first_mutation_sec,
    }


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
    from . import protocol

    evidence: dict[str, Any] = {
        "kubectl_snapshot": [],
        "token_usage": {},
        "trace_facts": {},
        "metrics": {},
        "error": None,
    }
    try:
        kubectl_log = protocol.stage_kubectl_log_path(run_dir, stage_id)
        evidence["kubectl_snapshot"] = collect_kubectl_snapshot(kubectl_log)

        if token_usage is not None:
            evidence["token_usage"] = token_usage
        else:
            agent_log = protocol.stage_agent_log_path(run_dir, stage_id)
            evidence["token_usage"] = normalize_token_usage(agent_log)

        evidence["trace_facts"] = compute_trace_facts(evidence["kubectl_snapshot"])

        enabled_metrics = case.get("metrics") or None
        evidence["metrics"] = dispatch_metrics(
            evidence["kubectl_snapshot"],
            case,
            role_bindings,
            enabled=enabled_metrics,
        )
    except Exception as exc:
        evidence["error"] = str(exc)

    protocol.ensure_stage_dir(run_dir, stage_id)
    evidence_path = protocol.stage_evidence_path(run_dir, stage_id)
    evidence_path.write_text(json.dumps(evidence, indent=2))
    return evidence
