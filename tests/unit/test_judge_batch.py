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
        _make_run(tmp_path / "run-a", [0.8, 1.0])  # mean 0.9
        _make_run(tmp_path / "run-b", [0.5])       # mean 0.5

        # Avoid hitting the LLM: stub run_judge_batch to echo stored judge.json.
        def fake_run_judge_batch(run_dir, **kwargs):
            out = {}
            for sd in sorted((run_dir / "stages").iterdir()):
                out[sd.name] = json.loads((sd / "judge.json").read_text())
            return out

        monkeypatch.setattr(batch, "run_judge_batch", fake_run_judge_batch)

        result = batch.judge_batch_dir(tmp_path)
        assert result["run_count"] == 2
        assert result["judged_count"] == 2
        # average of run means 0.9 and 0.5
        assert result["average_final_score"] == 0.7
        assert {r["run_id"] for r in result["runs"]} == {"run-a", "run-b"}

    def test_on_run_complete_callback_invoked(self, tmp_path, monkeypatch):
        _make_run(tmp_path / "run-a", [1.0])
        monkeypatch.setattr(
            batch, "run_judge_batch",
            lambda run_dir, **kw: {"stage_1": {"score": 1.0}},
        )
        seen = []
        batch.judge_batch_dir(tmp_path, on_run_complete=lambda *a: seen.append(a))
        assert seen == [("run-a", 1.0, 1, 1)]

    def test_dry_run_has_no_scores(self, tmp_path, monkeypatch):
        _make_run(tmp_path / "run-a", [1.0])
        monkeypatch.setattr(
            batch, "run_judge_batch",
            lambda run_dir, **kw: {"stage_1": {"dry_run": True}},
        )
        result = batch.judge_batch_dir(tmp_path, dry_run=True)
        assert result["average_final_score"] is None
        assert result["runs"][0]["score"] is None
