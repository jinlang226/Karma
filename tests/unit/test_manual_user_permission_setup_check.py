import importlib.util
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = (
        repo_root
        / "resources"
        / "rabbitmq-experiments"
        / "manual_user_permission"
        / "setup_precondition_check.py"
    )
    spec = importlib.util.spec_from_file_location("manual_user_permission_setup_check", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to import {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_permissions_table_preserves_empty_configure_column():
    mod = _load_module()
    raw = (
        "user\tconfigure\twrite\tread\n"
        "admin\t.*\t.*\t.*\n"
        "ops-user\t.*\t.*\t.*\n"
        "app-user\t\t.*\t.*\n"
    )
    parsed = mod._parse_permissions_table(raw)
    assert parsed["app-user"]["configure"] == ""
    assert parsed["app-user"]["write"] == ".*"
    assert parsed["app-user"]["read"] == ".*"


def test_parse_permissions_table_accepts_double_quote_empty_value():
    mod = _load_module()
    raw = 'user\tconfigure\twrite\tread\napp-user\t""\t.*\t.*\n'
    parsed = mod._parse_permissions_table(raw)
    assert mod._is_intentionally_broken_configure(parsed["app-user"]["configure"]) is True


def test_is_intentionally_broken_configure_rejects_non_empty():
    mod = _load_module()
    assert mod._is_intentionally_broken_configure(".*") is False

