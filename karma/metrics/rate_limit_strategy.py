"""rate_limit_strategy metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Compute the rate_limit_strategy score for one stage run.

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
    # Only applicable to nginx-ingress rate-limit cases.
    case_name = str(case.get("case_name") or "")
    if "rate_limit" not in case_name:
        return 1.0

    # Score is 1.0 if rate-limit annotations were applied via patch/apply,
    # 0.0 if no relevant mutation was found.
    _RATE_LIMIT_RESOURCES = frozenset({"ingresses", "ingress", "configmaps", "configmap"})
    _MUTATION_VERBS = frozenset({"apply", "create", "patch", "replace"})

    relevant = [
        e for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _MUTATION_VERBS
        and str(e.get("resource") or "").lower() in _RATE_LIMIT_RESOURCES
    ]
    return 1.0 if relevant else 0.0
