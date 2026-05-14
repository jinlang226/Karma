"""spark_scaling_efficiency metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Compute the spark_scaling_efficiency score for one stage run.

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
    # Measures scaling efficiency as the inverse of the number of scale
    # operations needed: fewer changes to reach the target = higher score.
    _SCALE_VERBS = frozenset({"scale", "patch", "apply"})
    _WORKER_RESOURCES = frozenset({
        "rayclusters", "raycluster",
        "sparkapplication", "sparkapplications",
        "statefulset", "statefulsets",
        "deployment", "deployments",
    })

    scale_ops = sum(
        1 for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _SCALE_VERBS
        and str(e.get("resource") or "").lower() in _WORKER_RESOURCES
    )
    if scale_ops == 0:
        return 0.5
    # Ideal is 1–2 operations; penalise iterative thrashing
    return round(max(0.0, 1.0 - (scale_ops - 1) * 0.1), 4)
