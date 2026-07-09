"""Unit tests for scripts/remote-agents/manage_copilot_campaign.py."""

import argparse
import importlib.util
import json
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
        assert Path("karma/protocol.py") in files
        assert Path("docs/default-system-prompt.md") in files


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
        assert "/remote/Karma/docs" in mkdir_command
        assert "/remote/Karma/karma/definitions" in mkdir_command
        assert "/remote/Karma/karma" in mkdir_command
        assert "/remote/Karma/karma/runtime" in mkdir_command
        assert "/remote/Karma/.benchmark/test-batch/hosts/host-example-com/logs" in mkdir_command

        run_calls = [" ".join(args) for args in recorded["run_calls"]]
        assert any("docs/default-system-prompt.md" in call for call in run_calls)
        assert any("karma/definitions/workflows.py" in call for call in run_calls)
        assert any("karma/protocol.py" in call for call in run_calls)
        assert any("karma/runtime/service.py" in call for call in run_calls)
        assert any("karma/runtime/workflow.py" in call for call in run_calls)
        assert any("karma/runtime/case.py" in call for call in run_calls)


class TestPrepareBatch:
    def test_weights_workflows_by_profile_count(self, tmp_path: Path):
        workflow_list = tmp_path / "workflows.txt"
        workflow_list.write_text(
            "".join(
                f"pass/workflow-{index:02d}.yaml\n"
                for index in range(1, 6)
            )
        )
        hosts_json = tmp_path / "hosts.json"
        hosts_json.write_text(
            json.dumps(
                {
                    "host-dual.example.com": {
                        "kubeconfigs": ["/tmp/kc-1", "/tmp/kc-2"],
                        "cluster_names": ["kind", "kc2"],
                    },
                    "host-single.example.com": {
                        "kubeconfigs": ["/tmp/kc-1"],
                        "cluster_names": ["kind"],
                    },
                }
            )
        )
        batch_dir = tmp_path / "batch"

        args = argparse.Namespace(
            batch_dir=str(batch_dir),
            workflow_list=str(workflow_list),
            hosts_json=str(hosts_json),
        )

        assert manage_copilot_campaign.prepare_batch(args) == 0

        assignments = json.loads((batch_dir / "host-assignments.json").read_text())
        assert assignments["host-dual.example.com"]["kubeconfigs"] == ["/tmp/kc-1", "/tmp/kc-2"]
        assert assignments["host-dual.example.com"]["cluster_names"] == ["kind", "kc2"]

        summary = json.loads((batch_dir / "shard-summary.json").read_text())
        assert summary["hosts_total"] == 2
        assert summary["profile_total"] == 3
        counts = {item["host"]: item["workflow_count"] for item in summary["shards"]}
        assert counts == {
            "host-dual.example.com": 3,
            "host-single.example.com": 2,
        }


class TestLaunchBatch:
    def test_launch_uses_assignment_kubeconfigs(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        batch_dir = tmp_path / "batch"
        batch_dir.mkdir()
        key_path = tmp_path / "ssh-key"
        key_path.write_text("key\n")
        assignment = manage_copilot_campaign.HostAssignment(
            host="host.example.com",
            shard_rel="shards/shard-01.txt",
            kubeconfigs=("/tmp/kc-1", "/tmp/kc-2"),
            cluster_names=("kind", "kc2"),
        )
        captured: dict[str, str] = {}

        def fake_ssh(host, remote_command, *, key_path, remote_user, check=True):
            captured["host"] = host
            captured["remote_command"] = remote_command
            return subprocess.CompletedProcess([host, remote_command], 0, "12345\n", "")

        monkeypatch.setattr(manage_copilot_campaign, "load_assignments", lambda batch_dir: [assignment])
        monkeypatch.setattr(manage_copilot_campaign, "batch_rel", lambda batch_dir: ".benchmark/test-batch")
        monkeypatch.setattr(manage_copilot_campaign, "workflow_list", lambda path: ["pass/a.yaml", "pass/b.yaml"])
        monkeypatch.setattr(manage_copilot_campaign, "ssh", fake_ssh)
        monkeypatch.setattr(manage_copilot_campaign.time, "sleep", lambda _: None)

        args = argparse.Namespace(
            batch_dir=str(batch_dir),
            remote_root="/remote/Karma",
            remote_user="jinlang",
            remote_python=".venv/bin/python",
            ssh_key=str(key_path),
            copilot_model="gpt-5-mini",
            remote_env_file=".benchmark/copilot.env",
            kubeconfig_path="/tmp/kc-1",
            runs_subdir="copilot-campaign-test",
            max_heavy=1,
            launch_settle_sec=0.0,
            namespace_cleanup_timeout=240,
        )

        assert manage_copilot_campaign.launch_batch(args) == 0
        assert captured["host"] == "host.example.com"
        assert "--kubeconfigs /tmp/kc-1,/tmp/kc-2" in captured["remote_command"]
        assert "--copilot-model gpt-5-mini" in captured["remote_command"]
