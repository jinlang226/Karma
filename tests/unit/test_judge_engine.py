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


class TestRubricJudgeModelPrecedence:
    """SR2: an explicit KARMA_JUDGE_MODEL must beat the agent-mirror in the
    rubric-grading path (run_judge), as it does in run_score.py's adjudication."""

    def _judge_input(self):
        return {"stage_id": "s1", "oracle": {"verdict": "pass"}, "rubric": {}}

    def test_explicit_env_beats_agent_mirror(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KARMA_JUDGE_MODEL", "gpt-4o")
        captured = {}

        def fake_call(_ji, **kw):
            captured["model"] = kw.get("model")
            return {"content": "{}"}

        with patch("karma.judge.engine.build_judge_input", return_value=self._judge_input()), \
             patch("karma.judge.engine.call_judge_llm", side_effect=fake_call), \
             patch("karma.judge.engine.aggregate_scores", return_value={"score": 50.0}), \
             patch("karma.judge.agent_defaults.resolve_agent_judge_defaults") as mirror:
            engine.run_judge(tmp_path, "s1", rubric={"items": []})

        mirror.assert_not_called()        # agent-mirror skipped
        assert captured["model"] is None  # -> client resolves KARMA_JUDGE_MODEL

    def test_mirrors_agent_when_no_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("KARMA_JUDGE_MODEL", raising=False)
        captured = {}

        def fake_call(_ji, **kw):
            captured["model"] = kw.get("model")
            return {"content": "{}"}

        with patch("karma.judge.engine.build_judge_input", return_value=self._judge_input()), \
             patch("karma.judge.engine.call_judge_llm", side_effect=fake_call), \
             patch("karma.judge.engine.aggregate_scores", return_value={"score": 50.0}), \
             patch("karma.judge.agent_defaults.resolve_agent_judge_defaults",
                   return_value={"model": "sonnet", "backend": "claude_cli"}) as mirror:
            engine.run_judge(tmp_path, "s1", rubric={"items": []})

        mirror.assert_called_once()
        assert captured["model"] == "sonnet"
