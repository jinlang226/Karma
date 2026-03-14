from pathlib import Path
from types import SimpleNamespace

from app.orchestrator_core.agent_runtime import launch_agent
from app.settings import ROOT


class _FakePopen:
    last_cmd = None

    def __init__(self, cmd):
        _FakePopen.last_cmd = list(cmd)
        self.args = cmd


def test_docker_launch_builds_expected_container_command():
    run_dir = ROOT / ".benchmark" / "it_docker_launch" / "run"
    bundle_dir = ROOT / ".benchmark" / "it_docker_launch" / "bundle"
    run_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        sandbox="docker",
        docker_image="bench-agent:test",
        agent_cmd="bash -lc 'echo hello'",
        _agent_auth_mount=None,
        real_kubectl=None,
    )
    env = {
        "BENCHMARK_RUN_DIR": str(run_dir),
        "BENCHMARK_SUBMIT_FILE": str(bundle_dir / "submit.signal"),
        "BENCHMARK_REAL_KUBECTL": "/opt/real-kubectl/kubectl",
    }

    proc = launch_agent(bundle_dir, env, args, environ={}, popen=_FakePopen)

    assert isinstance(proc, _FakePopen)
    cmd = _FakePopen.last_cmd
    assert cmd is not None
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "-v" in cmd
    joined = " ".join(cmd)
    assert f"{bundle_dir}:/workspace" in joined
    assert f"{run_dir}:/run" in joined
    assert "BENCHMARK_USAGE_OUTPUT=/run/agent_usage_raw.json" in joined
    assert "bench-agent:test" in cmd
    # agent command should be appended after image
    image_idx = cmd.index("bench-agent:test")
    assert cmd[image_idx + 1 : image_idx + 4] == ["bash", "-lc", "echo hello"]
