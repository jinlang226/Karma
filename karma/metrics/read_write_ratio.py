"""read_write_ratio metric plugin."""

from __future__ import annotations
from typing import Any


def compute(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
) -> float | dict[str, Any]:
    """Compute the read_write_ratio score for one stage run.

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
    _READ_VERBS = frozenset({"get", "list", "describe", "watch", "explain"})
    _WRITE_VERBS = frozenset({"apply", "create", "patch", "replace", "delete", "edit", "scale"})

    reads = sum(1 for e in kubectl_snapshot if str(e.get("verb") or "").lower() in _READ_VERBS)
    writes = sum(1 for e in kubectl_snapshot if str(e.get("verb") or "").lower() in _WRITE_VERBS)
    total = reads + writes
    if total == 0:
        return 1.0
    # Ideal is reads >= writes (ratio >= 0.5 of reads in total)
    ratio = reads / total
    return round(min(1.0, ratio * 2.0), 4)
