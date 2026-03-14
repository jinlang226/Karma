import re
import threading
from copy import deepcopy
from pathlib import Path
from subprocess import PIPE, STDOUT, Popen

from .helpers import build_judge_tokens, count_batch_runs, format_tokens_preview
from ..settings import ROOT
from ..util import ts_str


def list_judge_runs(app):
    items = []
    for run_dir in sorted(app.runs_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if not run_dir.is_dir():
            continue
        if run_dir.name.startswith("batch_"):
            continue
        meta = app._read_json_file(run_dir / "meta.json") or {}
        judge_result_path = run_dir / "judge" / "result_v1.json"
        judge_summary_path = run_dir / "judge" / "summary.md"
        judge_result = app._read_json_file(judge_result_path) if judge_result_path.exists() else {}
        scores = (judge_result or {}).get("scores") or {}
        items.append(
            {
                "run_dir": app._rel_path(run_dir),
                "service": str(meta.get("service") or ""),
                "case": str(meta.get("case") or ""),
                "status": meta.get("status"),
                "started_at": meta.get("setup_started_at"),
                "judge_status": (judge_result or {}).get("judge_status"),
                "judge_score": scores.get("final_score"),
                "judge_evaluated_at": (judge_result or {}).get("evaluated_at"),
                "judge_result_path": app._rel_path(judge_result_path) if judge_result_path.exists() else None,
                "judge_summary_path": app._rel_path(judge_summary_path) if judge_summary_path.exists() else None,
            }
        )
    return items


def list_judge_batches(app):
    items = []
    for batch_dir in sorted(app.runs_dir.glob("batch_*"), key=lambda p: p.name, reverse=True):
        if not batch_dir.is_dir():
            continue
        batch_index = app._read_json_file(batch_dir / "batch_index.json")
        judge_index = app._read_json_file(batch_dir / "judge_index.json")
        judge_summary = app._read_json_file(batch_dir / "judge_summary.json")

        run_count = count_batch_runs(batch_index)
        judged_count = len(judge_index) if isinstance(judge_index, list) else 0
        if run_count <= 0 and judged_count > 0:
            run_count = judged_count

        items.append(
            {
                "batch_dir": app._rel_path(batch_dir),
                "run_count": run_count,
                "judged_count": judged_count,
                "average_final_score": (judge_summary or {}).get("average_final_score"),
                "judge_generated_at": (judge_summary or {}).get("generated_at"),
                "judge_summary_path": app._rel_path(batch_dir / "judge_summary.json")
                if (batch_dir / "judge_summary.json").exists()
                else None,
            }
        )
    return items


def resolve_judge_target(target_type, target_path):
    ttype = str(target_type or "").strip().lower()
    if ttype not in ("run", "batch"):
        return None, "target_type must be run or batch"
    raw = str(target_path or "").strip()
    if not raw:
        return None, "target_path is required"
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists() or not path.is_dir():
        return None, f"target path not found: {raw}"
    try:
        path.relative_to(ROOT)
    except Exception:
        return None, "target_path must be inside repository"
    if ttype == "run":
        if path.name.startswith("batch_"):
            return None, "run target cannot be a batch directory"
        if not (path / "meta.json").exists():
            return None, "run target missing meta.json"
    else:
        if not path.name.startswith("batch_"):
            return None, "batch target must be a batch_* directory"
        if not (path / "batch_index.json").exists():
            return None, "batch target missing batch_index.json"
    return path, None


def build_judge_tokens_for_app(app, target_type, target_path, dry_run=False, judge_env_file=None):
    path, error = resolve_judge_target(target_type, target_path)
    if error:
        return None, error, None
    tokens, token_error = build_judge_tokens(
        target_type=target_type,
        target_path=app._rel_path(path),
        dry_run=dry_run,
        judge_env_file=judge_env_file,
    )
    return tokens, token_error, path


def judge_preview(app, payload):
    payload = payload or {}
    target_type = str(payload.get("target_type") or "").strip().lower()
    target_path = payload.get("target_path")
    dry_run = bool(payload.get("dry_run"))
    judge_env_file = payload.get("judge_env_file")
    tokens, error, _ = build_judge_tokens_for_app(
        app,
        target_type,
        target_path,
        dry_run=dry_run,
        judge_env_file=judge_env_file,
    )
    if error:
        return {"ok": False, "error": error}
    preview = format_tokens_preview(tokens)
    return {
        "ok": True,
        "command_one_line": preview.get("command_one_line"),
        "command_multi_line": preview.get("command_multi_line"),
        "tokens": preview.get("tokens"),
    }


def push_judge_event_locked(app, event_type, data):
    app.judge_event_seq += 1
    event = {
        "seq": int(app.judge_event_seq),
        "type": str(event_type),
        "data": deepcopy(data),
        "ts": ts_str(),
    }
    app.judge_event_history.append(event)
    if len(app.judge_event_history) > app.judge_event_limit:
        app.judge_event_history = app.judge_event_history[-app.judge_event_limit :]
    app.judge_event_cond.notify_all()
    return event


def get_judge_stream_snapshot(app):
    with app.judge_lock:
        jobs = []
        for job_id in reversed(app.judge_job_order):
            job = app.judge_jobs.get(job_id)
            if not job:
                continue
            jobs.append(judge_job_snapshot(job))
        return {"seq": int(app.judge_event_seq), "jobs": jobs}


def get_judge_events_since(app, since_seq, timeout_sec=15.0):
    try:
        cursor = int(since_seq)
    except Exception:
        cursor = 0
    if cursor < 0:
        cursor = 0
    wait_timeout = float(timeout_sec or 0)
    if wait_timeout < 0:
        wait_timeout = 0.0

    with app.judge_event_cond:
        current_seq = int(app.judge_event_seq)
        if current_seq <= cursor:
            app.judge_event_cond.wait(timeout=wait_timeout)
            current_seq = int(app.judge_event_seq)

        if not app.judge_event_history:
            return {"reset": False, "events": [], "current_seq": current_seq}

        oldest_seq = int(app.judge_event_history[0]["seq"])
        if cursor < oldest_seq - 1:
            return {"reset": True, "events": [], "current_seq": current_seq}

        events = [deepcopy(ev) for ev in app.judge_event_history if int(ev["seq"]) > cursor]
        return {"reset": False, "events": events, "current_seq": current_seq}


def parse_judge_progress_line(line):
    text = str(line or "").strip()
    match = re.match(
        r"^\[judge\]\s+(?P<label>\S+)\s+status=(?P<status>[^\s]+)\s+score=(?P<score>[^\s]+)(?:\s+prompt=(?P<prompt>\S+))?",
        text,
    )
    if not match:
        return None
    score = match.group("score")
    if score == "None":
        score = None
    else:
        try:
            score = float(score)
        except Exception:
            pass
    return {
        "label": match.group("label"),
        "status": match.group("status"),
        "score": score,
        "prompt": match.group("prompt"),
    }


def judge_job_snapshot(job):
    out = dict(job)
    out.pop("_progress_map", None)
    max_lines = 200
    lines = list(out.get("log_lines") or [])
    if len(lines) > max_lines:
        out["log_lines"] = lines[-max_lines:]
        out["log_truncated"] = len(lines) - max_lines
    else:
        out["log_lines"] = lines
        out["log_truncated"] = 0
    return out


def run_judge_job(app, job_id, tokens):
    proc = None
    try:
        proc = Popen(tokens, cwd=ROOT, stdout=PIPE, stderr=STDOUT, text=True)
        with app.judge_lock:
            job = app.judge_jobs.get(job_id)
            if job:
                job["pid"] = proc.pid
        for raw_line in proc.stdout or []:
            line = raw_line.rstrip("\n")
            with app.judge_lock:
                job = app.judge_jobs.get(job_id)
                if not job:
                    continue
                job.setdefault("log_lines", []).append(line)
                push_judge_event_locked(app, "job_log", {"job_id": job_id, "line": line})
                parsed = parse_judge_progress_line(line)
                if parsed:
                    key = parsed["label"]
                    index = job["_progress_map"].get(key)
                    if index is None:
                        job["_progress_map"][key] = len(job["progress"])
                        job["progress"].append(parsed)
                    else:
                        job["progress"][index] = parsed
                    push_judge_event_locked(app, "job_progress", {"job_id": job_id, "progress": parsed})
        exit_code = proc.wait()
        with app.judge_lock:
            job = app.judge_jobs.get(job_id)
            if job:
                job["status"] = "completed" if exit_code == 0 else "failed"
                job["exit_code"] = int(exit_code)
                job["finished_at"] = ts_str()
                if exit_code != 0:
                    job["error"] = "judge command failed"
                push_judge_event_locked(app, "job_upsert", {"job": judge_job_snapshot(job)})
                push_judge_event_locked(app, "invalidate_runs_batches", {"reason": "job_finished", "job_id": job_id})
    except Exception as exc:
        with app.judge_lock:
            job = app.judge_jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(exc)
                job["finished_at"] = ts_str()
                push_judge_event_locked(app, "job_upsert", {"job": judge_job_snapshot(job)})
                push_judge_event_locked(app, "invalidate_runs_batches", {"reason": "job_failed", "job_id": job_id})
    finally:
        if proc and proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass


def start_judge(app, payload):
    payload = payload or {}
    target_type = str(payload.get("target_type") or "").strip().lower()
    target_path = payload.get("target_path")
    dry_run = bool(payload.get("dry_run"))
    judge_env_file = payload.get("judge_env_file")
    tokens, error, _ = build_judge_tokens_for_app(
        app,
        target_type,
        target_path,
        dry_run=dry_run,
        judge_env_file=judge_env_file,
    )
    if error:
        return {"error": error}

    with app.judge_lock:
        for existing in app.judge_jobs.values():
            if existing.get("status") == "running":
                return {"error": "A judge job is already running"}
        job_id = f"judge_{ts_str()}_{len(app.judge_job_order) + 1}"
        job = {
            "id": job_id,
            "status": "running",
            "target_type": target_type,
            "target_path": str(target_path),
            "dry_run": dry_run,
            "judge_env_file": str(judge_env_file or ""),
            "tokens": tokens,
            "started_at": ts_str(),
            "finished_at": None,
            "exit_code": None,
            "error": None,
            "log_lines": [],
            "progress": [],
            "_progress_map": {},
        }
        app.judge_jobs[job_id] = job
        app.judge_job_order.append(job_id)
        push_judge_event_locked(app, "job_upsert", {"job": judge_job_snapshot(job)})

    thread = threading.Thread(target=run_judge_job, args=(app, job_id, tokens), daemon=True)
    thread.start()
    with app.judge_lock:
        return {"ok": True, "job": judge_job_snapshot(app.judge_jobs[job_id])}


def list_judge_jobs(app):
    with app.judge_lock:
        out = []
        for job_id in reversed(app.judge_job_order):
            job = app.judge_jobs.get(job_id)
            if not job:
                continue
            out.append(judge_job_snapshot(job))
        return out


def get_judge_job(app, job_id):
    with app.judge_lock:
        job = app.judge_jobs.get(job_id)
        if not job:
            return None
        return judge_job_snapshot(job)
