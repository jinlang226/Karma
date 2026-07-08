"""Unit tests for karma.protocol."""

import pytest
from pathlib import Path
from karma import protocol


class TestPathHelpers:
    def test_run_meta_path(self, tmp_path):
        assert protocol.run_meta_path(tmp_path) == tmp_path / "run.json"

    def test_workflow_state_path(self, tmp_path):
        assert protocol.workflow_state_path(tmp_path) == tmp_path / "workflow_state.json"

    def test_bundle_dir(self, tmp_path):
        assert protocol.bundle_dir(tmp_path) == tmp_path / "bundle"

    def test_bundle_kubeconfig_path(self, tmp_path):
        assert protocol.bundle_kubeconfig_path(tmp_path) == tmp_path / "bundle" / "kubeconfig"

    def test_bundle_env_path(self, tmp_path):
        assert protocol.bundle_env_path(tmp_path) == tmp_path / "bundle" / "env.json"

    def test_stage_dir(self, tmp_path):
        assert protocol.stage_dir(tmp_path, "stage_1") == tmp_path / "stages" / "stage_1"

    def test_stage_meta_path(self, tmp_path):
        p = protocol.stage_meta_path(tmp_path, "stage_1")
        assert p == tmp_path / "stages" / "stage_1" / "stage.json"

    def test_stage_prompt_path(self, tmp_path):
        p = protocol.stage_prompt_path(tmp_path, "stage_1")
        assert p == tmp_path / "stages" / "stage_1" / "prompt.txt"

    def test_stage_submit_path(self, tmp_path):
        p = protocol.stage_submit_path(tmp_path, "stage_1")
        assert p == tmp_path / "stages" / "stage_1" / "submit.txt"

    def test_stage_oracle_path(self, tmp_path):
        p = protocol.stage_oracle_path(tmp_path, "stage_1")
        assert p == tmp_path / "stages" / "stage_1" / "oracle.json"

    def test_stage_evidence_path(self, tmp_path):
        p = protocol.stage_evidence_path(tmp_path, "stage_1")
        assert p == tmp_path / "stages" / "stage_1" / "evidence.json"

    def test_stage_kubectl_log_path(self, tmp_path):
        p = protocol.stage_kubectl_log_path(tmp_path, "stage_1")
        assert p == tmp_path / "stages" / "stage_1" / "kubectl_log.jsonl"

    def test_stage_adversary_log_path(self, tmp_path):
        p = protocol.stage_adversary_log_path(tmp_path, "stage_1")
        assert p == tmp_path / "stages" / "stage_1" / "adversary.log"

    def test_stage_agent_log_path(self, tmp_path):
        p = protocol.stage_agent_log_path(tmp_path, "stage_1")
        assert p == tmp_path / "stages" / "stage_1" / "agent.log"


class TestEnsureHelpers:
    def test_ensure_stage_dir_creates_directory(self, tmp_path):
        path = protocol.ensure_stage_dir(tmp_path, "stage_1")
        assert path.exists()
        assert path.is_dir()

    def test_ensure_stage_dir_idempotent(self, tmp_path):
        protocol.ensure_stage_dir(tmp_path, "stage_1")
        protocol.ensure_stage_dir(tmp_path, "stage_1")

    def test_ensure_bundle_dir_creates_directory(self, tmp_path):
        path = protocol.ensure_bundle_dir(tmp_path)
        assert path.exists()
        assert path.is_dir()


class TestGenerateRunId:
    def test_contains_workflow_id(self):
        run_id = protocol.generate_run_id("my-workflow")
        assert "my-workflow" in run_id

    def test_explicit_ts_used(self):
        run_id = protocol.generate_run_id("wf", ts="20240101_120000")
        assert run_id == "wf-20240101_120000"

    def test_auto_ts_format(self):
        import re
        run_id = protocol.generate_run_id("wf")
        # wf-<15-char ts>-<short hex suffix>; the suffix avoids same-second collisions.
        assert re.match(r"^wf-\d{8}_\d{6}-[0-9a-f]+$", run_id), run_id

    def test_explicit_ts_is_deterministic(self):
        # An explicit ts is used verbatim with no suffix.
        assert protocol.generate_run_id("wf", ts="20240101_120000") == "wf-20240101_120000"

    def test_auto_ts_avoids_same_second_collision(self):
        assert protocol.generate_run_id("wf") != protocol.generate_run_id("wf")
