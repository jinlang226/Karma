"""spark_resource_tuning metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Compute the spark_resource_tuning score for one stage run.

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
    # Checks that the agent modified SparkApplication specs (resource tuning)
    # rather than just restarting jobs repeatedly.
    _SPARK_RESOURCES = frozenset({"sparkapplication", "sparkapplications"})
    _RESTART_VERBS = frozenset({"delete", "create"})
    _TUNE_VERBS = frozenset({"apply", "patch", "replace"})

    restarts = sum(
        1 for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _RESTART_VERBS
        and str(e.get("resource") or "").lower() in _SPARK_RESOURCES
    )
    tunes = sum(
        1 for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _TUNE_VERBS
        and str(e.get("resource") or "").lower() in _SPARK_RESOURCES
    )
    total = restarts + tunes
    if total == 0:
        return 0.5
    return round(tunes / total, 4)
