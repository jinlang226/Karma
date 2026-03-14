from __future__ import annotations

import json
import os
import signal
import threading
from pathlib import Path


def _submit_control_action(payload):
    text = str(payload or "").strip()
    if not text:
        return ""
    if text.lower() in ("cleanup", "abort", "stop"):
        return "cleanup"
    try:
        data = json.loads(text)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    action = str(data.get("action") or data.get("type") or "").strip().lower()
    if action in ("cleanup", "abort", "stop"):
        return "cleanup"
    return ""


def _normalize_final_sweep_mode(value, *, default, allow_inherit=False):
    text = str(value or "").strip().lower()
    allowed = {"full", "off"}
    if allow_inherit:
        allowed.add("inherit")
    if text in allowed:
        return text
    return default


def _normalize_stage_failure_mode(value, *, default, allow_inherit=False):
    text = str(value or "").strip().lower()
    allowed = {"continue", "terminate"}
    if allow_inherit:
        allowed.add("inherit")
    if text in allowed:
        return text
    return default


def _install_interrupt_handlers(handler):
    installed = []
    for signame in ("SIGTERM",):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            previous = signal.getsignal(sig)
            signal.signal(sig, handler)
            installed.append((sig, previous))
        except Exception:
            continue
    return installed


def _restore_interrupt_handlers(installed):
    for sig, previous in installed or []:
        try:
            signal.signal(sig, previous)
        except Exception:
            continue


def run_workflow(
    app,
    args,
    *,
    root,
    resources_dir,
    load_workflow_spec_fn,
    resolve_workflow_rows_fn,
    wait_for_idle_fn,
    ts_str_fn,
    attach_workflow_namespace_context_fn,
    workflow_effective_params_payload_fn,
    dump_json_fn,
    workflow_ensure_namespaces_fn,
    workflow_run_final_cleanup_fn,
    run_workflow_stage_fn,
    workflow_transition_log_fn,
    prepare_bundle_fn,
    workflow_publish_prompt_and_state_fn,
    prepare_agent_auth_mount_fn,
    namespace_env_vars_fn,
    launch_agent_fn,
    write_stage_fn,
    wait_for_start_signal_fn,
    stream_action_trace_fn,
    stream_agent_log_fn,
    wait_for_submit_or_agent_fn,
    write_submit_result_fn,
    append_submit_result_log_fn,
    workflow_submit_payload_fn,
    wait_for_status_fn,
    workflow_status_from_stage_fn,
    workflow_append_stage_result_fn,
    workflow_run_final_sweep_fn,
    run_stage_oracle_stateless_fn,
    render_workflow_prompt_fn,
    attach_agent_usage_fields_fn,
    terminate_agent_fn,
    workflow_namespace_values_fn=None,
    workflow_namespace_ensure_plan_fn=None,
):
    workflow = load_workflow_spec_fn(args.workflow)
    rows = resolve_workflow_rows_fn(app, workflow)
    spec = workflow.get("spec") if isinstance(workflow.get("spec"), dict) else {}
    workflow_final_sweep_mode = _normalize_final_sweep_mode(
        spec.get("final_sweep_mode"),
        default="full",
    )
    workflow_stage_failure_mode = _normalize_stage_failure_mode(
        spec.get("stage_failure_mode"),
        default="continue",
    )
    cli_final_sweep_mode = _normalize_final_sweep_mode(
        getattr(args, "final_sweep_mode", "inherit"),
        default="inherit",
        allow_inherit=True,
    )
    cli_stage_failure_mode = _normalize_stage_failure_mode(
        getattr(args, "stage_failure_mode", "inherit"),
        default="inherit",
        allow_inherit=True,
    )
    final_sweep_mode = (
        workflow_final_sweep_mode
        if cli_final_sweep_mode == "inherit"
        else cli_final_sweep_mode
    )
    stage_failure_mode = (
        workflow_stage_failure_mode
        if cli_stage_failure_mode == "inherit"
        else cli_stage_failure_mode
    )
    # Workflow runtime is compile-free: stage boundaries are resolved live via
    # precondition probe/apply/verify during each stage setup.
    compile_result = {"status": "removed", "artifact_path": None}
    compiled_artifact = {}

    wait_for_idle_fn(app, poll=1.0, log_every=60)

    wf_name = (workflow.get("metadata") or {}).get("name")
    workflow_run_dir = (root / "runs" / f"{ts_str_fn()}_workflow_run_{wf_name}").resolve()
    workflow_run_dir.mkdir(parents=True, exist_ok=True)

    def _run_dir_value(path_value):
        path = Path(path_value)
        if not path.is_absolute():
            path = (root / path).resolve()
        try:
            return str(path.relative_to(root))
        except Exception:
            return str(path)

    runtime_token = workflow_run_dir.name
    alias_map = attach_workflow_namespace_context_fn(rows, workflow, token=runtime_token, prefix="wf")

    def _default_namespace_values(_rows, _alias_map):
        raw = []
        if isinstance(_alias_map, dict):
            raw.extend(list(_alias_map.values()))
        for row in _rows or []:
            if not isinstance(row, dict):
                continue
            ctx = row.get("namespace_context")
            if not isinstance(ctx, dict):
                continue
            roles = ctx.get("roles")
            if not isinstance(roles, dict):
                continue
            raw.extend(list(roles.values()))
        return raw

    if callable(workflow_namespace_values_fn):
        raw_namespace_values = workflow_namespace_values_fn(rows, alias_map)
    else:
        raw_namespace_values = _default_namespace_values(rows, alias_map)
    namespace_values = []
    _seen_namespaces = set()
    for value in raw_namespace_values or []:
        text = str(value or "").strip()
        if not text or text in _seen_namespaces:
            continue
        _seen_namespaces.add(text)
        namespace_values.append(text)

    ensure_plan = {}
    if callable(workflow_namespace_ensure_plan_fn):
        ensure_plan = workflow_namespace_ensure_plan_fn(rows, alias_map)
    if not isinstance(ensure_plan, dict):
        ensure_plan = {}
    ensure_namespace_values = []
    _ensure_seen = set()
    for value in (ensure_plan.get("values") or namespace_values):
        text = str(value or "").strip()
        if not text or text in _ensure_seen:
            continue
        _ensure_seen.add(text)
        ensure_namespace_values.append(text)
    skipped_namespace_roles = [
        item
        for item in (ensure_plan.get("skipped") or [])
        if isinstance(item, dict)
    ]

    transition_log_path = workflow_run_dir / "workflow_transition.log"
    stage_results_path = workflow_run_dir / "workflow_stage_results.jsonl"
    effective_params_path = workflow_run_dir / "effective_params.json"
    dump_json_fn(effective_params_path, workflow_effective_params_payload_fn(rows))
    dump_json_fn(workflow_run_dir / "workflow_namespace_map.json", alias_map)
    submit_result_file = workflow_run_dir / "agent_bundle" / "submit_result.json"
    primary_stage_run_dir = None
    namespace_log = workflow_run_dir / "workflow_namespaces.log"
    if skipped_namespace_roles:
        namespace_log.parent.mkdir(parents=True, exist_ok=True)
        with namespace_log.open("a", encoding="utf-8") as handle:
            for item in skipped_namespace_roles:
                stage_id = str(item.get("stage_id") or "?")
                role = str(item.get("role") or "?")
                namespace = str(item.get("namespace") or "?")
                handle.write(
                    f"[{ts_str_fn()}] skip namespace precreate "
                    f"stage={stage_id} role={role} namespace={namespace} ownership=case\n"
                )
    ns_ready = workflow_ensure_namespaces_fn(ensure_namespace_values, namespace_log)
    if ns_ready.get("status") not in ("ok", "skipped"):
        write_stage_fn(workflow_run_dir, "setup_done", detail="status=setup_failed")
        cleanup = workflow_run_final_cleanup_fn(
            stage_contexts=[],
            workflow_run_dir=workflow_run_dir,
            namespace_values=namespace_values,
        )
        return {
            "status": "setup_failed",
            "workflow": wf_name,
            "workflow_path": workflow.get("path"),
            "run_dir": _run_dir_value(workflow_run_dir),
            "reason": "namespace_setup_failed",
            "last_error": ns_ready.get("error"),
            "cleanup_status": cleanup.get("status"),
            "cleanup_log": cleanup.get("cleanup_log"),
            "compiled_fingerprint": compiled_artifact.get("fingerprint"),
            "compiled_artifact_path": compile_result.get("artifact_path"),
            "terminal_base_status": "setup_failed",
            "primary_stage_run_dir": primary_stage_run_dir,
        }

    mode = str((workflow.get("spec") or {}).get("prompt_mode") or "progressive").strip()
    if mode not in ("progressive", "concat_stateful", "concat_blind"):
        mode = "progressive"
    stages = [(row.get("stage") or {}) for row in rows]
    total_stages = len(stages)
    stage_runs_dir = workflow_run_dir / "stage_runs"
    stage_runs_dir.mkdir(parents=True, exist_ok=True)

    def _stage_run_dir(index: int) -> Path:
        stage = stages[index] if 0 <= index < len(stages) else {}
        raw = str(stage.get("id") or f"stage_{index + 1}").strip().lower()
        slug = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in raw).strip("_")
        if not slug:
            slug = f"stage_{index + 1}"
        return stage_runs_dir / f"{index + 1:02d}_{slug}"

    stage_results = [None] * total_stages
    stage_contexts = []
    solve_failed = False
    terminal = False
    terminal_reason = None
    terminal_status = "workflow_fatal"
    terminal_base_status = None
    primary_stage_run_dir = None
    agent_exit_code_out = None
    interrupt_signal = None

    active_index = 0
    first_status = run_workflow_stage_fn(
        app,
        rows[0],
        args,
        skip_unit_ids=[],
        defer_cleanup=True,
        stage_run_dir=_stage_run_dir(active_index),
    )
    if first_status and first_status.get("run_dir"):
        primary_stage_run_dir = str(first_status.get("run_dir"))
        stage_contexts.append(
            {
                "stage_id": stages[0].get("id"),
                "service": stages[0].get("service") or (stages[0].get("case_ref") or {}).get("service"),
                "case": stages[0].get("case") or (stages[0].get("case_ref") or {}).get("case"),
                "case_data": rows[0].get("case_data") or {},
                "namespace_context": rows[0].get("namespace_context") or {},
                "stage_run_dir": str((root / first_status.get("run_dir", "")).resolve()),
            }
        )
    if not first_status or first_status.get("status") != "ready":
        write_stage_fn(workflow_run_dir, "setup_done", detail="status=setup_failed")
        cleanup = workflow_run_final_cleanup_fn(
            stage_contexts,
            workflow_run_dir,
            namespace_values=namespace_values,
        )
        return {
            "status": "setup_failed",
            "workflow": wf_name,
            "workflow_path": workflow.get("path"),
            "run_dir": _run_dir_value(workflow_run_dir),
            "stage": stages[0].get("id"),
            "reason": "stage_setup_failed",
            "last_error": (first_status or {}).get("last_error"),
            "cleanup_status": cleanup.get("status"),
            "cleanup_log": cleanup.get("cleanup_log"),
            "compiled_fingerprint": compiled_artifact.get("fingerprint"),
            "compiled_artifact_path": compile_result.get("artifact_path"),
            "terminal_base_status": "setup_failed",
            "primary_stage_run_dir": primary_stage_run_dir,
        }

    submit_hint = "Create the file `submit.signal` in this directory to submit."
    status = app.run_status()
    run_dir_value = status.get("run_dir")
    if run_dir_value:
        workflow_transition_log_fn(
            transition_log_path,
            f"stage setup ready {stages[0].get('id')} run_dir={run_dir_value}",
        )

    bundle_dir, submit_file, submit_result_file, start_file, _, real_kubectl = prepare_bundle_fn(
        app,
        stages[0].get("case_id"),
        workflow_run_dir,
        args,
        include_workspace_bin=args.sandbox != "docker",
        namespace_context=rows[0].get("namespace_context"),
    )
    submit_ack_file = Path(bundle_dir) / "submit.ack"
    workflow_publish_prompt_and_state_fn(
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
        terminal=False,
        terminal_reason=None,
        render_workflow_prompt_fn=render_workflow_prompt_fn,
        dump_json_fn=dump_json_fn,
        ts_str_fn=ts_str_fn,
    )


    auth_cleanup = None
    if args.agent_auth_path:
        auth_mount, auth_cleanup = prepare_agent_auth_mount_fn(
            args.agent_auth_path,
            args.agent_auth_dest,
        )
        if auth_mount:
            args._agent_auth_mount = auth_mount

    env = os.environ.copy()
    env_real_kubectl = real_kubectl
    if args.sandbox == "docker":
        env_real_kubectl = args.real_kubectl or "/opt/real-kubectl/kubectl"
    elif args.real_kubectl:
        env_real_kubectl = args.real_kubectl
    env.update(
        {
            "KUBECONFIG": str(bundle_dir / "kubeconfig-proxy"),
            "BENCHMARK_ACTION_TRACE_LOG": str(workflow_run_dir / "action_trace.jsonl"),
            "BENCHMARK_SUBMIT_FILE": str(submit_file),
            "BENCHMARK_START_FILE": str(start_file),
            "BENCHMARK_SUBMIT_RESULT_FILE": str(bundle_dir / "submit_result.json"),
            "BENCHMARK_REAL_KUBECTL": env_real_kubectl,
            "BENCHMARK_RUN_DIR": str(workflow_run_dir),
            "BENCHMARK_USAGE_OUTPUT": str(workflow_run_dir / "agent_usage_raw.json"),
        }
    )
    env.update(namespace_env_vars_fn(rows[active_index].get("namespace_context") or {}))
    env["PATH"] = f"{bundle_dir / 'bin'}:{env.get('PATH','')}"

    write_stage_fn(workflow_run_dir, "agent_start")
    agent_proc = None
    stream_stop = threading.Event()
    stream_threads = []
    signal_handlers = []

    def _raise_interrupt(signum, _frame):
        nonlocal interrupt_signal
        interrupt_signal = int(signum)
        raise KeyboardInterrupt()

    signal_handlers = _install_interrupt_handlers(_raise_interrupt)
    try:
        try:
            agent_proc = launch_agent_fn(bundle_dir, env, args)
            if args.manual_start:
                write_stage_fn(workflow_run_dir, "waiting_start")
                start_exit = wait_for_start_signal_fn(start_file, agent_proc=agent_proc, poll=1.0)
                if start_exit is not None:
                    cleanup = workflow_run_final_cleanup_fn(
                        stage_contexts,
                        workflow_run_dir,
                        namespace_values=namespace_values,
                    )
                    return {
                        "status": "agent_failed",
                        "workflow": wf_name,
                        "workflow_path": workflow.get("path"),
                        "run_dir": _run_dir_value(workflow_run_dir),
                        "agent_exit_code": start_exit,
                        "cleanup_status": cleanup.get("status"),
                        "cleanup_log": cleanup.get("cleanup_log"),
                        "compiled_fingerprint": compiled_artifact.get("fingerprint"),
                        "compiled_artifact_path": compile_result.get("artifact_path"),
                        "terminal_base_status": "agent_failed",
                        "primary_stage_run_dir": primary_stage_run_dir,
                    }
                write_stage_fn(workflow_run_dir, "start_received")

            stream_threads.append(
                threading.Thread(target=stream_action_trace_fn, args=(workflow_run_dir, stream_stop), daemon=True)
            )
            stream_threads.append(
                threading.Thread(target=stream_agent_log_fn, args=(workflow_run_dir, stream_stop), daemon=True)
            )
            for thread in stream_threads:
                thread.start()

            def _clear_submit_ack(stage_id=None):
                removed = False
                try:
                    submit_ack_file.unlink()
                    removed = True
                except FileNotFoundError:
                    return False
                except OSError:
                    return False
                if removed:
                    suffix = f" stage_id={stage_id}" if stage_id else ""
                    print(f"[orchestrator] submit_ack_cleared{suffix}", flush=True)
                return removed

            def _clear_stale_submit_result(stage_id=None):
                removed = False
                try:
                    Path(submit_result_file).unlink()
                    removed = True
                except FileNotFoundError:
                    return False
                except OSError:
                    return False
                if removed:
                    suffix = f" stage_id={stage_id}" if stage_id else ""
                    print(f"[orchestrator] submit_result_cleared{suffix}", flush=True)
                return removed

            def _write_submit_ack(stage_id=None):
                payload = {
                    "status": "received",
                    "ts": ts_str_fn(),
                }
                if stage_id:
                    payload["stage_id"] = stage_id
                try:
                    submit_ack_file.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
                except OSError:
                    return False
                suffix = f" stage_id={stage_id}" if stage_id else ""
                print(f"[orchestrator] submit_ack_written{suffix}", flush=True)
                return True

            def _persist_submit_result(result_payload):
                public_payload = result_payload
                if mode == "concat_blind" and isinstance(result_payload, dict):
                    public_payload = dict(result_payload)
                    public_payload.pop("verification_log", None)
                    wf_public = public_payload.get("workflow")
                    if isinstance(wf_public, dict):
                        wf_public = dict(wf_public)
                        for key in (
                            "stage_index",
                            "stage_total",
                            "stage_id",
                            "stage_attempt",
                            "stage_status",
                            "next_stage_id",
                        ):
                            wf_public.pop(key, None)
                        public_payload["workflow"] = wf_public
                write_ok = write_submit_result_fn(submit_result_file, public_payload)
                log_ok = append_submit_result_log_fn(workflow_run_dir, result_payload)
                wf = result_payload.get("workflow") if isinstance(result_payload, dict) else {}
                if isinstance(wf, dict):
                    print(
                        "[orchestrator] submit_result_written "
                        f"stage_id={wf.get('stage_id')} "
                        f"stage_status={wf.get('stage_status')} "
                        f"continue={wf.get('continue')} "
                        f"can_retry={result_payload.get('can_retry')} "
                        f"final={wf.get('final')} "
                        f"reason={wf.get('reason')} "
                        f"write_ok={write_ok} log_ok={log_ok}",
                        flush=True,
                    )
                else:
                    print(
                        f"[orchestrator] submit_result_written write_ok={write_ok} log_ok={log_ok}",
                        flush=True,
                    )
                return write_ok and log_ok

            while True:
                _clear_submit_ack(stage_id=stages[active_index].get("id"))
                write_stage_fn(workflow_run_dir, "waiting_submit")
                print(
                    f"[orchestrator] submit_wait stage_id={stages[active_index].get('id')}",
                    flush=True,
                )
                attempt_payload, agent_exit_code = wait_for_submit_or_agent_fn(
                    submit_file,
                    agent_proc,
                    timeout=args.submit_timeout,
                    poll=1.0,
                    grace=3.0,
                )
                if attempt_payload is None:
                    terminal = True
                    terminal_reason = "agent_exited" if agent_exit_code is not None else "submit_timeout"
                    terminal_status = "workflow_fatal"
                    terminal_base_status = "auto_failed" if agent_exit_code is not None else "submit_timeout"
                    print(
                        "[orchestrator] submit_wait_end "
                        f"stage_id={stages[active_index].get('id')} "
                        f"reason={terminal_reason} "
                        f"agent_exit_code={agent_exit_code}",
                        flush=True,
                    )
                    if agent_exit_code is not None:
                        agent_exit_code_out = agent_exit_code
                    result_payload = workflow_submit_payload_fn(
                        base_status=terminal_base_status,
                        attempt=0,
                        last_error=terminal_reason,
                        verification_log=None,
                        attempts_left=0,
                        time_left_sec=0,
                        can_retry=False,
                        mode=mode,
                        stage_index=active_index + 1,
                        stage_total=total_stages,
                        stage_id=stages[active_index].get("id"),
                        stage_attempt=0,
                        stage_status="fatal_error",
                        continue_flag=False,
                        final_flag=True,
                        next_stage_id=None,
                        reason=terminal_reason,
                    )
                    _persist_submit_result(result_payload)
                    break

                print(
                    "[orchestrator] submit_received "
                    f"stage_id={stages[active_index].get('id')} "
                    f"payload_bytes={len(str(attempt_payload))}",
                    flush=True,
                )

                _clear_stale_submit_result(stage_id=stages[active_index].get("id"))
                _write_submit_ack(stage_id=stages[active_index].get("id"))

                control_action = _submit_control_action(attempt_payload)
                if control_action == "cleanup":
                    terminal = True
                    terminal_reason = "manual_cleanup"
                    terminal_status = "workflow_fatal"
                    terminal_base_status = "auto_failed"
                    print(
                        f"[orchestrator] submit_control stage_id={stages[active_index].get('id')} action=cleanup",
                        flush=True,
                    )
                    result_payload = workflow_submit_payload_fn(
                        base_status="auto_failed",
                        attempt=0,
                        last_error="Manual cleanup requested",
                        verification_log=None,
                        attempts_left=0,
                        time_left_sec=0,
                        can_retry=False,
                        mode=mode,
                        stage_index=active_index + 1,
                        stage_total=total_stages,
                        stage_id=stages[active_index].get("id"),
                        stage_attempt=0,
                        stage_status="fatal_error",
                        continue_flag=False,
                        final_flag=True,
                        next_stage_id=None,
                        reason="manual_cleanup",
                    )
                    _persist_submit_result(result_payload)
                    break

                print(
                    f"[orchestrator] verification_start stage_id={stages[active_index].get('id')}",
                    flush=True,
                )
                submit_state = app.submit_run()
                if submit_state.get("error"):
                    terminal = True
                    terminal_reason = f"submit_error:{submit_state.get('error')}"
                    terminal_status = "workflow_fatal"
                    terminal_base_status = "auto_failed"
                    print(
                        "[orchestrator] verification_error "
                        f"stage_id={stages[active_index].get('id')} "
                        f"error={submit_state.get('error')}",
                        flush=True,
                    )
                    result_payload = workflow_submit_payload_fn(
                        base_status="auto_failed",
                        attempt=0,
                        last_error=submit_state.get("error"),
                        verification_log=None,
                        attempts_left=0,
                        time_left_sec=0,
                        can_retry=False,
                        mode=mode,
                        stage_index=active_index + 1,
                        stage_total=total_stages,
                        stage_id=stages[active_index].get("id"),
                        stage_attempt=0,
                        stage_status="fatal_error",
                        continue_flag=False,
                        final_flag=True,
                        next_stage_id=None,
                        reason="submit_error",
                    )
                    _persist_submit_result(result_payload)
                    break

                final = wait_for_status_fn(
                    app,
                    {"passed", "failed", "auto_failed", "setup_failed"},
                    timeout=args.verify_timeout,
                )
                if not final:
                    terminal = True
                    terminal_reason = "verify_timeout"
                    terminal_status = "workflow_fatal"
                    terminal_base_status = "verify_timeout"
                    print(
                        f"[orchestrator] verification_timeout stage_id={stages[active_index].get('id')}",
                        flush=True,
                    )
                    result_payload = workflow_submit_payload_fn(
                        base_status="verify_timeout",
                        attempt=0,
                        last_error="Verify timeout",
                        verification_log=None,
                        attempts_left=0,
                        time_left_sec=0,
                        can_retry=False,
                        mode=mode,
                        stage_index=active_index + 1,
                        stage_total=total_stages,
                        stage_id=stages[active_index].get("id"),
                        stage_attempt=0,
                        stage_status="fatal_error",
                        continue_flag=False,
                        final_flag=True,
                        next_stage_id=None,
                        reason="verify_timeout",
                    )
                    _persist_submit_result(result_payload)
                    break

                attempts = final.get("attempts", 0) or 0
                max_attempts = final.get("max_attempts") or attempts
                attempts_left = max(0, max_attempts - attempts)
                elapsed = final.get("elapsed_seconds", 0) or 0
                time_limit = final.get("time_limit_seconds", 0) or 0
                time_left = max(0, time_limit - elapsed) if time_limit else 0
                can_retry = final.get("status") == "failed" and attempts_left > 0 and time_left > 0

                stage_status, stage_reason = workflow_status_from_stage_fn(final)
                stage_id = stages[active_index].get("id")
                verification_log = (final.get("verification_logs") or [None])[-1]
                print(
                    "[orchestrator] verification_done "
                    f"stage_id={stage_id} "
                    f"base_status={final.get('status')} "
                    f"stage_status={stage_status} "
                    f"can_retry={can_retry} "
                    f"attempt={attempts} attempts_left={attempts_left}",
                    flush=True,
                )

                if stage_status == "fatal_error":
                    terminal = True
                    terminal_reason = stage_reason
                    terminal_status = "workflow_fatal"
                    terminal_base_status = final.get("status")
                    stage_results[active_index] = {
                        "stage_id": stage_id,
                        "status": "fatal_error",
                        "reason": stage_reason,
                        "attempts": attempts,
                        "run_dir": final.get("run_dir"),
                    }
                    workflow_append_stage_result_fn(
                        stage_results_path,
                        {
                            "stage_id": stage_id,
                            "attempt": attempts,
                            "status": "fatal_error",
                            "reason": stage_reason,
                            "run_dir": final.get("run_dir"),
                            "last_error": final.get("last_error"),
                        },
                    )
                    workflow_publish_prompt_and_state_fn(
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
                        terminal=True,
                        terminal_reason=terminal_reason,
                        render_workflow_prompt_fn=render_workflow_prompt_fn,
                        dump_json_fn=dump_json_fn,
                        ts_str_fn=ts_str_fn,
                    )
                    result_payload = workflow_submit_payload_fn(
                        base_status=final.get("status"),
                        attempt=attempts,
                        last_error=final.get("last_error"),
                        verification_log=verification_log,
                        attempts_left=attempts_left,
                        time_left_sec=time_left,
                        can_retry=False,
                        mode=mode,
                        stage_index=active_index + 1,
                        stage_total=total_stages,
                        stage_id=stage_id,
                        stage_attempt=attempts,
                        stage_status="fatal_error",
                        continue_flag=False,
                        final_flag=True,
                        next_stage_id=None,
                        reason=stage_reason,
                    )
                    _persist_submit_result(result_payload)
                    break

                if can_retry:
                    result_payload = workflow_submit_payload_fn(
                        base_status=final.get("status"),
                        attempt=attempts,
                        last_error=final.get("last_error"),
                        verification_log=verification_log,
                        attempts_left=attempts_left,
                        time_left_sec=time_left,
                        can_retry=True,
                        mode=mode,
                        stage_index=active_index + 1,
                        stage_total=total_stages,
                        stage_id=stage_id,
                        stage_attempt=attempts,
                        stage_status="failed_retryable",
                        continue_flag=False,
                        final_flag=False,
                        next_stage_id=None,
                        reason="oracle_failed_retryable",
                    )
                    _persist_submit_result(result_payload)
                    continue

                terminal_stage_status = "passed" if stage_status == "passed" else "failed_exhausted"
                if terminal_stage_status != "passed":
                    solve_failed = True
                stage_results[active_index] = {
                    "stage_id": stage_id,
                    "status": terminal_stage_status,
                    "reason": stage_reason,
                    "attempts": attempts,
                    "run_dir": final.get("run_dir"),
                }
                workflow_append_stage_result_fn(
                    stage_results_path,
                    {
                        "stage_id": stage_id,
                        "attempt": attempts,
                        "status": terminal_stage_status,
                        "reason": stage_reason,
                        "run_dir": final.get("run_dir"),
                        "last_error": final.get("last_error"),
                    },
                )

                if terminal_stage_status != "passed" and stage_failure_mode == "terminate":
                    terminal = True
                    terminal_reason = "stage_failed_terminate"
                    terminal_status = "failed"
                    terminal_base_status = final.get("status")
                    workflow_publish_prompt_and_state_fn(
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
                        terminal=True,
                        terminal_reason=terminal_reason,
                        render_workflow_prompt_fn=render_workflow_prompt_fn,
                        dump_json_fn=dump_json_fn,
                        ts_str_fn=ts_str_fn,
                    )
                    result_payload = workflow_submit_payload_fn(
                        base_status=final.get("status"),
                        attempt=attempts,
                        last_error=final.get("last_error"),
                        verification_log=verification_log,
                        attempts_left=0,
                        time_left_sec=0,
                        can_retry=False,
                        mode=mode,
                        stage_index=active_index + 1,
                        stage_total=total_stages,
                        stage_id=stage_id,
                        stage_attempt=attempts,
                        stage_status=terminal_stage_status,
                        continue_flag=False,
                        final_flag=True,
                        next_stage_id=None,
                        reason="stage_failed_nonretryable_terminate",
                    )
                    _persist_submit_result(result_payload)
                    break

                if active_index + 1 >= total_stages:
                    terminal = True
                    terminal_reason = "workflow_complete"
                    terminal_status = "failed" if solve_failed else "passed"
                    terminal_base_status = final.get("status")
                    workflow_publish_prompt_and_state_fn(
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
                        terminal=True,
                        terminal_reason=terminal_reason,
                        render_workflow_prompt_fn=render_workflow_prompt_fn,
                        dump_json_fn=dump_json_fn,
                        ts_str_fn=ts_str_fn,
                    )
                    result_payload = workflow_submit_payload_fn(
                        base_status=final.get("status"),
                        attempt=attempts,
                        last_error=final.get("last_error"),
                        verification_log=verification_log,
                        attempts_left=0,
                        time_left_sec=0,
                        can_retry=False,
                        mode=mode,
                        stage_index=active_index + 1,
                        stage_total=total_stages,
                        stage_id=stage_id,
                        stage_attempt=attempts,
                        stage_status=terminal_stage_status,
                        continue_flag=False,
                        final_flag=True,
                        next_stage_id=None,
                        reason="stage_terminal",
                    )
                    _persist_submit_result(result_payload)
                    break

                next_index = active_index + 1
                next_stage = stages[next_index]
                skip_ids = []
                workflow_transition_log_fn(
                    transition_log_path,
                    f"advance {stage_id} -> {next_stage.get('id')} skip_units={skip_ids}",
                )
                next_setup = run_workflow_stage_fn(
                    app,
                    rows[next_index],
                    args,
                    skip_unit_ids=skip_ids,
                    defer_cleanup=True,
                    stage_run_dir=_stage_run_dir(next_index),
                )
                if next_setup and next_setup.get("run_dir"):
                    stage_contexts.append(
                        {
                            "stage_id": next_stage.get("id"),
                            "service": next_stage.get("service") or (next_stage.get("case_ref") or {}).get("service"),
                            "case": next_stage.get("case") or (next_stage.get("case_ref") or {}).get("case"),
                            "case_data": rows[next_index].get("case_data") or {},
                            "namespace_context": rows[next_index].get("namespace_context") or {},
                            "stage_run_dir": str((root / next_setup.get("run_dir", "")).resolve()),
                        }
                    )
                if not next_setup or next_setup.get("status") != "ready":
                    terminal = True
                    terminal_reason = "next_stage_setup_failed"
                    terminal_status = "workflow_fatal"
                    terminal_base_status = "setup_failed"
                    workflow_append_stage_result_fn(
                        stage_results_path,
                        {
                            "stage_id": next_stage.get("id"),
                            "attempt": 0,
                            "status": "fatal_error",
                            "reason": "stage_setup_failed",
                            "run_dir": (next_setup or {}).get("run_dir"),
                            "last_error": (next_setup or {}).get("last_error"),
                        },
                    )
                    workflow_publish_prompt_and_state_fn(
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
                        terminal=True,
                        terminal_reason=terminal_reason,
                        render_workflow_prompt_fn=render_workflow_prompt_fn,
                        dump_json_fn=dump_json_fn,
                        ts_str_fn=ts_str_fn,
                    )
                    result_payload = workflow_submit_payload_fn(
                        base_status="setup_failed",
                        attempt=0,
                        last_error=(next_setup or {}).get("last_error"),
                        verification_log=None,
                        attempts_left=0,
                        time_left_sec=0,
                        can_retry=False,
                        mode=mode,
                        stage_index=next_index + 1,
                        stage_total=total_stages,
                        stage_id=next_stage.get("id"),
                        stage_attempt=0,
                        stage_status="fatal_error",
                        continue_flag=False,
                        final_flag=True,
                        next_stage_id=None,
                        reason=terminal_reason,
                    )
                    _persist_submit_result(result_payload)
                    break
                active_index = next_index
                workflow_publish_prompt_and_state_fn(
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
                    terminal=False,
                    terminal_reason=None,
                    render_workflow_prompt_fn=render_workflow_prompt_fn,
                    dump_json_fn=dump_json_fn,
                    ts_str_fn=ts_str_fn,
                )
                result_payload = workflow_submit_payload_fn(
                    base_status=final.get("status"),
                    attempt=attempts,
                    last_error=final.get("last_error"),
                    verification_log=verification_log,
                    attempts_left=0,
                    time_left_sec=0,
                    can_retry=False,
                    mode=mode,
                    stage_index=active_index,
                    stage_total=total_stages,
                    stage_id=stage_id,
                    stage_attempt=attempts,
                    stage_status=terminal_stage_status,
                    continue_flag=True,
                    final_flag=False,
                    next_stage_id=next_stage.get("id"),
                    reason="advance_next_stage",
                )
                _persist_submit_result(result_payload)
        except KeyboardInterrupt:
            terminal = True
            terminal_reason = "interrupted"
            terminal_status = "workflow_fatal"
            terminal_base_status = "interrupted"
            signal_text = f"signal={interrupt_signal}" if interrupt_signal is not None else "signal=keyboard_interrupt"
            print(
                "[orchestrator] interrupt_received "
                f"stage_id={stages[active_index].get('id')} {signal_text}",
                flush=True,
            )
            try:
                workflow_publish_prompt_and_state_fn(
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
                    terminal=True,
                    terminal_reason=terminal_reason,
                    render_workflow_prompt_fn=render_workflow_prompt_fn,
                    dump_json_fn=dump_json_fn,
                    ts_str_fn=ts_str_fn,
                )
                result_payload = workflow_submit_payload_fn(
                    base_status="auto_failed",
                    attempt=0,
                    last_error="Workflow interrupted",
                    verification_log=None,
                    attempts_left=0,
                    time_left_sec=0,
                    can_retry=False,
                    mode=mode,
                    stage_index=active_index + 1,
                    stage_total=total_stages,
                    stage_id=stages[active_index].get("id"),
                    stage_attempt=0,
                    stage_status="fatal_error",
                    continue_flag=False,
                    final_flag=True,
                    next_stage_id=None,
                    reason="interrupted",
                )
                _persist_submit_result(result_payload)
            except Exception:
                pass
        finally:
            stream_stop.set()
            for thread in stream_threads:
                thread.join(timeout=1.0)
            if auth_cleanup:
                auth_cleanup()
            if hasattr(args, "_agent_auth_mount"):
                args._agent_auth_mount = None
            terminate_agent_fn(agent_proc)
    finally:
        _restore_interrupt_handlers(signal_handlers)

    write_stage_fn(
        workflow_run_dir,
        "final_sweep",
        detail=f"stages={len(rows)} mode={final_sweep_mode}",
    )
    final_sweep = {}
    observed_final = {}
    final_sweep_status = "completed"
    final_sweep_reason = "compile_removed"
    if final_sweep_mode == "off":
        final_sweep_status = "skipped"
        final_sweep_reason = "disabled_by_config"
        print(
            f"[orchestrator] final sweep skipped stages={len(rows)} mode=off",
            flush=True,
        )
    elif terminal_reason in ("manual_cleanup", "interrupted"):
        final_sweep_status = "skipped"
        final_sweep_reason = "terminated_early"
        print(
            f"[orchestrator] final sweep skipped stages={len(rows)} reason={final_sweep_reason}",
            flush=True,
        )
    else:
        print(f"[orchestrator] final sweep start stages={len(rows)}", flush=True)
        final_sweep = workflow_run_final_sweep_fn(rows, workflow_run_dir)
        observed_final = {sid: str((payload or {}).get("status") or "") for sid, payload in final_sweep.items()}
        pass_count = sum(1 for status in observed_final.values() if status == "pass")
        fail_count = sum(1 for status in observed_final.values() if status == "fail")
        other_count = max(0, len(observed_final) - pass_count - fail_count)
        print(
            "[orchestrator] final sweep done "
            f"stages={len(observed_final)} pass={pass_count} fail={fail_count} other={other_count}",
            flush=True,
        )
    expected_final = {}
    regression = {}
    final_sweep_path = workflow_run_dir / "workflow_final_sweep.json"
    dump_json_fn(
        final_sweep_path,
        {
            "status": final_sweep_status,
            "mode": final_sweep_mode,
            "reason": final_sweep_reason,
            "expected_final": expected_final,
            "observed_final": observed_final,
            "regression": regression,
            "regression_analysis": {
                "status": "not_available",
                "reason": final_sweep_reason,
            },
            "details": final_sweep,
        },
    )

    write_stage_fn(
        workflow_run_dir,
        "workflow_cleanup",
        detail=f"stages={len(stage_contexts)} namespaces={len(namespace_values)}",
    )
    print(
        "[orchestrator] cleanup_start "
        f"stages={len(stage_contexts)} namespaces={len(namespace_values)}",
        flush=True,
    )
    cleanup = workflow_run_final_cleanup_fn(
        stage_contexts,
        workflow_run_dir,
        namespace_values=namespace_values,
    )
    print(f"[orchestrator] cleanup_done status={cleanup.get('status')}", flush=True)
    write_stage_fn(workflow_run_dir, "done", detail=f"status={terminal_status}")

    return attach_agent_usage_fields_fn(
        {
            "status": terminal_status,
            "workflow": wf_name,
            "workflow_path": workflow.get("path"),
            "run_dir": _run_dir_value(workflow_run_dir),
            "workflow_state_path": str((workflow_run_dir / "workflow_state.json").relative_to(root)),
            "workflow_stage_results_path": str(stage_results_path.relative_to(root)),
            "workflow_transition_log": str(transition_log_path.relative_to(root)),
            "workflow_final_sweep_path": str(final_sweep_path.relative_to(root)),
            "workflow_final_sweep_mode": final_sweep_mode,
            "workflow_stage_failure_mode": stage_failure_mode,
            "effective_params_path": str(effective_params_path.relative_to(root)),
            "cleanup_status": cleanup.get("status"),
            "cleanup_log": cleanup.get("cleanup_log"),
            "solve_failed": solve_failed,
            "terminal_reason": terminal_reason,
            "terminal_base_status": terminal_base_status,
            "primary_stage_run_dir": primary_stage_run_dir,
            "agent_exit_code": agent_exit_code_out,
            "compiled_fingerprint": compiled_artifact.get("fingerprint"),
            "compiled_artifact_path": compile_result.get("artifact_path"),
            "compiled_status": compile_result.get("status"),
        }
    )
