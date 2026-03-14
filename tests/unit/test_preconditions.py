import unittest

from app.preconditions import normalize_precondition_units


def test_normalize_precondition_units_empty():
    assert normalize_precondition_units({}) == []


def test_normalize_precondition_units_valid_shape():
    data = {
        "preconditionUnits": [
            {
                "id": "rabbitmq_ready",
                "probe": {"command": ["bash", "-lc", "echo probe"], "timeout_sec": 5},
                "apply": {
                    "commands": [
                        {"command": ["bash", "-lc", "echo apply"], "sleep": 1},
                    ]
                },
                "verify": {
                    "command": ["bash", "-lc", "echo verify"],
                    "retries": 3,
                    "interval_sec": 2,
                },
            }
        ]
    }
    units = normalize_precondition_units(data)
    assert len(units) == 1
    unit = units[0]
    assert unit["id"] == "rabbitmq_ready"
    assert unit["verify_retries"] == 3
    assert unit["verify_interval_sec"] == 2.0
    assert unit["probe_commands"][0]["command"][0] == "bash"
    assert unit["apply_commands"][0]["sleep"] == 1


def test_normalize_precondition_units_duplicate_id_rejected():
    data = {
        "preconditionUnits": [
            {
                "id": "dup",
                "probe": {"command": ["echo", "probe"]},
                "apply": {"command": ["echo", "apply"]},
                "verify": {"command": ["echo", "verify"]},
            },
            {
                "id": "dup",
                "probe": {"command": ["echo", "probe"]},
                "apply": {"command": ["echo", "apply"]},
                "verify": {"command": ["echo", "verify"]},
            },
        ]
    }
    with unittest.TestCase().assertRaises(ValueError):
        normalize_precondition_units(data)


def test_normalize_precondition_units_requires_probe_apply_verify():
    data = {
        "preconditionUnits": [
            {
                "id": "bad",
                "probe": {"command": ["echo", "probe"]},
                "apply": {"command": ["echo", "apply"]},
            }
        ]
    }
    with unittest.TestCase().assertRaises(ValueError):
        normalize_precondition_units(data)
