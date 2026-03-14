from __future__ import annotations

from pathlib import Path

from app.settings import ROOT


def _normalize_run_dir(run_dir):
    text = str(run_dir or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        return str(path)
    try:
        return str(path.resolve().relative_to(ROOT))
    except Exception:
        return str(path)


def iter_outcome_runs(case_id, outcome):
    if not isinstance(outcome, dict):
        return
    runs = outcome.get("runs")
    if isinstance(runs, list):
        for item in runs:
            if isinstance(item, dict):
                yield {"case_id": case_id, "outcome": item}
        return
    yield {"case_id": case_id, "outcome": outcome}


def judge_run_records(judge_engine, records, *, decode_case_id, fail_open=True):
    judged = []
    for item in records:
        outcome = item.get("outcome") or {}
        run_dir = _normalize_run_dir(outcome.get("run_dir"))
        if not run_dir:
            continue
        outcome["run_dir"] = run_dir
        service = None
        case = None
        try:
            service, case, _ = decode_case_id(item.get("case_id"))
        except Exception:
            pass
        try:
            result = judge_engine.evaluate_run(
                run_dir=run_dir,
                service=service,
                case=case,
            )
            outcome["judge"] = result
            judged.append(result)
        except Exception as exc:
            if not fail_open:
                raise
            fallback = {
                "judge_status": "error",
                "error": str(exc),
                "run_dir": run_dir,
                "service": service,
                "case": case,
                "warnings": [f"judge evaluation failed: {exc}"],
            }
            outcome["judge"] = fallback
            judged.append(fallback)
    return judged


def route_case_records_for_judging(
    judge_engine,
    case_id,
    outcome,
    *,
    command,
    judge_mode,
    pending_judge_records,
    judged_runs,
    decode_case_id,
    fail_open=True,
    iter_outcome_runs_fn=iter_outcome_runs,
    judge_run_records_fn=judge_run_records,
):
    run_records = list(iter_outcome_runs_fn(case_id, outcome))
    if not judge_engine:
        return run_records
    should_judge_now = judge_mode == "post-run" or command == "run"
    if should_judge_now:
        judged_runs.extend(
            judge_run_records_fn(
                judge_engine,
                run_records,
                decode_case_id=decode_case_id,
                fail_open=fail_open,
            )
        )
    else:
        pending_judge_records.extend(run_records)
    return run_records


def drain_pending_judge_records(
    judge_engine,
    pending_judge_records,
    judged_runs,
    *,
    decode_case_id,
    fail_open=True,
    judge_run_records_fn=judge_run_records,
):
    if not (judge_engine and pending_judge_records):
        return []
    newly_judged = judge_run_records_fn(
        judge_engine,
        pending_judge_records,
        decode_case_id=decode_case_id,
        fail_open=fail_open,
    )
    judged_runs.extend(newly_judged)
    return newly_judged


def write_batch_judge_summary(judge_engine, batch_dir, judged_runs, *, print_fn=print):
    if not (judge_engine and judged_runs):
        return None
    judge_paths = judge_engine.write_batch_summary(batch_dir, judged_runs)
    print_fn(
        f"[orchestrator] judge index: {judge_paths.get('judge_index_path')}",
        flush=True,
    )
    return judge_paths
