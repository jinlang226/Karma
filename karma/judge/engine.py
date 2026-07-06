"""
Judge orchestration and result writes.

Drives the full LLM-as-Judge evaluation pipeline for a stage or a batch
of stages. Loads artifacts from disk, assembles the request payload,
calls the LLM, aggregates scores, and writes the result.

This module must not import ``runtime.*``. The judge pipeline runs
entirely from artifacts written to disk, which means it can be invoked
post-hoc on any run directory without a live cluster or agent process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .input_builder import build_judge_input
from .rubric import load_rubric
from .client import call_judge_llm
from .scoring import aggregate_scores


def run_judge(
    run_dir: Path,
    stage_id: str,
    *,
    rubric: dict[str, Any] | None = None,
    rubric_overrides: dict[str, Any] | None = None,
    judge_model: str | None = None,
    judge_base_url: str | None = None,
    judge_api_key: str | None = None,
    judge_timeout_sec: int | None = None,
    judge_max_retries: int | None = None,
    include_outcome: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Evaluate one stage with the LLM judge and return the result dict.

    Loads evidence and oracle artifacts, assembles the request payload,
    calls the LLM via ``judge.client``, aggregates scores, and writes the
    result to ``{run_dir}/stages/{stage_id}/judge.json``.

    When *dry_run* is ``True``, builds and returns the judge input payload
    without calling the LLM. Useful for inspecting the request before
    committing tokens.

    Parameters
    ----------
    run_dir:
        Root directory of the run to evaluate.
    stage_id:
        ID of the stage to evaluate.
    rubric_overrides:
        Optional rubric overrides merged on top of the base rubric via
        ``judge.rubric.merge_rubric_overrides``.
    judge_model:
        LLM model name override. Falls back to the ``KARMA_JUDGE_MODEL``
        environment variable and then the client default.
    dry_run:
        When ``True``, returns the assembled input payload without calling
        the LLM.

    Raises
    ------
    RuntimeError
        When required artifacts are missing from *run_dir*.

    Returns
    -------
    dict
        Keys: ``stage_id``, ``verdict`` (``"pass"``, ``"fail"``, or
        ``"partial"``), ``score`` (float), ``rubric_items`` (list[dict]),
        ``reasoning`` (str), ``raw_response`` (dict).
    """
    # An explicit *rubric* (e.g. the one --rubric loaded once for the whole run)
    # wins; otherwise resolve the per-stage rubric from disk.
    if rubric is None:
        rubric = load_rubric(run_dir, stage_id, overrides=rubric_overrides)
    judge_input = build_judge_input(run_dir, stage_id, rubric=rubric)
    judge_input["_include_outcome"] = include_outcome

    if dry_run:
        return {"stage_id": stage_id, "dry_run": True, "input": judge_input}

    # When no judge model was named, mirror the agent that ran the tasks
    # (recorded in config.json) instead of the fixed gpt-4o default.
    judge_backend: str | None = None
    if judge_model is None:
        from .agent_defaults import resolve_agent_judge_defaults
        derived = resolve_agent_judge_defaults(run_dir)
        judge_model = derived.get("model")
        judge_backend = derived.get("backend")
        if judge_base_url is None:
            judge_base_url = derived.get("base_url")
        if judge_api_key is None:
            judge_api_key = derived.get("api_key")

    llm_kwargs: dict[str, Any] = {"model": judge_model}
    if judge_backend is not None:
        llm_kwargs["backend"] = judge_backend
    if judge_base_url is not None:
        llm_kwargs["base_url"] = judge_base_url
    if judge_api_key is not None:
        llm_kwargs["api_key"] = judge_api_key
    if judge_timeout_sec is not None:
        llm_kwargs["timeout_sec"] = judge_timeout_sec
    if judge_max_retries is not None:
        llm_kwargs["max_retries"] = judge_max_retries
    raw_response = call_judge_llm(judge_input, **llm_kwargs)
    # The oracle is authoritative: a stage the oracle failed can never be a
    # judge "pass". Thread its verdict into scoring so determine_verdict can
    # enforce that (judge_input["oracle"] is the persisted oracle result dict).
    oracle_verdict = (judge_input.get("oracle") or {}).get("verdict")
    result = aggregate_scores(
        raw_response, rubric=rubric, stage_id=stage_id, oracle_verdict=oracle_verdict
    )

    output_path = run_dir / "stages" / stage_id / "judge.json"
    try:
        import json
        output_path.write_text(json.dumps(result, indent=2))
    except Exception:
        pass

    return result


def run_judge_batch(
    run_dir: Path,
    *,
    stage_ids: list[str] | None = None,
    rubric_overrides: dict[str, Any] | None = None,
    judge_model: str | None = None,
    judge_base_url: str | None = None,
    judge_api_key: str | None = None,
    judge_timeout_sec: int | None = None,
    judge_max_retries: int | None = None,
    include_outcome: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Evaluate all stages in a run and return a batch result dict.

    When *stage_ids* is ``None``, discovers all stage directories under
    ``{run_dir}/stages/`` automatically.

    Parameters
    ----------
    run_dir:
        Root directory of the run to evaluate.
    stage_ids:
        Explicit list of stage IDs to evaluate, or ``None`` to discover
        all stages.
    rubric_overrides:
        Optional rubric overrides applied to every stage.
    judge_model:
        LLM model name override forwarded to each :func:`run_judge` call.
    dry_run:
        When ``True``, each stage returns its assembled judge input
        without calling the LLM. Forwarded to :func:`run_judge`.

    Returns
    -------
    dict
        Map of ``stage_id`` to the individual judge result dict for that
        stage.
    """
    if stage_ids is None:
        stages_dir = run_dir / "stages"
        if stages_dir.exists():
            stage_ids = sorted(d.name for d in stages_dir.iterdir() if d.is_dir())
        else:
            stage_ids = []

    batch: dict[str, Any] = {}
    for sid in (stage_ids or []):
        try:
            batch[sid] = run_judge(
                run_dir,
                sid,
                rubric_overrides=rubric_overrides,
                judge_model=judge_model,
                judge_base_url=judge_base_url,
                judge_api_key=judge_api_key,
                judge_timeout_sec=judge_timeout_sec,
                judge_max_retries=judge_max_retries,
                include_outcome=include_outcome,
                dry_run=dry_run,
            )
        except Exception as exc:
            batch[sid] = {"stage_id": sid, "verdict": "error", "error": str(exc)}
    return batch
