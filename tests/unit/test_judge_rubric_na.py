"""Rubric grading: distinguish an oracle fail (Not Applicable, counted 0) from a
judge fault (N/A, no score at all)."""

import json
from unittest.mock import patch

from karma.judge.scoring import aggregate_scores
from karma.judge.run_score import score_run

_RUBRIC = {"items": [{"id": "c", "weight": 1.0, "description": "x", "rubric": "y"}],
           "passing_threshold": 0.5}


def _run(tmp_path, stages):
    rd = tmp_path / "run"; rd.mkdir()
    (rd / "run.json").write_text(json.dumps({"run_id": "r", "status": "complete", "stages": stages}))
    return rd


class TestGradeableFlag:
    def test_empty_response_is_ungradeable(self):
        assert aggregate_scores({"content": ""}, rubric=_RUBRIC, stage_id="s")["gradeable"] is False

    def test_garbage_response_is_ungradeable(self):
        assert aggregate_scores({"content": "sorry, cannot"}, rubric=_RUBRIC, stage_id="s")["gradeable"] is False

    def test_a_real_zero_is_gradeable(self):
        # The LLM engaged and scored the item 0 -- a genuine 0, NOT a fault.
        r = aggregate_scores({"content": '[{"id":"c","score":0,"reasoning":"bad"}]'},
                             rubric=_RUBRIC, stage_id="s")
        assert r["gradeable"] is True and r["score"] == 0.0


class TestOracleFailNotApplicable:
    def test_failed_stage_is_not_applicable_and_still_counted(self, tmp_path):
        rd = _run(tmp_path, [{"stage_id": "stage_1", "status": "pass"},
                             {"stage_id": "stage_2", "status": "fail"}])
        with patch("karma.judge.engine.run_judge",
                   lambda run_dir, sid, **k: {"score": 100.0, "gradeable": True, "rubric_items": []}):
            res = score_run(rd, rubric=_RUBRIC, judge_model="test")
        ss = {s["stage_id"]: s for s in res["stage_scores"]}
        assert ss["stage_2"]["rubric_state"] == "not_applicable"
        assert ss["stage_2"]["score"] == 0.0        # oracle-fail stage still counts as 0
        assert res["score"] == 50.0                  # run IS scored (not N/A)
        assert not res.get("rubric_unavailable")


class TestJudgeFaultNA:
    def test_ungradeable_stage_fails_the_whole_rubric_judge(self, tmp_path):
        rd = _run(tmp_path, [{"stage_id": "stage_1", "status": "pass"},
                             {"stage_id": "stage_2", "status": "pass"}])

        def fake(run_dir, sid, **k):
            if sid == "stage_1":
                return {"score": 90.0, "gradeable": True, "rubric_items": []}
            return {"score": 0.0, "gradeable": False, "rubric_items": []}  # judge fault

        with patch("karma.judge.engine.run_judge", fake):
            res = score_run(rd, rubric=_RUBRIC, judge_model="test")
        assert res["score"] is None                  # no w/ rubric score at all
        assert res["rubric_unavailable"] is True
