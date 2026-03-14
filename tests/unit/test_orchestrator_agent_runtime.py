import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.orchestrator_core.agent_runtime import launch_agent, terminate_agent


class _FakePopen:
    last_cmd = None

    def __init__(self, cmd):
        _FakePopen.last_cmd = list(cmd)
        self.args = cmd
        self._poll_code = 0

    def poll(self):
        return self._poll_code

    def terminate(self):
        self._poll_code = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._poll_code = 0


def test_launch_agent_docker_writes_cidfile_argument_and_process_hint():
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "run"
        bundle_dir = Path(td) / "bundle"
        run_dir.mkdir(parents=True, exist_ok=True)
        bundle_dir.mkdir(parents=True, exist_ok=True)

        args = SimpleNamespace(
            sandbox="docker",
            docker_image="bench-agent:test",
            agent_cmd="sleep 5",
            _agent_auth_mount=None,
            real_kubectl=None,
        )
        env = {
            "BENCHMARK_RUN_DIR": str(run_dir),
            "BENCHMARK_SUBMIT_FILE": str(bundle_dir / "submit.signal"),
            "BENCHMARK_REAL_KUBECTL": "/opt/real-kubectl/kubectl",
        }

        proc = launch_agent(bundle_dir, env, args, environ={}, popen=_FakePopen)

        cmd = _FakePopen.last_cmd
        assert isinstance(proc, _FakePopen)
        assert cmd is not None
        assert "--cidfile" in cmd
        cid_idx = cmd.index("--cidfile")
        cid_path = Path(cmd[cid_idx + 1])
        assert cid_path == (run_dir / "agent_container.cid")
        assert getattr(proc, "_benchmark_cidfile", "").endswith("agent_container.cid")


def test_terminate_agent_force_removes_container_from_cidfile():
    with tempfile.TemporaryDirectory() as td:
        cidfile = Path(td) / "agent_container.cid"
        cidfile.write_text("container-abc\n", encoding="utf-8")

        proc = _FakePopen(["docker", "run"])
        proc._benchmark_cidfile = str(cidfile)

        calls = []

        def _fake_run(cmd, stdout=None, stderr=None, check=None):
            calls.append((list(cmd), check))
            return SimpleNamespace(returncode=0)

        terminate_agent(proc, run_cmd=_fake_run)

        assert calls == [(["docker", "rm", "-f", "container-abc"], False)]
        assert not cidfile.exists()
