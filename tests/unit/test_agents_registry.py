"""Unit tests for karma.agents.registry."""

import pytest
from pathlib import Path
from karma.agents.registry import (
    get_agent_meta,
    get_agent_folder,
    list_agents,
    resolve_agent,
)


class TestGetAgentMeta:
    def test_returns_dict_for_known_agent(self):
        meta = get_agent_meta("react")
        assert "folder" in meta
        assert "dockerfile" in meta
        assert "entrypoint" in meta

    def test_raises_for_unknown_agent(self):
        with pytest.raises(ValueError, match="unknown agent"):
            get_agent_meta("nonexistent-agent")

    def test_returns_copy_not_reference(self):
        meta1 = get_agent_meta("react")
        meta2 = get_agent_meta("react")
        meta1["folder"] = "mutated"
        assert meta2["folder"] != "mutated"


class TestGetAgentFolder:
    def test_returns_path(self):
        folder = get_agent_folder("react")
        assert isinstance(folder, Path)

    def test_folder_is_absolute(self):
        folder = get_agent_folder("react")
        assert folder.is_absolute()

    def test_raises_for_unknown_agent(self):
        with pytest.raises(ValueError):
            get_agent_folder("no-such-agent")


class TestListAgents:
    def test_returns_sorted_list(self):
        agents = list_agents()
        assert agents == sorted(agents)

    def test_contains_builtin_agents(self):
        agents = list_agents()
        assert "react" in agents
        assert "cli_runner" in agents

    def test_returns_list_of_strings(self):
        for name in list_agents():
            assert isinstance(name, str)


class TestResolveAgent:
    def test_local_mode_without_name(self):
        result = resolve_agent(None, sandbox_mode="local")
        assert result["sandbox_mode"] == "local"
        assert result.get("dockerfile") is None

    def test_docker_mode_with_name(self):
        result = resolve_agent("react", sandbox_mode="docker")
        assert result["sandbox_mode"] == "docker"
        assert result["dockerfile"] is not None

    def test_raises_when_docker_mode_has_no_name(self):
        with pytest.raises((ValueError, RuntimeError)):
            resolve_agent(None, sandbox_mode="docker")
