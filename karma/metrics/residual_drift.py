"""residual_drift metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Compute the residual_drift score for one stage run.

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
    # Residual drift is measured as the ratio of failed/error status mutations
    # to total mutations. A 5xx or error response on a mutation call suggests
    # the agent left things in an inconsistent state.
    _MUTATION_VERBS = frozenset({"apply", "create", "patch", "replace", "delete", "edit", "scale"})
    mutations = [
        e for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _MUTATION_VERBS
    ]
    if not mutations:
        return 1.0

    failed = sum(
        1 for e in mutations
        if isinstance(e.get("status"), int) and e["status"] >= 500
    )
    drift_ratio = failed / len(mutations)
    return round(max(0.0, 1.0 - drift_ratio), 4)
