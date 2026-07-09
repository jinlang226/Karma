"""Unit tests for karma.interfaces.http.cli_preview."""

from karma.interfaces.http import cli_preview


class TestBuildPreview:
    def test_case_command(self):
        out = cli_preview.build_preview({
            "command": "case",
            "target": {"service": "demo", "case": "configmap-update"},
            "flags": {"agent": "", "params": {"target_value": "y"}},
        })
        assert out["ok"] is True
        assert "run-case demo configmap-update" in out["command_one_line"]
        assert "--param target_value=y" in out["command_one_line"]

    def test_case_missing_target_is_error(self):
        out = cli_preview.build_preview({"command": "case", "target": {}, "flags": {}})
        assert out["ok"] is False
        assert any("target.service" in e for e in out["errors"])

    def test_workflow_command_with_dry_run(self):
        out = cli_preview.build_preview({
            "command": "workflow",
            "target": {"path": "workflows/demo.yaml"},
            "flags": {"agent": "cli_runner", "dry_run": True},
        })
        assert "run-workflow workflows/demo.yaml" in out["command_one_line"]
        assert "--dry-run" in out["command_one_line"]
        assert "--agent cli_runner" in out["command_one_line"]

    def test_judge_command(self):
        out = cli_preview.build_preview({
            "command": "judge",
            "target": {"run_dir": "runs/r1"},
            "flags": {"stage": "stage_1", "model": "gpt-4o"},
        })
        assert "judge runs/r1" in out["command_one_line"]
        assert "--stage stage_1" in out["command_one_line"]
        assert "--model gpt-4o" in out["command_one_line"]

    def test_docker_without_agent_is_error(self):
        out = cli_preview.build_preview({
            "command": "case",
            "target": {"service": "s", "case": "c"},
            "flags": {"sandbox": "docker"},
        })
        assert out["ok"] is False
        assert any("--agent is required" in e for e in out["errors"])

    def test_local_without_agent_warns(self):
        out = cli_preview.build_preview({
            "command": "case",
            "target": {"service": "s", "case": "c"},
            "flags": {"sandbox": "local"},
        })
        assert any("locally" in w for w in out["warnings"])

    def test_unknown_command_is_error(self):
        out = cli_preview.build_preview({"command": "bogus"})
        assert out["ok"] is False

    def test_multi_line_indents_flags(self):
        out = cli_preview.build_preview({
            "command": "case",
            "target": {"service": "s", "case": "c"},
            "flags": {"agent": "cli_runner", "sandbox": "docker", "timeout": 60},
        })
        assert " \\\n" in out["command_multi_line"]


class TestWorkflowKnobFlags:
    """The run-workflow command must carry every behavior knob so it reproduces
    the launch configuration -- but only emit non-defaults to stay clean."""

    def _cmd(self, flags):
        base = {"agent": "claude_code", "sandbox": "local"}
        base.update(flags)
        return cli_preview.build_preview({
            "command": "workflow", "target": {"path": "wf.yaml"}, "flags": base,
        })["command_one_line"]

    def test_non_default_knobs_are_emitted(self):
        cmd = self._cmd({"max_attempts": 3, "agent_session": "per_stage",
                         "stage_failure_mode": "continue", "final_sweep_mode": "off"})
        assert "--max-attempts 3" in cmd
        assert "--agent-session per_stage" in cmd
        assert "--stage-failure-mode continue" in cmd
        assert "--final-sweep-mode off" in cmd

    def test_default_knobs_are_omitted(self):
        cmd = self._cmd({"max_attempts": 1, "agent_session": "persistent",
                         "stage_failure_mode": "terminate", "final_sweep_mode": "auto"})
        for flag in ("--max-attempts", "--agent-session",
                     "--stage-failure-mode", "--final-sweep-mode"):
            assert flag not in cmd


class TestCliOptions:
    def test_lists_choices_and_defaults(self):
        opts = cli_preview.get_cli_options()
        assert "agents" in opts["choices"]
        assert opts["choices"]["sandbox"] == ["local", "docker"]
        assert opts["defaults"]["timeout"] == 900
