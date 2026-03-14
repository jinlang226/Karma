from unittest.mock import patch

from app.oracle import resolve_oracle_verify
from app.runner import BenchmarkApp
from app.settings import ROOT
from app.util import normalize_commands, parse_duration_seconds, read_yaml


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_all_real_cases_load_without_errors():
    app = _make_app()
    services = app.list_services()
    assert services, "No services discovered in resources/"

    total_cases = 0
    for service in services:
        for case in app.list_cases(service["name"]):
            details = app.get_case(case["id"])
            assert "error" not in details, f"Failed to load case {case['id']}: {details.get('error')}"
            total_cases += 1

    assert total_cases > 0, "No cases discovered in resources/"


def test_all_real_case_command_timeouts_are_valid_when_present():
    app = _make_app()
    for service in app.list_services():
        for case in app.list_cases(service["name"]):
            details = app.get_case(case["id"])
            assert "error" not in details
            path = ROOT / details["path"]
            data = read_yaml(path) or {}
            blocks = {
                "preOperationCommands": normalize_commands(data.get("preOperationCommands")),
                "cleanUpCommands": normalize_commands(data.get("cleanUpCommands")),
            }
            verify_cfg = resolve_oracle_verify(data)
            blocks["oracle.verify.commands"] = list(verify_cfg.get("commands") or [])
            blocks["oracle.verify.hooks.before_commands"] = list(verify_cfg.get("before_commands") or [])
            blocks["oracle.verify.hooks.after_commands"] = list(verify_cfg.get("after_commands") or [])
            for key, cmds in blocks.items():
                for item in cmds:
                    raw = item.get("timeout_sec")
                    if raw is None:
                        continue
                    parsed = parse_duration_seconds(raw)
                    assert parsed is not None and parsed > 0, (
                        f"Invalid timeout_sec={raw!r} in {path} block {key}"
                    )


def test_all_real_case_corpus_has_no_legacy_module_config():
    app = _make_app()
    legacy_sweep_key = "adver" + "sary_sweep"
    legacy_block_key = "adver" + "saries"
    legacy_options_key = "adver" + "saryOptions"
    for service in app.list_services():
        for case in app.list_cases(service["name"]):
            details = app.get_case(case["id"])
            assert "error" not in details
            path = ROOT / details["path"]
            data = read_yaml(path) or {}
            assert legacy_sweep_key not in data, f"Legacy sweep block found in {path}"
            assert legacy_block_key not in data, f"Legacy module block found in {path}"
            assert legacy_options_key not in details
