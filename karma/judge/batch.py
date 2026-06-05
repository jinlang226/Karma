"""
Cross-run batch evaluation.

``engine.run_judge_batch`` judges every *stage* within a single run. The
old framework also had a coarser notion of a *batch*: a directory holding
many independent run directories, judged together so an experiment's mean
score could be reported. That cross-run aggregation lived only in the old
codebase; this module restores it on top of the per-run engine.

A *batch directory* is any directory whose immediate children are run
directories (a child counts as a run when it contains a ``stages/``
subdirectory). Each run is judged via ``engine.run_judge_batch`` and its
per-stage scores are averaged into a single run score; the run scores are
then averaged into the batch's ``average_final_score``.

No ``runtime.*`` imports: like the rest of the judge package this operates
entirely on artifacts already written to disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import run_judge_batch


def _is_run_dir(path: Path) -> bool:
    """Return ``True`` when *path* looks like a run directory (has stages/)."""
    return path.is_dir() and (path / "stages").is_dir()


def discover_runs(batch_dir: Path) -> list[Path]:
    """Return the sorted run directories directly under *batch_dir*."""
    if not batch_dir.is_dir():
        return []
    return [d for d in sorted(batch_dir.iterdir()) if _is_run_dir(d)]


def _mean_stage_score(stage_results: dict[str, Any]) -> float | None:
    """Return the mean numeric ``score`` across stage result dicts, or ``None``."""
    scores = [
        float(r["score"])
        for r in stage_results.values()
        if isinstance(r, dict) and isinstance(r.get("score"), (int, float))
    ]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 3)


def judge_batch_dir(
    batch_dir: Path,
    *,
    rubric_overrides: dict[str, Any] | None = None,
    judge_model: str | None = None,
    dry_run: bool = False,
    on_run_complete: Any | None = None,
) -> dict[str, Any]:
    """Judge every run under *batch_dir* and return an aggregated result.

    Each discovered run is evaluated with :func:`engine.run_judge_batch`.
    The optional *on_run_complete* callback is invoked with
    ``(run_id, run_score, index, total)`` after each run so a caller can
    stream progress.

    Returns
    -------
    dict
        Keys: ``batch_dir`` (str), ``run_count`` (int), ``judged_count``
        (int, runs that produced a numeric score), ``average_final_score``
        (float or ``None``), and ``runs`` (list of per-run dicts each with
        ``run_id``, ``score``, and ``stages``).
    """
    run_dirs = discover_runs(batch_dir)
    runs: list[dict[str, Any]] = []
    run_scores: list[float] = []

    total = len(run_dirs)
    for idx, run_dir in enumerate(run_dirs):
        stage_results = run_judge_batch(
            run_dir,
            rubric_overrides=rubric_overrides,
            judge_model=judge_model,
            dry_run=dry_run,
        )
        score = None if dry_run else _mean_stage_score(stage_results)
        if score is not None:
            run_scores.append(score)
        runs.append(
            {"run_id": run_dir.name, "score": score, "stages": stage_results}
        )
        if on_run_complete is not None:
            try:
                on_run_complete(run_dir.name, score, idx + 1, total)
            except Exception:
                pass

    average = round(sum(run_scores) / len(run_scores), 3) if run_scores else None
    return {
        "batch_dir": str(batch_dir),
        "run_count": total,
        "judged_count": len(run_scores),
        "average_final_score": average,
        "runs": runs,
    }
