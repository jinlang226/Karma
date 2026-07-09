"""Unit tests for Linux Docker host-alias handling in karma.sandbox."""

import subprocess
from unittest.mock import MagicMock

from karma import sandbox


class TestDockerHostAlias:
    def test_linux_kubeconfig_adds_bridge_gateway_alias(self, tmp_path, monkeypatch):
        kubeconfig = tmp_path / "bundle-kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[:3] == ["docker", "network", "inspect"]:
                return subprocess.CompletedProcess(command, 0, stdout="172.17.0.1\n", stderr="")
            if command[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
            if command[:2] == ["docker", "run"]:
                return subprocess.CompletedProcess(command, 0, stdout="container-123\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(sandbox.sys, "platform", "linux")
        monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
        monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *a, **k: MagicMock())

        sandbox.launch_agent(
            {"image_tag": "karma-agent-test:latest"},
            sandbox_mode="docker",
            env_vars={"KUBECONFIG": "/root/.kube/config"},
            run_dir=tmp_path,
            agent_timeout_sec=5,
            kubeconfig_path=kubeconfig,
        )

        docker_run = next(command for command in calls if command[:2] == ["docker", "run"])
        alias_index = docker_run.index("--add-host")
        assert docker_run[alias_index + 1] == "host.docker.internal:172.17.0.1"

    def test_linux_kubeconfig_falls_back_to_host_gateway_alias(self, tmp_path, monkeypatch):
        kubeconfig = tmp_path / "bundle-kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[:3] == ["docker", "network", "inspect"]:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="bridge unavailable")
            if command[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
            if command[:2] == ["docker", "run"]:
                return subprocess.CompletedProcess(command, 0, stdout="container-123\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(sandbox.sys, "platform", "linux")
        monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
        monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *a, **k: MagicMock())

        sandbox.launch_agent(
            {"image_tag": "karma-agent-test:latest"},
            sandbox_mode="docker",
            env_vars={"KUBECONFIG": "/root/.kube/config"},
            run_dir=tmp_path,
            agent_timeout_sec=5,
            kubeconfig_path=kubeconfig,
        )

        docker_run = next(command for command in calls if command[:2] == ["docker", "run"])
        alias_index = docker_run.index("--add-host")
        assert docker_run[alias_index + 1] == "host.docker.internal:host-gateway"

