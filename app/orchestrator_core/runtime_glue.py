#!/usr/bin/env python3
import os
import subprocess
import time

import yaml

from app.oracle import resolve_oracle_verify
from app.test_schema import raise_for_legacy_test_yaml_keys
from app.orchestrator_core.agent_runtime import (
    collect_agent_llm_env as _agent_runtime_collect_llm_env,
    launch_agent as _agent_runtime_launch_agent,
    resolve_agent_defaults as _agent_runtime_resolve_agent_defaults,
    terminate_agent as _agent_runtime_terminate_agent,
    try_read_submit_file as _agent_runtime_try_read_submit_file,
    wait_for_start_signal as _agent_runtime_wait_for_start_signal,
    wait_for_submit_or_agent as _agent_runtime_wait_for_submit_or_agent,
)
from app.orchestrator_core.bundle import (
    create_proxy_kubeconfig as _bundle_create_proxy_kubeconfig,
    detect_real_kubectl as _bundle_detect_real_kubectl,
    prepare_bundle as _bundle_prepare_bundle,
    write_env_file as _bundle_write_env_file,
    write_kubectl_wrapper as _bundle_write_kubectl_wrapper,
    write_prompt as _bundle_write_prompt,
)
from app.orchestrator_core.artifacts import (
    append_submit_result_log as _artifacts_append_submit_result_log,
    attach_agent_usage_fields as _artifacts_attach_agent_usage_fields,
    ingest_agent_usage as _artifacts_ingest_agent_usage,
    relative_path as _artifacts_relative_path,
    write_stage as _artifacts_write_stage,
    write_submit_result as _artifacts_write_submit_result,
)
from app.orchestrator_core.case_runner import (
    run_case as _case_runner_run_case,
    run_case_once as _case_runner_run_case_once,
)
from app.orchestrator_core.execution_plan import (
    build_single_stage_plan as _execution_plan_build_single_stage_plan,
)
from app.orchestrator_core.glue_judge import (
    _drain_pending_judge_records,
    _route_case_records_for_judging,
    _write_batch_judge_summary,
)
from app.orchestrator_core.glue_runtime import (
    _collect_case_ids,
    _prepare_agent_auth_mount,
    _resolve_repo_root,
    _stream_action_trace,
    _stream_agent_log,
)
from app.orchestrator_core.glue_workflow import (
    _attach_workflow_namespace_context,
    _load_stage_case_row,
    _resolve_workflow_rows,
)
from app.orchestrator_core.workflow_run import (
    run_workflow as _workflow_run_run_workflow,
    run_workflow_stage as _workflow_run_run_workflow_stage,
    stage_setup_timeout as _workflow_run_stage_setup_timeout,
    workflow_append_stage_result as _workflow_run_append_stage_result,
    workflow_ensure_namespaces as _workflow_run_ensure_namespaces,
    workflow_namespace_ensure_plan as _workflow_run_namespace_ensure_plan,
    workflow_namespace_values as _workflow_run_namespace_values,
    workflow_namespace_cleanup_commands as _workflow_run_namespace_cleanup_commands,
    workflow_publish_prompt_and_state as _workflow_run_publish_prompt_and_state,
    workflow_run_final_cleanup as _workflow_run_run_final_cleanup,
    workflow_run_final_sweep as _workflow_run_run_final_sweep,
    workflow_stage_cleanup_commands as _workflow_run_stage_cleanup_commands,
    workflow_status_from_stage as _workflow_run_status_from_stage,
    workflow_submit_payload as _workflow_run_submit_payload,
    workflow_transition_log as _workflow_run_transition_log,
)
from app.orchestrator_core.cli import main as _orchestrator_core_cli_main
from app.orchestrator_core.common import (
    control_listen_from_url as _common_control_listen_from_url,
    is_local_host as _common_is_local_host,
    normalize_control_url as _common_normalize_control_url,
)
from app.orchestrator_core.exec_runtime import (
    append_log_line as _exec_runtime_append_log_line,
    resolve_step_timeout as _exec_runtime_resolve_step_timeout,
    run_command_list_logged as _exec_runtime_run_command_list_logged,
    wait_for_idle as _exec_runtime_wait_for_idle,
    wait_for_status as _exec_runtime_wait_for_status,
)
from app.orchestrator_core.namespace_runtime import (
    namespace_env_vars as _namespace_runtime_namespace_env_vars,
    prepare_exec_command as _namespace_runtime_prepare_exec_command,
)
from app.orchestrator_core.proxy import (
    docker_build_image as _proxy_docker_build_image,
    proxy_control_running as _proxy_proxy_control_running,
    resolve_api_server as _proxy_resolve_api_server,
    start_local_proxy as _proxy_start_local_proxy,
    wait_for_proxy as _proxy_wait_for_proxy,
)
from app.orchestrator_core.runtime_defaults import (
    AGENT_REGISTRY,
    DEFAULT_PROXY_CONTROL,
    DEFAULT_PROXY_LISTEN,
    DEFAULT_PROXY_TIMEOUT,
)
from app.preconditions import normalize_precondition_units
from app.settings import ROOT, RESOURCES_DIR
from app.util import (
    command_to_string,
    infer_command_timeout_seconds,
    list_requires_shell,
    normalize_commands,
    normalize_metrics,
    parse_duration_seconds,
    safe_join,
    sanitize_name,
    ts_str,
)
from app.workflow import (
    dump_json,
    load_workflow_spec,
    render_workflow_prompt,
)

def _normalize_control_url(control_url):
    return _common_normalize_control_url(control_url)


def _control_listen_from_url(control_url, default_host="127.0.0.1", default_port=8082):
    return _common_control_listen_from_url(
        control_url,
        default_host=default_host,
        default_port=default_port,
    )


def _is_local_host(host):
    return _common_is_local_host(host)


def _proxy_control_running(control_url):
    return _proxy_proxy_control_running(control_url, timeout=DEFAULT_PROXY_TIMEOUT)


def _start_local_proxy(repo_root, listen, control_listen, upstream, log_path):
    return _proxy_start_local_proxy(repo_root, listen, control_listen, upstream, log_path)


def _wait_for_proxy(control_url, timeout=5.0, poll=0.2):
    return _proxy_wait_for_proxy(
        control_url,
        timeout=timeout,
        poll=poll,
        request_timeout=DEFAULT_PROXY_TIMEOUT,
    )


def _resolve_agent_defaults(args, repo_root):
    return _agent_runtime_resolve_agent_defaults(
        args,
        repo_root,
        agent_registry=AGENT_REGISTRY,
        docker_build_image=_docker_build_image,
    )


def _collect_llm_env(args, repo_root):
    return _agent_runtime_collect_llm_env(
        args,
        repo_root,
        environ=os.environ,
    )


def _resolve_api_server(source_kubeconfig):
    return _proxy_resolve_api_server(
        source_kubeconfig,
        environ=os.environ,
    ).strip()


def _docker_build_image(tag, dockerfile, context_dir):
    return _proxy_docker_build_image(tag, dockerfile, context_dir)


def _write_prompt(bundle_dir, case_meta, submit_hint, namespace_context=None):
    return _bundle_write_prompt(
        bundle_dir,
        case_meta,
        submit_hint,
        namespace_context=namespace_context,
    )


def _write_kubectl_wrapper(bin_dir, real_kubectl):
    return _bundle_write_kubectl_wrapper(bin_dir, real_kubectl)


def _create_proxy_kubeconfig(output_path, source_kubeconfig, proxy_server):
    return _bundle_create_proxy_kubeconfig(output_path, source_kubeconfig, proxy_server)


def _detect_real_kubectl(wrapper_dir):
    return _bundle_detect_real_kubectl(wrapper_dir)


def _write_env_file(
    bundle_dir,
    kubeconfig_path,
    action_trace_path,
    submit_file,
    start_file=None,
    submit_result_file=None,
    extra_env=None,
    include_workspace_bin=True,
):
    return _bundle_write_env_file(
        bundle_dir,
        kubeconfig_path,
        action_trace_path,
        submit_file,
        start_file=start_file,
        submit_result_file=submit_result_file,
        extra_env=extra_env,
        include_workspace_bin=include_workspace_bin,
    )


def _wait_for_status(app, target_states, timeout=None, poll=1.0):
    return _exec_runtime_wait_for_status(
        app,
        target_states,
        timeout=timeout,
        poll=poll,
        time_module=time,
    )


def _wait_for_start_signal(path, agent_proc=None, poll=1.0):
    return _agent_runtime_wait_for_start_signal(path, agent_proc=agent_proc, poll=poll)


def _try_read_submit_file(path):
    return _agent_runtime_try_read_submit_file(path)


def _wait_for_submit_or_agent(submit_file, agent_proc, timeout, poll=1.0, grace=3.0):
    return _agent_runtime_wait_for_submit_or_agent(
        submit_file,
        agent_proc,
        timeout,
        poll=poll,
        grace=grace,
        read_submit_file=_try_read_submit_file,
    )


def _terminate_agent(agent_proc, grace=3.0):
    return _agent_runtime_terminate_agent(agent_proc, grace=grace)


def _wait_for_idle(app, poll=1.0, log_every=60):
    return _exec_runtime_wait_for_idle(
        app,
        poll=poll,
        log_every=log_every,
        print_fn=print,
        time_module=time,
    )


def _write_submit_result(path, payload):
    return _artifacts_write_submit_result(path, payload)


def _append_submit_result_log(run_dir, payload):
    return _artifacts_append_submit_result_log(run_dir, payload)


def _write_stage(run_dir, stage, detail=None):
    return _artifacts_write_stage(
        run_dir,
        stage,
        detail=detail,
        time_module=time,
        print_fn=print,
    )


def _relative_path(path):
    return _artifacts_relative_path(path, root=ROOT)


def _ingest_agent_usage(run_dir, root=ROOT):
    return _artifacts_ingest_agent_usage(run_dir, root=root)


def _attach_agent_usage_fields(outcome):
    return _artifacts_attach_agent_usage_fields(
        outcome,
        root=ROOT,
        ingest_agent_usage_fn=_ingest_agent_usage,
    )


def _prepare_bundle(
    app,
    case_id,
    run_dir,
    args,
    include_workspace_bin=True,
    namespace_context=None,
):
    return _bundle_prepare_bundle(
        app,
        case_id,
        run_dir,
        args,
        resources_dir=RESOURCES_DIR,
        include_workspace_bin=include_workspace_bin,
        namespace_context=namespace_context,
        write_prompt_fn=_write_prompt,
        create_proxy_kubeconfig_fn=_create_proxy_kubeconfig,
        detect_real_kubectl_fn=_detect_real_kubectl,
        write_kubectl_wrapper_fn=_write_kubectl_wrapper,
        write_env_file_fn=_write_env_file,
    )


def _launch_agent(bundle_dir, env, args):
    return _agent_runtime_launch_agent(
        bundle_dir,
        env,
        args,
        environ=os.environ,
        popen=subprocess.Popen,
    )


def _build_single_case_workflow_plan(app, case_id, args):
    def _single_case_namespace_context(case_data):
        data = case_data if isinstance(case_data, dict) else {}
        contract = data.get("namespace_contract") if isinstance(data.get("namespace_contract"), dict) else {}
        default_role = str(contract.get("default_role") or "default").strip() or "default"
        declared_roles = []
        for raw_role in (contract.get("required_roles") or []):
            role = str(raw_role or "").strip()
            if role and role not in declared_roles:
                declared_roles.append(role)
        for raw_role in (contract.get("optional_roles") or []):
            role = str(raw_role or "").strip()
            if role and role not in declared_roles:
                declared_roles.append(role)
        if default_role not in declared_roles:
            declared_roles.insert(0, default_role)
        roles = {}
        base_roles = contract.get("base_roles")
        if not isinstance(base_roles, dict):
            base_roles = contract.get("baseRoles")
        if isinstance(base_roles, dict):
            for role, namespace in base_roles.items():
                role_name = str(role or "").strip()
                ns_value = str(namespace or "").strip()
                if role_name and ns_value:
                    roles[role_name] = ns_value
        if not roles:
            base_namespace = str(contract.get("base_namespace") or contract.get("baseNamespace") or "").strip()
            if base_namespace:
                if len(declared_roles) <= 1:
                    roles[default_role] = base_namespace
                else:
                    for role_name in declared_roles:
                        suffix = sanitize_name(role_name).replace("_", "-").strip("-") or "default"
                        roles[role_name] = f"{base_namespace}-{suffix}"
        if default_role not in roles and roles:
            default_role = "default" if "default" in roles else next(iter(roles.keys()))
        if not roles:
            return {}
        return {"default_role": default_role, "roles": roles}

    case_meta = app.get_case(case_id)
    if case_meta.get("error"):
        raise RuntimeError(case_meta.get("error"))
    service = str(case_meta.get("service") or "").strip()
    case_name = str(case_meta.get("case") or "").strip()

    stage = {
        "id": "stage_single",
        "case_id": case_id,
        "service": service,
        "case": case_name,
        "case_ref": {"service": service, "case": case_name},
        "max_attempts": getattr(args, "max_attempts", None),
    }
    row = _load_stage_case_row(app, stage)
    namespace_context = _single_case_namespace_context(row.get("case_data"))
    plan = _execution_plan_build_single_stage_plan(
        case_id,
        row.get("case_data") or {},
        args,
        stage_id=stage["id"],
        workflow_id=f"single:{service}:{case_name}",
        service=service,
        case_name=case_name,
        namespace_context=namespace_context,
        resolved_params=row.get("resolved_params"),
        param_warnings=row.get("param_warnings"),
    )
    stage_plan = (plan.get("stages") or [None])[0] or {}
    stage_id = str(stage_plan.get("id") or stage.get("id") or "stage_single")
    row_stage = {
        "id": stage_id,
        "case_id": stage_plan.get("case_id") or case_id,
        "service": stage_plan.get("service") or service,
        "case": stage_plan.get("case") or case_name,
        "case_ref": {
            "service": stage_plan.get("service") or service,
            "case": stage_plan.get("case") or case_name,
        },
        "max_attempts": stage_plan.get("max_attempts"),
    }
    workflow = {
        "apiVersion": "benchmark/v1",
        "kind": "Workflow",
        "metadata": {
            "name": f"single-{service}-{case_name}",
        },
        "spec": {
            "prompt_mode": "progressive",
            "namespaces": [],
            "stages": [
                {
                    "id": stage_id,
                    "case_id": row_stage["case_id"],
                    "service": row_stage["service"],
                    "case": row_stage["case"],
                    "max_attempts": row_stage.get("max_attempts"),
                    "namespaces": [],
                }
            ],
        },
        "path": str((ROOT / case_meta.get("path", "")).resolve()),
        "execution_plan": plan,
    }
    workflow_row = {
        "stage": row_stage,
        "case_meta": dict(case_meta),
        "case_data": stage_plan.get("case_data_override") or row.get("case_data") or {},
        "case_path": row.get("case_path"),
        "resolved_params": stage_plan.get("resolved_params") or row.get("resolved_params") or {},
        "param_warnings": stage_plan.get("param_warnings") or row.get("param_warnings") or [],
        "namespace_contract": row.get("namespace_contract") or {},
        "prompt_block": row.get("prompt_block") or "",
        "workflow_namespace_aliases": [],
        "namespace_context": stage_plan.get("namespace_context") or {},
    }
    return workflow, [workflow_row], plan


def _single_case_attach_namespace_context(rows, workflow, token, prefix):
    _ = workflow, token, prefix
    for row in rows or []:
        row["workflow_namespace_aliases"] = []
        row["namespace_context"] = row.get("namespace_context") or {}
    return {}


def _run_single_case_workflow(app, case_id, args):
    workflow, rows, _plan = _build_single_case_workflow_plan(app, case_id, args)
    had_workflow = hasattr(args, "workflow")
    original_workflow = getattr(args, "workflow", None)
    args.workflow = f"synthetic://{case_id}"
    try:
        return _workflow_run_run_workflow(
            app,
            args,
            root=ROOT,
            resources_dir=RESOURCES_DIR,
            load_workflow_spec_fn=lambda _workflow_path: workflow,
            resolve_workflow_rows_fn=lambda _app, _workflow: rows,
            wait_for_idle_fn=_wait_for_idle,
            ts_str_fn=ts_str,
            attach_workflow_namespace_context_fn=_single_case_attach_namespace_context,
            workflow_effective_params_payload_fn=_workflow_effective_params_payload,
            dump_json_fn=dump_json,
            workflow_ensure_namespaces_fn=_workflow_ensure_namespaces,
            workflow_namespace_values_fn=_workflow_namespace_values,
            workflow_namespace_ensure_plan_fn=_workflow_namespace_ensure_plan,
            workflow_run_final_cleanup_fn=_workflow_run_final_cleanup,
            run_workflow_stage_fn=_run_workflow_stage,
            workflow_transition_log_fn=_workflow_transition_log,
            prepare_bundle_fn=_prepare_bundle,
            workflow_publish_prompt_and_state_fn=_workflow_publish_prompt_and_state,
            prepare_agent_auth_mount_fn=_prepare_agent_auth_mount,
            namespace_env_vars_fn=_namespace_env_vars,
            launch_agent_fn=_launch_agent,
            write_stage_fn=_write_stage,
            wait_for_start_signal_fn=_wait_for_start_signal,
            stream_action_trace_fn=_stream_action_trace,
            stream_agent_log_fn=_stream_agent_log,
            wait_for_submit_or_agent_fn=_wait_for_submit_or_agent,
            write_submit_result_fn=_write_submit_result,
            append_submit_result_log_fn=_append_submit_result_log,
            workflow_submit_payload_fn=_workflow_submit_payload,
            wait_for_status_fn=_wait_for_status,
            workflow_status_from_stage_fn=_workflow_status_from_stage,
            workflow_append_stage_result_fn=_workflow_append_stage_result,
            workflow_run_final_sweep_fn=_workflow_run_final_sweep,
            run_stage_oracle_stateless_fn=_run_stage_oracle_stateless,
            render_workflow_prompt_fn=render_workflow_prompt,
            attach_agent_usage_fields_fn=_attach_agent_usage_fields,
            terminate_agent_fn=_terminate_agent,
        )
    finally:
        if had_workflow:
            args.workflow = original_workflow
        else:
            delattr(args, "workflow")


def _run_case_once(app, case_id, args):
    return _case_runner_run_case_once(
        app,
        case_id,
        args,
        run_single_case_workflow_fn=_run_single_case_workflow,
        attach_agent_usage_fields=_attach_agent_usage_fields,
    )


def _load_case_yaml(app, case_id):
    case = app.get_case(case_id)
    if case.get("error"):
        raise RuntimeError(case["error"])
    path = ROOT / case.get("path", "")
    if not path.exists():
        raise RuntimeError(f"Case file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    try:
        context = str(path.relative_to(ROOT))
    except Exception:
        context = str(path)
    raise_for_legacy_test_yaml_keys(data, context=context)
    return data


def _run_case(app, case_id, args):
    return _case_runner_run_case(
        app,
        case_id,
        args,
        load_case_yaml_fn=_load_case_yaml,
        run_case_once_fn=_run_case_once,
    )


def _resolve_step_timeout(item, default_sec=300):
    return _exec_runtime_resolve_step_timeout(
        item,
        default_sec=default_sec,
        parse_duration_seconds_fn=parse_duration_seconds,
        infer_command_timeout_seconds_fn=infer_command_timeout_seconds,
    )


def _append_log_line(path, line):
    return _exec_runtime_append_log_line(path, line)


def _namespace_env_vars(namespace_context, default_ns=None, roles=None):
    return _namespace_runtime_namespace_env_vars(namespace_context, default_ns=default_ns, roles=roles)


def _prepare_exec_command(item, namespace_context, render_dir=None):
    return _namespace_runtime_prepare_exec_command(
        item,
        namespace_context,
        render_dir=render_dir,
        root=ROOT,
        environ=os.environ,
    )


def _run_command_list_logged(
    commands,
    log_path,
    default_timeout=300,
    fail_fast=True,
    namespace_context=None,
):
    return _exec_runtime_run_command_list_logged(
        commands,
        log_path,
        default_timeout=default_timeout,
        fail_fast=fail_fast,
        namespace_context=namespace_context,
        normalize_commands_fn=normalize_commands,
        prepare_exec_command_fn=_prepare_exec_command,
        resolve_step_timeout_fn=_resolve_step_timeout,
        command_to_string_fn=command_to_string,
        append_log_line_fn=_append_log_line,
        ts_str_fn=ts_str,
        list_requires_shell_fn=list_requires_shell,
        safe_join_fn=safe_join,
        subprocess_module=subprocess,
        time_module=time,
        cwd=ROOT,
    )


def _run_stage_oracle_stateless(case_data, log_path, namespace_context=None):
    verify_cfg = resolve_oracle_verify(case_data)
    verify_cmds = verify_cfg.get("commands") or []
    if not verify_cmds:
        _append_log_line(log_path, f"[{ts_str()}] ERROR: oracle.verify.commands not configured")
        return {"status": "error", "reason": "oracle.verify.commands not configured"}
    before_cmds = verify_cfg.get("before_commands") or []
    after_cmds = verify_cfg.get("after_commands") or []
    after_mode = verify_cfg.get("after_failure_mode") or "warn"
    out = {"status": "pass", "reason": "ok"}
    ok, kind, reason = _run_command_list_logged(
        before_cmds,
        log_path,
        default_timeout=600,
        fail_fast=True,
        namespace_context=namespace_context,
    )
    if not ok:
        out = {"status": "timeout" if kind == "timeout" else "error", "reason": f"before-hook {reason}"}
    else:
        ok, kind, reason = _run_command_list_logged(
            verify_cmds,
            log_path,
            default_timeout=600,
            fail_fast=True,
            namespace_context=namespace_context,
        )
        if not ok:
            out = {"status": "timeout" if kind == "timeout" else "fail", "reason": f"oracle {reason}"}
    ok, kind, reason = _run_command_list_logged(
        after_cmds,
        log_path,
        default_timeout=600,
        fail_fast=True,
        namespace_context=namespace_context,
    )
    if not ok and after_mode == "fail":
        out = {"status": "timeout" if kind == "timeout" else "error", "reason": f"after-hook {reason}"}
    elif not ok and after_mode == "warn":
        _append_log_line(log_path, f"[{ts_str()}] WARNING: after-hook failed ({reason})")
    return out


def _stage_setup_timeout(status, args):
    return _workflow_run_stage_setup_timeout(status, args)


def _workflow_status_from_stage(final_status):
    return _workflow_run_status_from_stage(final_status)


def _workflow_effective_params_payload(rows):
    payload = {}
    for row in rows or []:
        stage = row.get("stage") or {}
        stage_id = stage.get("id")
        if not stage_id:
            continue
        service = stage.get("service") or (stage.get("case_ref") or {}).get("service")
        case = stage.get("case") or (stage.get("case_ref") or {}).get("case")
        payload[stage_id] = {
            "service": service,
            "case": case,
            "params": row.get("resolved_params") or {},
            "param_sources": row.get("param_sources") or {},
            "warnings": row.get("param_warnings") or [],
            "namespaces": list(stage.get("namespaces") or []),
            "namespace_binding": dict(stage.get("namespace_binding") or {}),
        }
    return payload


def _run_workflow_stage(
    app,
    row,
    args,
    skip_unit_ids=None,
    defer_cleanup=True,
    stage_run_dir=None,
):
    return _workflow_run_run_workflow_stage(
        app,
        row,
        args,
        skip_unit_ids=skip_unit_ids,
        defer_cleanup=defer_cleanup,
        stage_run_dir=stage_run_dir,
        wait_for_status_fn=_wait_for_status,
        stage_setup_timeout_fn=_stage_setup_timeout,
    )


def _workflow_submit_payload(
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
    return _workflow_run_submit_payload(
        base_status=base_status,
        attempt=attempt,
        last_error=last_error,
        verification_log=verification_log,
        attempts_left=attempts_left,
        time_left_sec=time_left_sec,
        can_retry=can_retry,
        mode=mode,
        stage_index=stage_index,
        stage_total=stage_total,
        stage_id=stage_id,
        stage_attempt=stage_attempt,
        stage_status=stage_status,
        continue_flag=continue_flag,
        final_flag=final_flag,
        next_stage_id=next_stage_id,
        reason=reason,
    )


def _workflow_publish_prompt_and_state(
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
    render_workflow_prompt_fn=None,
    dump_json_fn=None,
    ts_str_fn=None,
):
    return _workflow_run_publish_prompt_and_state(
        workflow=workflow,
        rows=rows,
        mode=mode,
        final_sweep_mode=final_sweep_mode,
        stage_failure_mode=stage_failure_mode,
        active_index=active_index,
        stage_results=stage_results,
        submit_hint=submit_hint,
        bundle_dir=bundle_dir,
        workflow_run_dir=workflow_run_dir,
        solve_failed=solve_failed,
        terminal=terminal,
        terminal_reason=terminal_reason,
        render_workflow_prompt_fn=render_workflow_prompt_fn or render_workflow_prompt,
        dump_json_fn=dump_json_fn or dump_json,
        ts_str_fn=ts_str_fn or ts_str,
    )


def _workflow_append_stage_result(results_path, payload):
    _workflow_run_append_stage_result(results_path, payload)


def _workflow_transition_log(path, message):
    _workflow_run_transition_log(
        path,
        message,
        append_log_line_fn=_append_log_line,
        ts_str_fn=ts_str,
    )


def _workflow_stage_cleanup_commands(stage_ctx):
    return _workflow_run_stage_cleanup_commands(
        stage_ctx,
        resources_dir=RESOURCES_DIR,
        normalize_metrics_fn=normalize_metrics,
        normalize_commands_fn=normalize_commands,
    )


def _workflow_ensure_namespaces(namespaces, log_path):
    return _workflow_run_ensure_namespaces(
        namespaces,
        log_path,
        run_command_list_logged_fn=_run_command_list_logged,
    )


def _workflow_namespace_values(rows, alias_map):
    return _workflow_run_namespace_values(rows, alias_map)


def _workflow_namespace_ensure_plan(rows, alias_map):
    return _workflow_run_namespace_ensure_plan(rows, alias_map)


def _workflow_namespace_cleanup_commands(namespaces):
    return _workflow_run_namespace_cleanup_commands(namespaces)


def _workflow_run_final_cleanup(stage_contexts, workflow_run_dir, namespace_values=None):
    return _workflow_run_run_final_cleanup(
        stage_contexts,
        workflow_run_dir,
        namespace_values=namespace_values,
        workflow_stage_cleanup_commands_fn=_workflow_stage_cleanup_commands,
        workflow_namespace_cleanup_commands_fn=_workflow_namespace_cleanup_commands,
        run_command_list_logged_fn=_run_command_list_logged,
        append_log_line_fn=_append_log_line,
        ts_str_fn=ts_str,
        relative_path_fn=_relative_path,
    )


def _workflow_run_final_sweep(rows, workflow_run_dir):
    return _workflow_run_run_final_sweep(
        rows,
        workflow_run_dir,
        run_stage_oracle_stateless_fn=_run_stage_oracle_stateless,
    )


def _run_workflow(app, args):
    return _workflow_run_run_workflow(
        app,
        args,
        root=ROOT,
        resources_dir=RESOURCES_DIR,
        load_workflow_spec_fn=load_workflow_spec,
        resolve_workflow_rows_fn=_resolve_workflow_rows,
        wait_for_idle_fn=_wait_for_idle,
        ts_str_fn=ts_str,
        attach_workflow_namespace_context_fn=_attach_workflow_namespace_context,
        workflow_effective_params_payload_fn=_workflow_effective_params_payload,
        dump_json_fn=dump_json,
        workflow_ensure_namespaces_fn=_workflow_ensure_namespaces,
        workflow_namespace_values_fn=_workflow_namespace_values,
        workflow_namespace_ensure_plan_fn=_workflow_namespace_ensure_plan,
        workflow_run_final_cleanup_fn=_workflow_run_final_cleanup,
        run_workflow_stage_fn=_run_workflow_stage,
        workflow_transition_log_fn=_workflow_transition_log,
        prepare_bundle_fn=_prepare_bundle,
        workflow_publish_prompt_and_state_fn=_workflow_publish_prompt_and_state,
        prepare_agent_auth_mount_fn=_prepare_agent_auth_mount,
        namespace_env_vars_fn=_namespace_env_vars,
        launch_agent_fn=_launch_agent,
        write_stage_fn=_write_stage,
        wait_for_start_signal_fn=_wait_for_start_signal,
        stream_action_trace_fn=_stream_action_trace,
        stream_agent_log_fn=_stream_agent_log,
        wait_for_submit_or_agent_fn=_wait_for_submit_or_agent,
        write_submit_result_fn=_write_submit_result,
        append_submit_result_log_fn=_append_submit_result_log,
        workflow_submit_payload_fn=_workflow_submit_payload,
        wait_for_status_fn=_wait_for_status,
        workflow_status_from_stage_fn=_workflow_status_from_stage,
        workflow_append_stage_result_fn=_workflow_append_stage_result,
        workflow_run_final_sweep_fn=_workflow_run_final_sweep,
        run_stage_oracle_stateless_fn=_run_stage_oracle_stateless,
        render_workflow_prompt_fn=render_workflow_prompt,
        attach_agent_usage_fields_fn=_attach_agent_usage_fields,
        terminate_agent_fn=_terminate_agent,
    )


def _ensure_proxy_control():
    control_url = os.environ.get("BENCHMARK_PROXY_CONTROL_URL", DEFAULT_PROXY_CONTROL)
    if not control_url:
        return False
    return _proxy_control_running(control_url)


def main():
    return _orchestrator_core_cli_main(
        default_proxy_listen=DEFAULT_PROXY_LISTEN,
        default_proxy_control=DEFAULT_PROXY_CONTROL,
        resolve_repo_root_fn=_resolve_repo_root,
        collect_case_ids_fn=_collect_case_ids,
        normalize_control_url_fn=_normalize_control_url,
        is_local_host_fn=_is_local_host,
        proxy_control_running_fn=_proxy_control_running,
        control_listen_from_url_fn=_control_listen_from_url,
        resolve_api_server_fn=_resolve_api_server,
        start_local_proxy_fn=_start_local_proxy,
        wait_for_proxy_fn=_wait_for_proxy,
        resolve_agent_defaults_fn=_resolve_agent_defaults,
        collect_llm_env_fn=_collect_llm_env,
        ensure_proxy_control_fn=_ensure_proxy_control,
        run_workflow_fn=_run_workflow,
        run_case_fn=_run_case,
        route_case_records_for_judging_fn=_route_case_records_for_judging,
        drain_pending_judge_records_fn=_drain_pending_judge_records,
        write_batch_judge_summary_fn=_write_batch_judge_summary,
    )


if __name__ == "__main__":
    main()
