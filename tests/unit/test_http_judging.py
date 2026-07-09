"""Unit tests for karma.interfaces.http.judging."""

import json

import pytest

from karma.interfaces.http import judging


def _make_run(run_dir, judged_scores):
    run_dir.mkdir(parents=True)
    run_dir.joinpath("workflow_state.json").write_text(json.dumps({"status": "complete"}))
    for i, score in enumerate(judged_scores, start=1):
        sd = run_dir / "stages" / f"stage_{i}"
        sd.mkdir(parents=True)
        if score is not None:
            (sd / "judge.json").write_text(json.dumps({"score": score}))


class TestStartJudgeJob:
    def test_rejects_unknown_target_type(self, tmp_path):
        with pytest.raises(ValueError, match="target_type"):
            judging.start_judge_job("bogus", str(tmp_path))

    def test_rejects_missing_path(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            judging.start_judge_job("run", str(tmp_path / "nope"))

    def test_rejects_batch_path_outside_runs_dir(self, tmp_path):
        # SR4: an existing absolute path outside runs_dir must be rejected, not
        # judged. batch has no status gate, so this is the raw confinement check.
        runs = tmp_path / "runs"; runs.mkdir()
        evil = tmp_path / "evil"; evil.mkdir()  # exists, but outside runs/
        with pytest.raises(ValueError, match="outside the runs directory"):
            judging.start_judge_job("batch", str(evil), runs_dir=runs)

    def test_rejects_run_shaped_dir_outside_runs_dir(self, tmp_path):
        # SR4: even a run-shaped dir (valid workflow_state) outside runs_dir is
        # rejected -- the status gate alone is not confinement.
        runs = tmp_path / "runs"; runs.mkdir()
        evil = tmp_path / "evil"; evil.mkdir()
        (evil / "workflow_state.json").write_text(json.dumps({"status": "complete"}))
        with pytest.raises(ValueError, match="outside the runs directory"):
            judging.start_judge_job("run", str(evil), runs_dir=runs)


class TestListJudgeRuns:
    def test_annotates_judge_status(self, tmp_path):
        runs = tmp_path / "runs"
        _make_run(runs / "r-judged", [0.9])
        _make_run(runs / "r-pending", [None])
        result = {r["run_id"]: r for r in judging.list_judge_runs(runs)}
        assert result["r-judged"]["judge_status"] == "judged"
        assert result["r-pending"]["judge_status"] == "pending"


class TestListJudgeBatches:
    def test_groups_runs_into_batch(self, tmp_path):
        runs = tmp_path / "runs"
        batch_dir = runs / "experiment-1"
        _make_run(batch_dir / "run-a", [0.8])
        _make_run(batch_dir / "run-b", [0.6])
        batches = judging.list_judge_batches(runs)
        assert len(batches) == 1
        b = batches[0]
        assert b["name"] == "experiment-1"
        assert b["run_count"] == 2
        assert b["judged_count"] == 2
        assert b["average_final_score"] == 0.7

    def test_plain_run_is_not_a_batch(self, tmp_path):
        runs = tmp_path / "runs"
        _make_run(runs / "solo-run", [1.0])  # has its own stages/ => not a batch
        assert judging.list_judge_batches(runs) == []

    def test_missing_runs_dir_returns_empty(self, tmp_path):
        assert judging.list_judge_batches(tmp_path / "nope") == []


class TestJudgeStalenessRegressionHash:
    """#3: a changed regression-adjudication prompt (which lives outside runs/, so
    a mtime check can't see it) must mark an existing judge result stale."""

    def _make_judged(self, tmp_path, reg_hash):
        import os
        run = tmp_path / "r"
        run.mkdir(parents=True)
        (run / "workflow_state.json").write_text(json.dumps({"status": "complete"}))
        (run / "judge.json").write_text(json.dumps({
            "score": 100.0, "rubric_hash": None, "regression_prompt_hash": reg_hash,
        }))
        base = judging._run_input_mtime(run)          # excludes judge outputs
        os.utime(run / "judge.json", (base + 10, base + 10))  # fresh judge
        return run

    def test_current_when_regression_hash_matches(self, tmp_path):
        run = self._make_judged(tmp_path, "HASH_A")
        assert judging._judge_is_current(run, "judge", None, "HASH_A") is True

    def test_stale_when_regression_hash_differs(self, tmp_path):
        run = self._make_judged(tmp_path, "HASH_A")
        assert judging._judge_is_current(run, "judge", None, "HASH_B") is False

    def test_ignored_when_not_requested(self, tmp_path):
        run = self._make_judged(tmp_path, "HASH_A")
        assert judging._judge_is_current(run, "judge", None, None) is True
