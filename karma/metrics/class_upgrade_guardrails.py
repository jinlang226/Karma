"""class_upgrade_guardrails metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Compute the class_upgrade_guardrails score for one stage run.

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
    # Only applicable to nginx-ingress class upgrade cases.
    case_name = str(case.get("case_name") or "")
    if "class" not in case_name and "upgrade" not in case_name:
        return 1.0

    # Score is 1.0 if IngressClass resources were applied/patched without
    # also deleting existing Ingress resources (which would break traffic).
    _INGRESSCLASS_RESOURCES = frozenset({"ingressclass", "ingressclasses"})
    _MUTATION_VERBS = frozenset({"apply", "create", "patch"})
    _DELETE_VERBS = frozenset({"delete"})
    _INGRESS_RESOURCES = frozenset({"ingress", "ingresses"})

    class_mutations = [
        e for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _MUTATION_VERBS
        and str(e.get("resource") or "").lower() in _INGRESSCLASS_RESOURCES
    ]
    ingress_deletions = [
        e for e in kubectl_snapshot
        if str(e.get("verb") or "").lower() in _DELETE_VERBS
        and str(e.get("resource") or "").lower() in _INGRESS_RESOURCES
    ]

    if not class_mutations:
        return 0.5  # class not modified; uncertain
    if ingress_deletions:
        return 0.0  # deleted existing ingresses during upgrade
    return 1.0
