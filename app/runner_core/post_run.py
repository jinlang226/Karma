import json
import threading

from ..metrics import compute_metrics
from ..settings import ROOT
from ..snapshots import capture_snapshot
from ..util import (
    normalize_commands,
    parse_ts,
    ts_epoch,
    utc_now,
)


def cleanup_run(app):
    with app.run_lock:
        if app.run_state["status"] in ("setup_running", "verifying"):
            return {"error": "Run is still in progress"}

        data = app.run_state.get("data", {}) or {}
        cleanup_cmds = normalize_commands(data.get("cleanUpCommands"))
        decoy_cmds = app._decoy_cleanup_commands()
        run_dir = app.run_state.get("run_dir")
        if app.run_state.get("cleanup_status") == "running":
            return {"status": "cleaning", "log": app.run_state.get("cleanup_log")}
        if app.run_state.get("cleanup_status") == "done":
            app.run_state = app._empty_run_state()
            return {"status": "already_cleaned"}
        if app.run_state.get("cleanup_status") == "failed":
            return {"status": "cleanup_failed", "log": app.run_state.get("cleanup_log")}
        if app.run_state.get("status") not in ("passed", "auto_failed", "failed", "setup_failed"):
            app.run_state = app._empty_run_state()
            return {"status": "skipped"}
        if not (cleanup_cmds or decoy_cmds) or not run_dir:
            app.run_state = app._empty_run_state()
            return {"status": "no_cleanup"}

        log_path = ROOT / run_dir / "cleanup.log"
        cleanup_cmds_copy = list(decoy_cmds) + list(cleanup_cmds)

        app._stop_proxy_trace()
        app.run_state["cleanup_status"] = "running"
        app._set_timestamp("cleanup_started_at")
        app.run_state["cleanup_log"] = str(log_path.relative_to(ROOT))
        app._write_meta()

    thread = threading.Thread(
        target=app._run_cleanup_async,
        args=(cleanup_cmds_copy, log_path),
        daemon=True,
    )
    thread.start()
    return {"status": "cleaning", "log": str(log_path.relative_to(ROOT))}


def is_cleanup_deferred(app):
    return bool(app.run_state.get("defer_cleanup"))


def maybe_start_cleanup(app):
    if app._is_cleanup_deferred():
        return
    if app.run_state.get("cleanup_status") is not None:
        return
    data = app.run_state.get("data", {}) or {}
    cleanup_cmds = normalize_commands(data.get("cleanUpCommands"))
    run_dir = app.run_state.get("run_dir")
    decoy_cmds = app._decoy_cleanup_commands()
    if not (cleanup_cmds or decoy_cmds) or not run_dir:
        return
    log_path = ROOT / run_dir / "cleanup.log"
    cleanup_cmds_copy = list(decoy_cmds) + list(cleanup_cmds)

    app.run_state["cleanup_status"] = "running"
    app._set_timestamp("cleanup_started_at")
    app.run_state["cleanup_log"] = str(log_path.relative_to(ROOT))
    app._write_meta()

    thread = threading.Thread(
        target=app._run_cleanup_async,
        args=(cleanup_cmds_copy, log_path),
        daemon=True,
    )
    thread.start()


def run_cleanup_async(app, cmds, log_path, context=None):
    ok = app._run_command_list_stateless(cmds, log_path)
    if context is None:
        with app.run_lock:
            app.run_state["cleanup_status"] = "done" if ok else "failed"
            app._set_timestamp("cleanup_finished_at")
            app._write_meta()
        app._post_cleanup_metrics_from_state()
    else:
        app._post_cleanup_metrics_from_context(context)


def pause_solve_timer(app):
    if app.run_state.get("solve_paused"):
        return
    if not (app.run_state.get("solve_started_at_ts") or app.run_state.get("solve_started_at")):
        return
    now_ts = ts_epoch(utc_now())
    app.run_state["solve_paused"] = True
    app.run_state["solve_pause_started_at_ts"] = now_ts


def resume_solve_timer(app):
    if not app.run_state.get("solve_paused"):
        return
    now_ts = ts_epoch(utc_now())
    pause_started = app.run_state.get("solve_pause_started_at_ts")
    if pause_started is None:
        pause_started = now_ts
    pause_total = app.run_state.get("solve_pause_total_sec") or 0
    pause_total += max(0, now_ts - pause_started)
    app.run_state["solve_pause_total_sec"] = pause_total
    app.run_state["solve_pause_started_at_ts"] = None
    app.run_state["solve_paused"] = False


def maybe_compute_metrics(app):
    metrics = app.run_state.get("external_metrics") or []
    run_dir = app.run_state.get("run_dir")
    if not metrics or not run_dir:
        return
    if app._needs_snapshot() and not app.run_state.get("snapshot_post"):
        path = app._capture_snapshot_file("post")
        if path:
            app.run_state["snapshot_post"] = str(path.relative_to(ROOT))
    metrics_to_compute = list(metrics)
    if "residual_drift" in metrics_to_compute and not app.run_state.get("snapshot_post_cleanup"):
        metrics_to_compute = [item for item in metrics_to_compute if item != "residual_drift"]
    if not metrics_to_compute:
        return
    trace_path = app._action_trace_path()
    results = compute_metrics(metrics_to_compute, app.run_state, run_dir, trace_path=trace_path)
    if not results:
        return
    app._write_metrics(results)


def capture_snapshot_file(app, label):
    run_dir = app.run_state.get("run_dir")
    if not run_dir:
        return None
    path = ROOT / run_dir / f"snapshot_{label}.json"
    try:
        capture_snapshot(path)
    except Exception as exc:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"error": str(exc)}, indent=2))
    return path


def needs_snapshot(app):
    metrics = app.run_state.get("external_metrics") or []
    return (
        "blast_radius" in metrics
        or "decoy_integrity" in metrics
        or "host_specificity_guardrail" in metrics
        or "key_material_leakage" in metrics
        or "rate_limit_strategy" in metrics
    )


def needs_residual_drift(app):
    metrics = app.run_state.get("external_metrics") or []
    return "residual_drift" in metrics


def write_metrics(app, results, run_dir=None):
    run_dir = run_dir or app.run_state.get("run_dir")
    if not run_dir:
        return
    metrics_path = ROOT / run_dir / "external_metrics.json"
    existing = {}
    if metrics_path.exists():
        try:
            existing = json.loads(metrics_path.read_text())
        except Exception:
            existing = {}
    merged = dict(existing)
    merged.update(results)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(merged, indent=2))
    app.run_state["metrics_path"] = str(metrics_path.relative_to(ROOT))
    app._write_meta()


def post_cleanup_metrics_from_state(app):
    metrics = app.run_state.get("external_metrics") or []
    if "residual_drift" not in metrics:
        return
    run_dir = app.run_state.get("run_dir")
    if not run_dir:
        return
    path = app._capture_snapshot_file("post_cleanup")
    if path:
        app.run_state["snapshot_post_cleanup"] = str(path.relative_to(ROOT))
        app._write_meta()
    results = compute_metrics(["residual_drift"], app.run_state, run_dir)
    if results:
        app._write_metrics(results)


def post_cleanup_metrics_from_context(app, context):
    metrics = context.get("external_metrics") or []
    if "residual_drift" not in metrics:
        return
    run_dir = context.get("run_dir")
    if not run_dir:
        return
    path = ROOT / run_dir / "snapshot_post_cleanup.json"
    try:
        capture_snapshot(path)
    except Exception as exc:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"error": str(exc)}, indent=2))
    meta = {
        "service": context.get("service"),
        "case": context.get("case"),
    }
    results = compute_metrics(["residual_drift"], meta, run_dir)
    if not results:
        return
    metrics_path = ROOT / run_dir / "external_metrics.json"
    existing = {}
    if metrics_path.exists():
        try:
            existing = json.loads(metrics_path.read_text())
        except Exception:
            existing = {}
    merged = dict(existing)
    merged.update(results)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(merged, indent=2))


def solve_elapsed_seconds(app):
    started_ts = app.run_state.get("solve_started_at_ts")
    if started_ts is None:
        started = app.run_state.get("solve_started_at")
        start_dt = parse_ts(started)
        if start_dt is None:
            return 0
        started_ts = ts_epoch(start_dt)

    pause_total = app.run_state.get("solve_pause_total_sec") or 0
    now_ts = ts_epoch(utc_now())
    elapsed = now_ts - started_ts - pause_total

    if app.run_state.get("solve_paused"):
        pause_started = app.run_state.get("solve_pause_started_at_ts")
        if pause_started is None:
            pause_started = now_ts
        elapsed -= max(0, now_ts - pause_started)

    if elapsed < 0:
        return 0
    return int(elapsed)
