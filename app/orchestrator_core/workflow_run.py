from __future__ import annotations

import json
import shlex
from pathlib import Path

from app.orchestrator_core.workflow_engine import run_workflow


def _positive_int(value):
    try:
        num = int(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return num


def resolve_stage_max_attempts(stage_max_attempts, cli_max_attempts):
    stage_value = _positive_int(stage_max_attempts)
    cli_value = _positive_int(cli_max_attempts)
    if cli_value is None:
        return stage_value
    if stage_value is None:
        return cli_value
    return min(stage_value, cli_value)


def stage_setup_timeout(status, args):
    setup_timeout = args.setup_timeout
    if getattr(args, "setup_timeout_mode", "fixed") != "auto":
        return setup_timeout
    auto_timeout = status.get("setup_timeout_auto_sec")
    try:
        auto_timeout = int(auto_timeout) if auto_timeout else None
    except Exception:
        auto_timeout = None
    if auto_timeout:
        setup_timeout = max(setup_timeout, auto_timeout)
        print(
            f"[orchestrator] setup timeout auto={auto_timeout}s effective={setup_timeout}s",
            flush=True,
        )
    return setup_timeout


def workflow_status_from_stage(final_status):
    state = str(final_status.get("status") or "")
    kind = str(final_status.get("last_verification_kind") or "")
    if kind == "oracle_timeout":
        return "fatal_error", "oracle_timeout"
    if kind == "oracle_harness_error":
        return "fatal_error", "oracle_harness_error"
    if state == "passed":
        return "passed", "stage_passed"
    if state in ("failed", "auto_failed", "setup_failed"):
        return "failed", kind or "stage_failed"
    return "fatal_error", "unknown_stage_state"


def run_workflow_stage(
    app,
    row,
    args,
    *,
    skip_unit_ids=None,
    defer_cleanup=True,
    stage_run_dir=None,
    wait_for_status_fn,
    stage_setup_timeout_fn=stage_setup_timeout,
):
    stage = row.get("stage") or {}
    max_attempts_override = resolve_stage_max_attempts(
        stage.get("max_attempts"),
        getattr(args, "max_attempts", None),
    )
    if stage_run_dir:
        setattr(app, "_next_run_dir_override", str(stage_run_dir))
    start = app.start_run(
        stage.get("case_id"),
        max_attempts_override=max_attempts_override,
        defer_cleanup=defer_cleanup,
        skip_precondition_unit_ids=skip_unit_ids or [],
        case_data_override=row.get("case_data"),
        resolved_params=row.get("resolved_params"),
        namespace_context=row.get("namespace_context"),
        namespace_lifecycle_owner="orchestrator",
    )
    if start.get("error"):
        raise RuntimeError(start.get("error"))
    status = app.run_status()
    timeout = stage_setup_timeout_fn(status, args)
    status = wait_for_status_fn(app, {"ready", "setup_failed"}, timeout=timeout)
    return status


def workflow_submit_payload(
    *,
    base_status,
    attempt,
    last_error,
    verification_log,
    attempts_left,
    time_left_sec,
    can_retry,
    mode,
    stage_index,
    stage_total,
    stage_id,
    stage_attempt,
    stage_status,
    continue_flag,
    final_flag,
    next_stage_id,
    reason,
):
    payload = {
        "status": base_status,
        "attempt": attempt,
        "last_error": last_error,
        "verification_log": verification_log,
        "attempts_left": attempts_left,
        "time_left_sec": time_left_sec,
        "can_retry": bool(can_retry),
        "workflow": {
            "enabled": True,
            "mode": mode,
            "stage_index": int(stage_index),
            "stage_total": int(stage_total),
            "stage_id": stage_id,
            "stage_attempt": int(stage_attempt),
            "stage_status": stage_status,
            "continue": bool(continue_flag),
            "final": bool(final_flag),
            "next_stage_id": next_stage_id,
            "reason": reason,
        },
    }
    return payload


def workflow_compose_prompt(
    workflow,
    rows,
    mode,
    active_index,
    stage_results,
    submit_hint,
    *,
    render_workflow_prompt_fn,
):
    case_blocks = [row.get("prompt_block") or "" for row in rows]
    return render_workflow_prompt_fn(
        workflow=workflow,
        mode=mode,
        active_index=active_index,
        case_blocks=case_blocks,
        stage_results=stage_results,
        submit_hint=submit_hint,
    )


def workflow_machine_state_payload(
    workflow,
    rows,
    *,
    mode,
    final_sweep_mode,
    stage_failure_mode="continue",
    active_index,
    stage_results,
    solve_failed,
    terminal,
    terminal_reason,
    ts_str_fn,
):
    stages = (workflow.get("spec") or {}).get("stages") or []
    active_stage = stages[active_index] if (0 <= active_index < len(stages)) else None
    stage_params = {
        (row.get("stage") or {}).get("id"): row.get("resolved_params") or {}
        for row in rows
        if (row.get("stage") or {}).get("id")
    }
    stage_param_warnings = {
        (row.get("stage") or {}).get("id"): row.get("param_warnings") or []
        for row in rows
        if (row.get("stage") or {}).get("id")
    }
    stage_param_sources = {
        (row.get("stage") or {}).get("id"): row.get("param_sources") or {}
        for row in rows
        if (row.get("stage") or {}).get("id")
    }
    stage_namespaces = {
        (row.get("stage") or {}).get("id"): (row.get("namespace_context") or {}).get("roles") or {}
        for row in rows
        if (row.get("stage") or {}).get("id")
    }
    return {
        "workflow_name": (workflow.get("metadata") or {}).get("name"),
        "workflow_path": workflow.get("path"),
        "prompt_mode": mode,
        "final_sweep_mode": final_sweep_mode,
        "stage_failure_mode": stage_failure_mode,
        "active_stage_index": int(active_index + 1) if active_stage else None,
        "active_stage_id": (active_stage or {}).get("id"),
        "stage_total": len(stages),
        "stage_statuses": stage_results,
        "stage_params": stage_params,
        "stage_param_warnings": stage_param_warnings,
        "stage_param_sources": stage_param_sources,
        "stage_namespaces": stage_namespaces,
        "solve_status": "failed" if solve_failed else "passed",
        "terminal": bool(terminal),
        "terminal_reason": terminal_reason,
        "updated_at": ts_str_fn(),
    }


def workflow_publish_prompt_and_state(
    *,
    workflow,
    rows,
    mode,
    final_sweep_mode,
    stage_failure_mode="continue",
    active_index,
    stage_results,
    submit_hint,
    bundle_dir,
    workflow_run_dir,
    solve_failed,
    terminal,
    terminal_reason,
    render_workflow_prompt_fn,
    dump_json_fn,
    ts_str_fn,
):
    prompt = workflow_compose_prompt(
        workflow,
        rows,
        mode,
        active_index,
        stage_results,
        submit_hint,
        render_workflow_prompt_fn=render_workflow_prompt_fn,
    )
    prompt_path = Path(bundle_dir) / "PROMPT.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    machine_state = workflow_machine_state_payload(
        workflow,
        rows,
        mode=mode,
        final_sweep_mode=final_sweep_mode,
        stage_failure_mode=stage_failure_mode,
        active_index=active_index,
        stage_results=stage_results,
        solve_failed=solve_failed,
        terminal=terminal,
        terminal_reason=terminal_reason,
        ts_str_fn=ts_str_fn,
    )
    runtime_state_path = Path(workflow_run_dir) / "workflow_state.json"
    dump_json_fn(runtime_state_path, machine_state)

    bundle_state_path = Path(bundle_dir) / "WORKFLOW_STATE.json"
    if mode == "concat_stateful":
        dump_json_fn(bundle_state_path, machine_state)
    else:
        try:
            bundle_state_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return prompt_path, runtime_state_path


def workflow_append_stage_result(results_path, payload):
    path = Path(results_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def workflow_transition_log(path, message, *, append_log_line_fn, ts_str_fn):
    append_log_line_fn(path, f"[{ts_str_fn()}] {message}")


def workflow_namespace_values(rows, alias_map):
    values = []
    seen = set()

    def _add(value):
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        values.append(text)

    if isinstance(alias_map, dict):
        for namespace in alias_map.values():
            _add(namespace)
    elif isinstance(alias_map, (list, tuple, set)):
        for namespace in alias_map:
            _add(namespace)

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ctx = row.get("namespace_context")
        if not isinstance(ctx, dict):
            continue
        roles = ctx.get("roles")
        if not isinstance(roles, dict):
            continue
        for namespace in roles.values():
            _add(namespace)
    return values


def _normalized_role_ownership(contract):
    raw = {}
    if isinstance(contract, dict):
        raw = contract.get("role_ownership")
        if not isinstance(raw, dict):
            raw = contract.get("roleOwnership")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for role, owner in raw.items():
        role_name = str(role or "").strip()
        owner_name = str(owner or "").strip().lower()
        if not role_name:
            continue
        if owner_name not in ("framework", "case"):
            continue
        out[role_name] = owner_name
    return out


def workflow_namespace_ensure_plan(rows, alias_map):
    values = []
    seen = set()
    skipped = []
    seen_skipped = set()
    role_bound_namespaces = set()

    def _add(value):
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        values.append(text)

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        stage = row.get("stage") or {}
        stage_id = str(stage.get("id") or "").strip()
        contract = row.get("namespace_contract")
        ownership = _normalized_role_ownership(contract)
        ctx = row.get("namespace_context")
        if not isinstance(ctx, dict):
            continue
        roles = ctx.get("roles")
        if not isinstance(roles, dict):
            continue
        for role, namespace in roles.items():
            role_name = str(role or "").strip()
            ns_value = str(namespace or "").strip()
            if not role_name or not ns_value:
                continue
            role_bound_namespaces.add(ns_value)
            owner = str(ownership.get(role_name) or "framework").strip().lower()
            if owner == "case":
                key = (stage_id, role_name, ns_value)
                if key in seen_skipped:
                    continue
                seen_skipped.add(key)
                skipped.append(
                    {
                        "stage_id": stage_id,
                        "role": role_name,
                        "namespace": ns_value,
                        "owner": "case",
                    }
                )
                continue
            _add(ns_value)

    if isinstance(alias_map, dict):
        for namespace in alias_map.values():
            ns_value = str(namespace or "").strip()
            # Preserve legacy behavior for aliases that are not bound by any stage role.
            if ns_value and ns_value not in role_bound_namespaces:
                _add(ns_value)

    return {"values": values, "skipped": skipped}


def workflow_stage_cleanup_commands(stage_ctx, *, resources_dir, normalize_metrics_fn, normalize_commands_fn):
    from app.decoys import build_decoy_commands, list_decoy_files

    commands = []
    case_data = stage_ctx.get("case_data") or {}
    service = stage_ctx.get("service")
    case = stage_ctx.get("case")

    metrics = normalize_metrics_fn(case_data.get("externalMetrics"))
    if "decoy_integrity" in metrics and service and case:
        case_dir = Path(resources_dir) / service / case
        decoy_files = list_decoy_files(case_dir)
        commands.extend(build_decoy_commands(decoy_files, "delete"))

    commands.extend(normalize_commands_fn(case_data.get("cleanUpCommands")))
    return commands


def workflow_ensure_namespaces(namespaces, log_path, *, run_command_list_logged_fn):
    unique = []
    seen = set()
    for ns in namespaces or []:
        value = str(ns or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    if not unique:
        return {"status": "skipped", "error": None}
    cmds = []
    for ns in unique:
        cmds.append(
            {
                "command": [
                    "/bin/sh",
                    "-c",
                    f"kubectl get namespace {shlex.quote(ns)} >/dev/null 2>&1 || kubectl create namespace {shlex.quote(ns)}",
                ],
                "sleep": 0,
            }
        )
    ok, _, reason = run_command_list_logged_fn(cmds, log_path, default_timeout=120, fail_fast=True)
    return {"status": "ok" if ok else "failed", "error": reason}


def workflow_namespace_cleanup_commands(namespaces):
    cmds = []
    seen = set()
    for ns in namespaces or []:
        value = str(ns or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        cmds.append(
            {
                "command": [
                    "kubectl",
                    "delete",
                    "namespace",
                    value,
                    "--ignore-not-found=true",
                    "--wait=true",
                    "--timeout=180s",
                ],
                "sleep": 0,
            }
        )
    return cmds


def workflow_run_final_cleanup(
    stage_contexts,
    workflow_run_dir,
    *,
    namespace_values=None,
    workflow_stage_cleanup_commands_fn,
    workflow_namespace_cleanup_commands_fn=workflow_namespace_cleanup_commands,
    run_command_list_logged_fn,
    append_log_line_fn,
    ts_str_fn,
    relative_path_fn,
):
    cleanup_log = Path(workflow_run_dir) / "workflow_cleanup.log"
    any_cmd = False
    overall_ok = True
    for stage_ctx in reversed(stage_contexts):
        cmds = workflow_stage_cleanup_commands_fn(stage_ctx)
        if not cmds:
            continue
        any_cmd = True
        ok, _, _ = run_command_list_logged_fn(
            cmds,
            cleanup_log,
            default_timeout=600,
            fail_fast=False,
            namespace_context=stage_ctx.get("namespace_context"),
        )
        overall_ok = overall_ok and bool(ok)
    ns_cmds = workflow_namespace_cleanup_commands_fn(namespace_values)
    if ns_cmds:
        any_cmd = True
        ok, _, _ = run_command_list_logged_fn(ns_cmds, cleanup_log, default_timeout=240, fail_fast=False)
        overall_ok = overall_ok and bool(ok)
    if not any_cmd:
        append_log_line_fn(cleanup_log, f"[{ts_str_fn()}] no cleanup commands")
        return {"status": "no_cleanup", "cleanup_log": relative_path_fn(cleanup_log)}
    return {
        "status": "done" if overall_ok else "failed",
        "cleanup_log": relative_path_fn(cleanup_log),
    }


def workflow_run_final_sweep(rows, workflow_run_dir, *, run_stage_oracle_stateless_fn):
    sweep = {}
    for row in rows:
        stage = row.get("stage") or {}
        stage_id = stage.get("id")
        sweep_log = Path(workflow_run_dir) / f"workflow_final_sweep_{stage_id}.log"
        sweep[stage_id] = run_stage_oracle_stateless_fn(
            row.get("case_data") or {},
            sweep_log,
            namespace_context=row.get("namespace_context"),
        )
    return sweep
