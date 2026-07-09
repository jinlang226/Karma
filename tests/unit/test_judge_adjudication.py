"""Unit tests for karma.judge.run_score._parse_adjudication (M4).

The regression-sweep adjudicator must be conservative: any answer it cannot read
as an explicit verdict keeps the regression as legitimate (penalty preserved),
so a malformed or injected response can never forgive a regression into a 100.
"""

import json
from unittest.mock import patch

from karma.judge.run_score import _parse_adjudication, score_run


class TestParseAdjudication:
    def test_missing_key_defaults_to_legitimate_regression(self):
        # A dict WITHOUT the verdict key must keep the penalty (was the M4 bug:
        # bool(None) -> False forgave the regression).
        r = _parse_adjudication('{"reasoning": "no verdict field"}')
        assert r["legitimate_regression"] is True

    def test_non_dict_defaults_to_legitimate_regression(self):
        assert _parse_adjudication("not json").get("legitimate_regression") is True

    def test_explicit_false_is_respected(self):
        assert _parse_adjudication('{"legitimate_regression": false}')["legitimate_regression"] is False

    def test_explicit_true_is_respected(self):
        assert _parse_adjudication('{"legitimate_regression": true}')["legitimate_regression"] is True

    def test_string_verdicts_parse(self):
        assert _parse_adjudication('{"legitimate_regression": "false"}')["legitimate_regression"] is False
        assert _parse_adjudication('{"legitimate_regression": "yes"}')["legitimate_regression"] is True


def _write_run(run_dir):
    """A completed run: both stages passed, but the regression sweep now fails
    stage_1 -- so score_run adjudicates stage_1 (real regression vs false positive)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_dir.name,
        "status": "complete",
        "stages": [
            {"stage_id": "stage_1", "status": "pass"},
            {"stage_id": "stage_2", "status": "pass"},
        ],
        "regression_sweep": {
            "stage_1": {"verdict": "fail", "output": "pods not ready"},
            "stage_2": {"verdict": "pass"},
        },
    }))


class TestAdjudicationErrorNotCached:
    """Bug #1: an UNEXPECTED adjudication error must penalize this run but NOT be
    cached -- so a one-off fluke can't freeze a stage's penalty forever."""

    def test_error_penalizes_run_but_is_not_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KARMA_JUDGE_MODEL", "test-model")  # deterministic; no mirror
        _write_run(tmp_path)
        # A plain ValueError (NOT JudgeLLMUnavailable, which subclasses RuntimeError
        # and would abort) -> the generic `except` path.
        with patch("karma.judge.run_score.call_judge_llm", side_effect=ValueError("boom")):
            result = score_run(tmp_path, judge_model="test-model")
        # Conservative: the stage is still counted as a real regression THIS run.
        assert result["legitimate_regressions"] == 1
        # ...but the error verdict was NOT written to the shared cache.
        cache = tmp_path / "regression_adjudication.json"
        adj = (json.loads(cache.read_text()).get("adjudications") or {}) if cache.exists() else {}
        assert "stage_1" not in adj

    def test_rejudge_recovers_after_a_transient_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KARMA_JUDGE_MODEL", "test-model")
        _write_run(tmp_path)
        # First judge: adjudication errors -> penalty, nothing cached.
        with patch("karma.judge.run_score.call_judge_llm", side_effect=ValueError("boom")):
            r1 = score_run(tmp_path, judge_model="test-model")
        assert r1["legitimate_regressions"] == 1
        # Second judge: the LLM now works -> it must RE-adjudicate (cache miss),
        # not inherit the frozen error verdict, and recover to a false positive.
        good = {"content": '{"legitimate_regression": false}', "model": "test-model"}
        with patch("karma.judge.run_score.call_judge_llm", return_value=good) as mock_llm:
            r2 = score_run(tmp_path, judge_model="test-model")
        mock_llm.assert_called()                      # re-adjudicated, not reused
        assert r2["legitimate_regressions"] == 0      # false positive -> not penalized
