import json
import os
import re
import signal
import threading
import time
from copy import deepcopy
from pathlib import Path
from subprocess import PIPE, STDOUT, Popen

from ..orchestrator_cli import get_orchestrator_cli_options
from .helpers import build_workflow_tokens, format_tokens_preview
from ..settings import ROOT
from ..util import ts_str
from ..workflow import (
    WORKFLOW_PROMPT_MODES,
    load_workflow_spec,
    parse_workflow_yaml_text,
    workflow_spec_to_builder_draft,
)


WORKFLOW_RUNNER_ORIGIN = "workflow_runner"
MANUAL_RUNNER_ORIGIN = "manual_runner"
_KNOWN_ORIGINS = {WORKFLOW_RUNNER_ORIGIN, MANUAL_RUNNER_ORIGIN}
WORKFLOW_SOURCE_CLI = "cli"
WORKFLOW_SOURCE_UI = "ui"
WORKFLOW_PROFILE_DEFAULT = "default"
WORKFLOW_PROFILE_UI_DEBUG_LOCAL = "ui_debug_local"
UI_WORKFLOW_DEBUG_LOCAL_ENV = "BENCHMARK_UI_WORKFLOW_DEBUG_LOCAL"
UI_WORKFLOW_DEBUG_HOLD_CMD = "sleep 86400"
UI_WORKFLOW_DEBUG_SUBMIT_TIMEOUT_SEC = 24 * 60 * 60
WORKFLOW_PROMPT_MAX_CHARS_DEFAULT = 24_000
WORKFLOW_PROMPT_MAX_CHARS_LIMIT = 120_000


def list_workflow_candidate_paths():
    candidates = set()
    workflows_root = ROOT / "workflows"
    if workflows_root.exists():
        for ext in ("*.yaml", "*.yml"):
            for path in workflows_root.rglob(ext):
                if path.is_file():
                    candidates.add(path.resolve())
    resources_root = ROOT / "resources"
    if resources_root.exists():
        for name in ("workflow.yaml", "workflow.yml"):
            for path in resources_root.rglob(name):
                if path.is_file():
                    candidates.add(path.resolve())
    return sorted(candidates, key=lambda p: str(p), reverse=True)


def list_workflow_files(app):
    rows = []
    for path in list_workflow_candidate_paths():
        rel = app._rel_path(path)
        try:
            wf = load_workflow_spec(str(path))
            spec = wf.get("spec") or {}
            stages = list(spec.get("stages") or [])
            rows.append(
                {
                    "path": rel,
                    "name": (wf.get("metadata") or {}).get("name") or path.stem,
                    "prompt_mode": spec.get("prompt_mode") or "progressive",
                    "stage_count": len(stages),
                    "status": "ok",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "path": rel,
                    "name": path.stem,
                    "prompt_mode": None,
                    "stage_count": 0,
                    "status": "invalid",
                    "error": str(exc),
                }
            )
    return rows


def resolve_workflow_target(workflow_path):
    raw = str(workflow_path or "").strip()
    if not raw:
        return None, "workflow_path is required"
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists() or not path.is_file():
        return None, f"workflow file not found: {raw}"
    if path.suffix.lower() not in (".yaml", ".yml"):
        return None, "workflow_path must point to .yaml/.yml"
    try:
        path.relative_to(ROOT)
    except Exception:
        return None, "workflow_path must be inside repository"
    try:
        load_workflow_spec(str(path))
    except Exception as exc:
        return None, f"invalid workflow spec: {exc}"
    return path, None


def build_workflow_tokens_for_app(app, action, workflow_path, flags=None, dry_run=False):
    action_text = str(action or "").strip().lower()
    if action_text != "run":
        return None, "action must be run", None
    path, error = resolve_workflow_target(workflow_path)
    if error:
        return None, error, None
    rel = app._rel_path(path)
    options = get_orchestrator_cli_options()
    tokens, token_error = build_workflow_tokens(
        action=action_text,
        workflow_path=rel,
        flags=flags or {},
        defaults=options.get("defaults") or {},
        choices=options.get("choices") or {},
        dry_run=dry_run,
    )
    return tokens, token_error, path


def workflow_preview(app, payload):
    payload = payload or {}
    workflow_path = payload.get("workflow_path")
    flags = payload.get("flags") or {}
    run_tokens, run_error, resolved_path = build_workflow_tokens_for_app(
        app,
        "run",
        workflow_path,
        flags=flags,
        dry_run=bool(payload.get("dry_run_run")),
    )
    if run_error:
        return {"ok": False, "error": run_error}
    run_preview = format_tokens_preview(run_tokens)
    return {
        "ok": True,
        "workflow_path": app._rel_path(resolved_path),
        "run": run_preview,
        "run_one_line": run_preview.get("command_one_line"),
        "run_multi_line": run_preview.get("command_multi_line"),
        "run_tokens": run_preview.get("tokens"),
    }


def workflow_import(app, payload):
    _ = app
    payload = payload or {}
    yaml_text = str(payload.get("yaml_text") or payload.get("yaml") or "")
    workflow_path = str(payload.get("workflow_path") or "").strip()
    if not yaml_text.strip():
        return {"ok": False, "error": "yaml_text is required"}
    try:
        spec = parse_workflow_yaml_text(yaml_text, workflow_path_hint=workflow_path or None)
        draft = workflow_spec_to_builder_draft(spec)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "draft": draft,
        "workflow_name": (draft.get("metadata") or {}).get("name") or "",
        "prompt_mode": ((draft.get("spec") or {}).get("prompt_mode") or "progressive"),
        "stage_count": len(((draft.get("spec") or {}).get("stages") or [])),
    }


def workflow_job_origin(job):
    if not isinstance(job, dict):
        return WORKFLOW_RUNNER_ORIGIN
    origin = str(job.get("origin") or WORKFLOW_RUNNER_ORIGIN).strip().lower()
    return origin or WORKFLOW_RUNNER_ORIGIN


def normalize_workflow_job_origin(value):
    origin = str(value or WORKFLOW_RUNNER_ORIGIN).strip().lower()
    if origin not in _KNOWN_ORIGINS:
        return WORKFLOW_RUNNER_ORIGIN
    return origin


def normalize_workflow_request_source(value):
    source = str(value or "").strip().lower()
    if source == WORKFLOW_SOURCE_UI:
        return WORKFLOW_SOURCE_UI
    return WORKFLOW_SOURCE_CLI


def _is_falsey_text(value):
    text = str(value if value is not None else "").strip().lower()
    return text in ("0", "false", "no", "off")


def ui_workflow_debug_local_enabled(environ=None):
    env = environ if environ is not None else os.environ
    return not _is_falsey_text(env.get(UI_WORKFLOW_DEBUG_LOCAL_ENV, "1"))


def _resolve_workflow_sandbox_mode(action, flags, tokens):
    if str(action or "").strip().lower() != "run":
        return ""
    if isinstance(flags, dict):
        raw = str(flags.get("sandbox") or "").strip().lower()
        if raw in ("local", "docker"):
            return raw
    if isinstance(tokens, list):
        for idx, token in enumerate(tokens):
            if token != "--sandbox":
                continue
            if idx + 1 >= len(tokens):
                break
            mode = str(tokens[idx + 1] or "").strip().lower()
            if mode in ("local", "docker"):
                return mode
    return ""


def resolve_workflow_execution_profile(action, payload, flags, *, environ=None):
    action_text = str(action or "").strip().lower()
    request = payload if isinstance(payload, dict) else {}
    source = normalize_workflow_request_source(request.get("source"))
    mode = str(request.get("execution_mode") or "").strip().lower()
    resolved_flags = deepcopy(flags) if isinstance(flags, dict) else {}
    profile = WORKFLOW_PROFILE_DEFAULT
    warnings = []

    if action_text == "run" and source == WORKFLOW_SOURCE_UI:
        force_docker = mode in ("docker", "standard", "default")
        if force_docker:
            resolved_flags["sandbox"] = "docker"
        elif ui_workflow_debug_local_enabled(environ=environ):
            profile = WORKFLOW_PROFILE_UI_DEBUG_LOCAL
            resolved_flags["sandbox"] = "local"
            if not str(resolved_flags.get("agent_cmd") or "").strip():
                resolved_flags["agent_cmd"] = UI_WORKFLOW_DEBUG_HOLD_CMD
            submit_timeout = str(resolved_flags.get("submit_timeout") or "").strip()
            if not submit_timeout:
                resolved_flags["submit_timeout"] = UI_WORKFLOW_DEBUG_SUBMIT_TIMEOUT_SEC
            resolved_flags["manual_start"] = False
        else:
            if mode in ("debug", ""):
                warnings.append("ui_debug_local_disabled")

    return {
        "source": source,
        "execution_mode": mode,
        "profile": profile,
        "flags": resolved_flags,
        "warnings": warnings,
    }


def workflow_job_capabilities(job):
    if not isinstance(job, dict):
        return {"can_submit": False, "can_cleanup": False}
    if str(job.get("kind") or "").strip().lower() != "run":
        return {"can_submit": False, "can_cleanup": False}
    if not bool(job.get("interactive_controls")):
        return {"can_submit": False, "can_cleanup": False}
    if str(job.get("status") or "").strip().lower() != "running":
        return {"can_submit": False, "can_cleanup": False}
    phase = str(job.get("phase") or "").strip().lower()
    if phase == "agent_waiting":
        return {"can_submit": True, "can_cleanup": True}
    return {"can_submit": False, "can_cleanup": True}


def workflow_job_can_submit(job):
    return bool(workflow_job_capabilities(job).get("can_submit"))


def workflow_job_can_cleanup(job):
    return bool(workflow_job_capabilities(job).get("can_cleanup"))


def workflow_job_is_visible(job):
    return workflow_job_origin(job) == WORKFLOW_RUNNER_ORIGIN


def workflow_event_is_visible(app, event):
    data = (event or {}).get("data")
    if not isinstance(data, dict):
        return True
    job = data.get("job")
    if isinstance(job, dict):
        return workflow_job_is_visible(job)
    job_id = str(data.get("job_id") or "").strip()
    if not job_id:
        return True
    target = app.workflow_jobs.get(job_id)
    if not isinstance(target, dict):
        return True
    return workflow_job_is_visible(target)


def push_workflow_event_locked(app, event_type, data):
    app.workflow_event_seq += 1
    event = {
        "seq": int(app.workflow_event_seq),
        "type": str(event_type),
        "data": deepcopy(data),
        "ts": ts_str(),
    }
    app.workflow_event_history.append(event)
    if len(app.workflow_event_history) > app.workflow_event_limit:
        app.workflow_event_history = app.workflow_event_history[-app.workflow_event_limit :]
    app.workflow_event_cond.notify_all()
    return event


def workflow_job_snapshot(job):
    out = deepcopy(job)
    out.setdefault("origin", WORKFLOW_RUNNER_ORIGIN)
    out.setdefault("request_source", WORKFLOW_SOURCE_CLI)
    out.setdefault("execution_profile", WORKFLOW_PROFILE_DEFAULT)
    out.setdefault("sandbox_mode", "")
    out.setdefault("interactive_controls", False)
    max_lines = 300
    lines = list((out.get("logs") or {}).get("orchestrator", {}).get("lines") or [])
    truncated = 0
    if len(lines) > max_lines:
        truncated = len(lines) - max_lines
        lines = lines[-max_lines:]
    out.setdefault("logs", {}).setdefault("orchestrator", {})
    out["logs"]["orchestrator"]["lines"] = lines
    out["logs"]["orchestrator"]["truncated"] = truncated
    out["logs"]["orchestrator"]["total_lines"] = len((job.get("logs") or {}).get("orchestrator", {}).get("lines") or [])
    out["prompt"] = _workflow_prompt_meta(out)
    caps = workflow_job_capabilities(out)
    out["can_submit"] = bool(caps.get("can_submit"))
    out["can_cleanup"] = bool(caps.get("can_cleanup"))
    return out


def get_workflow_stream_snapshot(app):
    with app.workflow_lock:
        jobs = []
        for job_id in reversed(app.workflow_job_order):
            job = app.workflow_jobs.get(job_id)
            if not job:
                continue
            if not workflow_job_is_visible(job):
                continue
            jobs.append(workflow_job_snapshot(job))
        return {
            "schema": "workflow_stream.v2",
            "seq": int(app.workflow_event_seq),
            "server_epoch_ms": int(time.time() * 1000),
            "jobs": jobs,
        }


def get_workflow_events_since(app, since_seq, timeout_sec=15.0):
    try:
        cursor = int(since_seq)
    except Exception:
        cursor = 0
    if cursor < 0:
        cursor = 0
    wait_timeout = float(timeout_sec or 0)
    if wait_timeout < 0:
        wait_timeout = 0.0

    with app.workflow_event_cond:
        current_seq = int(app.workflow_event_seq)
        if current_seq <= cursor:
            app.workflow_event_cond.wait(timeout=wait_timeout)
            current_seq = int(app.workflow_event_seq)

        if not app.workflow_event_history:
            return {"reset": False, "events": [], "current_seq": current_seq}

        oldest_seq = int(app.workflow_event_history[0]["seq"])
        if cursor < oldest_seq - 1:
            return {"reset": True, "events": [], "current_seq": current_seq}

        events = [
            deepcopy(ev)
            for ev in app.workflow_event_history
            if int(ev["seq"]) > cursor and workflow_event_is_visible(app, ev)
        ]
        return {"reset": False, "events": events, "current_seq": current_seq}


def parse_workflow_phase_line(line):
    text = str(line or "").strip()
    stage_match = re.search(r"\[orchestrator\]\s+stage=([a-zA-Z0-9_:-]+)", text)
    if stage_match:
        raw = stage_match.group(1)
        mapped = {
            "setup_start": "stage_setup",
            "setup_done": "stage_setup",
            "agent_start": "agent_running",
            "waiting_start": "agent_waiting",
            "waiting_submit": "agent_waiting",
            "start_received": "agent_running",
            "final_sweep": "final_sweep",
            "workflow_cleanup": "cleanup",
            "done": "done",
        }.get(raw, "stage_setup")
        return mapped, f"orchestrator stage={raw}"

    if "workflow transition" in text:
        return "transition", text
    if "final sweep" in text:
        return "final_sweep", text
    if "cleanup" in text:
        return "cleanup", text
    return None, None


def parse_workflow_artifact_line(job, line):
    text = str(line or "")
    for key in (
        "run_dir",
        "workflow_state_path",
        "workflow_stage_results_path",
        "workflow_transition_log",
        "workflow_final_sweep_path",
        "artifact_path",
    ):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text)
        if not match:
            continue
        value = match.group(1)
        if key == "artifact_path":
            job["compiled_artifact_path"] = value
        else:
            job[key] = value


def run_workflow_job(app, job_id, tokens):
    proc = None
    try:
        proc = Popen(
            tokens,
            cwd=ROOT,
            stdout=PIPE,
            stderr=STDOUT,
            text=True,
            start_new_session=True,
        )
        with app.workflow_lock:
            job = app.workflow_jobs.get(job_id)
            if job:
                job["pid"] = proc.pid
        for raw_line in proc.stdout or []:
            line = raw_line.rstrip("\n")
            with app.workflow_lock:
                job = app.workflow_jobs.get(job_id)
                if not job:
                    continue
                job["server_epoch_ms"] = int(time.time() * 1000)
                logs = job.setdefault("logs", {}).setdefault(
                    "orchestrator",
                    {"lines": [], "truncated": 0, "total_lines": 0},
                )
                logs.setdefault("lines", []).append(line)
                logs["total_lines"] = len(logs.get("lines") or [])
                parse_workflow_artifact_line(job, line)
                push_workflow_event_locked(
                    app,
                    "log_append",
                    {
                        "job_id": job_id,
                        "stream": "orchestrator",
                        "from_line": logs["total_lines"],
                        "lines": [line],
                        "total_lines": logs["total_lines"],
                        "truncated": 0,
                    },
                )
                phase, msg = parse_workflow_phase_line(line)
                if phase:
                    job["phase"] = phase
                    job["phase_message"] = msg
                    job["rev"] = int(job.get("rev") or 0) + 1
                    caps = workflow_job_capabilities(job)
                    push_workflow_event_locked(
                        app,
                        "job_phase",
                        {
                            "job_id": job_id,
                            "rev": job["rev"],
                            "phase": phase,
                            "phase_message": msg,
                            "active_stage_id": job.get("active_stage_id"),
                            "active_stage_index": job.get("active_stage_index"),
                            "stage_total": job.get("stage_total"),
                            "active_attempt": job.get("active_attempt"),
                            "max_attempts": job.get("max_attempts"),
                            "solve_elapsed_sec": job.get("solve_elapsed_sec"),
                            "solve_limit_sec": job.get("solve_limit_sec"),
                            "solve_paused": bool(job.get("solve_paused")),
                            "pause_reason": job.get("pause_reason"),
                            "server_epoch_ms": job.get("server_epoch_ms"),
                            "interactive_controls": bool(job.get("interactive_controls")),
                            "can_submit": bool(caps.get("can_submit")),
                            "can_cleanup": bool(caps.get("can_cleanup")),
                            "prompt": _workflow_prompt_meta(job),
                        },
                    )
        exit_code = proc.wait()
        with app.workflow_lock:
            job = app.workflow_jobs.get(job_id)
            if job:
                job["status"] = "completed" if exit_code == 0 else "failed"
                job["exit_code"] = int(exit_code)
                job["finished_at"] = ts_str()
                job["phase"] = "done" if exit_code == 0 else "cleanup"
                if exit_code != 0:
                    job["error"] = "workflow command failed"
                job["rev"] = int(job.get("rev") or 0) + 1
                snap = workflow_job_snapshot(job)
                push_workflow_event_locked(app, "job_upsert", {"job": snap})
                push_workflow_event_locked(
                    app,
                    "job_complete",
                    {
                        "job_id": job_id,
                        "rev": job["rev"],
                        "status": job["status"],
                        "exit_code": job["exit_code"],
                        "error": job.get("error"),
                        "finished_at": job["finished_at"],
                        "artifacts": {
                            "run_dir": job.get("run_dir"),
                            "workflow_state_path": job.get("workflow_state_path"),
                            "workflow_stage_results_path": job.get("workflow_stage_results_path"),
                            "workflow_transition_log": job.get("workflow_transition_log"),
                            "workflow_final_sweep_path": job.get("workflow_final_sweep_path"),
                            "compiled_artifact_path": job.get("compiled_artifact_path"),
                        },
                    },
                )
                push_workflow_event_locked(
                    app,
                    "invalidate_workflow_files",
                    {"reason": "job_finished", "job_id": job_id},
                )
    except Exception as exc:
        with app.workflow_lock:
            job = app.workflow_jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(exc)
                job["finished_at"] = ts_str()
                job["rev"] = int(job.get("rev") or 0) + 1
                push_workflow_event_locked(app, "job_upsert", {"job": workflow_job_snapshot(job)})
                push_workflow_event_locked(
                    app,
                    "error",
                    {"job_id": job_id, "message": str(exc), "fatal": True},
                )
    finally:
        if proc and proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass


def start_workflow(app, payload):
    payload = payload or {}
    action = str(payload.get("action") or "").strip().lower()
    workflow_path = payload.get("workflow_path")
    origin = normalize_workflow_job_origin(payload.get("origin"))
    profile = resolve_workflow_execution_profile(
        action,
        payload,
        payload.get("flags") or {},
    )
    flags = profile.get("flags") or {}
    dry_run = bool(payload.get("dry_run"))
    tokens, error, resolved_path = build_workflow_tokens_for_app(
        app,
        action,
        workflow_path,
        flags=flags,
        dry_run=dry_run,
    )
    if error:
        return {"error": error}

    workflow_name = ""
    prompt_mode = "progressive"
    try:
        wf = load_workflow_spec(str(resolved_path))
        workflow_name = (wf.get("metadata") or {}).get("name") or ""
        prompt_mode = str((wf.get("spec") or {}).get("prompt_mode") or "progressive")
    except Exception:
        workflow_name = Path(str(resolved_path or workflow_path)).stem
        prompt_mode = "progressive"
    if prompt_mode not in WORKFLOW_PROMPT_MODES:
        prompt_mode = "progressive"

    request_source = str(profile.get("source") or WORKFLOW_SOURCE_CLI)
    execution_profile = str(profile.get("profile") or WORKFLOW_PROFILE_DEFAULT)
    sandbox_mode = _resolve_workflow_sandbox_mode(action, flags, tokens)
    interactive_controls = (
        str(action or "").strip().lower() == "run"
        and execution_profile == WORKFLOW_PROFILE_UI_DEBUG_LOCAL
    )
    start_warnings = list(profile.get("warnings") or [])

    with app.workflow_lock:
        for existing in app.workflow_jobs.values():
            if existing.get("status") == "running":
                return {"error": "A workflow job is already running"}
        job_id = f"wf_{ts_str()}_{len(app.workflow_job_order) + 1}"
        now = ts_str()
        phase_default = "agent_waiting"
        phase = str(payload.get("initial_phase") or "").strip().lower() or phase_default
        phase_message = str(payload.get("phase_message") or "").strip() or "starting"
        job = {
            "id": job_id,
            "origin": origin,
            "kind": action,
            "status": "running",
            "workflow_name": workflow_name or "workflow",
            "workflow_path": app._rel_path(resolved_path),
            "prompt_mode": prompt_mode,
            "request_source": request_source,
            "execution_profile": execution_profile,
            "sandbox_mode": sandbox_mode,
            "interactive_controls": bool(interactive_controls),
            "phase": phase,
            "phase_message": phase_message,
            "active_stage_id": None,
            "active_stage_index": None,
            "stage_total": None,
            "active_attempt": None,
            "max_attempts": None,
            "run_dir": None,
            "compiled_artifact_path": None,
            "workflow_state_path": None,
            "workflow_stage_results_path": None,
            "workflow_transition_log": None,
            "workflow_final_sweep_path": None,
            "solve_elapsed_sec": None,
            "solve_limit_sec": None,
            "solve_paused": False,
            "pause_reason": None,
            "server_epoch_ms": int(time.time() * 1000),
            "progress_pct": None,
            "error": None,
            "exit_code": None,
            "started_at": now,
            "finished_at": None,
            "stages": {},
            "stage_order": [],
            "logs": {
                "orchestrator": {"lines": [], "truncated": 0, "total_lines": 0},
                "agent": {"lines": [], "truncated": 0, "total_lines": 0},
                "submit": {"lines": [], "truncated": 0, "total_lines": 0},
                "transition": {"lines": [], "truncated": 0, "total_lines": 0},
            },
            "tokens": tokens,
            "dry_run": dry_run,
            "rev": 1,
        }
        if start_warnings:
            job["warnings"] = list(start_warnings)
        app.workflow_jobs[job_id] = job
        app.workflow_job_order.append(job_id)
        push_workflow_event_locked(app, "job_upsert", {"job": workflow_job_snapshot(job)})

    thread = threading.Thread(target=run_workflow_job, args=(app, job_id, tokens), daemon=True)
    thread.start()
    with app.workflow_lock:
        out = {"ok": True, "job": workflow_job_snapshot(app.workflow_jobs[job_id])}
        if start_warnings:
            out["warnings"] = list(start_warnings)
        return out


def list_workflow_jobs(app):
    with app.workflow_lock:
        out = []
        for job_id in reversed(app.workflow_job_order):
            job = app.workflow_jobs.get(job_id)
            if not job:
                continue
            if not workflow_job_is_visible(job):
                continue
            out.append(workflow_job_snapshot(job))
        return out


def get_workflow_job(app, job_id):
    with app.workflow_lock:
        job = app.workflow_jobs.get(job_id)
        if not job:
            return None
        return workflow_job_snapshot(job)


def _resolve_run_dir_path(run_dir):
    raw = str(run_dir or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    try:
        path.relative_to(ROOT)
    except Exception:
        return None
    return path


def _resolve_workflow_run_dir(job):
    if str((job or {}).get("kind") or "").strip().lower() != "run":
        return None
    run_dir = _resolve_run_dir_path((job or {}).get("run_dir"))
    if run_dir is not None and run_dir.is_dir():
        return run_dir

    workflow_name = str((job or {}).get("workflow_name") or "").strip()
    if not workflow_name:
        return None
    runs_root = ROOT / "runs"
    if not runs_root.exists():
        return None
    suffix = f"_workflow_run_{workflow_name}"
    candidates = [item for item in runs_root.glob(f"*{suffix}") if item.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _format_workflow_epoch_ts(epoch):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except Exception:
        return None


def _resolve_workflow_prompt_path(job):
    run_dir = _resolve_workflow_run_dir(job)
    if run_dir is None:
        return None
    path = (run_dir / "agent_bundle" / "PROMPT.md").resolve()
    try:
        path.relative_to(ROOT)
    except Exception:
        return None
    return path


def _workflow_prompt_meta(job):
    prompt_path = _resolve_workflow_prompt_path(job)
    if prompt_path is None:
        return {
            "available": False,
            "path": None,
            "updated_at": None,
            "size_bytes": None,
        }
    rel = str(prompt_path.relative_to(ROOT))
    try:
        stat = prompt_path.stat()
    except OSError:
        return {
            "available": False,
            "path": rel,
            "updated_at": None,
            "size_bytes": None,
        }
    return {
        "available": bool(prompt_path.is_file()),
        "path": rel,
        "updated_at": _format_workflow_epoch_ts(stat.st_mtime),
        "size_bytes": int(stat.st_size),
    }


def _normalize_workflow_prompt_max_chars(max_chars):
    try:
        value = int(max_chars)
    except Exception:
        value = WORKFLOW_PROMPT_MAX_CHARS_DEFAULT
    if value <= 0:
        value = WORKFLOW_PROMPT_MAX_CHARS_DEFAULT
    return max(1, min(value, WORKFLOW_PROMPT_MAX_CHARS_LIMIT))


def _resolve_workflow_submit_signal_path(job):
    run_dir = _resolve_workflow_run_dir(job)
    if run_dir is None:
        return None
    return run_dir / "agent_bundle" / "submit.signal"


def _write_workflow_submit_signal(path, payload=""):
    signal_path = Path(path)
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(str(payload), encoding="utf-8")
    return signal_path


def _workflow_job_pid(job):
    if not isinstance(job, dict):
        return None
    try:
        pid = int(job.get("pid") or 0)
    except Exception:
        return None
    if pid <= 0:
        return None
    return pid


def _interrupt_workflow_job(job):
    pid = _workflow_job_pid(job)
    if pid is None:
        return False, "Workflow process is not available"
    try:
        try:
            pgid = os.getpgid(pid)
        except Exception:
            pgid = None
        if pgid and pgid > 0:
            os.killpg(pgid, signal.SIGINT)
        else:
            os.kill(pid, signal.SIGINT)
        return True, None
    except ProcessLookupError:
        return True, None
    except Exception as exc:
        return False, str(exc)


def _workflow_control_error(message, status=400):
    return {"error": str(message), "http_status": int(status)}


def _lookup_workflow_job_for_control(app, job_id):
    target_id = str(job_id or "").strip()
    if not target_id:
        return "", None, _workflow_control_error("job_id is required", status=400)
    with app.workflow_lock:
        job = deepcopy(app.workflow_jobs.get(target_id))
    if not isinstance(job, dict):
        return target_id, None, _workflow_control_error("Workflow job not found", status=404)
    return target_id, job, None


def get_workflow_job_prompt(app, job_id, max_chars=None):
    target_id, job, error = _lookup_workflow_job_for_control(app, job_id)
    if error:
        return error

    prompt_path = _resolve_workflow_prompt_path(job)
    phase = str(job.get("phase") or "").strip().lower()
    if prompt_path is None:
        return {
            "ok": True,
            "job_id": target_id,
            "available": False,
            "prompt": "",
            "truncated": False,
            "path": None,
            "updated_at": None,
            "size_bytes": None,
            "phase": phase,
            "reason": "run_dir_not_ready",
        }

    meta = _workflow_prompt_meta(job)
    if not meta.get("available"):
        return {
            "ok": True,
            "job_id": target_id,
            "available": False,
            "prompt": "",
            "truncated": False,
            "path": meta.get("path"),
            "updated_at": meta.get("updated_at"),
            "size_bytes": meta.get("size_bytes"),
            "phase": phase,
            "reason": "prompt_not_ready",
        }

    try:
        text = prompt_path.read_text(encoding="utf-8")
    except Exception as exc:
        return _workflow_control_error(f"Failed to read workflow prompt: {exc}", status=500)

    limit = _normalize_workflow_prompt_max_chars(max_chars)
    truncated = len(text) > limit
    if truncated:
        text = text[:limit]
    return {
        "ok": True,
        "job_id": target_id,
        "available": True,
        "prompt": text,
        "truncated": bool(truncated),
        "path": meta.get("path"),
        "updated_at": meta.get("updated_at"),
        "size_bytes": meta.get("size_bytes"),
        "phase": phase,
    }


def submit_workflow_job(app, job_id):
    target_id, job, error = _lookup_workflow_job_for_control(app, job_id)
    if error:
        return error
    if not bool(job.get("interactive_controls")):
        return _workflow_control_error("Workflow job is not interactive", status=409)
    if not workflow_job_can_submit(job):
        if str(job.get("status") or "").strip().lower() != "running":
            return _workflow_control_error("Workflow job is not running", status=409)
        return _workflow_control_error("Workflow job is not waiting for submit", status=409)
    signal_path = _resolve_workflow_submit_signal_path(job)
    if signal_path is None:
        return _workflow_control_error("Submit channel is not ready", status=409)
    try:
        _write_workflow_submit_signal(signal_path, payload="")
    except Exception as exc:
        return _workflow_control_error(f"Failed to submit workflow job: {exc}", status=500)
    return {"ok": True, "job_id": target_id, "status": "verifying"}


def cleanup_workflow_job(app, job_id):
    target_id, job, error = _lookup_workflow_job_for_control(app, job_id)
    if error:
        return error
    if not bool(job.get("interactive_controls")):
        return _workflow_control_error("Workflow job is not interactive", status=409)
    if not workflow_job_can_cleanup(job):
        if str(job.get("status") or "").strip().lower() != "running":
            return _workflow_control_error("Workflow job is not running", status=409)
        return _workflow_control_error("Workflow job is not waiting for cleanup", status=409)
    phase = str(job.get("phase") or "").strip().lower()
    if phase == "agent_waiting":
        signal_path = _resolve_workflow_submit_signal_path(job)
        if signal_path is None:
            return _workflow_control_error("Cleanup channel is not ready", status=409)
        payload = json.dumps({"action": "cleanup", "reason": "manual_cleanup"})
        try:
            _write_workflow_submit_signal(signal_path, payload=payload)
        except Exception as exc:
            return _workflow_control_error(f"Failed to cleanup workflow job: {exc}", status=500)
    else:
        ok, signal_error = _interrupt_workflow_job(job)
        if not ok:
            return _workflow_control_error(f"Failed to cleanup workflow job: {signal_error}", status=500)
    return {"ok": True, "job_id": target_id, "status": "cleaning"}
