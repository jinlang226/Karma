from pathlib import Path

from app.settings import ROOT, resolve_runs_dir


def test_resolve_runs_dir_default_subdir():
    resolved = resolve_runs_dir(root=ROOT, runs_subdir="runs")
    assert resolved == (ROOT / "runs").resolve()


def test_resolve_runs_dir_nested_subdir_under_repo():
    subdir = ".benchmark/test_runs/unit_example"
    resolved = resolve_runs_dir(root=ROOT, runs_subdir=subdir)
    assert resolved == (ROOT / subdir).resolve()


def test_resolve_runs_dir_rejects_path_escape():
    try:
        resolve_runs_dir(root=ROOT, runs_subdir="../outside")
    except RuntimeError as exc:
        assert "BENCHMARK_RUNS_SUBDIR" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for escaping runs subdir")


def test_resolve_runs_dir_rejects_absolute_path_outside_repo():
    outside = Path("/tmp/codex-runs-outside-repo")
    try:
        resolve_runs_dir(root=ROOT, runs_subdir=str(outside))
    except RuntimeError as exc:
        assert "BENCHMARK_RUNS_SUBDIR" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for outside absolute path")
