"""time_to_first_mutation metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Compute the time_to_first_mutation score for one stage run.

    Parameters
    ----------
    kubectl_snapshot:
        Parsed kubectl call list produced by evidence.collect_kubectl_snapshot.
    case:
        Normalized case descriptor from definitions.cases.normalize_case.
    role_bindings:
        Map of namespace role name to physical namespace name.

    Returns
    -------
    float
        Score in [0.0, 1.0].
    dict
        {"error": "<message>"} when scoring cannot be completed.
    """
    _MUTATION_VERBS = frozenset({"apply", "create", "patch", "replace", "delete", "edit", "scale"})
    first_ts: float | None = None
    start_ts: float | None = None
    for entry in kubectl_snapshot:
        ts = entry.get("timestamp")
        if ts is None:
            continue
        try:
            t = float(ts)
        except (TypeError, ValueError):
            continue
        if start_ts is None:
            start_ts = t
        if str(entry.get("verb") or "").lower() in _MUTATION_VERBS and first_ts is None:
            first_ts = t

    if first_ts is None or start_ts is None:
        return 0.5

    elapsed = max(0.0, first_ts - start_ts)
    return round(max(0.0, 1.0 - elapsed / 300.0), 4)
