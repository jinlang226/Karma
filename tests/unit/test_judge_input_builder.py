"""Unit tests for judge input assembly -- stage-task reconstruction.

The judge must know WHICH stage it is grading and see that stage's OWN task,
regardless of prompt mode. The stored prompt.txt is the mode-assembled prompt
(the whole workflow for concat modes), so the judge reconstructs the single task
from the case named in config.json (like the UI's jump-to-case).
"""

import json

from karma.judge.input_builder import (
    reconstruct_stage_task,
    stage_position,
    render_judge_prompt,
)


def _run_with_case(tmp_path):
    res = tmp_path / "cases"
    case = res / "svc" / "mycase" / "test.yaml"
    case.parent.mkdir(parents=True)
    case.write_text(
        "prompt: Do the ${BENCH_NAMESPACE} thing\n"
        "oracle:\n  verify:\n    commands:\n      - command: 'true'\n"
    )
    rd = tmp_path / "run"
    rd.mkdir()
    (rd / "config.json").write_text(json.dumps({"stages": [
        {"id": "stage_01", "service": "svc", "case_name": "mycase", "param_overrides": {}},
        {"id": "stage_02", "service": "svc", "case_name": "mycase", "param_overrides": {}},
    ]}))
    return rd, res


class TestReconstructStageTask:
    def test_returns_single_case_task_with_unresolved_placeholder(self, tmp_path):
        rd, res = _run_with_case(tmp_path)
        t = reconstruct_stage_task(rd, "stage_01", resources_dir=res)
        assert "Do the" in t
        assert "${BENCH_NAMESPACE}" in t          # runtime placeholder left unresolved
        assert "=== STAGE" not in t                # a single task, not the mode-assembled blob

    def test_position_labels(self, tmp_path):
        rd, _ = _run_with_case(tmp_path)
        assert stage_position(rd, "stage_01") == "STAGE 1 of 2"
        assert stage_position(rd, "stage_02") == "STAGE 2 of 2"

    def test_empty_when_unresolvable(self, tmp_path):
        rd = tmp_path / "run"; rd.mkdir()          # no config.json
        assert reconstruct_stage_task(rd, "stage_01") == ""
        assert stage_position(rd, "stage_01") == ""


class TestJudgePromptStageAware:
    def _input(self, **kw):
        base = {"stage_id": "stage_02", "rubric": {"items": []},
                "oracle": {"verdict": "pass"}, "trace_facts": {}}
        base.update(kw)
        return base

    def test_names_the_stage_and_shows_its_task(self):
        p = render_judge_prompt(self._input(
            stage_position="STAGE 2 of 3", stage_task="Sync the policy across nodes."))
        assert "STAGE 2 of 3" in p
        assert "Sync the policy across nodes." in p
        assert "reconstructed from its case" in p   # placeholder caveat present

    def test_falls_back_to_prompt_text_when_reconstruction_failed(self):
        p = render_judge_prompt(self._input(stage_task="", prompt_text="THE ASSEMBLED BLOB"))
        assert "THE ASSEMBLED BLOB" in p            # never left with nothing
