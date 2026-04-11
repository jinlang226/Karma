"""Unit tests for karma.judge.scoring."""

import pytest
from karma.judge.scoring import (
    _extract_json,
    parse_llm_scores,
    compute_aggregate_score,
    determine_verdict,
    aggregate_scores,
)

_RUBRIC = {
    "items": [
        {"id": "correctness", "weight": 0.6, "description": "Task completed correctly."},
        {"id": "efficiency",  "weight": 0.4, "description": "Minimal unnecessary operations."},
    ],
    "passing_threshold": 0.7,
}


class TestExtractJson:
    def test_parses_bare_json(self):
        result = _extract_json('[{"id": "a", "score": 1.0}]')
        assert result[0]["id"] == "a"

    def test_parses_fenced_json(self):
        text = '```json\n[{"id": "b", "score": 0.5}]\n```'
        result = _extract_json(text)
        assert result[0]["score"] == 0.5

    def test_returns_none_on_invalid(self):
        assert _extract_json("not json at all") is None


class TestParseLlmScores:
    def test_list_format_parsed(self):
        raw = {
            "content": '[{"id": "correctness", "score": 0.9, "reasoning": "good"},'
                       '{"id": "efficiency", "score": 0.7, "reasoning": "ok"}]'
        }
        scores = parse_llm_scores(raw, rubric=_RUBRIC)
        ids = {s["id"] for s in scores}
        assert ids == {"correctness", "efficiency"}

    def test_missing_item_gets_zero_score(self):
        raw = {"content": '[{"id": "correctness", "score": 1.0, "reasoning": "perfect"}]'}
        scores = parse_llm_scores(raw, rubric=_RUBRIC)
        efficiency = next(s for s in scores if s["id"] == "efficiency")
        assert efficiency["score"] == 0.0
        assert "not scored" in efficiency["reasoning"]

    def test_score_clamped_to_unit_interval(self):
        raw = {"content": '[{"id": "correctness", "score": 1.5, "reasoning": "great"},'
                          '{"id": "efficiency", "score": -0.3, "reasoning": "bad"}]'}
        scores = parse_llm_scores(raw, rubric=_RUBRIC)
        for s in scores:
            assert 0.0 <= s["score"] <= 1.0

    def test_empty_response_gives_all_zeros(self):
        raw = {"content": ""}
        scores = parse_llm_scores(raw, rubric=_RUBRIC)
        assert all(s["score"] == 0.0 for s in scores)


class TestComputeAggregateScore:
    def test_weighted_average(self):
        scores = [
            {"id": "correctness", "score": 1.0},
            {"id": "efficiency",  "score": 0.0},
        ]
        result = compute_aggregate_score(scores, rubric=_RUBRIC)
        assert result == pytest.approx(0.6, abs=1e-4)

    def test_all_perfect_gives_one(self):
        scores = [
            {"id": "correctness", "score": 1.0},
            {"id": "efficiency",  "score": 1.0},
        ]
        assert compute_aggregate_score(scores, rubric=_RUBRIC) == pytest.approx(1.0)

    def test_empty_rubric_gives_zero(self):
        assert compute_aggregate_score([], rubric={"items": [], "passing_threshold": 0.7}) == 0.0


class TestDetermineVerdict:
    def test_pass_above_threshold(self):
        assert determine_verdict(0.9, rubric=_RUBRIC) == "pass"

    def test_partial_between_half_and_threshold(self):
        assert determine_verdict(0.4, rubric=_RUBRIC) == "partial"

    def test_fail_below_half_threshold(self):
        assert determine_verdict(0.1, rubric=_RUBRIC) == "fail"

    def test_oracle_fail_overrides_llm_score(self):
        assert determine_verdict(1.0, rubric=_RUBRIC, oracle_verdict="fail") == "fail"

    def test_oracle_pass_does_not_override(self):
        result = determine_verdict(0.9, rubric=_RUBRIC, oracle_verdict="pass")
        assert result == "pass"


class TestAggregateScores:
    def test_returns_required_keys(self):
        raw = {
            "content": '[{"id": "correctness", "score": 0.8, "reasoning": "good"},'
                       '{"id": "efficiency", "score": 0.6, "reasoning": "ok"}]'
        }
        result = aggregate_scores(raw, rubric=_RUBRIC, stage_id="stage_1")
        for key in ("stage_id", "verdict", "score", "rubric_items", "reasoning", "raw_response"):
            assert key in result

    def test_stage_id_preserved(self):
        raw = {"content": "[]"}
        result = aggregate_scores(raw, rubric=_RUBRIC, stage_id="my-stage")
        assert result["stage_id"] == "my-stage"
