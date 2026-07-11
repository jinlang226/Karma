"""The copilot-CLI judge backend (keyless judging via the GitHub `copilot` CLI)."""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from karma.judge.client import _resolve_backend, _call_copilot_cli, call_judge_llm
from karma.judge.agent_defaults import resolve_agent_judge_defaults


def _stream(final_text, *, extra_message=None):
    """A Copilot --output-format json JSONL stream: reasoning + assistant message(s)."""
    lines = ['{"type":"assistant.reasoning","data":{"content":"planning"}}']
    if extra_message is not None:
        lines.append(json.dumps({"type": "assistant.message", "data": {"content": extra_message}}))
    lines.append(json.dumps({"type": "assistant.message", "data": {"content": final_text}}))
    return "\n".join(lines) + "\n"


class TestResolveBackendCopilot:
    def test_explicit_arg_selects_copilot(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # present, but explicit wins
        assert _resolve_backend("copilot_cli", "sk-test") == "copilot_cli"

    def test_env_override_selects_copilot(self, monkeypatch):
        monkeypatch.setenv("KARMA_JUDGE_BACKEND", "copilot_cli")
        assert _resolve_backend(None, "sk-test") == "copilot_cli"

    def test_copilot_is_never_auto_picked(self, monkeypatch):
        # Auto-selection only chooses openai/claude_cli -- never copilot.
        monkeypatch.delenv("KARMA_JUDGE_BACKEND", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KARMA_JUDGE_API_KEY", raising=False)
        assert _resolve_backend(None, None) == "claude_cli"


class TestCallCopilotCli:
    def test_returns_shape_and_parses_last_assistant_message(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=_stream('[{"id":"validity","score":0.7}]', extra_message="intermediate"),
            stderr="",
        )
        with patch("subprocess.run", return_value=fake) as m:
            out = _call_copilot_cli("PROMPT-TEXT", "claude-sonnet-4", 30)
        # Takes the FINAL assistant.message, not the intermediate one.
        assert out["content"] == '[{"id":"validity","score":0.7}]'
        assert out["model"] == "claude-sonnet-4" and out["finish_reason"] == "stop"
        argv = m.call_args[0][0]
        # Security: the judge prompt embeds agent-authored text, so it must NOT
        # auto-approve tools -- assert --allow-all is absent.
        assert "--allow-all" not in argv
        assert "--output-format" in argv and "json" in argv
        assert "--prompt" in argv and argv[argv.index("--prompt") + 1] == "PROMPT-TEXT"
        assert "--model" in argv and argv[argv.index("--model") + 1] == "claude-sonnet-4"

    def test_omits_model_when_none(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=_stream("ok"), stderr="")
        with patch("subprocess.run", return_value=fake) as m:
            _call_copilot_cli("p", None, 30)
        assert "--model" not in m.call_args[0][0]

    def test_raises_on_nonzero(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        with patch("subprocess.run", return_value=fake):
            with pytest.raises(RuntimeError, match="copilot CLI judge failed"):
                _call_copilot_cli("p", None, 30)

    def test_raises_when_no_assistant_message(self):
        # Only reasoning events, no assistant.message -> nothing usable.
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"type":"assistant.reasoning","data":{"content":"thinking"}}\n', stderr="",
        )
        with patch("subprocess.run", return_value=fake):
            with pytest.raises(RuntimeError, match="no assistant message"):
                _call_copilot_cli("p", None, 30)


class TestCallJudgeLlmRoutesToCopilot:
    def test_routes_and_parses(self, monkeypatch):
        monkeypatch.delenv("KARMA_JUDGE_MODEL", raising=False)
        monkeypatch.delenv("KARMA_COPILOT_AGENT_MODEL", raising=False)
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_stream('{"x":{"score":0.5}}'), stderr="",
        )
        with patch("subprocess.run", return_value=fake) as m:
            out = call_judge_llm({"rubric": {"items": []}, "trace_facts": {}}, backend="copilot_cli")
        assert m.call_args[0][0][0] == "copilot"
        assert "--allow-all" not in m.call_args[0][0]
        assert out["content"] == '{"x":{"score":0.5}}'

    def test_model_precedence_judge_model_wins_over_agent_model(self, monkeypatch):
        monkeypatch.setenv("KARMA_JUDGE_MODEL", "gpt-5")
        monkeypatch.setenv("KARMA_COPILOT_AGENT_MODEL", "claude-sonnet-4")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=_stream("ok"), stderr="")
        with patch("subprocess.run", return_value=fake) as m:
            call_judge_llm({"rubric": {"items": []}, "trace_facts": {}}, backend="copilot_cli")
        argv = m.call_args[0][0]
        assert argv[argv.index("--model") + 1] == "gpt-5"

    def test_model_falls_back_to_copilot_agent_model(self, monkeypatch):
        monkeypatch.delenv("KARMA_JUDGE_MODEL", raising=False)
        monkeypatch.setenv("KARMA_COPILOT_AGENT_MODEL", "claude-sonnet-4")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=_stream("ok"), stderr="")
        with patch("subprocess.run", return_value=fake) as m:
            call_judge_llm({"rubric": {"items": []}, "trace_facts": {}}, backend="copilot_cli")
        argv = m.call_args[0][0]
        assert argv[argv.index("--model") + 1] == "claude-sonnet-4"


class TestAgentDefaultsMirrorsCopilot:
    def test_copilot_run_mirrors_to_copilot_backend(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KARMA_COPILOT_AGENT_MODEL", "claude-sonnet-4")
        (tmp_path / "config.json").write_text(json.dumps({"agent": "copilot"}))
        d = resolve_agent_judge_defaults(Path(tmp_path))
        assert d["backend"] == "copilot_cli" and d["model"] == "claude-sonnet-4"

    def test_copilot_run_without_model_still_uses_copilot_backend(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KARMA_COPILOT_AGENT_MODEL", raising=False)
        (tmp_path / "config.json").write_text(json.dumps({"agent": "copilot"}))
        d = resolve_agent_judge_defaults(Path(tmp_path))
        assert d["backend"] == "copilot_cli" and "model" not in d
