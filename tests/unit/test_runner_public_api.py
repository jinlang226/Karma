import inspect
from unittest.mock import patch

from app.runner import BenchmarkApp


PUBLIC_METHODS = [
    "start_run",
    "submit_run",
    "cleanup_run",
    "abort_run",
    "abort_active_run",
    "finalize_active_run_without_submit",
    "run_status",
    "run_metrics",
    "proxy_status",
    "orchestrator_options",
    "orchestrator_preview",
    "list_workflow_files",
    "workflow_preview",
    "start_workflow",
    "list_workflow_jobs",
    "get_workflow_job",
    "get_workflow_stream_snapshot",
    "get_workflow_events_since",
    "list_judge_runs",
    "list_judge_batches",
    "judge_preview",
    "start_judge",
    "list_judge_jobs",
    "get_judge_job",
    "get_judge_stream_snapshot",
    "get_judge_events_since",
]


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_public_runner_api_methods_exist_and_are_callable():
    app = _make_app()
    for method_name in PUBLIC_METHODS:
        assert hasattr(app, method_name)
        assert callable(getattr(app, method_name))


def test_public_runner_api_signatures_are_stable():
    expected = {
        "start_run": [
            "self",
            "case_id",
            "max_attempts_override",
            "defer_cleanup",
            "skip_precondition_unit_ids",
            "case_data_override",
            "resolved_params",
            "namespace_context",
            "namespace_lifecycle_owner",
        ],
        "submit_run": ["self"],
        "cleanup_run": ["self"],
        "abort_run": ["self", "reason", "exit_code"],
        "abort_active_run": ["self", "reason", "exit_code"],
        "finalize_active_run_without_submit": ["self", "status", "reason"],
        "orchestrator_preview": ["self", "payload"],
        "workflow_preview": ["self", "payload"],
        "start_workflow": ["self", "payload"],
        "get_workflow_events_since": ["self", "since_seq", "timeout_sec"],
        "judge_preview": ["self", "payload"],
        "start_judge": ["self", "payload"],
        "get_judge_events_since": ["self", "since_seq", "timeout_sec"],
    }

    for method_name, params in expected.items():
        signature = inspect.signature(getattr(BenchmarkApp, method_name))
        assert list(signature.parameters.keys()) == params


def test_workflow_wrappers_delegate_to_runner_core():
    app = _make_app()
    with patch("app.runner.workflow_jobs_core.workflow_preview", return_value={"ok": True}) as preview_mock, patch(
        "app.runner.workflow_jobs_core.start_workflow",
        return_value={"ok": True, "job": {"id": "wf_1"}},
    ) as start_mock, patch(
        "app.runner.workflow_jobs_core.get_workflow_events_since",
        return_value={"reset": False, "events": [], "current_seq": 0},
    ) as events_mock:
        preview = app.workflow_preview({"workflow_path": "workflows/demo.yaml"})
        started = app.start_workflow({"action": "run", "workflow_path": "workflows/demo.yaml"})
        events = app.get_workflow_events_since(0, timeout_sec=0.0)

    assert preview == {"ok": True}
    assert started == {"ok": True, "job": {"id": "wf_1"}}
    assert events == {"reset": False, "events": [], "current_seq": 0}
    preview_mock.assert_called_once_with(app, {"workflow_path": "workflows/demo.yaml"})
    start_mock.assert_called_once_with(app, {"action": "run", "workflow_path": "workflows/demo.yaml"})
    events_mock.assert_called_once_with(app, 0, timeout_sec=0.0)


def test_orchestrator_preview_and_options_delegate_to_cli_helpers():
    app = _make_app()
    with patch("app.runner.get_orchestrator_cli_options", return_value={"defaults": {"sandbox": "docker"}}) as opts_mock, patch(
        "app.runner.build_orchestrator_preview",
        return_value={"ok": True, "command": "python3 orchestrator.py run ..."},
    ) as preview_mock:
        options = app.orchestrator_options()
        preview = app.orchestrator_preview({"service": "rabbitmq-experiments"})

    assert options == {"defaults": {"sandbox": "docker"}}
    assert preview["ok"] is True
    opts_mock.assert_called_once_with()
    preview_mock.assert_called_once_with({"service": "rabbitmq-experiments"})
