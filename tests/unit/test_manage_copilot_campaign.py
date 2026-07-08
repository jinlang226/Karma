"""Unit tests for scripts/remote-agents/manage_copilot_campaign.py."""

import importlib.util
import subprocess
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts/remote-agents/manage_copilot_campaign.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location(
    "manage_copilot_campaign", MODULE_PATH
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
manage_copilot_campaign = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules["manage_copilot_campaign"] = manage_copilot_campaign
MODULE_SPEC.loader.exec_module(manage_copilot_campaign)


class TestCampaignSupportFiles:
    def test_includes_persistent_session_runtime_files(self):
        files = set(manage_copilot_campaign.campaign_support_files())

        assert Path("scripts/remote-agents/run_workflow_queue.py") in files
        assert Path("karma/agents/copilot/Dockerfile") in files
        assert Path("karma/agents/copilot/entrypoint.sh") in files
        assert Path("karma/definitions/workflows.py") in files
        assert Path("karma/runtime/service.py") in files
        assert Path("karma/runtime/workflow.py") in files
        assert Path("karma/runtime/case.py") in files


class TestSyncHost:
    def test_syncs_runtime_files_and_creates_remote_dirs(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        assignment = manage_copilot_campaign.HostAssignment(
            host="host.example.com",
            shard_rel="shards/shard-01.txt",
        )
        env_file = tmp_path / "copilot.env"
        env_file.write_text("GITHUB_TOKEN=test\n")
        key_path = tmp_path / "ssh-key"
        key_path.write_text("key\n")

        recorded: dict[str, list[list[str]] | list[str]] = {
            "run_calls": [],
            "ssh_commands": [],
        }

        def fake_run(args, *, check=True):
            recorded["run_calls"].append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

        def fake_ssh(host, remote_command, *, key_path, remote_user, check=True):
            recorded["ssh_commands"].append(remote_command)
            return subprocess.CompletedProcess([host, remote_command], 0, "", "")

        monkeypatch.setattr(manage_copilot_campaign, "run", fake_run)
        monkeypatch.setattr(manage_copilot_campaign, "ssh", fake_ssh)
        monkeypatch.setattr(
            manage_copilot_campaign,
            "batch_rel",
            lambda batch_dir: ".benchmark/test-batch",
        )
        monkeypatch.setattr(
            manage_copilot_campaign,
            "batch_files",
            lambda batch_dir, assignment: [],
        )
        monkeypatch.setattr(
            manage_copilot_campaign,
            "workflow_files_for_assignment",
            lambda batch_dir, assignment: [],
        )
        monkeypatch.setattr(
            manage_copilot_campaign,
            "workflow_list",
            lambda path: [],
        )

        result = manage_copilot_campaign.sync_host(
            assignment,
            batch_dir=tmp_path / "batch",
            env_file=env_file,
            key_path=key_path,
            remote_root="/remote/Karma",
            remote_user="jinlang",
        )

        assert result["host"] == "host.example.com"
        mkdir_command = recorded["ssh_commands"][0]
        assert "/remote/Karma/karma/definitions" in mkdir_command
        assert "/remote/Karma/karma/runtime" in mkdir_command
        assert "/remote/Karma/.benchmark/test-batch/hosts/host-example-com/logs" in mkdir_command

        run_calls = [" ".join(args) for args in recorded["run_calls"]]
        assert any("karma/definitions/workflows.py" in call for call in run_calls)
        assert any("karma/runtime/service.py" in call for call in run_calls)
        assert any("karma/runtime/workflow.py" in call for call in run_calls)
        assert any("karma/runtime/case.py" in call for call in run_calls)
