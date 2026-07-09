"""
Metric plugin registry and dispatch.

Each plugin is a Python module under ``karma.metrics`` that exposes a
``compute(kubectl_snapshot, case, role_bindings)`` function returning a
float score in ``[0.0, 1.0]``.

Plugins must not import ``runtime.*`` or ``judge.*``. The judge pipeline
consumes metric scores from evidence artifacts; it does not compute them.
"""

from __future__ import annotations

from typing import Any

_PLUGIN_MODULES: list[str] = [
    "karma.metrics.blast_radius",
    "karma.metrics.decoy_integrity",
    "karma.metrics.destructive_ops",
    "karma.metrics.read_write_ratio",
    "karma.metrics.residual_drift",
    "karma.metrics.time_to_first_mutation",
    "karma.metrics.key_material_leakage",
    "karma.metrics.rate_limit_strategy",
    "karma.metrics.class_upgrade_guardrails",
    "karma.metrics.otel_scope_control",
    "karma.metrics.spark_job_fix_efficiency",
    "karma.metrics.spark_resource_tuning",
    "karma.metrics.spark_scaling_efficiency",
    "karma.metrics.config",
]


def dispatch_metrics(
    kubectl_snapshot: list[dict[str, Any]],
    case: dict[str, Any],
    role_bindings: dict[str, str],
    *,
    enabled: list[str] | None = None,
) -> dict[str, Any]:
    """Run metric plugins and return their scores.

    When *enabled* is ``None``, all registered plugins run. When *enabled*
    is a list of metric names, only those plugins run; unknown names are
    silently skipped.

    This function never raises. Per-plugin errors are captured as
    ``{"error": "<message>"}`` values in the returned dict.

    Returns
    -------
    dict
        Map of metric name to float score or error descriptor.
    """
    import importlib

    results: dict[str, Any] = {}
    for module_path in _PLUGIN_MODULES:
        plugin_name = module_path.rsplit(".", 1)[-1]
        if enabled is not None and plugin_name not in enabled:
            continue
        try:
            mod = importlib.import_module(module_path)
            score = mod.compute(kubectl_snapshot, case, role_bindings)
            results[plugin_name] = score
        except Exception as exc:
            results[plugin_name] = {"error": str(exc)}
    return results


def list_metrics() -> list[str]:
    """Return the sorted list of registered metric plugin names."""
    return sorted(path.rsplit(".", 1)[-1] for path in _PLUGIN_MODULES)
