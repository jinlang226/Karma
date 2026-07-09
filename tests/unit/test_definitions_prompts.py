"""Unit tests for karma.definitions.prompts."""

import pytest
from karma.definitions.prompts import (
    VALID_PROMPT_MODES,
    _expand_placeholders,
    assemble_agent_prompt,
    render_stage_prompt,
)
from karma.judge.input_builder import render_judge_prompt


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

    def test_concat_stateful_spans_full_workflow_with_status_markers(self):
        # Concat spans the WHOLE workflow -- future stages included -- with a
        # COMPLETED / ACTIVE / UPCOMING status header per stage.
        prompts = ["first", "second", "third"]
        result = assemble_agent_prompt(prompts, current_index=1, prompt_mode="concat_stateful")
        assert "first" in result and "second" in result
        assert "third" in result          # the FUTURE stage is included
        assert "COMPLETED" in result       # stage 1 (before current)
        assert "ACTIVE" in result          # stage 2 (current)
        assert "UPCOMING" in result        # stage 3 (future)

    def test_concat_stateful_first_stage_sees_future(self):
        # At the very first stage, the agent already sees every later stage.
        prompts = ["one", "two", "three"]
        result = assemble_agent_prompt(prompts, current_index=0, prompt_mode="concat_stateful")
        assert "two" in result and "three" in result
        assert result.index("ACTIVE") < result.index("UPCOMING")  # active before future

    def test_concat_no_duplication(self):
        # SR6 regression: no stage prompt may appear more than once, in either
        # concat mode, at any stage index. Distinctive tokens avoid colliding
        # with letters in the status-marker words.
        prompts = ["<<one>>", "<<two>>", "<<three>>"]
        for mode in ("concat_stateful", "concat_blind"):
            for idx in range(3):
                out = assemble_agent_prompt(prompts, current_index=idx, prompt_mode=mode)
                for tok in prompts:
                    assert out.count(tok) == 1

    def test_concat_blind_has_separators_but_no_status(self):
        # Blind gets "=== STAGE k of n ===" boundaries so the tasks are parseable,
        # but NO status marker -- the agent stays blind to which stage is active.
        prompts = ["first", "second", "third"]
        result = assemble_agent_prompt(prompts, current_index=1, prompt_mode="concat_blind")
        assert "first" in result and "second" in result and "third" in result
        assert "=== STAGE 1 of 3 ===" in result and "=== STAGE 3 of 3 ===" in result
        for status in ("ACTIVE", "COMPLETED", "UPCOMING"):
            assert status not in result

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

    def test_prologue_is_prepended(self):
        # The mode prologue orients the agent and must come FIRST, before the tasks.
        result = assemble_agent_prompt(
            ["do the task"], current_index=0, prompt_mode="progressive",
            prologue="ORIENTATION: read the structure below.",
        )
        assert result.startswith("ORIENTATION:")
        assert "do the task" in result


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


_RUBRIC = {
    "items": [
        {"id": "correctness", "weight": 0.6, "description": "Correct.", "rubric": "Did it work?"},
    ],
    "passing_threshold": 0.7,
}


class TestRenderJudgePrompt:
    def _make_input(self, **kwargs) -> dict:
        base = {
            "stage_id": "stage_1",
            "rubric": _RUBRIC,
            "oracle": {"verdict": "pass"},
            "evidence": {},
            "trace_facts": {
                "total_calls": 5,
                "mutation_calls": 2,
                "read_calls": 3,
                "unique_resources": ["pods", "services"],
                "namespaces_touched": ["karma-run-1"],
                "first_mutation_sec": 1.0,
            },
            "submit_text": "I fixed it.",
            "prompt_text": "Fix the failing pod.",
        }
        base.update(kwargs)
        return base

    def test_submit_text_appears_in_output(self):
        result = render_judge_prompt(self._make_input())
        assert "I fixed it." in result

    def test_prompt_text_appears_in_output(self):
        result = render_judge_prompt(self._make_input())
        assert "Fix the failing pod." in result

    def test_oracle_verdict_appears_in_output(self):
        result = render_judge_prompt(self._make_input())
        assert "pass" in result

    def test_rubric_item_id_appears_in_output(self):
        result = render_judge_prompt(self._make_input())
        assert "correctness" in result

    def test_namespace_listed_in_output(self):
        result = render_judge_prompt(self._make_input())
        assert "karma-run-1" in result

    def test_missing_submit_text_uses_placeholder(self):
        result = render_judge_prompt(self._make_input(submit_text=None))
        assert "(not submitted)" in result

    def test_missing_prompt_text_uses_placeholder(self):
        result = render_judge_prompt(self._make_input(prompt_text=None))
        assert "(not available)" in result

    def test_custom_template_is_used(self):
        result = render_judge_prompt(
            self._make_input(),
            template="STAGE={stage_id}",
        )
        assert result == "STAGE=stage_1"

    def test_non_dict_trace_facts_handled_gracefully(self):
        inp = self._make_input(trace_facts=None)
        result = render_judge_prompt(inp)
        assert "(none)" in result


class TestLoadPromptModePrologues:
    """The per-mode prologue file has ONE source of truth (docs/), overridable via
    a path, with STRICT validation: exactly the three mode keys, each non-empty."""

    def test_default_loads_all_three_modes(self):
        from karma.definitions.prompts import load_prompt_mode_prologues, VALID_PROMPT_MODES
        d = load_prompt_mode_prologues()
        assert set(d) == set(VALID_PROMPT_MODES)
        assert all(v.strip() for v in d.values())

    def _write(self, tmp_path, body):
        p = tmp_path / "prologues.yaml"
        p.write_text(body)
        return str(p)

    def test_custom_file_is_used(self, tmp_path):
        from karma.definitions.prompts import load_prompt_mode_prologues
        path = self._write(tmp_path,
            "progressive: A\nconcat_stateful: B\nconcat_blind: C\n")
        assert load_prompt_mode_prologues(path) == {
            "progressive": "A", "concat_stateful": "B", "concat_blind": "C"}

    def test_missing_key_rejected(self, tmp_path):
        from karma.definitions.prompts import load_prompt_mode_prologues
        path = self._write(tmp_path, "progressive: A\nconcat_stateful: B\n")  # no concat_blind
        with pytest.raises(RuntimeError, match="missing.*concat_blind"):
            load_prompt_mode_prologues(path)

    def test_unknown_key_rejected(self, tmp_path):
        from karma.definitions.prompts import load_prompt_mode_prologues
        path = self._write(tmp_path,
            "progressive: A\nconcat_stateful: B\nconcat_blind: C\nconcat_bind: D\n")  # typo
        with pytest.raises(RuntimeError, match="unknown.*concat_bind"):
            load_prompt_mode_prologues(path)

    def test_empty_value_rejected(self, tmp_path):
        from karma.definitions.prompts import load_prompt_mode_prologues
        path = self._write(tmp_path,
            'progressive: ""\nconcat_stateful: B\nconcat_blind: C\n')
        with pytest.raises(RuntimeError, match="non-empty"):
            load_prompt_mode_prologues(path)
