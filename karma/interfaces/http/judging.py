"""
Async judge jobs and judge-oriented listings for the HTTP interface.

The synchronous ``POST /api/judge`` route is fine for a one-shot call, but
the UI needs to fire a judge run and watch it progress -- especially for a
cross-run batch that may judge dozens of runs. This module runs judge work
on a background thread, publishes per-stage / per-run progress to the
shared :data:`events.hub` (keyed by judge-job id), and tracks job state so
the UI can poll and stream just like it does for runs.

It also provides the browse listings the Judge view needs: the run list
annotated with judge status, and the batch list (directories that group
multiple runs).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ...protocol import generate_run_id
from ...judge.engine import run_judge
from ...judge.batch import discover_runs, judge_batch_dir
from .events import hub
from . import catalog


_judge_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _register(job_id: str, meta: dict[str, Any]) -> None:
    with _lock:
        _judge_jobs[job_id] = meta


def _update(job_id: str, updates: dict[str, Any]) -> None:
    with _lock:
        if job_id in _judge_jobs:
            _judge_jobs[job_id].update(updates)


def get_judge_job(job_id: str) -> dict[str, Any] | None:
    """Return the status dict for *job_id*, or ``None`` when unknown."""
    with _lock:
        job = _judge_jobs.get(job_id)
    return dict(job) if job else None


def list_judge_jobs() -> list[dict[str, Any]]:
    """Return all judge job status dicts, newest registration last."""
    with _lock:
        return [dict(j) for j in _judge_jobs.values()]


def _judge_run_streaming(
    job_id: str, run_dir: Path, judge_model: str | None, dry_run: bool
) -> dict[str, Any]:
    """Judge each stage of *run_dir*, publishing per-stage progress."""
    stages_dir = run_dir / "stages"
    stage_ids = (
        sorted(d.name for d in stages_dir.iterdir() if d.is_dir())
        if stages_dir.exists()
        else []
    )
    results: dict[str, Any] = {}
    for sid in stage_ids:
        try:
            result = run_judge(run_dir, sid, judge_model=judge_model, dry_run=dry_run)
        except Exception as exc:
            result = {"stage_id": sid, "verdict": "error", "error": str(exc)}
        results[sid] = result
        hub.publish(job_id, {
            "type": "judge_progress",
            "job_id": job_id,
            "run_id": run_dir.name,
            "stage_id": sid,
            "verdict": result.get("verdict"),
            "score": result.get("score"),
        })
    return {"target_type": "run", "run_id": run_dir.name, "stages": results}


def _judge_batch_streaming(
    job_id: str, batch_dir: Path, judge_model: str | None, dry_run: bool
) -> dict[str, Any]:
    """Judge each run under *batch_dir*, publishing per-run progress."""
    def _on_run(run_id: str, score: Any, index: int, total: int) -> None:
        hub.publish(job_id, {
            "type": "judge_progress",
            "job_id": job_id,
            "run_id": run_id,
            "score": score,
            "index": index,
            "total": total,
        })

    return judge_batch_dir(
        batch_dir,
        judge_model=judge_model,
        dry_run=dry_run,
        on_run_complete=_on_run,
    )


def start_judge_job(
    target_type: str,
    target_path: str,
    *,
    judge_model: str | None = None,
    dry_run: bool = False,
) -> str:
    """Start an async judge job and return its id immediately.

    *target_type* is ``"run"`` (judge every stage of one run dir) or
    ``"batch"`` (judge every run under a batch dir). Progress streams to
    the hub under the returned job id; the final result is stored on the
    job and a terminal ``judge_complete`` event closes the stream.

    Raises
    ------
    ValueError
        When *target_type* is unknown or *target_path* does not exist.
    """
    if target_type not in ("run", "batch"):
        raise ValueError("target_type must be 'run' or 'batch'")
    path = Path(target_path)
    if not path.exists():
        raise ValueError(f"target path not found: {target_path}")

    job_id = generate_run_id(f"judge-{target_type}")
    _register(job_id, {
        "job_id": job_id,
        "kind": "judge",
        "target_type": target_type,
        "target_path": str(path),
        "dry_run": dry_run,
        "status": "running",
    })

    def _run() -> None:
        try:
            if target_type == "run":
                result = _judge_run_streaming(job_id, path, judge_model, dry_run)
            else:
                result = _judge_batch_streaming(job_id, path, judge_model, dry_run)
            _update(job_id, {"status": "complete", "result": result})
            hub.publish(job_id, {
                "type": "judge_complete", "job_id": job_id, "status": "complete",
            })
        except Exception as exc:
            _update(job_id, {"status": "error", "error": str(exc)})
            hub.publish(job_id, {
                "type": "judge_complete", "job_id": job_id,
                "status": "error", "error": str(exc),
            })
        finally:
            hub.close(job_id)

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def list_judge_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Return runs annotated for the Judge view (judge status + score)."""
    runs = catalog.list_runs(runs_dir)
    for r in runs:
        r["judge_status"] = "judged" if r.get("judged") else "pending"
    return runs


def list_judge_batches(runs_dir: Path) -> list[dict[str, Any]]:
    """Return batch directories under *runs_dir*.

    A batch is a directory whose own children are run directories. This
    lets an experiment that groups many runs in one folder appear as a
    single judgeable batch, mirroring the old batches table.
    """
    result: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return result
    for child in sorted(runs_dir.iterdir(), reverse=True):
        if not child.is_dir() or (child / "stages").is_dir():
            continue
        run_dirs = discover_runs(child)
        if not run_dirs:
            continue
        judged = 0
        scores: list[float] = []
        for rd in run_dirs:
            stage_scores: list[float] = []
            for jp in (rd / "stages").glob("*/judge.json"):
                jd = _read_json(jp)
                if jd and isinstance(jd.get("score"), (int, float)):
                    stage_scores.append(float(jd["score"]))
            if stage_scores:
                judged += 1
                scores.append(sum(stage_scores) / len(stage_scores))
        result.append({
            "batch_dir": str(child),
            "name": child.name,
            "run_count": len(run_dirs),
            "judged_count": judged,
            "average_final_score": round(sum(scores) / len(scores), 3) if scores else None,
        })
    return result


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON object from *path*, returning ``None`` on any error."""
    import json
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None
