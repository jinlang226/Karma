import importlib.util
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = (
        repo_root
        / "resources"
        / "rabbitmq-experiments"
        / "manual_backup_restore"
        / "setup_precondition_check.py"
    )
    spec = importlib.util.spec_from_file_location("manual_backup_restore_setup_check", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to import {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_restore_pvc_phases_allows_pending_data_pvc():
    mod = _load_module()
    errors = mod._validate_restore_pvc_phases(
        {
            "rabbitmq-backup": "Bound",
            "data-rabbitmq-0": "Pending",
        }
    )
    assert errors == []


def test_validate_restore_pvc_phases_requires_bound_backup_pvc():
    mod = _load_module()
    errors = mod._validate_restore_pvc_phases(
        {
            "rabbitmq-backup": "Pending",
            "data-rabbitmq-0": "Pending",
        }
    )
    assert any("rabbitmq-backup is not Bound" in err for err in errors)


def test_validate_restore_pvc_phases_rejects_unusable_data_pvc():
    mod = _load_module()
    errors = mod._validate_restore_pvc_phases(
        {
            "rabbitmq-backup": "Bound",
            "data-rabbitmq-0": "Lost",
        }
    )
    assert any("data-rabbitmq-0 is not restorable" in err for err in errors)
