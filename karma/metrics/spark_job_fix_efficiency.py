"""spark_job_fix_efficiency metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Compute the spark_job_fix_efficiency score for one stage run.

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
    # Measures fix efficiency as the ratio of SparkApplication patch/apply
    # mutations to total mutations; low-overhead fixes score higher.
    _SPARK_RESOURCES = frozenset({"sparkapplication", "sparkapplications",
                                  "scheduledsparkapplication", "scheduledsparkapplications"})
    _MUTATION_VERBS = frozenset({"apply", "create", "patch", "replace", "delete"})

    total_mutations = sum(
        1 for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _MUTATION_VERBS
    )
    spark_mutations = sum(
        1 for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _MUTATION_VERBS
        and str(e.get("resource") or "").lower() in _SPARK_RESOURCES
    )
    if total_mutations == 0:
        return 0.5
    # Reward targeted Spark resource changes; penalise broad or repeated mutations.
    efficiency = spark_mutations / total_mutations
    return round(min(1.0, efficiency * 2.0), 4)
