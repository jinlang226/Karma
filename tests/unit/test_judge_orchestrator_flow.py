from app.orchestrator_core.judge_flow import iter_outcome_runs, judge_run_records
from app.settings import ROOT


def test_iter_outcome_runs_handles_single_and_sweep():
    single = {"status": "passed", "run_dir": "runs/x"}
    runs = list(iter_outcome_runs("cid", single))
    assert len(runs) == 1
    assert runs[0]["outcome"]["run_dir"] == "runs/x"

    sweep = {
        "status": "sweep_completed",
        "runs": [
            {"status": "passed", "run_dir": "runs/a"},
            {"status": "failed", "run_dir": "runs/b"},
        ],
    }
    runs = list(iter_outcome_runs("cid", sweep))
    assert len(runs) == 2
    assert [item["outcome"]["run_dir"] for item in runs] == ["runs/a", "runs/b"]


class _FakeJudge:
    def evaluate_run(self, run_dir, service=None, case=None):
        return {
            "judge_status": "ok",
            "run_dir": run_dir,
            "service": service,
            "case": case,
            "final_score": 4.2,
        }


def test_judge_run_records_attaches_results():
    outcome = {"status": "passed", "run_dir": "runs/abc"}
    records = [{"case_id": "invalid-case-id", "outcome": outcome}]

    judged = judge_run_records(
        _FakeJudge(),
        records,
        decode_case_id=lambda _case_id: ("rabbitmq-experiments", "manual_monitoring", "test.yaml"),
        fail_open=True,
    )
    assert len(judged) == 1
    assert outcome.get("judge", {}).get("judge_status") == "ok"
    assert judged[0]["final_score"] == 4.2


def test_judge_run_records_normalizes_absolute_run_dir_under_root():
    abs_run_dir = str((ROOT / "runs" / "abs-case").resolve())
    outcome = {"status": "passed", "run_dir": abs_run_dir}
    records = [{"case_id": "invalid-case-id", "outcome": outcome}]

    judged = judge_run_records(
        _FakeJudge(),
        records,
        decode_case_id=lambda _case_id: ("rabbitmq-experiments", "manual_monitoring", "test.yaml"),
        fail_open=True,
    )
    assert len(judged) == 1
    assert judged[0]["run_dir"] == "runs/abs-case"
    assert outcome["run_dir"] == "runs/abs-case"
