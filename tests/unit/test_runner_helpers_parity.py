import json
import shutil
from unittest.mock import patch

from app.orchestrator_cli import get_orchestrator_cli_options
from app.runner import BenchmarkApp
from app.runner_core import judge_jobs as judge_jobs_core
from app.runner_core import workflow_jobs as workflow_jobs_core
from app.runner_core.helpers import (
    build_judge_tokens,
    build_workflow_tokens,
    count_batch_runs,
    format_tokens_preview,
    sanitize_label_value,
)
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_sanitize_label_value_wrapper_parity():
    app = _make_app()
    for raw in (None, "alpha", "a/b", "-bad-", "x" * 120):
        assert app._sanitize_label_value(raw) == sanitize_label_value(raw)


def test_count_batch_runs_wrapper_parity():
    rows = [
        {
            "run_dir": "runs/a",
            "runs": [{"run_dir": "runs/b"}],
            "result": {"run_dir": "runs/c", "runs": [{"run_dir": "runs/a"}]},
        }
    ]
    assert count_batch_runs(rows) == 3


def test_format_tokens_preview_wrapper_parity():
    tokens = ["python3", "orchestrator.py", "workflow-run", "--workflow", "workflows/x.yaml"]
    preview = format_tokens_preview(tokens)
    assert preview["tokens"] == tokens
    assert "workflow-run" in preview["command_one_line"]


def test_build_workflow_tokens_wrapper_parity():
    app = _make_app()
    workflows_dir = ROOT / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    wf_path = workflows_dir / "unit_r1_parity.yaml"
    wf_path.write_text(
        "\n".join(
            [
                "apiVersion: benchmark/v1alpha1",
                "kind: Workflow",
                "metadata:",
                "  name: unit-r1-parity",
                "spec:",
                "  prompt_mode: progressive",
                "  stages:",
                "  - id: s1",
                "    service: rabbitmq-experiments",
                "    case: manual_monitoring",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rel = str(wf_path.relative_to(ROOT))
    flags = {"sandbox": "docker", "agent": "react", "max_attempts": 1}
    options = get_orchestrator_cli_options()
    try:
        wrapped_tokens, wrapped_error, resolved = workflow_jobs_core.build_workflow_tokens_for_app(
            app,
            "run",
            rel,
            flags=flags,
            dry_run=False,
        )
        helper_tokens, helper_error = build_workflow_tokens(
            action="run",
            workflow_path=rel,
            flags=flags,
            defaults=options.get("defaults") or {},
            choices=options.get("choices") or {},
            dry_run=False,
        )
        assert wrapped_error == helper_error is None
        assert wrapped_tokens == helper_tokens
        assert resolved is not None
    finally:
        wf_path.unlink(missing_ok=True)
        try:
            workflows_dir.rmdir()
        except Exception:
            pass


def test_build_judge_tokens_wrapper_parity():
    app = _make_app()
    run_dir = RUNS_DIR / "unit_r1_judge_target"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps({"service": "svc", "case": "case"}), encoding="utf-8")
    rel = str(run_dir.relative_to(ROOT))
    try:
        wrapped_tokens, wrapped_error, resolved = judge_jobs_core.build_judge_tokens_for_app(
            app,
            "run",
            rel,
            dry_run=True,
            judge_env_file="judge.env",
        )
        helper_tokens, helper_error = build_judge_tokens(
            "run",
            rel,
            dry_run=True,
            judge_env_file="judge.env",
        )
        assert wrapped_error == helper_error is None
        assert wrapped_tokens == helper_tokens
        assert resolved is not None
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
