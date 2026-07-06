"""
Run-level scoring: objective stage-pass score + LLM adjudication of regression
sweep failures.

The score is mostly objective and only calls the LLM to filter false positives
out of the regression sweep:

* Each stage contributes 0.0-1.0 to the score. A stage whose oracle failed
  contributes 0.0; an oracle-passing stage contributes 1.0 by default, or --
  when an optional *rubric* is supplied -- the rubric judge's 0-1 score for that
  stage. ``score = sum(contributions) / total_stages * 100``; with no rubric
  this is exactly ``passed_stages / total_stages * 100`` -- the objective base.
* The regression sweep runs only after every stage's oracle passed (KARMA's
  workflow loop re-runs each passed stage's oracle once the whole workflow
  finished). So:
    - all stages passed and the sweep is clean      -> score = 100.
    - all stages passed and the sweep has failures  -> the LLM adjudicates each
      regressed stage. A *legitimate* regression (the agent's later work really
      broke an earlier stage) lowers the score; a *false positive* (a later
      stage was legitimately supposed to change the same shared state, so the
      stale re-check no longer applies) does not.
      ``score = (total_stages - legitimate_regressions) / total_stages * 100``.

The LLM is therefore never used to "grade" a passing run -- only to decide
whether a regression-sweep failure is real. This module reads only on-disk
artifacts and never imports ``runtime.*``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .client import call_judge_llm
from .scoring import _extract_json

# Cap each stage prompt included in the adjudicator context (keep the call bounded).
_PROMPT_CAP = 2000


def _read_json(path: Path) -> dict[str, Any]:
    """Return the parsed JSON at *path*, or an empty dict on any failure."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _read_text(path: Path, cap: int | None = None) -> str:
    """Return the text at *path* (optionally tail-capped), or '' on failure."""
    try:
        txt = path.read_text()
    except Exception:
        return ""
    if cap is not None and len(txt) > cap:
        return txt[:cap] + "\n...[truncated]..."
    return txt


def _stage_prompt(run_dir: Path, stage_id: str) -> str:
    """Return the rendered agent prompt for *stage_id*, capped."""
    return _read_text(run_dir / "stages" / stage_id / "prompt.txt", cap=_PROMPT_CAP)


def _build_adjudication_prompt(
    run_dir: Path,
    stage_id: str,
    regression_output: str,
    ordered_stage_ids: list[str],
) -> str:
    """Render the prompt asking the LLM whether a sweep failure is a regression."""
    others = []
    for sid in ordered_stage_ids:
        marker = " (THE STAGE IN QUESTION)" if sid == stage_id else ""
        others.append(f"### {sid}{marker}\n{_stage_prompt(run_dir, sid) or '(no prompt recorded)'}")
    all_prompts = "\n\n".join(others)
    return (
        "You are auditing a multi-stage Kubernetes benchmark run. Each stage asked an\n"
        "agent to perform a task; an automated oracle then checked the result. Every\n"
        "stage passed its oracle when it ran. After the whole workflow finished, KARMA\n"
        "re-ran each passed stage's oracle once more (a \"regression sweep\") to see\n"
        "whether the agent's later actions broke an earlier stage's success.\n\n"
        f"Stage \"{stage_id}\" PASSED when it ran, but its oracle now FAILS on re-run.\n"
        "Decide whether this is a REAL REGRESSION (the agent carelessly broke this\n"
        "stage's result with later actions) or a FALSE POSITIVE (the failure is\n"
        "expected -- a LATER stage was legitimately supposed to change the same state,\n"
        "so the stale re-check no longer applies).\n\n"
        f"## Oracle re-run output for {stage_id} (now failing)\n{regression_output or '(none)'}\n\n"
        "## Every stage's task, in execution order (a later stage may legitimately\n"
        "## change the state this stale oracle checks)\n"
        f"{all_prompts}\n\n"
        "Respond with ONLY a JSON object on one line:\n"
        '{"legitimate_regression": true|false, "reasoning": "<one or two sentences>"}\n'
        "- legitimate_regression=true  => the agent really broke this stage (counts against the score)\n"
        "- legitimate_regression=false => false positive; a later stage legitimately changed this state\n"
    )


def _parse_adjudication(content: str) -> dict[str, Any]:
    """Parse the LLM adjudication JSON; default to a legitimate regression.

    Defaulting to ``legitimate_regression=true`` is the conservative choice: if
    the model's answer can't be read, we keep the penalty rather than silently
    awarding full marks.
    """
    obj = _extract_json(content or "")
    if not isinstance(obj, dict):
        return {"legitimate_regression": True, "reasoning": "unparseable adjudication; kept as regression"}
    val = obj.get("legitimate_regression")
    if isinstance(val, str):
        val = val.strip().lower() in ("true", "yes", "1")
    return {
        "legitimate_regression": bool(val),
        "reasoning": str(obj.get("reasoning") or "").strip(),
    }


def score_run(
    run_dir: Path,
    *,
    rubric: dict[str, Any] | None = None,
    judge_model: str | None = None,
    judge_base_url: str | None = None,
    judge_api_key: str | None = None,
    judge_timeout_sec: int | None = None,
    judge_max_retries: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compute the run-level score and write ``{run_dir}/judge.json``.

    Returns the result dict (see module docstring for the scoring model). When
    *dry_run* is true, the adjudicator prompts are assembled and returned but no
    LLM call is made.
    """
    meta = _read_json(run_dir / "run.json") or _read_json(run_dir / "workflow_state.json")
    config = _read_json(run_dir / "config.json")
    stage_results = meta.get("stages") or meta.get("stage_results") or []
    ordered_ids = [s.get("stage_id") for s in stage_results if s.get("stage_id")]
    # Denominator is the whole workflow's stage count: a run that fails early and
    # never reaches later stages should not get credit for the stages it skipped.
    try:
        declared = int(config.get("stage_total") or 0)
    except Exception:
        declared = 0
    total = max(len(stage_results), declared)
    passed = sum(1 for s in stage_results if s.get("status") == "pass")

    # Per-stage contribution to the run score (each 0.0-1.0):
    #   oracle failed            -> 0.0  (the oracle is authoritative)
    #   oracle passed, no rubric -> 1.0  (flat full marks -- the default)
    #   oracle passed, w/ rubric -> the rubric judge's 0-1 score for that stage
    # Without a rubric these are 1.0/0.0, so base_score is exactly the old
    # passed/total fraction; a real regression later zeroes a stage (see sweep).
    contributions: dict[str, float] = {}
    rubric_log: list[str] = []
    for s in stage_results:
        sid = s.get("stage_id")
        if not sid:
            continue
        if s.get("status") != "pass":
            contributions[sid] = 0.0
        elif rubric is None or dry_run:
            contributions[sid] = 1.0
        else:
            from .engine import run_judge
            try:
                res = run_judge(
                    run_dir, sid, rubric=rubric,
                    judge_model=judge_model, judge_base_url=judge_base_url,
                    judge_api_key=judge_api_key, judge_timeout_sec=judge_timeout_sec,
                    judge_max_retries=judge_max_retries,
                )
                frac = max(0.0, min(1.0, float(res.get("score") or 0.0)))
                contributions[sid] = frac
                rubric_log.append(f"[judge]   rubric {sid} -> {round(frac, 3)}")
            except Exception as exc:  # grading failed -> keep the oracle pass
                contributions[sid] = 1.0
                rubric_log.append(f"[judge]   rubric {sid} FAILED ({exc}) -> 1.0")

    def _compose() -> float:
        return round(sum(contributions.values()) / total * 100.0, 1) if total else 0.0

    scored_with_rubric = rubric is not None and not dry_run
    base_score = _compose()

    result: dict[str, Any] = {
        "score": base_score,
        "score_max": 100.0,
        "method": ("stage-rubric + regression-adjudication" if scored_with_rubric
                   else "stage-pass + regression-adjudication"),
        "total_stages": total,
        "passed_stages": passed,
        "base_score": base_score,
        "all_passed": total > 0 and passed == total,
        "regression_sweep_run": False,
        "regression_failures": 0,
        "legitimate_regressions": 0,
        "regressions": [],
    }

    # Human-readable log, persisted to {run_dir}/judge.log alongside judge.json.
    log: list[str] = [
        f"[judge] run {run_dir.name}",
        f"[judge] {passed}/{total} stages passed -> base score {base_score}"
        + (" (rubric-scored)" if scored_with_rubric else ""),
    ]
    log.extend(rubric_log)

    # Only adjudicate when every stage passed -- otherwise the score is purely the
    # objective pass fraction and the LLM is not involved at all.
    if total == 0 or passed < total:
        result["summary"] = (
            f"{passed}/{total} stages passed -> objective score {base_score}."
            if total else "no stages to score."
        )
        log.append(f"[judge] not all stages passed -> objective score {base_score} (no LLM)")
        log.append(f"[judge] done: {result['summary']}")
        if not dry_run:
            _write(run_dir, result, log)
        return result

    sweep = meta.get("regression_sweep") or {}
    result["regression_sweep_run"] = bool(sweep)
    failures = [(sid, v) for sid, v in sweep.items() if (v or {}).get("verdict") != "pass"]
    result["regression_failures"] = len(failures)

    if not failures:
        # All oracles passed and nothing regressed -> the base score stands
        # (100.0 without a rubric; the summed rubric fractions with one).
        result["score"] = base_score
        result["summary"] = (
            f"all stages passed and the regression sweep is clean -> {base_score}."
            if sweep else f"all stages passed (single-stage / no sweep) -> {base_score}."
        )
        log.append(
            f"[judge] regression sweep clean -> {base_score}" if sweep
            else f"[judge] all stages passed, no regression sweep -> {base_score}"
        )
        if not dry_run:
            _write(run_dir, result, log)
        return result

    # Adjudicate each regression-sweep failure: real regression or false positive?
    if dry_run:
        result["dry_run"] = True
        result["regressions"] = [
            {
                "stage_id": sid,
                "prompt": _build_adjudication_prompt(
                    run_dir, sid, (v or {}).get("output") or "", ordered_ids
                ),
            }
            for sid, v in failures
        ]
        return result

    log.append(
        f"[judge] all {total} stages passed; regression sweep has {len(failures)} "
        f"failure(s) -> adjudicating each (real regression vs false positive)"
    )

    # When the caller did not name a judge model, mirror the agent that ran the
    # tasks (recorded in config.json) instead of the fixed gpt-4o default. An
    # explicit base_url/api_key still wins over the mirrored one.
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
        if judge_model:
            log.append(f"[judge] no model specified -> mirroring run agent ({judge_model})")

    legit = 0
    regressions: list[dict[str, Any]] = []
    model_used: str | None = None
    for sid, v in failures:
        output = (v or {}).get("output") or ""
        prompt = _build_adjudication_prompt(run_dir, sid, output, ordered_ids)
        try:
            raw = call_judge_llm(
                None,
                prompt=prompt,
                model=judge_model,
                base_url=judge_base_url,
                api_key=judge_api_key,
                backend=judge_backend,
                timeout_sec=judge_timeout_sec or 120,
                max_retries=judge_max_retries if judge_max_retries is not None else 3,
            )
            model_used = raw.get("model") or model_used
            verdict = _parse_adjudication(raw.get("content") or "")
        except Exception as exc:
            # On adjudication failure, conservatively keep it as a real regression.
            verdict = {"legitimate_regression": True, "reasoning": f"adjudication error: {exc}"}
        is_legit = bool(verdict.get("legitimate_regression"))
        if is_legit:
            legit += 1
            contributions[sid] = 0.0  # a real regression zeroes this stage
        reasoning = verdict.get("reasoning") or ""
        regressions.append({
            "stage_id": sid,
            "legitimate": is_legit,
            "reasoning": reasoning,
            "output": output,
        })
        log.append(
            f"[judge]   {sid}: {'REAL REGRESSION' if is_legit else 'false positive'} "
            f"-- {reasoning}"
        )

    score = _compose()  # regressed stages were zeroed in the loop above
    result["legitimate_regressions"] = legit
    result["regressions"] = regressions
    result["score"] = score
    result["model"] = model_used
    fp = len(failures) - legit
    result["summary"] = (
        f"all {total} stages passed; {len(failures)} regression-sweep failure(s): "
        f"{legit} real regression(s), {fp} false positive(s) -> score {score}."
    )
    log.append(f"[judge] adjudicated by {model_used or 'judge'}")
    log.append(f"[judge] done: {result['summary']}")
    _write(run_dir, result, log)
    return result


def _write(run_dir: Path, result: dict[str, Any], log: list[str] | None = None) -> None:
    """Persist the run-level judge result to ``{run_dir}/judge.json`` and, when
    provided, the human-readable judge log to ``{run_dir}/judge.log``."""
    try:
        (run_dir / "judge.json").write_text(json.dumps(result, indent=2))
    except Exception:
        pass
    if log is not None:
        try:
            (run_dir / "judge.log").write_text("\n".join(log) + "\n")
        except Exception:
            pass
