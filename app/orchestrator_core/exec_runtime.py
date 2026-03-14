from __future__ import annotations

import subprocess
import time
from pathlib import Path

from app.orchestrator_core.namespace_runtime import prepare_exec_command
from app.settings import ROOT
from app.util import (
    command_to_string,
    infer_command_timeout_seconds,
    list_requires_shell,
    normalize_commands,
    parse_duration_seconds,
    safe_join,
    ts_str,
)


def wait_for_status(app, target_states, timeout=None, poll=1.0, *, time_module=time):
    start = time_module.time()
    while True:
        status = app.run_status()
        state = status.get("status")
        if state in target_states:
            return status
        if timeout and time_module.time() - start > timeout:
            return None
        time_module.sleep(poll)


def wait_for_cleanup(app, timeout=None, poll=1.0, log_every=0, *, print_fn=print, time_module=time):
    start = time_module.time()
    last_log = 0.0
    while True:
        status = app.run_status()
        cleanup_status = status.get("cleanup_status")
        if cleanup_status in ("done", "failed"):
            return status
        if cleanup_status is None and status.get("cleanup_log") is None:
            return status
        if status.get("status") not in ("passed", "auto_failed") and cleanup_status is None:
            return status
        if timeout and time_module.time() - start > timeout:
            return status
        if log_every and time_module.time() - last_log >= log_every:
            last_log = time_module.time()
            print_fn(
                "[orchestrator] waiting for cleanup "
                f"status={status.get('status')} cleanup={cleanup_status} "
                f"log={status.get('cleanup_log')}",
                flush=True,
            )
        time_module.sleep(poll)


def wait_for_idle(app, poll=1.0, log_every=60, *, print_fn=print, time_module=time):
    last_log = 0.0
    while True:
        status = app.run_status()
        state = status.get("status")
        cleanup_status = status.get("cleanup_status")

        active = state in ("setup_running", "ready", "verifying")
        cleanup_block = cleanup_status in ("running", "failed")

        if not active and not cleanup_block:
            if cleanup_status is None and state in ("passed", "auto_failed", "failed", "setup_failed"):
                result = app.cleanup_run()
                if result.get("status") in ("no_cleanup", "already_cleaned", "skipped"):
                    return status
            elif state == "idle":
                return status
            elif cleanup_status == "done":
                return status
            else:
                return status

        if log_every and time_module.time() - last_log >= log_every:
            last_log = time_module.time()
            print_fn(
                "[orchestrator] waiting for idle "
                f"status={state} cleanup={cleanup_status} log={status.get('cleanup_log')}",
                flush=True,
            )
        time_module.sleep(poll)


def resolve_step_timeout(
    item,
    default_sec=300,
    *,
    parse_duration_seconds_fn=parse_duration_seconds,
    infer_command_timeout_seconds_fn=infer_command_timeout_seconds,
):
    raw = item.get("timeout_sec")
    parsed = parse_duration_seconds_fn(raw) if raw is not None else None
    if parsed is not None and parsed > 0:
        return int(parsed)
    inferred = infer_command_timeout_seconds_fn(item.get("command"))
    if inferred:
        return int(inferred) + 30
    return int(default_sec)


def append_log_line(path, line):
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as handle:
        handle.write(str(line) + "\n")


def run_command_list_logged(
    commands,
    log_path,
    default_timeout=300,
    fail_fast=True,
    namespace_context=None,
    *,
    normalize_commands_fn=normalize_commands,
    prepare_exec_command_fn=prepare_exec_command,
    resolve_step_timeout_fn=resolve_step_timeout,
    command_to_string_fn=command_to_string,
    append_log_line_fn=append_log_line,
    ts_str_fn=ts_str,
    list_requires_shell_fn=list_requires_shell,
    safe_join_fn=safe_join,
    subprocess_module=subprocess,
    time_module=time,
    cwd=ROOT,
):
    cmds = normalize_commands_fn(commands)
    if not cmds:
        return True, None, None
    render_dir = Path(log_path).parent / "rendered_manifests"
    for idx, item in enumerate(cmds, start=1):
        command, env = prepare_exec_command_fn(item, namespace_context, render_dir=render_dir)
        if command is None:
            continue
        timeout_sec = resolve_step_timeout_fn(item, default_sec=default_timeout)
        cmd_text = command_to_string_fn(command)
        append_log_line_fn(
            log_path,
            f"[{ts_str_fn()}] COMMAND {idx}/{len(cmds)}: {cmd_text} (timeout={timeout_sec}s)",
        )
        try:
            if isinstance(command, list):
                if list_requires_shell_fn(command):
                    proc = subprocess_module.run(
                        safe_join_fn(command),
                        cwd=cwd,
                        text=True,
                        stdout=subprocess_module.PIPE,
                        stderr=subprocess_module.PIPE,
                        env=env,
                        shell=True,
                        check=False,
                        timeout=timeout_sec,
                    )
                else:
                    proc = subprocess_module.run(
                        command,
                        cwd=cwd,
                        text=True,
                        stdout=subprocess_module.PIPE,
                        stderr=subprocess_module.PIPE,
                        env=env,
                        shell=False,
                        check=False,
                        timeout=timeout_sec,
                    )
            else:
                proc = subprocess_module.run(
                    str(command),
                    cwd=cwd,
                    text=True,
                    stdout=subprocess_module.PIPE,
                    stderr=subprocess_module.PIPE,
                    env=env,
                    shell=True,
                    check=False,
                    timeout=timeout_sec,
                )
        except subprocess_module.TimeoutExpired:
            append_log_line_fn(log_path, f"[{ts_str_fn()}] ERROR: Command timed out after {timeout_sec}s")
            if fail_fast:
                return False, "timeout", f"timeout after {timeout_sec}s"
            continue
        except Exception as exc:  # noqa: BLE001
            append_log_line_fn(log_path, f"[{ts_str_fn()}] ERROR: {exc}")
            if fail_fast:
                return False, "error", str(exc)
            continue

        if proc.stdout:
            append_log_line_fn(log_path, proc.stdout.rstrip())
        if proc.stderr:
            append_log_line_fn(log_path, proc.stderr.rstrip())
        append_log_line_fn(log_path, f"[{ts_str_fn()}] EXIT {proc.returncode}")
        if proc.returncode != 0:
            message = f"exit={proc.returncode}"
            if fail_fast:
                return False, "nonzero", message
            continue
        sleep_seconds = item.get("sleep", 0) or 0
        if sleep_seconds:
            time_module.sleep(float(sleep_seconds))
    return True, None, None
