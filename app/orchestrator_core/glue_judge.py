from app.orchestrator_core.judge_flow import (
    drain_pending_judge_records as _judge_flow_drain_pending_judge_records,
    iter_outcome_runs as _judge_flow_iter_outcome_runs,
    judge_run_records as _judge_flow_judge_run_records,
    route_case_records_for_judging as _judge_flow_route_case_records_for_judging,
    write_batch_judge_summary as _judge_flow_write_batch_judge_summary,
)


def _iter_outcome_runs(case_id, outcome):
    yield from _judge_flow_iter_outcome_runs(case_id, outcome)


def _judge_run_records(judge_engine, records, decode_case_id, fail_open=True):
    return _judge_flow_judge_run_records(
        judge_engine,
        records,
        decode_case_id=decode_case_id,
        fail_open=fail_open,
    )


def _route_case_records_for_judging(
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
):
    return _judge_flow_route_case_records_for_judging(
        judge_engine,
        case_id,
        outcome,
        command=command,
        judge_mode=judge_mode,
        pending_judge_records=pending_judge_records,
        judged_runs=judged_runs,
        decode_case_id=decode_case_id,
        fail_open=fail_open,
        iter_outcome_runs_fn=_iter_outcome_runs,
        judge_run_records_fn=_judge_run_records,
    )


def _drain_pending_judge_records(
    judge_engine,
    pending_judge_records,
    judged_runs,
    *,
    decode_case_id,
    fail_open=True,
):
    return _judge_flow_drain_pending_judge_records(
        judge_engine,
        pending_judge_records,
        judged_runs,
        decode_case_id=decode_case_id,
        fail_open=fail_open,
        judge_run_records_fn=_judge_run_records,
    )


def _write_batch_judge_summary(judge_engine, batch_dir, judged_runs):
    return _judge_flow_write_batch_judge_summary(
        judge_engine,
        batch_dir,
        judged_runs,
        print_fn=print,
    )
