import re

from app.runner_core import helpers


def test_split_command_tokens_handles_shell_and_fallback():
    assert helpers.split_command_tokens("echo 'a b'") == ["echo", "a b"]
    # Invalid shell quoting falls back to plain split.
    assert helpers.split_command_tokens("echo 'broken") == ["echo", "'broken"]
    assert helpers.split_command_tokens(["kubectl", "get", "pods"]) == ["kubectl", "get", "pods"]
    assert helpers.split_command_tokens(None) == []


def test_label_helpers_enforce_dns_safe_values():
    out = helpers.sanitize_label_value("networkpolicy_block")
    assert out == "networkpolicy_block"
    raw = "***invalid***" * 20
    hashed = helpers.sanitize_label_value(raw)
    assert len(hashed) == 16
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-._")
    assert hashed == helpers.label_hash(normalized)


def test_default_timeout_matrix_for_common_commands():
    assert helpers.default_timeout_sec_for_command(["kubectl", "wait", "pod/foo", "--timeout=30s"], "setup") == 900
    assert helpers.default_timeout_sec_for_command(["kubectl", "apply", "-f", "x.yaml"], "setup") == 120
    assert helpers.default_timeout_sec_for_command(["kubectl", "delete", "pod/foo"], "setup") == 180
    assert helpers.default_timeout_sec_for_command(["python3", "oracle.py"], "verification") == 600
    assert helpers.default_timeout_sec_for_command(["/bin/sh", "-c", "echo hi"], "setup") == 300


def test_resolve_step_timeout_respects_precedence():
    explicit = helpers.resolve_step_timeout_sec({"command": ["echo", "x"], "timeout_sec": "7"}, "setup")
    assert explicit == 7

    inferred = helpers.resolve_step_timeout_sec(
        {"command": ["kubectl", "wait", "--timeout=20s", "pod/foo"]},
        "setup",
    )
    assert inferred == 50  # inferred 20 + buffer 30

    fallback = helpers.resolve_step_timeout_sec({"command": ["echo", "x"]}, "setup")
    assert fallback == 300


def test_build_workflow_tokens_rejects_compile_action():
    tokens, error = helpers.build_workflow_tokens(
        action="compile",
        workflow_path="workflows/demo.yaml",
        flags={},
        defaults={},
        choices={},
        dry_run=True,
    )
    assert tokens is None
    assert error == "action must be run"


def test_build_workflow_tokens_run_semantics_and_fallbacks():
    defaults = {
        "sandbox": "docker",
        "agent": "react",
    }
    choices = {
        "sandbox": ["local", "docker"],
        "agents": ["react"],
        "setup_timeout_mode": ["fixed", "auto"],
        "final_sweep_mode": ["inherit", "full", "off"],
        "stage_failure_mode": ["inherit", "continue", "terminate"],
    }
    tokens, error = helpers.build_workflow_tokens(
        action="run",
        workflow_path="workflows/demo.yaml",
        flags={
            "sandbox": "invalid",
            "agent": "react",
            "max_attempts": 2,
            "final_sweep_mode": "off",
            "stage_failure_mode": "terminate",
        },
        defaults=defaults,
        choices=choices,
        dry_run=False,
    )
    assert error is None
    # Invalid sandbox falls back to defaults.
    idx = tokens.index("--sandbox")
    assert tokens[idx + 1] == "docker"
    assert "--max-attempts" in tokens and "2" in tokens
    assert "--final-sweep-mode" in tokens and "off" in tokens
    assert "--stage-failure-mode" in tokens and "terminate" in tokens


def test_build_judge_tokens_validation_and_format():
    tokens, error = helpers.build_judge_tokens("run", "runs/demo", dry_run=True, judge_env_file="judge.env")
    assert error is None
    assert tokens == ["python3", "scripts/judge.py", "run", "--run-dir", "runs/demo", "--dry-run", "--judge-env-file", "judge.env"]

    tokens, error = helpers.build_judge_tokens("invalid", "runs/demo")
    assert tokens is None
    assert "target_type must be run or batch" in error

    tokens, error = helpers.build_judge_tokens("run", "")
    assert tokens is None
    assert "target_path is required" in error
