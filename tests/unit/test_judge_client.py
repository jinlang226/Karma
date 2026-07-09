"""Unit tests for karma.judge.client failure handling (SR1).

A judge call that never produces a verdict -- no key, OR a persistent bad-key /
wrong-model / 5xx / timeout after all retries -- must raise JudgeLLMUnavailable so
BOTH grading paths (rubric grade and regression adjudication) abort instead of
fabricating a favorable score (a 1.0 in the rubric path).
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from karma.judge.client import JudgeLLMUnavailable, _build_client, call_judge_llm


class TestJudgeUnavailable:
    def test_no_key_raises_unavailable(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(JudgeLLMUnavailable):
                _build_client(api_key=None)

    def test_persistent_call_failure_raises_unavailable(self):
        # Key present -> _build_client succeeds; the .create() call keeps failing
        # (e.g. 401 on an expired key). After retries this must be
        # JudgeLLMUnavailable (a subclass of RuntimeError), NOT the bare
        # RuntimeError that used to slip past the abort into a fabricated 1.0.
        fake = MagicMock()
        fake.chat.completions.create.side_effect = RuntimeError("401 Unauthorized")
        with patch("karma.judge.client._build_client", return_value=fake):
            with pytest.raises(JudgeLLMUnavailable):
                call_judge_llm(
                    None, prompt="grade this", api_key="sk-bad",
                    backend="openai", max_retries=0,
                )

    def test_a_working_call_still_returns(self):
        fake = MagicMock()
        resp = fake.chat.completions.create.return_value
        resp.choices[0].message.content = "ok"
        resp.model = "m"
        resp.usage.prompt_tokens = 1
        resp.usage.completion_tokens = 1
        resp.usage.total_tokens = 2
        resp.choices[0].finish_reason = "stop"
        with patch("karma.judge.client._build_client", return_value=fake):
            out = call_judge_llm(None, prompt="x", api_key="sk-ok", backend="openai", max_retries=0)
        assert out["content"] == "ok"
