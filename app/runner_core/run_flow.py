import json
import threading
import time
from pathlib import Path
from subprocess import PIPE, TimeoutExpired, run

from ..decoys import build_decoy_commands, list_decoy_files
from ..oracle import resolve_oracle_verify
from ..preconditions import normalize_precondition_units
from ..settings import RESOURCES_DIR, ROOT
from ..util import (
    command_to_string,
    list_requires_shell,
    normalize_commands,
    normalize_metrics,
    safe_join,
    ts_str,
)


def command_list_budget_seconds(app, cmds, stage):
    total = 0
    for item in cmds or []:
        if not isinstance(item, dict):
            continue
        if item.get("command") is None:
            continue
        timeout_sec = app._resolve_step_timeout_sec(item, stage)
        total += int(timeout_sec) if timeout_sec else 0
        sleep_seconds = item.get("sleep", 0) or 0
        try:
            total += int(float(sleep_seconds))
        except Exception:
            pass
    return int(total)


def extract_precondition_check_config(data):
    setup_self_check = (data or {}).get("setup_self_check") or {}
    if not isinstance(setup_self_check, dict):
        return None
    candidate = setup_self_check.get("precondition_check")
    if isinstance(candidate, dict):
        return candidate
    legacy_keys = {
        "commands",
        "mode",
        "budget_sec",
        "budgetSec",
        "poll_sec",
        "pollSec",
        "consecutive_passes",
        "consecutivePasses",
    }
    if any(key in setup_self_check for key in legacy_keys):
        return setup_self_check
    return None


def derive_precondition_check_from_units(app, data, precondition_units=None):
    units = precondition_units if precondition_units is not None else app._resolve_precondition_units(data)
    if not units:
        return None

    commands = []
    seen = set()
    for unit in units:
        for item in normalize_commands(unit.get("probe_commands")):
            command = item.get("command")
            if command is None:
                continue
            signature = (
                f"{command_to_string(command)}::{item.get('timeout_sec')}::{item.get('sleep', 0)}::"
                f"{item.get('namespace_role')}"
            )
            if signature in seen:
                continue
            seen.add(signature)
            commands.append(item)
    if not commands:
        return None

    return {
        "mode": "required",
        "budget_sec": 0,
        "poll_sec": 5,
        "consecutive_passes": 1,
        "commands": commands,
    }


def resolve_precondition_check_config(app, data, precondition_units=None):
    raw = extract_precondition_check_config(data)
    if raw:
        return app._normalize_setup_check_config(raw)
    return app._derive_precondition_check_from_units(data, precondition_units=precondition_units)


def normalize_setup_check_config(raw, default_mode="required"):
    if not isinstance(raw, dict):
        return None
    mode = str(raw.get("mode") or default_mode).strip().lower()
    if mode not in ("required", "warn", "off"):
        mode = default_mode
    try:
        budget_sec = int(raw.get("budget_sec") if raw.get("budget_sec") is not None else raw.get("budgetSec") or 0)
    except Exception:
        budget_sec = 0
    try:
        poll_sec = int(raw.get("poll_sec") if raw.get("poll_sec") is not None else raw.get("pollSec") or 5)
    except Exception:
        poll_sec = 5
    try:
        consecutive_passes = int(
            raw.get("consecutive_passes")
            if raw.get("consecutive_passes") is not None
            else raw.get("consecutivePasses")
            or 1
        )
    except Exception:
        consecutive_passes = 1
    commands = normalize_commands(raw.get("commands"))
    return {
        "mode": mode,
        "budget_sec": max(0, budget_sec),
        "poll_sec": max(1, poll_sec),
        "consecutive_passes": max(1, consecutive_passes),
        "commands": commands,
    }


def estimate_check_budget_seconds(app, cfg, stage):
    if not cfg:
        return 0
    if cfg.get("mode") == "off":
        return 0
    commands = cfg.get("commands") or []
    if not commands:
        return 0
    budget_sec = int(cfg.get("budget_sec") or 0)
    if budget_sec > 0:
        return budget_sec
    base = app._command_list_budget_seconds(commands, stage)
    if base <= 0:
        base = 30
    poll = int(cfg.get("poll_sec") or 1)
    streak = int(cfg.get("consecutive_passes") or 1)
    return int(base + (poll * max(1, streak)))


def resolve_precondition_units(data, raise_on_invalid=False):
    try:
        return normalize_precondition_units(data or {})
    except Exception as exc:
        if raise_on_invalid:
            raise RuntimeError(f"Invalid preconditionUnits: {exc}") from exc
        return None


def precondition_units_budget_seconds(app, units):
    total = 0
    for unit in units or []:
        probe_sec = app._command_list_budget_seconds(unit.get("probe_commands"), "setup")
        apply_sec = app._command_list_budget_seconds(unit.get("apply_commands"), "setup")
        verify_once_sec = app._command_list_budget_seconds(unit.get("verify_commands"), "setup")
        retries = max(1, int(unit.get("verify_retries") or 1))
        interval_sec = max(0, int(float(unit.get("verify_interval_sec") or 0)))
        total += probe_sec + apply_sec + (verify_once_sec * retries) + (interval_sec * max(0, retries - 1))
    return int(total)


def compute_setup_timeout_auto(app, data):
    """
    Compute an upper bound for setup wall-clock time.

    This is used by the headless orchestrator to avoid timing out while a case
    is legitimately waiting on long `kubectl wait --timeout=...` steps.

    Formula:
    - sum(per-command timeout + sleep) over setup apply steps
      (preconditionUnits if configured; otherwise preOperationCommands)
    - + estimated decoy apply timeouts (when enabled)
    - + setup precondition-check budget (if configured)
    - + slack
    """
    precondition_units = app._resolve_precondition_units(data)
    if precondition_units:
        preop_sec = app._precondition_units_budget_seconds(precondition_units)
    else:
        preop_cmds = normalize_commands((data or {}).get("preOperationCommands"))
        preop_sec = app._command_list_budget_seconds(preop_cmds, "setup")

    decoy_sec = 0
    metrics = normalize_metrics((data or {}).get("externalMetrics"))
    if "decoy_integrity" in metrics:
        case_dir = RESOURCES_DIR / (app.run_state.get("service") or "") / (app.run_state.get("case") or "")
        decoy_files = list_decoy_files(case_dir)
        if decoy_files:
            decoy_cmds = build_decoy_commands(decoy_files, "apply")
            decoy_sec = app._command_list_budget_seconds(decoy_cmds, "decoy")

    precondition_cfg = app._resolve_precondition_check_config(data, precondition_units=precondition_units)
    precondition_check_sec = app._estimate_check_budget_seconds(precondition_cfg, "setup_check")

    slack_sec = 60
    if precondition_cfg and precondition_cfg.get("poll_sec"):
        slack_sec += int(precondition_cfg.get("poll_sec"))

    total = int(preop_sec + decoy_sec + precondition_check_sec + slack_sec)
    breakdown = {
        "preoperation_sec": preop_sec,
        "decoy_sec": decoy_sec,
        "precondition_check_sec": precondition_check_sec,
        "slack_sec": slack_sec,
    }
    return total, breakdown


def set_setup_phase(app, phase):
    phase_labels = {
        "precondition_apply": "Precondition Apply",
        "precondition_check": "Precondition Check",
        "decoy_apply": "Decoy Apply",
        "ready": "Ready",
    }
    with app.run_lock:
        if app.run_state.get("status") != "setup_running":
            return
        app.run_state["setup_phase"] = phase
        if phase:
            app.run_state["current_step"] = f"phase:{phase_labels.get(phase, phase)}"
        app._write_meta()


def record_setup_warning(app, warning):
    if not warning:
        return
    with app.run_lock:
        warnings = app.run_state.get("setup_warnings") or []
        warnings.append(str(warning))
        app.run_state["setup_warnings"] = warnings
        app._write_meta()


def write_setup_checks_summary(app, records):
    run_dir = app.run_state.get("run_dir")
    if not run_dir:
        return
    path = ROOT / run_dir / "setup_checks.json"
    payload = {
        "generated_at": ts_str(),
        "checks": records or [],
        "warnings": list(app.run_state.get("setup_warnings") or []),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    with app.run_lock:
        app.run_state["setup_checks_path"] = str(path.relative_to(ROOT))
        app._write_meta()


def fail_setup(app, reason=None):
    with app.run_lock:
        if app.run_state.get("status") != "setup_running":
            return
        if reason:
            app.run_state["last_error"] = str(reason)
        app.run_state["status"] = "setup_failed"
        app.run_state["current_step"] = None
        app._set_timestamp("finished_at")
        app._write_meta()
    app._stop_proxy_trace()
    app._maybe_compute_metrics()
    app._maybe_start_cleanup()


def run_setup_check_loop(app, check_id, cfg, log_path, stage, records):
    mode = cfg.get("mode")
    commands = cfg.get("commands") or []
    record = {
        "id": check_id,
        "mode": mode,
        "stage": stage,
        "result": "skipped",
        "attempts": 0,
        "budget_sec": int(cfg.get("budget_sec") or 0),
        "poll_sec": int(cfg.get("poll_sec") or 0),
        "consecutive_passes": int(cfg.get("consecutive_passes") or 1),
        "log": str(log_path.relative_to(ROOT)),
    }

    if mode == "off":
        record["result"] = "skipped"
        records.append(record)
        return True

    if not commands:
        msg = f"{check_id}: no commands configured"
        if mode == "required":
            record["result"] = "failed"
            record["error"] = msg
            records.append(record)
            app._fail_setup(msg)
            return False
        record["result"] = "warn"
        record["warning"] = msg
        records.append(record)
        app._record_setup_warning(msg)
        return True

    budget_sec = int(cfg.get("budget_sec") or 0)
    if budget_sec <= 0:
        budget_sec = app._estimate_check_budget_seconds(cfg, stage)
    poll_sec = int(cfg.get("poll_sec") or 5)
    consecutive_required = int(cfg.get("consecutive_passes") or 1)
    started = time.time()
    streak = 0

    while True:
        with app.run_lock:
            if app.run_state.get("status") != "setup_running":
                record["result"] = "aborted"
                records.append(record)
                return False
            app.run_state["current_step"] = (
                f"{stage}:{check_id} attempt={record['attempts'] + 1}"
            )
            app._write_meta()
        record["attempts"] += 1
        app._append_log(
            log_path,
            f"[{ts_str()}] CHECK {check_id} attempt={record['attempts']} "
            f"streak={streak}/{consecutive_required} budget={budget_sec}s",
        )
        ok = app._run_command_list_stateless(commands, log_path, stage=stage)
        if ok:
            streak += 1
            if streak >= consecutive_required:
                record["result"] = "passed"
                record["elapsed_sec"] = int(time.time() - started)
                records.append(record)
                with app.run_lock:
                    if app.run_state.get("status") == "setup_running":
                        app.run_state["current_step"] = f"{stage}:{check_id} passed"
                        app._write_meta()
                return True
        else:
            streak = 0

        elapsed = time.time() - started
        remaining = budget_sec - elapsed
        if remaining <= 0:
            break
        time.sleep(min(poll_sec, max(0, remaining)))

    record["elapsed_sec"] = int(time.time() - started)
    msg = f"{check_id}: did not pass within setup check budget ({budget_sec}s)"
    if mode == "required":
        record["result"] = "failed"
        record["error"] = msg
        records.append(record)
        app._fail_setup(msg)
        return False
    record["result"] = "warn"
    record["warning"] = msg
    records.append(record)
    app._record_setup_warning(msg)
    return True


def run_precondition_check(app, records, precondition_units=None):
    with app.run_lock:
        data = app.run_state.get("data") or {}
        run_dir = app.run_state.get("run_dir")
    if not run_dir:
        return True

    cfg = app._resolve_precondition_check_config(data, precondition_units=precondition_units)
    if not cfg:
        warning = "setup_self_check.precondition_check is not configured; skipping precondition check"
        records.append(
            {
                "id": "precondition_check",
                "mode": "off",
                "stage": "setup_check",
                "result": "skipped",
                "warning": warning,
            }
        )
        app._record_setup_warning(warning)
        return True

    log_path = ROOT / run_dir / "setup_precondition_check.log"
    return app._run_setup_check_loop(
        "precondition_check",
        cfg,
        log_path,
        stage="setup_check",
        records=records,
    )


def run_probe_command_list(app, cmds, log_path, stage, label):
    commands = normalize_commands(cmds)
    if not commands:
        return False, "probe commands are empty"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(commands)
    for idx, item in enumerate(commands, start=1):
        command, env = app._prepare_exec_item(item)
        if command is None:
            return False, "probe command is missing"

        with app.run_lock:
            app.run_state["current_step"] = f"{stage}:{label}:{idx}/{total}"
        cmd_str = command_to_string(command)
        timeout_sec = app._resolve_step_timeout_sec(item, stage)
        app._append_log(
            log_path,
            f"[{ts_str()}] PROBE {label} COMMAND {idx}/{total}: {cmd_str} (timeout={timeout_sec}s)",
        )
        try:
            if isinstance(command, list):
                if list_requires_shell(command):
                    result = run(
                        safe_join(command),
                        cwd=ROOT,
                        text=True,
                        stdout=PIPE,
                        stderr=PIPE,
                        env=env,
                        shell=True,
                        check=False,
                        timeout=timeout_sec,
                    )
                else:
                    result = run(
                        command,
                        cwd=ROOT,
                        text=True,
                        stdout=PIPE,
                        stderr=PIPE,
                        env=env,
                        check=False,
                        timeout=timeout_sec,
                    )
            else:
                result = run(
                    str(command),
                    cwd=ROOT,
                    text=True,
                    stdout=PIPE,
                    stderr=PIPE,
                    env=env,
                    check=False,
                    shell=True,
                    timeout=timeout_sec,
                )
        except TimeoutExpired:
            app._append_log(log_path, f"[{ts_str()}] ERROR: Probe command timed out after {timeout_sec}s")
            with app.run_lock:
                app.run_state["current_step"] = None
            return False, f"probe timeout after {timeout_sec}s"
        except Exception as exc:  # noqa: BLE001
            app._append_log(log_path, f"[{ts_str()}] ERROR: {exc}")
            with app.run_lock:
                app.run_state["current_step"] = None
            return False, str(exc)

        if result.stdout:
            app._append_log(log_path, result.stdout.rstrip())
        if result.stderr:
            app._append_log(log_path, result.stderr.rstrip())
        app._append_log(log_path, f"[{ts_str()}] EXIT {result.returncode}")
        if int(result.returncode) != 0:
            with app.run_lock:
                app.run_state["current_step"] = None
            return False, f"exit={result.returncode}"

        sleep_seconds = item.get("sleep", 0) or 0
        if sleep_seconds:
            time.sleep(float(sleep_seconds))
    with app.run_lock:
        app.run_state["current_step"] = None
    return True, None


def run_precondition_units(app, units, log_path):
    for idx, unit in enumerate(units or [], start=1):
        unit_id = unit.get("id") or f"unit_{idx}"
        probe_ok, probe_err = app._run_probe_command_list(
            unit.get("probe_commands"),
            log_path,
            stage="setup",
            label=f"{unit_id}:probe",
        )
        if probe_ok:
            app._append_log(log_path, f"[{ts_str()}] PRECONDITION {unit_id}: already satisfied")
            continue

        app._append_log(
            log_path,
            f"[{ts_str()}] PRECONDITION {unit_id}: not satisfied ({probe_err or 'non-zero'}) -> apply",
        )
        ok = app._run_command_list(unit.get("apply_commands"), log_path, stage="setup")
        if not ok:
            return False

        retries = max(1, int(unit.get("verify_retries") or 1))
        interval_sec = max(0.0, float(unit.get("verify_interval_sec") or 0.0))
        passed = False
        last_err = None
        for attempt in range(1, retries + 1):
            app._append_log(log_path, f"[{ts_str()}] PRECONDITION {unit_id}: verify {attempt}/{retries}")
            passed, last_err = app._run_probe_command_list(
                unit.get("verify_commands"),
                log_path,
                stage="setup",
                label=f"{unit_id}:verify",
            )
            if passed:
                break
            if attempt < retries and interval_sec > 0:
                time.sleep(interval_sec)
        if not passed:
            with app.run_lock:
                app.run_state["last_error"] = (
                    f"precondition unit '{unit_id}' verify failed after {retries} attempt(s): "
                    f"{last_err or 'non-zero'}"
                )
                app._write_meta()
            return False
    return True


def run_setup(app):
    check_records = []
    if app._needs_residual_drift():
        path = app._capture_snapshot_file("base")
        if path:
            with app.run_lock:
                app.run_state["snapshot_base"] = str(path.relative_to(ROOT))
                app._write_meta()

    app._set_setup_phase("precondition_apply")
    data = app.run_state.get("data", {}) or {}
    log_path = ROOT / app.run_state["setup_log"]
    try:
        precondition_units = app._resolve_precondition_units(data, raise_on_invalid=True)
        skip_ids = {
            str(item).strip()
            for item in (app.run_state.get("skip_precondition_unit_ids") or [])
            if str(item).strip()
        }
        if precondition_units and skip_ids:
            original_count = len(precondition_units)
            precondition_units = [
                unit
                for unit in precondition_units
                if str(unit.get("id") or "").strip() not in skip_ids
            ]
            skipped_count = original_count - len(precondition_units)
            if skipped_count > 0:
                app._record_setup_warning(
                    f"workflow carryover: skipped {skipped_count} precondition unit(s): {', '.join(sorted(skip_ids))}"
                )
    except RuntimeError as exc:
        with app.run_lock:
            app.run_state["last_error"] = str(exc)
            app._write_meta()
        app._append_log(log_path, f"[{ts_str()}] ERROR: {exc}")
        app._fail_setup()
        app._write_setup_checks_summary(check_records)
        return
    if precondition_units:
        if normalize_commands(data.get("preOperationCommands")):
            app._record_setup_warning(
                "preconditionUnits detected: preOperationCommands are ignored for setup apply"
            )
        ok = app._run_precondition_units(precondition_units, log_path)
    else:
        cmds = normalize_commands(data.get("preOperationCommands"))
        ok = app._run_command_list(cmds, log_path, stage="setup")
    with app.run_lock:
        still_running = app.run_state["status"] == "setup_running"
    if not still_running:
        app._write_setup_checks_summary(check_records)
        return
    if not ok:
        app._fail_setup()
        app._write_setup_checks_summary(check_records)
        return

    app._set_setup_phase("precondition_check")
    if not app._run_precondition_check(check_records, precondition_units=precondition_units):
        app._write_setup_checks_summary(check_records)
        return

    app._set_setup_phase("decoy_apply")
    decoy_ok = app._apply_decoys_if_needed()
    with app.run_lock:
        still_running = app.run_state["status"] == "setup_running"
    if not still_running:
        app._write_setup_checks_summary(check_records)
        return
    if not decoy_ok:
        app._fail_setup()
        app._write_setup_checks_summary(check_records)
        return

    app._set_setup_phase("ready")
    with app.run_lock:
        if app._needs_snapshot():
            path = app._capture_snapshot_file("pre")
            if path:
                app.run_state["snapshot_pre"] = str(path.relative_to(ROOT))
                app._write_meta()

        app.run_state["status"] = "ready"
        app._set_timestamp("setup_finished_at")
        app._set_timestamp("solve_started_at")
        app.run_state["solve_pause_total_sec"] = 0
        app.run_state["solve_pause_started_at_ts"] = None
        app.run_state["solve_paused"] = False
        app.run_state["current_step"] = None
        app._write_meta()
    app._write_setup_checks_summary(check_records)


def submit_run(app):
    with app.run_lock:
        if app.run_state["status"] not in ("ready", "failed"):
            return {"error": "Run is not ready for submission"}

        if app._auto_fail_if_limits_exceeded():
            return {"status": app.run_state["status"], "error": app.run_state["last_error"]}

        data = app.run_state.get("data", {})
        verify_cfg = resolve_oracle_verify(data)
        verification_cmds = verify_cfg.get("commands") or []
        if not verification_cmds:
            return {"warning": "No oracle.verify.commands defined for this test."}
        wait_cmds = []
        hook_before_cmds = verify_cfg.get("before_commands") or []
        hook_after_cmds = verify_cfg.get("after_commands") or []
        after_failure_mode = verify_cfg.get("after_failure_mode") or "warn"

        app.run_state["attempts"] += 1
        attempt = app.run_state["attempts"]
        log_name = f"verification_{attempt}.log"
        log_path = Path(app.run_state["run_dir"]) / log_name
        app.run_state["verification_logs"].append(str(log_path))
        app._pause_solve_timer()
        app.run_state["status"] = "verifying"
        app.run_state["current_step"] = None
        app.run_state["verification_warnings"] = []
        app._write_meta()

        thread = threading.Thread(
            target=app._run_verification,
            args=(
                wait_cmds,
                hook_before_cmds,
                verification_cmds,
                hook_after_cmds,
                after_failure_mode,
                log_path,
                attempt,
            ),
            daemon=True,
        )
        thread.start()
        return {"status": "verifying"}


def run_verification(app, wait_cmds, before_cmds, verify_cmds, after_cmds, after_failure_mode, log_path, attempt):
    log_full_path = ROOT / log_path
    ok = True
    post_cleanup_warning = None
    failure_step = None
    verification_kind = "oracle_pass"
    try:
        if wait_cmds:
            app._append_log(log_full_path, f"[{ts_str()}] HOOK wait: starting")
            ok = app._run_command_list(wait_cmds, log_full_path, stage="verification")
            if not ok:
                failure_step = "wait_hook"
                verification_kind = "oracle_harness_error"
        if ok and before_cmds:
            app._append_log(log_full_path, f"[{ts_str()}] HOOK before: starting")
            ok = app._run_command_list(before_cmds, log_full_path, stage="verification")
            if not ok:
                failure_step = "before_hook"
                verification_kind = "oracle_harness_error"
        if ok:
            app._append_log(log_full_path, f"[{ts_str()}] ORACLE verification: starting")
            ok = app._run_command_list(verify_cmds, log_full_path, stage="verification")
            if not ok:
                failure_step = "oracle"
                last_error = str(app.run_state.get("last_error") or "")
                verification_kind = "oracle_timeout" if "timed out" in last_error.lower() else "oracle_fail"
    finally:
        if after_cmds:
            app._append_log(log_full_path, f"[{ts_str()}] HOOK after: starting")
            after_ok = app._run_command_list_stateless(after_cmds, log_full_path, stage="verification")
            if not after_ok:
                msg = "verification after-hook commands failed"
                if after_failure_mode == "fail":
                    ok = False
                    failure_step = "after_hook"
                    verification_kind = "oracle_harness_error"
                    with app.run_lock:
                        if not app.run_state.get("last_error"):
                            app.run_state["last_error"] = msg
                        app._write_meta()
                else:
                    post_cleanup_warning = msg
                    app._append_log(log_full_path, f"[{ts_str()}] WARNING: {msg}")
    with app.run_lock:
        if app.run_state["status"] != "verifying":
            return
        app._resume_solve_timer()
        if post_cleanup_warning:
            warnings = app.run_state.get("verification_warnings") or []
            warnings.append(post_cleanup_warning)
            app.run_state["verification_warnings"] = warnings
        app.run_state["last_verification_kind"] = verification_kind
        app.run_state["last_verification_step"] = failure_step
        if ok:
            app.run_state["status"] = "passed"
            app._set_timestamp("finished_at")
            app._write_meta()
            app._stop_proxy_trace()
            app._maybe_compute_metrics()
            app._maybe_start_cleanup()
        else:
            app.run_state["status"] = "failed"
            app.run_state["finished_at"] = None
            app.run_state["finished_at_ts"] = None
            app._write_meta()
