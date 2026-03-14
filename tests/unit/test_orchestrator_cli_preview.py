import builtins
from pathlib import Path
from unittest.mock import patch

from app.orchestrator_cli import build_orchestrator_preview, get_orchestrator_cli_options
from app.settings import ROOT


def test_options_expose_agents_and_defaults():
    opts = get_orchestrator_cli_options()
    assert "choices" in opts
    assert "defaults" in opts
    expected_agents = sorted(
        p.name
        for p in (Path(ROOT) / "agent_tests").iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "Dockerfile").exists()
    )
    assert opts["choices"]["agents"] == expected_agents
    assert "runbook_mode" not in opts["defaults"]


def test_case_scope_derives_run_command():
    payload = {
        "scope": {"type": "case", "service": "rabbitmq-experiments", "case": "manual_monitoring"},
        "flags": {
            "agent": "react",
            "sandbox": "docker",
            "agent_build": True,
        },
    }
    result = build_orchestrator_preview(payload)
    assert result["ok"] is True
    cmd = result["command_one_line"]
    assert "orchestrator.py run" in cmd
    assert "--service rabbitmq-experiments" in cmd
    assert "--case manual_monitoring" in cmd


def test_service_scope_uses_batch_service():
    payload = {
        "scope": {"type": "service", "service": "rabbitmq-experiments"},
        "flags": {"agent": "react", "sandbox": "local"},
    }
    result = build_orchestrator_preview(payload)
    assert result["ok"] is True
    cmd = result["command_one_line"]
    assert "orchestrator.py batch" in cmd
    assert "--service rabbitmq-experiments" in cmd


def test_all_scope_uses_batch_all():
    payload = {
        "scope": {"type": "all"},
        "flags": {"agent": "react", "sandbox": "local"},
    }
    result = build_orchestrator_preview(payload)
    assert result["ok"] is True
    assert "--all" in result["command_one_line"]


def test_default_proxy_is_not_rendered():
    opts = get_orchestrator_cli_options()
    payload = {
        "scope": {"type": "service", "service": "rabbitmq-experiments"},
        "flags": {
            "agent": "react",
            "sandbox": "local",
            "proxy_server": opts["defaults"]["proxy_server"],
        },
    }
    result = build_orchestrator_preview(payload)
    assert result["ok"] is True
    assert "--proxy-server" not in result["command_one_line"]


def test_custom_proxy_is_rendered():
    payload = {
        "scope": {"type": "service", "service": "rabbitmq-experiments"},
        "flags": {
            "agent": "react",
            "sandbox": "local",
            "proxy_server": "127.0.0.1:19081",
        },
    }
    result = build_orchestrator_preview(payload)
    assert result["ok"] is True
    assert "--proxy-server 127.0.0.1:19081" in result["command_one_line"]


def test_agent_build_requires_docker():
    payload = {
        "scope": {"type": "service", "service": "rabbitmq-experiments"},
        "flags": {
            "agent": "react",
            "sandbox": "local",
            "agent_build": True,
        },
    }
    result = build_orchestrator_preview(payload)
    assert result["ok"] is False
    assert any("agent-build requires --sandbox docker" in err for err in result["errors"])


def test_judge_flags_render_when_set():
    payload = {
        "scope": {"type": "service", "service": "rabbitmq-experiments"},
        "flags": {
            "agent": "react",
            "sandbox": "local",
            "judge_mode": "post-run",
            "judge_model": "openai/gpt-4o-mini",
            "judge_base_url": "https://openrouter.ai/api/v1",
            "judge_timeout": 180,
            "judge_max_retries": 3,
            "judge_prompt_version": "v2",
            "judge_fail_open": False,
        },
    }
    result = build_orchestrator_preview(payload)
    assert result["ok"] is True
    cmd = result["command_one_line"]
    assert "--judge-mode post-run" in cmd
    assert "--judge-model openai/gpt-4o-mini" in cmd
    assert "--judge-base-url https://openrouter.ai/api/v1" in cmd
    assert "--judge-timeout 180" in cmd
    assert "--judge-max-retries 3" in cmd
    assert "--judge-prompt-version v2" in cmd
    assert "--judge-fail-closed" in cmd


def test_options_do_not_import_orchestrator_module():
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "orchestrator":
            raise AssertionError("orchestrator module should not be imported")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=guarded_import):
        opts = get_orchestrator_cli_options()
        assert opts["choices"]["agents"]
