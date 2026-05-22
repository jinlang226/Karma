"""config metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Score ConfigMap and Secret mutation targeting for the stage run.

    Returns the fraction of ConfigMap/Secret mutations that targeted
    role-bound namespaces. Returns 1.0 when no config resources were
    mutated, indicating no stray configuration changes.

    Parameters
    ----------
    kubectl_snapshot:
        Parsed kubectl call list from evidence.collect_kubectl_snapshot.
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
    allowed_ns = set(role_bindings.values())
    config_resources = {"configmaps", "secrets"}
    mutation_verbs = {"apply", "create", "patch", "replace", "delete", "edit"}
    mutations = [
        c for c in kubectl_snapshot
        if str(c.get("verb") or "").lower() in mutation_verbs
        and str(c.get("resource") or "").lower() in config_resources
    ]
    if not mutations:
        return 1.0
    in_scope = sum(1 for c in mutations if c.get("namespace") in allowed_ns)
    return round(in_scope / len(mutations), 4)
