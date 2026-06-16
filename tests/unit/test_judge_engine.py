"""Unit tests for karma.judge.engine wiring."""

from unittest.mock import patch
from karma.judge import engine


def test_run_judge_threads_oracle_verdict_into_scoring(tmp_path):
    # The oracle is authoritative: run_judge must pass the oracle verdict from
    # the judge input into aggregate_scores so determine_verdict can force a
    # "fail" on any stage the oracle failed. (Previously oracle_verdict was
    # never passed, leaving that branch dead and letting the judge "pass" an
    # oracle-failed run.)
    judge_input = {"stage_id": "s1", "oracle": {"verdict": "fail"}, "rubric": {}}
    with patch("karma.judge.engine.load_rubric",
               return_value={"items": [], "passing_threshold": 0.5}), \
         patch("karma.judge.engine.build_judge_input", return_value=judge_input), \
         patch("karma.judge.engine.call_judge_llm", return_value={"content": "[]"}), \
         patch("karma.judge.engine.aggregate_scores",
               return_value={"verdict": "fail"}) as mock_agg:
        engine.run_judge(tmp_path, "s1")

    assert mock_agg.call_args.kwargs.get("oracle_verdict") == "fail"
