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


class TestCliOptions:
    def test_lists_choices_and_defaults(self):
        opts = cli_preview.get_cli_options()
        assert "agents" in opts["choices"]
        assert opts["choices"]["sandbox"] == ["local", "docker"]
        assert opts["defaults"]["timeout"] == 900
