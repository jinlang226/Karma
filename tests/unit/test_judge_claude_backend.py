"""The claude-CLI judge backend (keyless judging via `claude --print`)."""
import subprocess
from unittest.mock import patch

import pytest

from karma.judge.client import _resolve_backend, _call_claude_cli, call_judge_llm


class TestResolveBackend:
    def test_auto_uses_claude_cli_without_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KARMA_JUDGE_API_KEY", raising=False)
        monkeypatch.delenv("KARMA_JUDGE_BACKEND", raising=False)
        assert _resolve_backend(None, None) == "claude_cli"

    def test_auto_uses_openai_with_key(self, monkeypatch):
        monkeypatch.delenv("KARMA_JUDGE_BACKEND", raising=False)
        assert _resolve_backend(None, "sk-test") == "openai"

    def test_explicit_arg_wins(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert _resolve_backend("claude_cli", "sk-test") == "claude_cli"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KARMA_JUDGE_BACKEND", "claude_cli")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert _resolve_backend(None, "sk-test") == "claude_cli"


class TestClaudeCli:
    def test_returns_response_shape(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='[{"id":"a","score":1.0}]', stderr=""
        )
        with patch("subprocess.run", return_value=fake) as m:
            out = _call_claude_cli("PROMPT-TEXT", "sonnet", 30)
        assert out["content"] == '[{"id":"a","score":1.0}]'
        assert out["model"] == "sonnet" and out["finish_reason"] == "stop"
        # Security (C1): the judge prompt embeds agent-authored text, so the judge
        # must never auto-approve tool calls. Assert it does NOT skip permissions,
        # disallows the executing tools, and passes the prompt on stdin (not as a
        # positional arg the variadic --disallowedTools could swallow).
        argv = m.call_args[0][0]
        assert "--dangerously-skip-permissions" not in argv
        assert "--disallowedTools" in argv
        assert "Bash" in argv[argv.index("--disallowedTools") + 1]
        assert "PROMPT-TEXT" not in argv
        assert m.call_args[1].get("input") == "PROMPT-TEXT"

    def test_raises_on_nonzero(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        with patch("subprocess.run", return_value=fake):
            with pytest.raises(RuntimeError, match="claude CLI judge failed"):
                _call_claude_cli("p", "sonnet", 30)

    def test_call_judge_llm_routes_to_claude(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KARMA_JUDGE_API_KEY", raising=False)
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"x":{"score":0.5}}', stderr=""
        )
        with patch("subprocess.run", return_value=fake) as m:
            out = call_judge_llm({"rubric": {"items": []}, "trace_facts": {}})
        # gpt- default must be remapped to a claude model
        assert "sonnet" in m.call_args[0][0]
        assert out["content"] == '{"x":{"score":0.5}}'
