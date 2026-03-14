from app.orchestrator_core import judge_flow


class _FakeJudge:
    def __init__(self):
        self.calls = []

    def evaluate_run(self, run_dir, service=None, case=None):
        self.calls.append(
            {
                "run_dir": run_dir,
                "service": service,
                "case": case,
            }
        )
        return {
            "judge_status": "ok",
            "run_dir": run_dir,
            "service": service,
            "case": case,
            "final_score": 4.5,
        }

    def write_batch_summary(self, _batch_dir, judged_runs):
        return {
            "judge_index_path": "runs/batch/judge_index.json",
            "judge_summary_path": "runs/batch/judge_summary.json",
            "judge_leaderboard_path": "runs/batch/judge_leaderboard.csv",
            "runs_judged": len(judged_runs),
        }


def _decode_case_id(_case_id):
    return "rabbitmq-experiments", "manual_monitoring", "test.yaml"


def test_route_case_records_post_batch_queues_then_drain_judges():
    judge = _FakeJudge()
    pending = []
    judged = []
    outcome = {
        "status": "sweep_completed",
        "runs": [
            {"status": "passed", "run_dir": "runs/a"},
            {"status": "failed", "run_dir": "runs/b"},
        ],
    }

    records = judge_flow.route_case_records_for_judging(
        judge,
        "case-id",
        outcome,
        command="batch",
        judge_mode="post-batch",
        pending_judge_records=pending,
        judged_runs=judged,
        decode_case_id=_decode_case_id,
        fail_open=True,
    )

    assert len(records) == 2
    assert len(pending) == 2
    assert judged == []

    drained = judge_flow.drain_pending_judge_records(
        judge,
        pending,
        judged,
        decode_case_id=_decode_case_id,
        fail_open=True,
    )
    assert len(drained) == 2
    assert len(judged) == 2
    assert all(item.get("judge_status") == "ok" for item in judged)


def test_route_case_records_run_command_judges_immediately():
    judge = _FakeJudge()
    pending = []
    judged = []
    outcome = {"status": "passed", "run_dir": "runs/one"}

    records = judge_flow.route_case_records_for_judging(
        judge,
        "case-id",
        outcome,
        command="run",
        judge_mode="post-batch",
        pending_judge_records=pending,
        judged_runs=judged,
        decode_case_id=_decode_case_id,
        fail_open=True,
    )

    assert len(records) == 1
    assert pending == []
    assert len(judged) == 1
    assert judged[0].get("run_dir") == "runs/one"


def test_write_batch_judge_summary_contract():
    judge = _FakeJudge()
    logs = []
    out = judge_flow.write_batch_judge_summary(
        judge,
        "runs/batch_x",
        [{"judge_status": "ok"}, {"judge_status": "ok"}],
        print_fn=lambda msg, flush=True: logs.append((msg, flush)),
    )
    assert out["runs_judged"] == 2
    assert out["judge_index_path"].endswith("judge_index.json")
    assert logs and logs[0][0].startswith("[orchestrator] judge index:")
