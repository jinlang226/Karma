import tempfile
from pathlib import Path

from app.orchestrator_core import workflow_validation


def test_validate_stage_namespace_contract_missing_role_raises():
    row = {
        "stage": {
            "id": "stage-a",
            "namespaces": ["cluster_a"],
            "namespace_binding": {"source": "cluster_a"},
        },
        "namespace_contract": {
            "required_roles": ["source", "target"],
            "default_role": "source",
        },
    }

    try:
        workflow_validation.validate_stage_namespace_contract(row)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "missing required namespace role(s): target" in str(exc)


def test_validate_stage_namespace_contract_invalid_role_ownership_value_raises():
    row = {
        "stage": {
            "id": "stage-a",
            "namespaces": ["cluster_a"],
            "namespace_binding": {"source": "cluster_a"},
        },
        "namespace_contract": {
            "required_roles": ["source"],
            "default_role": "source",
            "role_ownership": {"source": "invalid"},
        },
    }

    try:
        workflow_validation.validate_stage_namespace_contract(row)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "namespace_contract.role_ownership has invalid value(s)" in str(exc)


def test_validate_stage_namespace_contract_undeclared_role_ownership_key_raises():
    row = {
        "stage": {
            "id": "stage-a",
            "namespaces": ["cluster_a"],
            "namespace_binding": {"source": "cluster_a"},
        },
        "namespace_contract": {
            "required_roles": ["source"],
            "default_role": "source",
            "role_ownership": {"target": "case"},
        },
    }

    try:
        workflow_validation.validate_stage_namespace_contract(row)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "namespace_contract.role_ownership has undeclared role(s): target" in str(exc)


def test_command_hygiene_violations_detects_hardcoded_namespace_and_namespace_kind():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manifest = tmp_path / "bad.yaml"
        manifest.write_text(
            """
apiVersion: v1
kind: Namespace
metadata:
  name: bad
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: cfg
  namespace: rabbitmq
""".strip()
            + "\n",
            encoding="utf-8",
        )

        violations = workflow_validation.command_hygiene_violations(
            ["kubectl", "-n", "rabbitmq", "apply", "-f", str(manifest)],
            tmp_path / "test.yaml",
        )

        assert any("hardcoded kubectl namespace" in item for item in violations)
        assert any("kind Namespace" in item for item in violations)
        assert any("metadata.namespace" in item for item in violations)


def test_workflow_namespace_hygiene_violations_dedupes_entries():
    case_data = {
        "preOperationCommands": [
            {"command": ["kubectl", "-n", "rabbitmq", "get", "pods"]},
            {"command": ["kubectl", "-n", "rabbitmq", "get", "pods"]},
        ]
    }

    violations = workflow_validation.workflow_namespace_hygiene_violations(case_data, "test.yaml")

    assert violations.count("hardcoded kubectl namespace 'rabbitmq' is not allowed; use namespace_role or placeholders") == 1
