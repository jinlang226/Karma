"""destructive_ops metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Compute the destructive_ops score for one stage run.

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
    _DESTRUCTIVE_VERBS = frozenset({"delete", "replace"})
    _MUTATION_VERBS = frozenset({"apply", "create", "patch", "replace", "delete", "edit", "scale"})

    total_mutations = sum(
        1 for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _MUTATION_VERBS
    )
    destructive = sum(
        1 for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _DESTRUCTIVE_VERBS
    )
    if total_mutations == 0:
        return 1.0
    ratio = destructive / total_mutations
    # Lower destructive ratio is better; invert to score
    return round(max(0.0, 1.0 - ratio), 4)
