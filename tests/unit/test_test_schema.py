from app.oracle import resolve_oracle_verify
from app.preconditions import normalize_precondition_units
from app.test_schema import find_legacy_test_yaml_keys, raise_for_legacy_test_yaml_keys


def test_find_legacy_test_yaml_keys_detects_and_sorts():
    data = {
        "verificationHooks": {},
        "precondition_units": [],
        "referenceSolutionCommands": [],
    }
    assert find_legacy_test_yaml_keys(data) == [
        "precondition_units",
        "referenceSolutionCommands",
        "verificationHooks",
    ]


def test_raise_for_legacy_test_yaml_keys_reports_migration_guidance():
    data = {
        "verificationCommands": [],
        "verification_hooks": {},
        "reference_solution_commands": [],
    }
    try:
        raise_for_legacy_test_yaml_keys(data, context="resources/demo/example/test.yaml")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        text = str(exc)
        assert "resources/demo/example/test.yaml uses unsupported legacy field(s)" in text
        assert "verificationCommands: Use oracle.verify.commands" in text
        assert "verification_hooks: Use oracle.verify.hooks" in text
        assert "reference_solution_commands: Remove field (unsupported)" in text


def test_resolve_oracle_verify_does_not_fallback_to_legacy_top_level_keys():
    cfg = resolve_oracle_verify(
        {
            "verificationCommands": [{"command": ["bash", "-lc", "echo legacy"]}],
            "verificationHooks": {"beforeCommands": [{"command": ["echo", "x"]}]},
        }
    )
    assert cfg["source"] == "oracle.verify"
    assert cfg["commands"] == []
    assert cfg["before_commands"] == []
    assert cfg["after_commands"] == []
    assert cfg["after_failure_mode"] == "warn"


def test_normalize_precondition_units_does_not_fallback_to_legacy_alias():
    units = normalize_precondition_units(
        {
            "precondition_units": [
                {
                    "id": "legacy",
                    "probe": {"command": ["echo", "probe"]},
                    "apply": {"command": ["echo", "apply"]},
                    "verify": {"command": ["echo", "verify"]},
                }
            ]
        }
    )
    assert units == []

