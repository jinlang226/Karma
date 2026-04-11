"""Unit tests for karma.definitions.prompts."""

import pytest
from karma.definitions.prompts import (
    VALID_PROMPT_MODES,
    _expand_placeholders,
    assemble_agent_prompt,
    render_stage_prompt,
)


class TestExpandPlaceholders:
    def test_replaces_known_key(self):
        result = _expand_placeholders("hello ${name}", {"name": "world"})
        assert result == "hello world"

    def test_leaves_unknown_key_unchanged(self):
        result = _expand_placeholders("${unknown}", {})
        assert result == "${unknown}"

    def test_multiple_placeholders(self):
        result = _expand_placeholders(
            "${a} and ${b}", {"a": "foo", "b": "bar"}
        )
        assert result == "foo and bar"

    def test_no_placeholders(self):
        assert _expand_placeholders("plain text", {}) == "plain text"


class TestValidPromptModes:
    def test_contains_expected_modes(self):
        assert "progressive" in VALID_PROMPT_MODES
        assert "concat_stateful" in VALID_PROMPT_MODES
        assert "concat_blind" in VALID_PROMPT_MODES


class TestAssembleAgentPrompt:
    def test_progressive_returns_only_current(self):
        prompts = ["stage 1 prompt", "stage 2 prompt", "stage 3 prompt"]
        result = assemble_agent_prompt(prompts, current_index=2, prompt_mode="progressive")
        assert "stage 3 prompt" in result
        assert "stage 1 prompt" not in result

    def test_concat_stateful_includes_prior_with_marker(self):
        prompts = ["first", "second"]
        result = assemble_agent_prompt(prompts, current_index=1, prompt_mode="concat_stateful")
        assert "first" in result
        assert "(ACTIVE)" in result

    def test_concat_blind_includes_prior_without_marker(self):
        prompts = ["first", "second"]
        result = assemble_agent_prompt(prompts, current_index=1, prompt_mode="concat_blind")
        assert "first" in result
        assert "(ACTIVE)" not in result

    def test_adversary_hint_appended(self):
        prompts = ["do the thing"]
        result = assemble_agent_prompt(
            prompts, current_index=0,
            prompt_mode="progressive",
            adversary_hint="something is broken",
        )
        assert "something is broken" in result

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="prompt_mode"):
            assemble_agent_prompt(["p"], current_index=0, prompt_mode="bad_mode")


class TestRenderStagePrompt:
    def test_raises_when_no_prompt_template(self):
        with pytest.raises(ValueError):
            render_stage_prompt({}, {}, {})

    def test_expands_builtin_variables(self):
        case_data = {"prompt": "stage ${stage_id} of ${workflow_id}"}
        stage = {"id": "stage_1"}
        workflow = {"id": "wf-smoke"}
        result = render_stage_prompt(case_data, stage, workflow)
        assert "stage_1" in result
        assert "wf-smoke" in result
