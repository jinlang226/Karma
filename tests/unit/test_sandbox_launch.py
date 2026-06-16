"""Unit tests for karma.sandbox.launch_agent command override (--agent-cmd)."""

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
