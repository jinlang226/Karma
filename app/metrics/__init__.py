from .blast_radius import compute as _blast_radius
from .class_upgrade_guardrails import compute as _class_upgrade_guardrails
from .decoy_integrity import compute as _decoy_integrity
from .destructive_ops import compute as _destructive_ops
from .host_specificity_guardrail import compute as _host_specificity_guardrail
from .key_material_leakage import compute as _key_material_leakage
from .otel_scope_control import compute as _otel_scope_control
from .rate_limit_strategy import compute as _rate_limit_strategy
from .read_write_ratio import compute as _read_write_ratio
from .residual_drift import compute as _residual_drift
from .spark_job_fix_efficiency import compute as _spark_job_fix_efficiency
from .spark_resource_tuning import compute as _spark_resource_tuning
from .spark_scaling_efficiency import compute as _spark_scaling_efficiency
from .time_to_first_mutation import compute as _time_to_first_mutation

METRIC_TOOLS = {
    "blast_radius": _blast_radius,
    "class_upgrade_guardrails": _class_upgrade_guardrails,
    "decoy_integrity": _decoy_integrity,
    "destructive_ops": _destructive_ops,
    "host_specificity_guardrail": _host_specificity_guardrail,
    "key_material_leakage": _key_material_leakage,
    "otel_scope_control": _otel_scope_control,
    "rate_limit_strategy": _rate_limit_strategy,
    "read_write_ratio": _read_write_ratio,
    "residual_drift": _residual_drift,
    "spark_job_fix_efficiency": _spark_job_fix_efficiency,
    "spark_resource_tuning": _spark_resource_tuning,
    "spark_scaling_efficiency": _spark_scaling_efficiency,
    "time_to_first_mutation": _time_to_first_mutation,
}


def compute_metrics(selected, meta, run_dir, trace_path=None):
    results = {}
    if not selected:
        return results
    for name in selected:
        tool = METRIC_TOOLS.get(name)
        if not tool:
            results[name] = {"error": "unknown metric"}
            continue
        results[name] = tool(meta, run_dir, trace_path=trace_path)
    return results
