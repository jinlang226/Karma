from unittest.mock import patch

from app.runner import BenchmarkApp
from app.util import infer_command_timeout_seconds, normalize_commands, parse_duration_seconds


def _make_app():
    # Unit tests should not require cluster access.
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_parse_duration_seconds():
    assert parse_duration_seconds("120") == 120
    assert parse_duration_seconds("120s") == 120
    assert parse_duration_seconds("5m") == 300
    assert parse_duration_seconds("2h") == 7200
    assert parse_duration_seconds(15) == 15
    assert parse_duration_seconds(None) is None


def test_infer_command_timeout_seconds():
    assert infer_command_timeout_seconds(["kubectl", "wait", "--timeout=600s", "pod/foo"]) == 600
    assert infer_command_timeout_seconds("kubectl wait --timeout 5m pod/foo") == 300
    assert (
        infer_command_timeout_seconds(
            [
                "/bin/sh",
                "-c",
                "kubectl wait --timeout=120s pod/foo; kubectl wait --timeout=10s pod/bar",
            ]
        )
        == 120
    )
    assert infer_command_timeout_seconds("kubectl get ns --request-timeout=0") is None


def test_normalize_commands_preserves_timeout():
    cmds = normalize_commands(
        [
            {"command": ["echo", "hi"], "sleep": 1, "timeout_sec": 12},
            {"command": ["echo", "bye"], "sleep": 0, "timeoutSec": "15"},
        ]
    )
    assert cmds[0]["timeout_sec"] == 12
    assert cmds[1]["timeout_sec"] == "15"


def test_compute_setup_timeout_auto_includes_inferred_and_slack():
    app = _make_app()
    app.run_state["service"] = "dummy"
    app.run_state["case"] = "dummy"

    data = {
        "externalMetrics": [],
        "preOperationCommands": [
            {"command": ["kubectl", "wait", "--timeout=10s", "pod/foo"], "sleep": 0},
        ],
    }
    total, breakdown = app._compute_setup_timeout_auto(data)
    assert breakdown["preoperation_sec"] == 40  # 10s inferred + 30s buffer
    assert breakdown["precondition_check_sec"] == 0
    assert breakdown["slack_sec"] == 60
    assert total == 100


def test_compute_setup_timeout_auto_uses_precondition_units_when_configured():
    app = _make_app()
    app.run_state["service"] = "dummy"
    app.run_state["case"] = "dummy"

    data = {
        "preOperationCommands": [{"command": ["bash", "-lc", "echo legacy"], "sleep": 0, "timeout_sec": 100}],
        "preconditionUnits": [
            {
                "id": "u1",
                "probe": {"command": ["bash", "-lc", "echo probe"], "timeout_sec": 5},
                "apply": {"command": ["bash", "-lc", "echo apply"], "timeout_sec": 20},
                "verify": {
                    "command": ["bash", "-lc", "echo verify"],
                    "timeout_sec": 7,
                    "retries": 2,
                    "interval_sec": 3,
                },
            }
        ],
    }

    total, breakdown = app._compute_setup_timeout_auto(data)
    # preoperation: probe(5) + apply(20) + verify(7*2) + interval(3) = 42
    # derived setup_check from probe: base(5) + poll(5) = 10
    # slack includes poll(5): 65
    assert breakdown["preoperation_sec"] == 42
    assert breakdown["precondition_check_sec"] == 10
    assert breakdown["slack_sec"] == 65
    assert total == 117
