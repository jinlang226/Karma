from __future__ import annotations


def _normalize_single_case_status(payload):
    status = str((payload or {}).get("status") or "").strip()
    if status == "failed":
        base = str((payload or {}).get("terminal_base_status") or "").strip()
        if base in ("failed", "auto_failed", "setup_failed"):
            return base
        return "failed"
    if status != "workflow_fatal":
        return status

    reason = str((payload or {}).get("terminal_reason") or "").strip()
    if reason == "submit_timeout":
        return "submit_timeout"
    if reason == "verify_timeout":
        return "verify_timeout"
    if reason == "agent_exited":
        return "agent_failed"
    if reason == "submit_error":
        return "auto_failed"
    return status


def run_case_once(
    app,
    case_id,
    args,
    *,
    run_single_case_workflow_fn,
    attach_agent_usage_fields,
):
    workflow_outcome = run_single_case_workflow_fn(app, case_id, args)
    if not isinstance(workflow_outcome, dict):
        raise RuntimeError("single-case workflow execution returned invalid payload")

    outcome = dict(workflow_outcome)
    outcome["status"] = _normalize_single_case_status(workflow_outcome)
    for key in (
        "compiled",
        "compiled_status",
        "compiled_fingerprint",
        "compiled_cached_fingerprint",
        "compiled_artifact_path",
        "compiled_warning",
    ):
        outcome.pop(key, None)

    status = app.run_status()
    if not outcome.get("metrics_path") and status.get("metrics_path"):
        outcome["metrics_path"] = status.get("metrics_path")
    if not outcome.get("cleanup_status") and status.get("cleanup_status"):
        outcome["cleanup_status"] = status.get("cleanup_status")
    if not outcome.get("cleanup_log") and status.get("cleanup_log"):
        outcome["cleanup_log"] = status.get("cleanup_log")

    return attach_agent_usage_fields(outcome)


def run_case(
    app,
    case_id,
    args,
    *,
    load_case_yaml_fn,
    run_case_once_fn,
):
    _ = load_case_yaml_fn
    return run_case_once_fn(app, case_id, args)
