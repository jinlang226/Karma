"""Unit tests for karma.definitions.workflows."""

import pytest
from pathlib import Path
from karma.definitions.workflows import (
    load_workflow_file,
    normalize_workflow,
    resolve_workflow_rows,
    single_case_to_workflow,
    get_all_stage_ids,
    _parse_stage_param_ref,
    _namespace_aliases_for_stage,
)


class TestLoadWorkflowFile:
    def test_loads_valid_yaml(self, tmp_path):
        p = tmp_path / "workflow.yaml"
        p.write_text("metadata:\n  id: wf-1\n")
        data = load_workflow_file(p)
        assert data["metadata"]["id"] == "wf-1"

    def test_raises_when_missing(self, tmp_path):
        with pytest.raises(RuntimeError, match="not found"):
            load_workflow_file(tmp_path / "missing.yaml")

    def test_raises_when_not_a_mapping(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("- item\n")
        with pytest.raises(RuntimeError, match="YAML object"):
            load_workflow_file(p)


class TestParseStageParamRef:
    def test_valid_reference(self):
        result = _parse_stage_param_ref("${stages.stage_1.params.target_node}")
        assert result == {"stage_id": "stage_1", "param": "target_node"}

    def test_returns_none_for_literal(self):
        assert _parse_stage_param_ref("rabbit@pod-0") is None

    def test_returns_none_for_partial_match(self):
        assert _parse_stage_param_ref("prefix-${stages.s.params.p}") is None

    def test_whitespace_is_stripped(self):
        result = _parse_stage_param_ref("  ${stages.s1.params.key}  ")
        assert result == {"stage_id": "s1", "param": "key"}


class TestNamespaceAliasesForStage:
    def test_defaults_to_default(self):
        assert _namespace_aliases_for_stage({}) == ["default"]

    def test_returns_declared_aliases(self):
        stage = {"namespaces": ["primary", "secondary"]}
        assert _namespace_aliases_for_stage(stage) == ["primary", "secondary"]

    def test_ignores_blank_entries(self):
        stage = {"namespaces": ["primary", "", "  "]}
        assert _namespace_aliases_for_stage(stage) == ["primary"]


class TestNormalizeWorkflow:
    def test_raises_on_missing_id(self, tmp_path):
        raw = {"metadata": {}, "spec": {"stages": []}}
        with pytest.raises(ValueError):
            normalize_workflow(raw, resources_dir=tmp_path)

    def test_raises_on_invalid_prompt_mode(self, tmp_path):
        raw = {
            "metadata": {"id": "wf"},
            "spec": {"prompt_mode": "invalid", "stages": []},
        }
        with pytest.raises(ValueError, match="prompt_mode"):
            normalize_workflow(raw, resources_dir=tmp_path)

    def test_returns_normalized_dict(self, tmp_path):
        raw = {
            "metadata": {"id": "my-wf"},
            "spec": {
                "stages": [
                    {"id": "stage_1", "service": "svc", "case": "my-case"}
                ]
            },
        }
        result = normalize_workflow(raw, resources_dir=tmp_path)
        assert result["id"] == "my-wf"
        assert len(result["stages"]) == 1
        assert result["adversary"] == []


class TestSingleCaseToWorkflow:
    def test_produces_single_stage(self):
        wf = single_case_to_workflow("svc", "my-case")
        assert len(wf["stages"]) == 1
        assert wf["stages"][0]["id"] == "stage_1"

    def test_stage_references_service_and_case(self):
        wf = single_case_to_workflow("rabbitmq-experiments", "failover")
        stage = wf["stages"][0]
        assert stage["service"] == "rabbitmq-experiments"
        assert stage["case_name"] == "failover"

    def test_param_overrides_attached(self):
        wf = single_case_to_workflow("svc", "case", {"key": "val"})
        assert wf["stages"][0]["param_overrides"]["key"] == "val"

    def test_adversary_list_is_empty(self):
        wf = single_case_to_workflow("svc", "case")
        assert wf["adversary"] == []


class TestGetAllStageIds:
    def test_returns_ordered_ids(self):
        wf = {"stages": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
        assert get_all_stage_ids(wf) == ["a", "b", "c"]

    def test_empty_workflow(self):
        assert get_all_stage_ids({}) == []
