import os
import uuid
from copy import deepcopy
from pathlib import Path

from ..settings import MAX_ATTEMPTS, MAX_TIME_MINUTES, ROOT
from ..util import sanitize_name, ts_str


BRIDGE_FLAG_ENV = "BENCHMARK_MANUAL_WORKFLOW_BRIDGE"
MANUAL_RUNNER_ORIGIN = "manual_runner"
_MANUAL_WORKFLOW_DIR = ".benchmark/manual_workflows"
_MANUAL_AGENT_HOLD_CMD = "sleep 86400"


def _truthy(value):
    text = str(value or "").strip().lower()
    return text in ("1", "true", "yes", "on")


def manual_workflow_bridge_enabled(environ=None):
    env = environ if environ is not None else os.environ
    return _truthy(env.get(BRIDGE_FLAG_ENV))


def empty_manual_workflow_session():
    return {
        "active_job_id": None,
        "case_id": None,
        "service": None,
        "case": None,
        "test_file": None,
        "source": MANUAL_RUNNER_ORIGIN,
    }


def has_active_manual_workflow_session(session):
    if not isinstance(session, dict):
        return False
    return bool(str(session.get("active_job_id") or "").strip())


def _to_int(value, fallback):
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _to_positive_int(value):
    try:
        parsed = int(value)
    except Exception:
        return None
    if parsed <= 0:
        return None
    return parsed


def manual_bridge_start_eligible(
    *,
    defer_cleanup=False,
    skip_precondition_unit_ids=None,
    case_data_override=None,
    resolved_params=None,
    namespace_context=None,
):
    if bool(defer_cleanup):
        return False
    if case_data_override is not None:
        return False
    if isinstance(skip_precondition_unit_ids, (list, tuple, set)):
        if any(str(item).strip() for item in skip_precondition_unit_ids):
            return False
    elif skip_precondition_unit_ids is not None:
        return False
    if isinstance(resolved_params, dict):
        if resolved_params:
            return False
    elif resolved_params is not None:
        return False
    if isinstance(namespace_context, dict):
        if namespace_context:
            return False
    elif namespace_context is not None:
        return False
    return True


def _manual_workflow_name(service, case_name, stamp=None):
    token = str(stamp or ts_str()).strip().lower()
    token = sanitize_name(token)
    entropy = str(uuid.uuid4().hex[:8]).lower()
    return f"manual-{sanitize_name(service).lower()}-{sanitize_name(case_name).lower()}-{token}-{entropy}"


def _manual_workflow_doc(
    *,
    workflow_name,
    service,
    case_name,
    case_path,
    max_attempts_override=None,
):
    stage = {
        "id": "stage_1",
        "service": str(service),
        "case": str(case_name),
        "case_path": str(case_path),
    }
    max_attempts = _to_positive_int(max_attempts_override)
    if max_attempts:
        stage["max_attempts"] = max_attempts
    return {
        "apiVersion": "benchmark/v1alpha1",
        "kind": "Workflow",
        "metadata": {"name": workflow_name},
        "spec": {
            "prompt_mode": "progressive",
            "stages": [stage],
        },
    }


def write_manual_workflow_file(
    *,
    service,
    case_name,
    case_path,
    max_attempts_override=None,
    root=ROOT,
    dump_yaml=None,
):
    workflow_name = _manual_workflow_name(service, case_name, stamp=ts_str())
    workflow_doc = _manual_workflow_doc(
        workflow_name=workflow_name,
        service=service,
        case_name=case_name,
        case_path=case_path,
        max_attempts_override=max_attempts_override,
    )
    base_dir = Path(root) / _MANUAL_WORKFLOW_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"{workflow_name}.yaml"
    if dump_yaml is None:
        import yaml

        text = yaml.safe_dump(workflow_doc, sort_keys=False)
    else:
        text = dump_yaml(workflow_doc)
    path.write_text(text, encoding="utf-8")
    return path


def manual_workflow_start_payload(
    workflow_path,
    *,
    max_attempts_override=None,
):
    flags = {
        "sandbox": "local",
        "agent_cmd": _MANUAL_AGENT_HOLD_CMD,
        "submit_timeout": int(24 * 60 * 60),
        "max_attempts": _to_positive_int(max_attempts_override),
    }
    if not flags.get("max_attempts"):
        flags.pop("max_attempts", None)
    return {
        "action": "run",
        "workflow_path": str(workflow_path),
        "flags": flags,
        "origin": MANUAL_RUNNER_ORIGIN,
        "initial_phase": "stage_setup",
        "phase_message": "starting",
    }


def manual_workflow_name(session):
    if isinstance(session, dict):
        raw = str(session.get("workflow_name") or "").strip()
        if raw:
            return raw
        raw_path = str(session.get("workflow_path") or "").strip()
        if raw_path:
            return Path(raw_path).stem
    return ""


def resolve_manual_workflow_run_dir(session, job, *, root=ROOT):
    if isinstance(job, dict):
        run_dir = str(job.get("run_dir") or "").strip()
        if run_dir:
            path = Path(run_dir)
            if not path.is_absolute():
                path = (Path(root) / path).resolve()
            if path.exists() and path.is_dir():
                return path

    name = manual_workflow_name(session)
    if not name:
        return None
    runs_root = Path(root) / "runs"
    if not runs_root.exists():
        return None
    suffix = f"_workflow_run_{name}"
    candidates = []
    for item in runs_root.glob(f"*{suffix}"):
        if not item.is_dir():
            continue
        candidates.append(item)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def resolve_manual_submit_signal_path(session, job, *, root=ROOT):
    run_dir = resolve_manual_workflow_run_dir(session, job, root=root)
    if run_dir is None:
        return None
    return run_dir / "agent_bundle" / "submit.signal"


def resolve_manual_cleanup_log_path(session, job, *, root=ROOT):
    run_dir = resolve_manual_workflow_run_dir(session, job, root=root)
    if run_dir is None:
        return None
    return run_dir / "workflow_cleanup.log"


def write_manual_submit_signal(signal_path, payload=None):
    path = Path(signal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "" if payload is None else str(payload)
    path.write_text(text, encoding="utf-8")
    return path


def _resolve_workflow_state(workflow_state_path, *, root=ROOT, read_json_file=None):
    raw = str(workflow_state_path or "").strip()
    if not raw:
        return {}
    path = Path(raw)
    if not path.is_absolute():
        path = (Path(root) / path).resolve()
    if read_json_file is None:
        try:
            import json

            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    try:
        payload = read_json_file(path)
    except Exception:
        payload = None
    return payload if isinstance(payload, dict) else {}


def workflow_job_to_manual_status(job, *, workflow_state=None):
    job = deepcopy(job) if isinstance(job, dict) else {}
    state = workflow_state if isinstance(workflow_state, dict) else {}
    phase = str(job.get("phase") or "").strip().lower()
    job_status = str(job.get("status") or "").strip().lower()

    if job_status == "running":
        if phase in ("compile_init", "namespace_setup", "stage_setup"):
            return "setup_running"
        if phase in ("agent_waiting", "agent_running"):
            return "ready"
        return "verifying"

    if job_status == "completed":
        terminal = bool(state.get("terminal"))
        terminal_reason = str(state.get("terminal_reason") or "").strip().lower()
        solve_status = str(state.get("solve_status") or "").strip().lower()
        if not terminal:
            return "passed"
        if terminal_reason == "workflow_complete":
            return "failed" if solve_status == "failed" else "passed"
        if terminal_reason == "stage_failed_terminate":
            return "failed"
        if "setup" in terminal_reason:
            return "setup_failed"
        return "auto_failed"

    if job_status == "failed":
        return "auto_failed"
    return "idle"


def workflow_job_can_submit(job):
    if not isinstance(job, dict):
        return False
    if str(job.get("status") or "").strip().lower() != "running":
        return False
    phase = str(job.get("phase") or "").strip().lower()
    return phase == "agent_waiting"


def map_workflow_job_to_run_status(
    job,
    *,
    session=None,
    workflow_state=None,
    case_summary=None,
    cluster_ok=True,
    cluster_error=None,
):
    job = deepcopy(job) if isinstance(job, dict) else {}
    state = workflow_state if isinstance(workflow_state, dict) else {}
    case_payload = deepcopy(case_summary) if isinstance(case_summary, dict) else None
    session_obj = session if isinstance(session, dict) else {}

    status_value = workflow_job_to_manual_status(job, workflow_state=state)
    max_attempts = _to_int(job.get("max_attempts"), MAX_ATTEMPTS)
    elapsed = _to_int(job.get("solve_elapsed_sec"), 0)
    time_limit = _to_int(job.get("solve_limit_sec"), MAX_TIME_MINUTES * 60)
    setup_phase = "precondition_apply" if status_value == "setup_running" else None

    if case_payload is None:
        case_payload = {
            "id": session_obj.get("case_id"),
            "service": session_obj.get("service"),
            "case": session_obj.get("case"),
            "display_name": session_obj.get("case"),
            "test_file": session_obj.get("test_file"),
        }
        if not case_payload.get("id"):
            case_payload = None

    return {
        "status": status_value,
        "case": case_payload,
        "attempts": _to_int(job.get("active_attempt"), 0),
        "max_attempts": max_attempts,
        "elapsed_seconds": elapsed,
        "time_limit_seconds": time_limit,
        "run_dir": job.get("run_dir"),
        "setup_log": None,
        "cleanup_log": None,
        "cleanup_status": None,
        "verification_logs": [],
        "current_step": job.get("phase_message"),
        "last_error": job.get("error"),
        "metrics_path": None,
        "cluster_ok": bool(cluster_ok),
        "cluster_error": cluster_error,
        "has_verification": True,
        "can_submit": workflow_job_can_submit(job),
        "verification_warnings": [],
        "resolved_params": {},
        "setup_timeout_auto_sec": None,
        "setup_timeout_auto_breakdown": None,
        "setup_phase": setup_phase,
        "setup_warnings": [],
        "setup_checks_path": None,
        "defer_cleanup": False,
        "skip_precondition_unit_ids": [],
        "last_verification_kind": None,
        "last_verification_step": None,
        "namespace_context": {},
        "namespace_lifecycle_owner": "orchestrator",
    }


def map_manual_session_to_run_status(
    session,
    job,
    *,
    root=ROOT,
    read_json_file=None,
    case_summary=None,
    cluster_ok=True,
    cluster_error=None,
):
    if not has_active_manual_workflow_session(session):
        return None
    if not isinstance(job, dict):
        return {
            "status": "auto_failed",
            "case": case_summary,
            "attempts": 0,
            "max_attempts": MAX_ATTEMPTS,
            "elapsed_seconds": 0,
            "time_limit_seconds": MAX_TIME_MINUTES * 60,
            "run_dir": None,
            "setup_log": None,
            "cleanup_log": None,
            "cleanup_status": None,
            "verification_logs": [],
            "current_step": None,
            "last_error": "manual workflow job not found",
            "metrics_path": None,
            "cluster_ok": bool(cluster_ok),
            "cluster_error": cluster_error,
            "has_verification": True,
            "can_submit": False,
            "verification_warnings": [],
            "resolved_params": {},
            "setup_timeout_auto_sec": None,
            "setup_timeout_auto_breakdown": None,
            "setup_phase": None,
            "setup_warnings": [],
            "setup_checks_path": None,
            "defer_cleanup": False,
            "skip_precondition_unit_ids": [],
            "last_verification_kind": None,
            "last_verification_step": None,
            "namespace_context": {},
            "namespace_lifecycle_owner": "orchestrator",
        }

    workflow_state = _resolve_workflow_state(
        job.get("workflow_state_path"),
        root=root,
        read_json_file=read_json_file,
    )
    return map_workflow_job_to_run_status(
        job,
        session=session,
        workflow_state=workflow_state,
        case_summary=case_summary,
        cluster_ok=cluster_ok,
        cluster_error=cluster_error,
    )
