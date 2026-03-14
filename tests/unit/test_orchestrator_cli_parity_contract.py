import argparse

from app.orchestrator_cli import get_orchestrator_cli_options
from app.orchestrator_core import cli as orchestrator_cli_core


def _run_subparser(default_proxy_listen="127.0.0.1:8081"):
    parser = orchestrator_cli_core.build_parser(default_proxy_listen=default_proxy_listen)
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return subparsers.choices["run"]


def _workflow_run_subparser(default_proxy_listen="127.0.0.1:8081"):
    parser = orchestrator_cli_core.build_parser(default_proxy_listen=default_proxy_listen)
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return subparsers.choices["workflow-run"]


def _action_map(parser):
    return {
        action.dest: action
        for action in parser._actions
        if getattr(action, "dest", None) and action.dest != "help"
    }


def test_preview_enum_choices_match_run_parser_choices():
    opts = get_orchestrator_cli_options()
    preview_choices = opts.get("choices") or {}
    run_parser = _run_subparser()
    actions = _action_map(run_parser)

    pairs = [
        ("sandbox", "sandbox"),
        ("setup_timeout_mode", "setup_timeout_mode"),
        ("judge_mode", "judge_mode"),
    ]
    for preview_key, parser_dest in pairs:
        parser_action = actions[parser_dest]
        assert set(parser_action.choices or []) == set(preview_choices[preview_key])


def test_preview_defaults_match_run_parser_defaults_for_stable_flags():
    default_proxy = "127.0.0.1:8081"
    opts = get_orchestrator_cli_options()
    preview_defaults = opts.get("defaults") or {}
    run_parser = _run_subparser(default_proxy_listen=default_proxy)
    actions = _action_map(run_parser)

    expected_equal = [
        "agent",
        "agent_build",
        "agent_cleanup",
        "manual_start",
        "proxy_server",
        "submit_timeout",
        "setup_timeout",
        "setup_timeout_mode",
        "verify_timeout",
        "cleanup_timeout",
        "judge_mode",
        "judge_timeout",
        "judge_max_retries",
        "judge_prompt_version",
        "judge_include_outcome",
        "judge_fail_open",
    ]
    for key in expected_equal:
        assert actions[key].default == preview_defaults[key]


def test_workflow_run_parser_exposes_final_sweep_mode_flag():
    opts = get_orchestrator_cli_options()
    preview_choices = opts.get("choices") or {}
    preview_defaults = opts.get("defaults") or {}
    workflow_parser = _workflow_run_subparser()
    actions = _action_map(workflow_parser)
    action = actions["final_sweep_mode"]
    assert set(action.choices or []) == set(preview_choices["final_sweep_mode"])
    assert action.default == preview_defaults["final_sweep_mode"]


def test_workflow_run_parser_exposes_stage_failure_mode_flag():
    opts = get_orchestrator_cli_options()
    preview_choices = opts.get("choices") or {}
    preview_defaults = opts.get("defaults") or {}
    workflow_parser = _workflow_run_subparser()
    actions = _action_map(workflow_parser)
    action = actions["stage_failure_mode"]
    assert set(action.choices or []) == set(preview_choices["stage_failure_mode"])
    assert action.default == preview_defaults["stage_failure_mode"]


def test_workflow_run_defaults_agent_build_true():
    workflow_parser = _workflow_run_subparser()
    actions = _action_map(workflow_parser)
    assert actions["agent_build"].default is True
