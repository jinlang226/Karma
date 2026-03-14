from __future__ import annotations

from typing import Any


_LEGACY_KEY_MESSAGES = {
    "verificationCommands": "Use oracle.verify.commands",
    "verificationHooks": "Use oracle.verify.hooks",
    "verification_hooks": "Use oracle.verify.hooks",
    "precondition_units": "Use preconditionUnits",
    "referenceSolutionCommands": "Remove field (unsupported)",
    "reference_solution_commands": "Remove field (unsupported)",
}


def find_legacy_test_yaml_keys(case_data: dict[str, Any] | None) -> list[str]:
    data = case_data or {}
    if not isinstance(data, dict):
        return []
    found = [key for key in _LEGACY_KEY_MESSAGES if key in data]
    return sorted(found)


def raise_for_legacy_test_yaml_keys(case_data: dict[str, Any] | None, *, context: str = "test.yaml") -> None:
    found = find_legacy_test_yaml_keys(case_data)
    if not found:
        return
    details = "; ".join(f"{key}: {_LEGACY_KEY_MESSAGES[key]}" for key in found)
    raise ValueError(f"{context} uses unsupported legacy field(s): {details}")

