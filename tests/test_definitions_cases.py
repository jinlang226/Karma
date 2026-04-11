"""Unit tests for karma.definitions.cases."""

import pytest
from pathlib import Path
from karma.definitions.cases import (
    case_path,
    load_case_file,
    resolve_case_params,
    normalize_namespace_contract,
    normalize_precondition_units,
    normalize_oracle_config,
    normalize_decoy_config,
    normalize_case,
)


class TestCasePath:
    def test_returns_correct_path(self, tmp_path):
        result = case_path(tmp_path, "rabbitmq-experiments", "failover")
        assert result == tmp_path / "rabbitmq-experiments" / "failover" / "test.yaml"

    def test_does_not_require_file_to_exist(self, tmp_path):
        result = case_path(tmp_path, "svc", "missing-case")
        assert not result.exists()


class TestLoadCaseFile:
    def test_loads_valid_yaml(self, tmp_path):
        p = tmp_path / "svc" / "my-case" / "test.yaml"
        p.parent.mkdir(parents=True)
        p.write_text("prompt: hello\n")
        data = load_case_file(tmp_path, "svc", "my-case")
        assert data["prompt"] == "hello"

    def test_raises_when_missing(self, tmp_path):
        with pytest.raises(RuntimeError, match="not found"):
            load_case_file(tmp_path, "svc", "no-such-case")

    def test_raises_when_not_a_mapping(self, tmp_path):
        p = tmp_path / "svc" / "bad" / "test.yaml"
        p.parent.mkdir(parents=True)
        p.write_text("- item\n")
        with pytest.raises(RuntimeError, match="YAML object"):
            load_case_file(tmp_path, "svc", "bad")


class TestResolveCaseParams:
    def test_defaults_used_when_no_overrides(self):
        data = {"params": {"timeout": {"default": 30}}}
        resolved, warnings = resolve_case_params(data)
        assert resolved["timeout"] == 30
        assert warnings == []

    def test_overrides_take_priority(self):
        data = {"params": {"timeout": {"default": 30}}}
        resolved, _ = resolve_case_params(data, {"timeout": 60})
        assert resolved["timeout"] == 60

    def test_unknown_override_produces_warning(self):
        data = {"params": {}}
        _, warnings = resolve_case_params(data, {"unknown_key": "val"})
        assert any("unknown_key" in w for w in warnings)

    def test_empty_case_returns_empty_params(self):
        resolved, warnings = resolve_case_params({})
        assert resolved == {}
        assert warnings == []


class TestNormalizeNamespaceContract:
    def test_empty_when_absent(self):
        result = normalize_namespace_contract({})
        assert result == {"required_roles": [], "optional_roles": []}

    def test_parses_required_and_optional_roles(self):
        data = {
            "namespace_contract": {
                "required_roles": ["primary", "secondary"],
                "optional_roles": ["monitoring"],
            }
        }
        result = normalize_namespace_contract(data)
        assert result["required_roles"] == ["primary", "secondary"]
        assert result["optional_roles"] == ["monitoring"]

    def test_deduplicates_roles(self):
        data = {"namespace_contract": {"required_roles": ["a", "a", "b"]}}
        result = normalize_namespace_contract(data)
        assert result["required_roles"] == ["a", "b"]


class TestNormalizePreconditionUnits:
    def test_returns_empty_list_when_absent(self):
        assert normalize_precondition_units({}) == []

    def test_raises_on_missing_probe(self):
        data = {
            "preconditions": [
                {"apply": ["kubectl apply -f x.yaml"], "verify": ["kubectl get pod"]}
            ]
        }
        with pytest.raises(RuntimeError, match="probe"):
            normalize_precondition_units(data)

    def test_normalizes_valid_unit(self):
        data = {
            "preconditions": [
                {
                    "probe": ["kubectl get ns target"],
                    "apply": ["kubectl create ns target"],
                    "verify": ["kubectl get ns target"],
                }
            ]
        }
        units = normalize_precondition_units(data)
        assert len(units) == 1
        assert len(units[0]["probe_commands"]) == 1
        assert units[0]["on_probe_fail"] == "error"


class TestNormalizeOracleConfig:
    def test_empty_oracle_returns_empty_lists(self):
        result = normalize_oracle_config({})
        assert result["verify_commands"] == []
        assert result["before_commands"] == []
        assert result["after_commands"] == []
        assert result["script_path"] is None

    def test_after_failure_mode_defaults_to_warn(self):
        result = normalize_oracle_config({"oracle": {"verify": {"commands": ["true"]}}})
        assert result["after_failure_mode"] == "warn"


class TestNormalizeCase:
    def test_raises_on_structural_error(self, tmp_path):
        data = {
            "preconditions": [{"apply": ["x"], "verify": ["y"]}]
        }
        with pytest.raises(RuntimeError):
            normalize_case(data, "svc", "bad-case")

    def test_returns_required_keys(self, tmp_path):
        data = {"prompt": "do the thing"}
        result = normalize_case(data, "svc", "my-case")
        for key in ("service", "case_name", "params", "namespace_contract",
                    "precondition_units", "oracle", "decoys", "warnings"):
            assert key in result


class TestLegacyFormatRejection:
    def test_pre_operation_commands_rejected(self, tmp_path):
        p = tmp_path / "svc" / "legacy-case" / "test.yaml"
        p.parent.mkdir(parents=True)
        p.write_text("preOperationCommands:\n  - kubectl apply -f x.yaml\n")
        with pytest.raises(RuntimeError) as exc_info:
            load_case_file(tmp_path, "svc", "legacy-case")
        assert "preOperationCommands" in str(exc_info.value)
        assert "preconditionUnits" in str(exc_info.value)

    def test_verification_commands_rejected(self, tmp_path):
        p = tmp_path / "svc" / "legacy-case2" / "test.yaml"
        p.parent.mkdir(parents=True)
        p.write_text("verificationCommands:\n  - kubectl get pod\n")
        with pytest.raises(RuntimeError) as exc_info:
            load_case_file(tmp_path, "svc", "legacy-case2")
        assert "verificationCommands" in str(exc_info.value)
        assert "oracle.verify.commands" in str(exc_info.value)

    def test_contemporary_format_loads_without_error(self, tmp_path):
        p = tmp_path / "svc" / "good-case" / "test.yaml"
        p.parent.mkdir(parents=True)
        p.write_text("prompt: do the thing\n")
        data = load_case_file(tmp_path, "svc", "good-case")
        assert data["prompt"] == "do the thing"
