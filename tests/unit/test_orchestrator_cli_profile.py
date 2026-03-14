import io
import os
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

from app.orchestrator_core import cli as orchestrator_cli_core


class _FakeBenchmarkApp:
    def __init__(self, runs_dir):
        self.runs_dir = runs_dir


def _invoke_main(argv, run_workflow_fn):
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
        output = io.StringIO()
        with redirect_stdout(output):
            orchestrator_cli_core.main(
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
                run_workflow_fn=run_workflow_fn,
                run_case_fn=lambda *_args, **_kwargs: {"status": "passed"},
                route_case_records_for_judging_fn=lambda *_args, **_kwargs: None,
                drain_pending_judge_records_fn=lambda *_args, **_kwargs: None,
                write_batch_judge_summary_fn=lambda *_args, **_kwargs: None,
                argv=argv,
            )
        return output.getvalue()


def test_workflow_run_profile_supplies_required_workflow_and_flags():
    with tempfile.TemporaryDirectory() as td:
        profile_path = Path(td) / "profile.yaml"
        profile_path.write_text(
            yaml.safe_dump(
                {
                    "workflow": "workflows/from-profile.yaml",
                    "max_attempts": 3,
                    "final_sweep_mode": "off",
                    "stage_failure_mode": "terminate",
                }
            ),
            encoding="utf-8",
        )

        captured = {}

        def _run_workflow(_app, args):
            captured["workflow"] = args.workflow
            captured["max_attempts"] = args.max_attempts
            captured["final_sweep_mode"] = args.final_sweep_mode
            captured["stage_failure_mode"] = args.stage_failure_mode
            return {"status": "ok"}

        _invoke_main(["workflow-run", "--profile", str(profile_path)], _run_workflow)

        assert captured["workflow"] == "workflows/from-profile.yaml"
        assert captured["max_attempts"] == 3
        assert captured["final_sweep_mode"] == "off"
        assert captured["stage_failure_mode"] == "terminate"


def test_workflow_run_cli_flags_override_profile_values():
    with tempfile.TemporaryDirectory() as td:
        profile_path = Path(td) / "profile.yaml"
        profile_path.write_text(
            yaml.safe_dump(
                {
                    "workflow": "workflows/from-profile.yaml",
                    "max_attempts": 9,
                    "final_sweep_mode": "off",
                }
            ),
            encoding="utf-8",
        )

        captured = {}

        def _run_workflow(_app, args):
            captured["workflow"] = args.workflow
            captured["max_attempts"] = args.max_attempts
            captured["final_sweep_mode"] = args.final_sweep_mode
            return {"status": "ok"}

        _invoke_main(
            [
                "workflow-run",
                "--profile",
                str(profile_path),
                "--workflow",
                "workflows/from-cli.yaml",
                "--max-attempts",
                "2",
                "--final-sweep-mode",
                "full",
            ],
            _run_workflow,
        )

        assert captured["workflow"] == "workflows/from-cli.yaml"
        assert captured["max_attempts"] == 2
        assert captured["final_sweep_mode"] == "full"


def test_workflow_run_profile_command_mismatch_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        profile_path = Path(td) / "profile.yaml"
        profile_path.write_text(
            yaml.safe_dump(
                {
                    "command": "run",
                    "workflow": "workflows/from-profile.yaml",
                }
            ),
            encoding="utf-8",
        )

        failed = False
        code = None
        try:
            _invoke_main(
                ["workflow-run", "--profile", str(profile_path)],
                lambda *_args, **_kwargs: {"status": "ok"},
            )
        except SystemExit as exc:
            failed = True
            code = exc.code

        assert failed is True
        assert code == 2


def test_shipped_workflow_profiles_load_successfully():
    repo_root = Path(__file__).resolve().parents[2]
    profiles_dir = repo_root / "profiles"
    profile_names = ("debug.yaml", "codex.yaml")

    for profile_name in profile_names:
        profile_path = profiles_dir / profile_name
        assert profile_path.exists(), f"missing shipped profile: {profile_name}"

        captured = {}

        def _run_workflow(_app, args):
            captured["profile"] = Path(args.profile).name
            captured["workflow"] = args.workflow
            captured["agent"] = args.agent
            captured["sandbox"] = args.sandbox
            captured["agent_build"] = args.agent_build
            captured["agent_cmd"] = args.agent_cmd
            return {"status": "ok"}

        _invoke_main(
            [
                "workflow-run",
                "--profile",
                str(profile_path),
                "--workflow",
                "workflows/workflow-demo.yaml",
            ],
            _run_workflow,
        )

        assert captured["profile"] == profile_name
        assert captured["workflow"] == "workflows/workflow-demo.yaml"
        assert captured["agent"] == "cli-runner"
        assert captured["sandbox"] == "docker"
        assert captured["agent_build"] is True
        assert str(captured["agent_cmd"] or "").strip()
