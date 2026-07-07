"""Unit tests for karma.sandbox.launch_agent command override (--agent-cmd)."""

import os
import time

from karma.sandbox import launch_agent


class TestCommandOverride:
    def test_local_command_override_runs_in_place_of_entrypoint(self, tmp_path):
        # With command_override set, local mode runs the given shell command
        # instead of the registered entrypoint -- and needs no agent_meta.
        marker = tmp_path / "ran.txt"
        proc = launch_agent(
            {},  # no folder/entrypoint
            sandbox_mode="local",
            env_vars={},
            run_dir=tmp_path,
            agent_timeout_sec=5,
            command_override=f"bash -c 'echo started > {marker}'",
        )
        proc.wait(timeout_sec=10)
        assert marker.read_text().strip() == "started"
        # output is captured to the run dir's agent.log
        assert (tmp_path / "agent.log").exists()

    def test_command_override_receives_env(self, tmp_path):
        marker = tmp_path / "env.txt"
        proc = launch_agent(
            {},
            sandbox_mode="local",
            env_vars={"PROBE_VAR": "xyz"},
            run_dir=tmp_path,
            agent_timeout_sec=5,
            command_override=f"bash -c 'echo $PROBE_VAR > {marker}'",
        )
        proc.wait(timeout_sec=10)
        assert marker.read_text().strip() == "xyz"


class TestTerminateKillsProcessGroup:
    def test_terminate_kills_the_agent_child_not_just_the_wrapper(self, tmp_path):
        # Real shape of C3: a bash wrapper that starts a long-lived child (the
        # agent CLI) and waits on it. terminate() must kill the WHOLE process
        # group -- otherwise the child is orphaned and keeps running.
        pidfile = tmp_path / "child.pid"
        proc = launch_agent(
            {}, sandbox_mode="local", env_vars={}, run_dir=tmp_path,
            agent_timeout_sec=5,
            command_override=f"bash -c 'sleep 120 & echo $! > {pidfile}; wait'",
        )
        # Wait for the child ("agent CLI") to record its pid.
        child_pid = None
        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip():
                child_pid = int(pidfile.read_text().strip())
                break
            time.sleep(0.1)
        assert child_pid is not None, "child never started"
        os.kill(child_pid, 0)  # alive (no exception)

        proc.terminate()

        # The child must be reaped, not left orphaned (allow time for init reaping).
        dead = False
        for _ in range(50):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                dead = True
                break
            time.sleep(0.1)
        assert dead, f"agent child {child_pid} survived terminate() (C3 regression)"
