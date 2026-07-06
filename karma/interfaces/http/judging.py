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

# Score (0-100, = % of stages passed) at/above which the streamed progress
# verdict reads "pass". Display-only; the stored score is the source of truth.
_PASS_VERDICT_THRESHOLD = 50


def _register(job_id: str, meta: dict[str, Any]) -> None:
    with _lock:
        _judge_jobs[job_id] = meta


def _update(job_id: str, updates: dict[str, Any]) -> None:
    with _lock:
        if job_id in _judge_jobs:
            _judge_jobs[job_id].update(updates)


def request_judge_cancel(job_id: str) -> bool:
    """Flag a running judge job to stop after its current run finishes.

    Returns True if a running job was flagged. Cancellation is cooperative --
    the "Judge all" loop checks the flag between runs (the in-flight run is not
    interrupted mid-scoring), so a click stops all *remaining* work promptly.
    """
    with _lock:
        job = _judge_jobs.get(job_id)
        if not job or job.get("status") != "running":
            return False
        job["cancel_requested"] = True
        return True


def _cancel_requested(job_id: str) -> bool:
    """Return whether cancellation has been requested for *job_id*."""
    with _lock:
        job = _judge_jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


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
    job_id: str, run_dir: Path, judge_model: str | None, dry_run: bool,
    rubric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score the run: objective stage-pass score + LLM adjudication of any
    regression-sweep failures (false-positive filtering)."""
    from ...judge.run_score import score_run

    hub.publish(job_id, {
        "type": "judge_progress", "job_id": job_id, "run_id": run_dir.name,
        "message": "scoring stages and adjudicating regression sweep",
    })
    result = score_run(run_dir, rubric=rubric, judge_model=judge_model, dry_run=dry_run)
    hub.publish(job_id, {
        "type": "judge_progress", "job_id": job_id, "run_id": run_dir.name,
        "score": result.get("score"),
        "verdict": "pass" if (result.get("score") or 0) >= _PASS_VERDICT_THRESHOLD else "fail",
        "message": result.get("summary"),
    })
    return {"target_type": "run", "run_id": run_dir.name, "result": result}


def _run_has_score(run_dir: Path) -> bool:
    """True if *run_dir* already has a run-level score (``judge.json``).

    Used by "Judge all" to skip runs that are already scored under the current
    (run-level) model. Legacy per-stage ``judge.json`` from the old per-stage
    rubric does NOT count, so the first "Judge all" upgrades every run to the
    objective stage-pass + regression-adjudication score.
    """
    rj = catalog._read_json(run_dir / "judge.json")
    return bool(rj and isinstance(rj.get("score"), (int, float)))


def _run_needs_llm(run_dir: Path) -> bool:
    """Whether scoring this run will invoke the LLM adjudicator.

    Mirrors ``judge.run_score.score_run``'s decision: the LLM runs only for a run
    where every stage passed AND the regression sweep has failures to adjudicate.
    Every other run -- failed/partial (objective stage-pass %) or all-passed with
    a clean sweep (100) -- is scored statically with no LLM. Used to order the
    "Judge all" worklist so the free/static runs are scored first and the costly
    LLM ones last.
    """
    meta = (catalog._read_json(run_dir / "run.json")
            or catalog._read_json(run_dir / "workflow_state.json"))
    config = catalog._read_json(run_dir / "config.json")
    stage_results = meta.get("stages") or meta.get("stage_results") or []
    try:
        declared = int(config.get("stage_total") or 0)
    except Exception:
        declared = 0
    total = max(len(stage_results), declared)
    passed = sum(1 for s in stage_results if s.get("status") == "pass")
    if total == 0 or passed < total:
        return False  # partial/failed run -> objective score, no LLM
    sweep = meta.get("regression_sweep") or {}
    return any((v or {}).get("verdict") != "pass" for v in sweep.values())


def _judge_all_streaming(
    job_id: str, runs_dir: Path, judge_model: str | None, dry_run: bool,
    rubric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score every UNSCORED finished run under *runs_dir* (the "Judge all" button).

    Each run is scored with the run-level scorer (objective stage-pass + LLM
    adjudication of regression-sweep failures). In-progress runs and runs that
    already have a score are skipped (re-judge a single run with its own Judge
    button).
    """
    from ...judge.run_score import score_run

    # Discover run dirs recursively so runs filed under a grouping folder (e.g.
    # runs/examples/<run>) are judged too -- a top-level iterdir would only see
    # the grouping folder and score nothing.
    run_dirs = (
        sorted(catalog._find_run_dirs(runs_dir), key=lambda p: p.name)
        if runs_dir.exists() else []
    )

    # Pre-filter to the runs that actually need judging. The progress counter
    # (index/total) must reflect REAL work, not crawl through the already-scored
    # majority: with e.g. 147/151 runs already scored, emitting per-run progress
    # for every skip made the button read "47/151" -- which looks like "47 judged
    # of 151" when only one run was being judged. Skips are tallied, not streamed.
    worklist: list[Path] = []
    already_scored = 0
    not_judgeable = 0
    for rd in run_dirs:
        state = (catalog._read_json(rd / "workflow_state.json")
                 or catalog._read_json(rd / "run.json") or {})
        status = str(state.get("status") or "").strip().lower()
        # Only judge runs with a complete outcome. Skip interrupted / unknown /
        # absent / in-progress runs -- they have nothing to score.
        if status not in ("complete", "failed", "error", "passed", "cancelled"):
            not_judgeable += 1
            continue
        if _run_has_score(rd):
            already_scored += 1
            continue
        worklist.append(rd)

    total = len(worklist)
    # Order the worklist so the free/static runs are scored first and the costly
    # LLM-adjudicated runs last: fast bulk feedback (the many objective/clean runs
    # fly by), cost transparency up front, and a cancel that keeps every cheap
    # result while skipping only the expensive tail.
    static_work: list[Path] = []
    llm_work: list[Path] = []
    for rd in worklist:
        (llm_work if _run_needs_llm(rd) else static_work).append(rd)
    ordered = static_work + llm_work

    # Announce the scan up front so the UI can frame the work ("Judging N of M;
    # K already scored") instead of inferring it from a crawling counter.
    hub.publish(job_id, {
        "type": "judge_scan", "job_id": job_id, "to_judge": total,
        "already_scored": already_scored, "not_judgeable": not_judgeable,
        "total_runs": len(run_dirs),
        "static_count": len(static_work), "llm_count": len(llm_work),
    })

    results: dict[str, Any] = {}
    cancelled = False
    for i, rd in enumerate(ordered, start=1):
        # Cooperative cancel: stop before starting the next run (the current run,
        # if any, has already finished) so a click halts the remaining worklist.
        if _cancel_requested(job_id):
            cancelled = True
            break
        try:
            res = score_run(rd, rubric=rubric, judge_model=judge_model, dry_run=dry_run)
            results[rd.name] = {"score": res.get("score"), "summary": res.get("summary")}
            hub.publish(job_id, {
                "type": "judge_progress", "job_id": job_id, "run_id": rd.name,
                "score": res.get("score"), "index": i, "total": total,
            })
        except Exception as exc:
            results[rd.name] = {"error": str(exc)}
            hub.publish(job_id, {
                "type": "judge_progress", "job_id": job_id, "run_id": rd.name,
                "index": i, "total": total, "message": f"error: {exc}",
            })
    return {
        "target_type": "all", "count": len(results), "runs": results,
        "already_scored": already_scored, "not_judgeable": not_judgeable,
        "total_runs": len(run_dirs), "cancelled": cancelled,
    }


def _judge_batch_streaming(
    job_id: str, batch_dir: Path, judge_model: str | None, dry_run: bool,
    rubric: dict[str, Any] | None = None,
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
        rubric=rubric,
        judge_model=judge_model,
        dry_run=dry_run,
        on_run_complete=_on_run,
    )


def start_judge_job(
    target_type: str,
    target_path: str,
    *,
    runs_dir: Path | None = None,
    judge_model: str | None = None,
    rubric: dict[str, Any] | None = None,
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
    if target_type not in ("run", "batch", "all"):
        raise ValueError("target_type must be 'run', 'batch', or 'all'")
    if target_type == "all":
        # Score every run in the browsed folder scope (the "Judge all" button).
        # A non-empty target_path narrows "all" to that subfolder (judged
        # recursively); empty target_path judges the whole runs/ tree. The scan
        # root flows straight into _judge_all_streaming's recursive discovery.
        base = Path(runs_dir) if runs_dir is not None else Path("runs")
        path = base
        sub = str(target_path or "").strip().strip("/")
        if sub:
            candidate = (base / sub).resolve()
            # Confine to runs_dir -- reject "../" escapes and non-directories.
            if not candidate.is_relative_to(base.resolve()) or not candidate.is_dir():
                raise ValueError(f"invalid folder scope: {target_path}")
            path = candidate
    else:
        path = Path(target_path)
        # The UI passes a bare run_id (e.g. "demo-configmap-update-..."); resolve it
        # against runs_dir so the Results-page Judge button works (it has no path).
        if not path.exists() and runs_dir is not None:
            candidate = catalog.resolve_run_dir(Path(runs_dir), str(target_path))
            if candidate is not None:
                path = candidate
        if not path.exists():
            raise ValueError(f"target path not found: {target_path}")

        # A run must have a recorded, complete outcome to be judged. An
        # interrupted or unknown run never finished, so it has nothing to score.
        if target_type == "run":
            state = (catalog._read_json(path / "workflow_state.json")
                     or catalog._read_json(path / "run.json") or {})
            status = str(state.get("status") or "").strip().lower()
            judgeable = ("complete", "failed", "error", "passed", "cancelled")
            if status in ("", "unknown"):
                raise ValueError(
                    f"run '{path.name}' has an unknown status and cannot be judged; "
                    "it has no recorded outcome to score"
                )
            if status == "interrupted":
                raise ValueError(
                    f"run '{path.name}' was interrupted and cannot be judged; "
                    "it has no complete outcome to score"
                )
            if status not in judgeable:
                raise ValueError(
                    f"run '{path.name}' is still in progress ({status}); "
                    "wait for it to finish before judging"
                )

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
                result = _judge_run_streaming(job_id, path, judge_model, dry_run, rubric)
            elif target_type == "all":
                result = _judge_all_streaming(job_id, path, judge_model, dry_run, rubric)
            else:
                result = _judge_batch_streaming(job_id, path, judge_model, dry_run, rubric)
            final_status = ("cancelled"
                            if isinstance(result, dict) and result.get("cancelled")
                            else "complete")
            _update(job_id, {"status": final_status, "result": result})
            hub.publish(job_id, {
                "type": "judge_complete", "job_id": job_id, "status": final_status,
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
            # A run is judged if it has a run-level judge.json (the canonical
            # source the runs list + detail prefer) OR, as a legacy fallback, any
            # per-stage judge.json. Checking only the per-stage files made
            # judged_count always 0 for run-level-judged runs.
            score: float | None = None
            run_judge = catalog._read_json(rd / "judge.json")
            if run_judge and isinstance(run_judge.get("score"), (int, float)):
                score = float(run_judge["score"])
            else:
                stage_scores: list[float] = []
                for jp in (rd / "stages").glob("*/judge.json"):
                    jd = catalog._read_json(jp)
                    if jd and isinstance(jd.get("score"), (int, float)):
                        stage_scores.append(float(jd["score"]))
                if stage_scores:
                    score = sum(stage_scores) / len(stage_scores)
            if score is not None:
                judged += 1
                scores.append(score)
        result.append({
            "batch_dir": str(child),
            "name": child.name,
            "run_count": len(run_dirs),
            "judged_count": judged,
            "average_final_score": round(sum(scores) / len(scores), 3) if scores else None,
        })
    return result
