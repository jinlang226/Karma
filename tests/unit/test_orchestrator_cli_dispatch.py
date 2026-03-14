import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.orchestrator_core import cli as orchestrator_cli_core
class _FakeBenchmarkApp:
    def __init__(self, runs_dir):
        self.runs_dir = runs_dir


def _base_args(command):
    return SimpleNamespace(
        command=command,
        proxy_server="127.0.0.1:65535",
        sandbox="local",
        source_kubeconfig="",
        judge_mode="off",
        judge_fail_open=True,
        agent_cleanup=False,
        results_json="",
        workflow="workflows/demo.yaml",
    )


def test_run_command_executes_single_case_path_only():
    import app.runner as runner_mod

    with tempfile.TemporaryDirectory() as td, patch.object(
        runner_mod,
        "BenchmarkApp",
        lambda: _FakeBenchmarkApp(Path(td) / "runs"),
    ):
        args = _base_args("run")
        run_calls = []
        route_calls = []
        drained = []

        def _run_case(_app, case_id, _args):
            run_calls.append(case_id)
            return {"status": "passed", "run_dir": f"runs/{case_id}"}

        def _route(_judge_engine, case_id, outcome, **_kwargs):
            route_calls.append((case_id, outcome.get("status")))
            return []

        def _drain(_judge_engine, pending_judge_records, judged_runs, **_kwargs):
            drained.append((list(pending_judge_records), list(judged_runs)))
            return []

        output = io.StringIO()
        with redirect_stdout(output):
            orchestrator_cli_core.run_parsed_args(
                args,
                default_proxy_listen="127.0.0.1:8081",
                default_proxy_control="http://127.0.0.1:8082",
                resolve_repo_root_fn=lambda: Path.cwd(),
                collect_case_ids_fn=lambda _app, _args: ["case-1", "case-2", "case-3"],
                normalize_control_url_fn=lambda url: url,
                is_local_host_fn=lambda _host: False,
                proxy_control_running_fn=lambda _url: True,
                control_listen_from_url_fn=lambda _url: "127.0.0.1:8082",
                resolve_api_server_fn=lambda _source: "https://127.0.0.1:6443",
                start_local_proxy_fn=lambda *_args, **_kwargs: None,
                wait_for_proxy_fn=lambda *_args, **_kwargs: True,
                resolve_agent_defaults_fn=lambda _args, _repo_root: None,
                collect_llm_env_fn=lambda _args, _repo_root: {},
                ensure_proxy_control_fn=lambda: True,
                run_workflow_fn=lambda *_args, **_kwargs: {},
                run_case_fn=_run_case,
                route_case_records_for_judging_fn=_route,
                drain_pending_judge_records_fn=_drain,
                write_batch_judge_summary_fn=lambda *_args, **_kwargs: None,
            )

        payload = json.loads(output.getvalue())
        assert run_calls == ["case-1"]
        assert route_calls == [("case-1", "passed")]
        assert len(drained) == 1
        assert len(payload) == 1
        assert payload[0]["case_id"] == "case-1"
        assert payload[0]["result"]["status"] == "passed"


def test_workflow_run_exception_exits_with_error_payload():
    import app.runner as runner_mod

    with tempfile.TemporaryDirectory() as td, patch.object(
        runner_mod,
        "BenchmarkApp",
        lambda: _FakeBenchmarkApp(Path(td) / "runs"),
    ), patch.dict(
        os.environ,
        {"BENCHMARK_PROXY_AUTOSTART": "0", "BENCHMARK_PROXY_CONTROL_URL": "127.0.0.1:1"},
        clear=False,
    ):
        args = _base_args("workflow-run")
        output = io.StringIO()
        failed = False
        with redirect_stdout(output):
            try:
                orchestrator_cli_core.run_parsed_args(
                    args,
                    default_proxy_listen="127.0.0.1:8081",
                    default_proxy_control="http://127.0.0.1:8082",
                    resolve_repo_root_fn=lambda: Path.cwd(),
                    collect_case_ids_fn=lambda _app, _args: ["case-1"],
                    normalize_control_url_fn=lambda url: url,
                    is_local_host_fn=lambda _host: True,
                    proxy_control_running_fn=lambda _url: True,
                    control_listen_from_url_fn=lambda _url: "127.0.0.1:8082",
                    resolve_api_server_fn=lambda _source: "https://127.0.0.1:6443",
                    start_local_proxy_fn=lambda *_args, **_kwargs: None,
                    wait_for_proxy_fn=lambda *_args, **_kwargs: True,
                    resolve_agent_defaults_fn=lambda _args, _repo_root: None,
                    collect_llm_env_fn=lambda _args, _repo_root: {},
                    ensure_proxy_control_fn=lambda: True,
                    run_workflow_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("workflow boom")),
                    run_case_fn=lambda *_args, **_kwargs: {},
                    route_case_records_for_judging_fn=lambda *_args, **_kwargs: None,
                    drain_pending_judge_records_fn=lambda *_args, **_kwargs: None,
                    write_batch_judge_summary_fn=lambda *_args, **_kwargs: None,
                )
            except SystemExit as exc:
                failed = True
                assert exc.code == 1
        assert failed is True
        payload = json.loads(output.getvalue())
        assert payload["status"] == "error"
        assert "workflow boom" in payload["error"]


def test_run_command_no_cases_selected_exits_nonzero():
    import app.runner as runner_mod

    with tempfile.TemporaryDirectory() as td, patch.object(
        runner_mod,
        "BenchmarkApp",
        lambda: _FakeBenchmarkApp(Path(td) / "runs"),
    ), patch.dict(
        os.environ,
        {"BENCHMARK_PROXY_AUTOSTART": "0", "BENCHMARK_PROXY_CONTROL_URL": "127.0.0.1:1"},
        clear=False,
    ):
        args = _base_args("run")
        output = io.StringIO()
        failed = False
        with redirect_stdout(output):
            try:
                orchestrator_cli_core.run_parsed_args(
                    args,
                    default_proxy_listen="127.0.0.1:8081",
                    default_proxy_control="http://127.0.0.1:8082",
                    resolve_repo_root_fn=lambda: Path.cwd(),
                    collect_case_ids_fn=lambda _app, _args: [],
                    normalize_control_url_fn=lambda url: url,
                    is_local_host_fn=lambda _host: True,
                    proxy_control_running_fn=lambda _url: True,
                    control_listen_from_url_fn=lambda _url: "127.0.0.1:8082",
                    resolve_api_server_fn=lambda _source: "https://127.0.0.1:6443",
                    start_local_proxy_fn=lambda *_args, **_kwargs: None,
                    wait_for_proxy_fn=lambda *_args, **_kwargs: True,
                    resolve_agent_defaults_fn=lambda _args, _repo_root: None,
                    collect_llm_env_fn=lambda _args, _repo_root: {},
                    ensure_proxy_control_fn=lambda: True,
                    run_workflow_fn=lambda *_args, **_kwargs: {},
                    run_case_fn=lambda *_args, **_kwargs: {},
                    route_case_records_for_judging_fn=lambda *_args, **_kwargs: None,
                    drain_pending_judge_records_fn=lambda *_args, **_kwargs: None,
                    write_batch_judge_summary_fn=lambda *_args, **_kwargs: None,
                )
            except SystemExit as exc:
                failed = True
                assert exc.code == 1
        assert failed is True
        assert "No cases selected." in output.getvalue()


def test_run_command_autostarts_local_proxy_when_requested():
    import app.runner as runner_mod

    with tempfile.TemporaryDirectory() as td, patch.object(
        runner_mod,
        "BenchmarkApp",
        lambda: _FakeBenchmarkApp(Path(td) / "runs"),
    ), patch.dict(
        os.environ,
        {"BENCHMARK_PROXY_AUTOSTART": "1"},
        clear=False,
    ):
        args = _base_args("run")
        args.proxy_server = "127.0.0.1:8081"
        start_calls = []
        output = io.StringIO()
        with redirect_stdout(output):
            orchestrator_cli_core.run_parsed_args(
                args,
                default_proxy_listen="127.0.0.1:8081",
                default_proxy_control="http://127.0.0.1:8082",
                resolve_repo_root_fn=lambda: Path.cwd(),
                collect_case_ids_fn=lambda _app, _args: ["case-1"],
                normalize_control_url_fn=lambda url: url,
                is_local_host_fn=lambda _host: True,
                proxy_control_running_fn=lambda _url: False,
                control_listen_from_url_fn=lambda _url: "127.0.0.1:8082",
                resolve_api_server_fn=lambda _source: "https://127.0.0.1:6443",
                start_local_proxy_fn=lambda *call_args, **_kwargs: start_calls.append(call_args) or None,
                wait_for_proxy_fn=lambda *_args, **_kwargs: True,
                resolve_agent_defaults_fn=lambda _args, _repo_root: None,
                collect_llm_env_fn=lambda _args, _repo_root: {},
                ensure_proxy_control_fn=lambda: True,
                run_workflow_fn=lambda *_args, **_kwargs: {},
                run_case_fn=lambda *_args, **_kwargs: {"status": "passed", "run_dir": "runs/case-1"},
                route_case_records_for_judging_fn=lambda *_args, **_kwargs: None,
                drain_pending_judge_records_fn=lambda *_args, **_kwargs: None,
                write_batch_judge_summary_fn=lambda *_args, **_kwargs: None,
            )
        payload = json.loads(output.getvalue())
        assert len(payload) == 1
        assert payload[0]["case_id"] == "case-1"
        assert start_calls
