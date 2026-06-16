"""
Judge isolation test: judge/* must run entirely from on-disk artifacts.

Writes synthetic oracle and evidence artifacts to a temp run directory,
then verifies that the judge pipeline can evaluate them without needing
a live cluster, agent process, or any runtime imports.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from karma import protocol
from karma.judge.input_builder import build_judge_input
from karma.judge.engine import run_judge


_RUBRIC = {
    "items": [
        {"id": "correctness", "weight": 0.7, "description": "Task done.", "rubric": "Did it work?"},
        {"id": "efficiency",  "weight": 0.3, "description": "Minimal ops.", "rubric": "Any waste?"},
    ],
    "passing_threshold": 0.7,
}

_ORACLE_ARTIFACT = {
    "verdict": "pass",
    "output": "all checks passed",
    "before_output": "",
    "after_output": "",
    "script_output": None,
    "error": None,
}

_EVIDENCE_ARTIFACT = {
    "kubectl_snapshot": [
        {"timestamp": "t0", "verb": "GET", "resource": "pods",
         "namespace": "ns", "name": None, "status": 200, "duration_ms": 12}
    ],
    "token_usage": {"prompt_tokens": 100, "completion_tokens": 50,
                    "total_tokens": 150, "turns": 3},
    "trace_facts": {"total_calls": 1, "mutation_calls": 0, "read_calls": 1,
                    "unique_resources": ["pods"], "namespaces_touched": ["ns"],
                    "first_mutation_sec": None},
    "metrics": {},
}


@pytest.fixture()
def run_dir(tmp_path):
    protocol.ensure_stage_dir(tmp_path, "stage_1")
    protocol.stage_oracle_path(tmp_path, "stage_1").write_text(
        json.dumps(_ORACLE_ARTIFACT)
    )
    protocol.stage_evidence_path(tmp_path, "stage_1").write_text(
        json.dumps(_EVIDENCE_ARTIFACT)
    )
    protocol.stage_prompt_path(tmp_path, "stage_1").write_text("do the thing")
    protocol.stage_submit_path(tmp_path, "stage_1").write_text("I fixed it")
    return tmp_path


class TestBuildJudgeInput:
    def test_builds_from_artifacts(self, run_dir):
        result = build_judge_input(run_dir, "stage_1", rubric=_RUBRIC)
        assert result["stage_id"] == "stage_1"
        assert result["oracle"]["verdict"] == "pass"
        assert result["submit_text"] == "I fixed it"
        assert result["prompt_text"] == "do the thing"

    def test_raises_when_oracle_missing(self, tmp_path):
        protocol.ensure_stage_dir(tmp_path, "stage_x")
        protocol.stage_evidence_path(tmp_path, "stage_x").write_text("{}")
        with pytest.raises(RuntimeError, match="oracle artifact missing"):
            build_judge_input(tmp_path, "stage_x", rubric=_RUBRIC)

    def test_raises_when_evidence_missing(self, tmp_path):
        protocol.ensure_stage_dir(tmp_path, "stage_y")
        protocol.stage_oracle_path(tmp_path, "stage_y").write_text("{}")
        with pytest.raises(RuntimeError, match="evidence artifact missing"):
            build_judge_input(tmp_path, "stage_y", rubric=_RUBRIC)


class TestRunJudgeDryRun:
    def test_dry_run_returns_input_without_llm_call(self, run_dir):
        with patch("karma.judge.engine.load_rubric", return_value=_RUBRIC):
            result = run_judge(run_dir, "stage_1", dry_run=True)
        assert result["dry_run"] is True
        assert "input" in result

    def test_dry_run_does_not_call_llm(self, run_dir):
        with patch("karma.judge.engine.load_rubric", return_value=_RUBRIC), \
             patch("karma.judge.engine.call_judge_llm") as mock_llm:
            run_judge(run_dir, "stage_1", dry_run=True)
            mock_llm.assert_not_called()
