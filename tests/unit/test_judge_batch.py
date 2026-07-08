"""Unit tests for karma.judge.batch (cross-run evaluation)."""

import json

from karma.judge import batch


def _make_run(run_dir, stage_scores):
    """Create a run dir with one judge.json per stage carrying a score."""
    for i, score in enumerate(stage_scores, start=1):
        sd = run_dir / "stages" / f"stage_{i}"
        sd.mkdir(parents=True)
        (sd / "judge.json").write_text(json.dumps({"stage_id": f"stage_{i}", "score": score}))


class TestDiscoverRuns:
    def test_finds_run_dirs_with_stages(self, tmp_path):
        _make_run(tmp_path / "run-a", [1.0])
        (tmp_path / "not-a-run").mkdir()
        found = batch.discover_runs(tmp_path)
        assert [p.name for p in found] == ["run-a"]

    def test_missing_dir_returns_empty(self, tmp_path):
        assert batch.discover_runs(tmp_path / "nope") == []


class TestJudgeBatchDir:
    def test_aggregates_average_across_runs(self, tmp_path, monkeypatch):
        _make_run(tmp_path / "run-a", [1.0])
        _make_run(tmp_path / "run-b", [1.0])

        # Avoid hitting the LLM: stub the run-level scorer to return a per-run score.
        run_score = {"run-a": 90.0, "run-b": 50.0}
        monkeypatch.setattr(
            "karma.judge.run_score.score_run",
            lambda run_dir, **kw: {"score": run_score[run_dir.name], "summary": "x"},
        )

        result = batch.judge_batch_dir(tmp_path)
        assert result["run_count"] == 2
        assert result["judged_count"] == 2
        # mean of the per-run scores 90.0 and 50.0
        assert result["average_final_score"] == 70.0
        assert {r["run_id"] for r in result["runs"]} == {"run-a", "run-b"}

    def test_on_run_complete_callback_invoked(self, tmp_path, monkeypatch):
        _make_run(tmp_path / "run-a", [1.0])
        monkeypatch.setattr(
            "karma.judge.run_score.score_run",
            lambda run_dir, **kw: {"score": 100.0, "summary": "x"},
        )
        seen = []
        batch.judge_batch_dir(tmp_path, on_run_complete=lambda *a: seen.append(a))
        assert seen == [("run-a", 100.0, 1, 1)]

    def test_dry_run_has_no_scores(self, tmp_path, monkeypatch):
        _make_run(tmp_path / "run-a", [1.0])
        monkeypatch.setattr(
            "karma.judge.run_score.score_run",
            lambda run_dir, **kw: {"score": 100.0, "summary": "x"},
        )
        result = batch.judge_batch_dir(tmp_path, dry_run=True)
        assert result["average_final_score"] is None
        assert result["runs"][0]["score"] is None
