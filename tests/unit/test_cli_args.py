"""Validity tests for every CLI subcommand and argument.

Covers: each subcommand parses with valid args, invalid values are
rejected, dispatch reaches the right handler, and -- crucially -- every
command string the web UI's CLI preview emits is accepted by the real
parser (so the "copy this command" button never produces an invalid line).
"""

import pytest
from unittest.mock import patch
from karma.interfaces.cli.main import _build_parser, main
from karma.interfaces.http.cli_preview import build_preview


def _parse(argv):
    return _build_parser().parse_args(argv)


class TestSubcommandParsing:
    def test_run_case_all_flags(self):
        ns = _parse([
            "run-case", "demo", "configmap-update", "--agent", "claude_code",
            "--sandbox", "local", "--param", "k=v", "--param", "n=1",
            "--timeout", "60", "--runs-dir", "r", "--resources-dir", "res",
            "--profile", "p", "--output", "json",
        ])
        assert ns.command == "run-case" and ns.service == "demo"
        assert ns.case == "configmap-update" and ns.param == ["k=v", "n=1"]
        assert ns.timeout == 60 and ns.output == "json"

    def test_run_workflow_all_flags(self):
        ns = _parse([
            "run-workflow", "wf.yaml", "--agent", "x", "--sandbox", "docker",
            "--dry-run", "--output", "json", "--profile", "p",
        ])
        assert ns.command == "run-workflow" and ns.workflow == "wf.yaml"
        assert ns.dry_run is True and ns.sandbox == "docker"

    def test_manual_all_flags(self):
        ns = _parse(["manual", "demo", "configmap-update", "--param", "a=b",
                     "--runs-dir", "r", "--resources-dir", "res", "--profile", "p"])
        assert ns.command == "manual" and ns.service == "demo"

    def test_judge_all_flags(self):
        ns = _parse(["judge", "runs/r1", "--stage", "stage_1", "--model",
                     "gpt-4o", "--base-url", "http://x", "--api-key", "k",
                     "--timeout", "30", "--dry-run", "--output", "json"])
        assert ns.command == "judge" and ns.run_dir == "runs/r1"
        assert ns.stage == "stage_1" and ns.dry_run is True
        assert ns.base_url == "http://x" and ns.api_key == "k" and ns.timeout == 30

    def test_inline_judge_flag(self):
        assert _parse(["run-case", "demo", "configmap-update", "--judge"]).judge is True
        assert _parse(["run-workflow", "wf.yaml", "--judge"]).judge is True

    def test_judge_batch_and_failure_flags(self):
        ns = _parse(["judge", "runs/", "--batch", "--max-retries", "5", "--fail-closed"])
        assert ns.batch is True and ns.max_retries == 5 and ns.fail_open is False
        assert _parse(["judge", "runs/r"]).fail_open is True  # fail-open default

    def test_docker_provisioning_flags(self):
        from karma.interfaces.cli.main import _build_sandbox_options
        ns = _parse(["run-case", "demo", "cm", "--sandbox", "docker", "--agent", "x",
                     "--agent-build", "--agent-tag", "t:1", "--agent-cleanup",
                     "--agent-auth-path", "/h", "--agent-auth-dest", "/c"])
        opts = _build_sandbox_options(ns)
        assert opts["build_image"] is True and opts["image_tag"] == "t:1"
        assert opts["extra_mounts"][0][1] == "/c"
        # no docker flags -> None (local runs pass sandbox_options=None)
        assert _build_sandbox_options(_parse(["run-case", "demo", "cm"])) is None

    def test_info_flags(self):
        assert _parse(["info", "--agents", "--metrics"]).command == "info"

    def test_workflow_run_controls(self):
        ns = _parse(["run-workflow", "wf.yaml", "--stage-failure-mode", "continue",
                     "--final-sweep-mode", "off", "--setup-timeout", "30",
                     "--verify-timeout", "45"])
        assert ns.stage_failure_mode == "continue" and ns.final_sweep_mode == "off"
        assert ns.setup_timeout == 30 and ns.verify_timeout == 45

    def test_run_case_max_attempts_and_timeouts(self):
        ns = _parse(["run-case", "demo", "cm", "--max-attempts", "3",
                     "--setup-timeout", "20", "--verify-timeout", "25"])
        assert ns.max_attempts == 3 and ns.setup_timeout == 20

    def test_run_batch_flags(self):
        ns = _parse(["run-batch", "--all", "--results-json", "out.json",
                     "--judge-mode", "post-batch", "--agent", "x"])
        assert ns.command == "run-batch" and ns.all is True
        assert ns.results_json == "out.json" and ns.judge_mode == "post-batch"
        ns2 = _parse(["run-batch", "--service", "demo", "--case", "demo/cm"])
        assert ns2.service == "demo" and ns2.case == ["demo/cm"]


class TestBatchSelection:
    def test_select_by_service_all_and_explicit(self, tmp_path):
        from karma.interfaces.cli.main import _select_batch_cases
        import argparse
        for svc, case in [("a", "c1"), ("a", "c2"), ("b", "c1")]:
            p = tmp_path / svc / case / "test.yaml"
            p.parent.mkdir(parents=True)
            p.write_text("prompt: x\n")

        def ns(**kw):
            n = argparse.Namespace(all=False, service=None, case=[])
            for k, v in kw.items():
                setattr(n, k, v)
            return n

        assert len(_select_batch_cases(ns(all=True), tmp_path)) == 3
        assert {c for c in _select_batch_cases(ns(service="a"), tmp_path)} == {("a", "c1"), ("a", "c2")}
        assert _select_batch_cases(ns(case=["b/c1"]), tmp_path) == [("b", "c1")]
        assert _select_batch_cases(ns(), tmp_path) == []


class TestInvalidArguments:
    @pytest.mark.parametrize("argv", [
        ["run-case", "s", "c", "--sandbox", "vm"],     # bad choice
        ["run-case", "s", "c", "--output", "xml"],     # bad choice
        ["run-case", "only-service"],                  # missing positional
        ["run-workflow"],                              # missing path
        ["manual", "svc"],                             # missing case positional
        ["judge"],                                     # missing run_dir
        ["frobnicate"],                                # unknown subcommand
    ])
    def test_rejected(self, argv):
        with pytest.raises(SystemExit):
            _parse(argv)


class TestDispatch:
    def test_run_case_dispatches_to_runtime(self, tmp_path):
        result = {"status": "complete", "run_id": "r", "duration_sec": 0.0, "stages": []}
        with patch("karma.interfaces.cli.main.run_case", return_value=result) as m:
            main(["run-case", "demo", "configmap-update", "--runs-dir", str(tmp_path)])
        m.assert_called_once()

    def test_manual_dispatches(self):
        with patch("karma.interfaces.cli.main._cmd_manual") as m:
            main(["manual", "demo", "configmap-update"])
        m.assert_called_once()

    def test_info_lists_registries(self, capsys):
        with pytest.raises(SystemExit):
            main(["info"])
        out = capsys.readouterr().out
        assert "agents:" in out and "metrics:" in out


class TestUiGeneratedCommandsAreValid:
    """Every command the web UI's CLI preview emits must parse on the real CLI."""

    @pytest.mark.parametrize("payload", [
        {"command": "case",
         "target": {"service": "demo", "case": "configmap-update"},
         "flags": {"agent": "claude_code", "sandbox": "local", "timeout": 60,
                   "params": {"target_value": "x"}, "output": "json",
                   "runs_dir": "r", "resources_dir": "res", "profile": "p"}},
        {"command": "workflow",
         "target": {"path": "workflows/demo.yaml"},
         "flags": {"agent": "cli_runner", "sandbox": "docker", "dry_run": True,
                   "output": "json"}},
        {"command": "judge",
         "target": {"run_dir": "runs/r1"},
         "flags": {"stage": "stage_1", "model": "gpt-4o", "dry_run": True,
                   "output": "json"}},
    ])
    def test_preview_command_parses_on_real_cli(self, payload):
        preview = build_preview(payload)
        assert preview["ok"], preview["errors"]
        tokens = preview["tokens"]
        assert tokens[:2] == ["python", "orchestrator.py"]
        # The remaining tokens are the actual CLI argv -- the real parser must
        # accept them without error.
        ns = _build_parser().parse_args(tokens[2:])
        assert ns.command in ("run-case", "run-workflow", "judge")
