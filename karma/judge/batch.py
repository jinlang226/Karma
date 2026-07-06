"""
Cross-run batch evaluation.

A *batch directory* is a directory whose immediate children are run
directories (a child counts as a run when it contains a ``stages/``
subdirectory) -- an experiment holding many independent runs, judged together
so its mean score can be reported.

Each run is scored with :func:`judge.run_score.score_run` (the same run-level
scorer the Results-page buttons use), and those per-run scores are averaged
into the batch's ``average_final_score``. Using the one scorer keeps the batch
mean consistent with the per-run scores and lets an optional rubric flow
through to every run.

No ``runtime.*`` imports: like the rest of the judge package this operates
entirely on artifacts already written to disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _is_run_dir(path: Path) -> bool:
    """Return ``True`` when *path* looks like a run directory (has stages/)."""
    return path.is_dir() and (path / "stages").is_dir()


def discover_runs(batch_dir: Path) -> list[Path]:
    """Return the sorted run directories directly under *batch_dir*."""
    if not batch_dir.is_dir():
        return []
    return [d for d in sorted(batch_dir.iterdir()) if _is_run_dir(d)]


def judge_batch_dir(
    batch_dir: Path,
    *,
    rubric: dict[str, Any] | None = None,
    judge_model: str | None = None,
    judge_base_url: str | None = None,
    judge_api_key: str | None = None,
    judge_timeout_sec: int | None = None,
    judge_max_retries: int | None = None,
    dry_run: bool = False,
    on_run_complete: Any | None = None,
) -> dict[str, Any]:
    """Score every run under *batch_dir* and return the experiment mean.

    Each run is scored with :func:`judge.run_score.score_run` -- the same
    run-level scorer the Results-page buttons use -- so the batch mean and the
    per-run scores are consistent, and an optional *rubric* flows through to
    every run. The optional *on_run_complete* callback is invoked with
    ``(run_id, run_score, index, total)`` after each run so a caller can stream
    progress.

    Returns
    -------
    dict
        Keys: ``batch_dir`` (str), ``run_count`` (int), ``judged_count``
        (int, runs that produced a numeric score), ``average_final_score``
        (float or ``None``), and ``runs`` (list of per-run dicts each with
        ``run_id``, ``score``, and ``summary``).
    """
    from .run_score import score_run

    run_dirs = discover_runs(batch_dir)
    runs: list[dict[str, Any]] = []
    run_scores: list[float] = []

    total = len(run_dirs)
    for idx, run_dir in enumerate(run_dirs):
        result = score_run(
            run_dir,
            rubric=rubric,
            judge_model=judge_model,
            judge_base_url=judge_base_url,
            judge_api_key=judge_api_key,
            judge_timeout_sec=judge_timeout_sec,
            judge_max_retries=judge_max_retries,
            dry_run=dry_run,
        )
        score = None if dry_run else result.get("score")
        if isinstance(score, (int, float)):
            run_scores.append(float(score))
        runs.append(
            {"run_id": run_dir.name, "score": score, "summary": result.get("summary")}
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
