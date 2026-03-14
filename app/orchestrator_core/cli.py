import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import yaml


_PROFILE_COMMAND_KEY_ALIASES = {
    "workflow-run": ("workflow-run", "workflow_run"),
    "run": ("run",),
    "batch": ("batch",),
}
_PROFILE_RESERVED_KEYS = {
    "command",
    "common",
    "args",
    "commands",
    "run",
    "batch",
    "workflow-run",
    "workflow_run",
}
_PROFILE_DEST_ALIASES = {
    "workflow_path": "workflow",
}


def _iter_parser_actions(parser):
    for action in getattr(parser, "_actions", []):
        yield action
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from _iter_parser_actions(child)


def _parser_option_maps(parser):
    option_to_action = {}
    dest_to_action = {}
    for action in _iter_parser_actions(parser):
        dest = getattr(action, "dest", None)
        if dest:
            dest_to_action[dest] = action
        for opt in getattr(action, "option_strings", []) or []:
            option_to_action[opt] = action
    return option_to_action, dest_to_action


def _collect_explicit_cli_dests(parser, argv_tokens):
    option_to_action, _ = _parser_option_maps(parser)
    explicit = set()
    tokens = list(argv_tokens or [])
    index = 0
    while index < len(tokens):
        token = str(tokens[index] or "")
        if token == "--":
            break
        option = token
        has_inline_value = False
        if token.startswith("--") and "=" in token:
            option, _ = token.split("=", 1)
            has_inline_value = True
        action = option_to_action.get(option)
        if not action:
            index += 1
            continue
        explicit.add(action.dest)
        if has_inline_value:
            index += 1
            continue

        consumes_value = True
        if isinstance(
            action,
            (
                argparse._StoreTrueAction,
                argparse._StoreFalseAction,
                argparse._StoreConstAction,
                argparse._AppendConstAction,
                argparse._CountAction,
            ),
        ):
            consumes_value = False
        else:
            nargs = getattr(action, "nargs", None)
            if nargs == 0:
                consumes_value = False
            elif nargs == "?":
                consumes_value = index + 1 < len(tokens) and not str(tokens[index + 1]).startswith("-")
            elif nargs in ("*", "+"):
                while index + 1 < len(tokens) and not str(tokens[index + 1]).startswith("-"):
                    index += 1
                consumes_value = False
        if consumes_value and index + 1 < len(tokens):
            index += 1
        index += 1
    return explicit


def _normalize_profile_key(key):
    text = str(key or "").strip()
    if not text:
        return ""
    text = text.replace("-", "_")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = text.lower()
    return _PROFILE_DEST_ALIASES.get(text, text)


def _parse_profile_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n", ""}:
        return False
    raise ValueError(f"expected boolean-like value, got {value!r}")


def _coerce_profile_value(action, raw_value):
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        value = _parse_profile_bool(raw_value)
    elif raw_value is None:
        value = None
    elif action.type is not None:
        value = action.type(raw_value)
    elif isinstance(action.default, bool):
        value = _parse_profile_bool(raw_value)
    elif isinstance(action.default, int) and not isinstance(raw_value, int):
        value = int(raw_value)
    else:
        value = raw_value

    choices = getattr(action, "choices", None)
    if choices and value not in choices:
        allowed = ", ".join(str(item) for item in choices)
        raise ValueError(f"value {value!r} is not in {{{allowed}}}")
    return value


def _load_profile_payload(profile_path):
    raw_path = str(profile_path or "").strip()
    if not raw_path:
        raise ValueError("profile path is empty")
    path = Path(raw_path)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"profile file not found: {raw_path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ValueError(f"failed to parse profile file {raw_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"profile file must contain a mapping/object: {raw_path}")
    return payload, path


def _command_profile_overrides(payload, command):
    command_text = str(command or "").strip().lower()
    aliases = _PROFILE_COMMAND_KEY_ALIASES.get(command_text, (command_text,))
    out = {}

    command_hint = str(payload.get("command") or "").strip().lower()
    if command_hint and command_hint not in aliases:
        raise ValueError(f"profile command mismatch: profile targets '{command_hint}', invoked '{command_text}'")

    common = payload.get("common")
    if isinstance(common, dict):
        out.update(common)

    generic_args = payload.get("args")
    if isinstance(generic_args, dict):
        out.update(generic_args)

    commands = payload.get("commands")
    if isinstance(commands, dict):
        for alias in aliases:
            section = commands.get(alias)
            if isinstance(section, dict):
                out.update(section)

    for alias in aliases:
        section = payload.get(alias)
        if isinstance(section, dict):
            out.update(section)

    for key, value in payload.items():
        normalized = _normalize_profile_key(key)
        if normalized in _PROFILE_RESERVED_KEYS:
            continue
        if isinstance(value, dict):
            continue
        out[key] = value

    return out


def _apply_profile_overrides(args, parser, explicit_cli_dests):
    profile_raw = str(getattr(args, "profile", "") or "").strip()
    if not profile_raw:
        return args

    payload, profile_path = _load_profile_payload(profile_raw)
    overrides = _command_profile_overrides(payload, args.command)
    _, dest_to_action = _parser_option_maps(parser)

    errors = []
    for key, raw_value in overrides.items():
        dest = _normalize_profile_key(key)
        if not dest or dest in ("command", "profile"):
            continue
        action = dest_to_action.get(dest)
        if action is None:
            continue
        if dest in explicit_cli_dests:
            continue
        try:
            value = _coerce_profile_value(action, raw_value)
        except Exception as exc:
            errors.append(f"{dest}: {exc}")
            continue
        setattr(args, dest, value)

    if errors:
        joined = "; ".join(errors)
        raise ValueError(f"invalid profile values in {profile_path}: {joined}")
    args.profile = str(profile_path)
    return args


def add_common_args(subparser, *, default_proxy_listen):
    subparser.add_argument("--case-id", help="Base64 case id.")
    subparser.add_argument("--service", help="Service name.")
    subparser.add_argument("--case", help="Case name.")
    subparser.add_argument("--all", action="store_true", help="Run all cases.")
    subparser.add_argument(
        "--agent",
        default="react",
        help="Agent name (default: react).",
    )
    subparser.add_argument("--agent-build", action="store_true", help="Build agent image.")
    subparser.add_argument("--agent-tag", help="Override agent image tag.")
    subparser.add_argument(
        "--agent-cleanup",
        action="store_true",
        help="Remove the built agent image after run.",
    )
    subparser.add_argument(
        "--manual-start",
        action="store_true",
        help="Wait for start.signal before starting the submit timer.",
    )
    subparser.add_argument("--llm-env-file", help="Path to LLM env file.")
    subparser.add_argument("--agent-cmd", help="Command to launch the agent.")
    subparser.add_argument("--agent-auth-path", help="Host path to CLI auth file or directory.")
    subparser.add_argument("--agent-auth-dest", help="Container path to mount auth data.")
    subparser.add_argument("--sandbox", choices=["local", "docker"], default="local")
    subparser.add_argument("--docker-image", help="Docker image for sandbox.")
    subparser.add_argument("--source-kubeconfig", help="Source kubeconfig path.")
    subparser.add_argument("--proxy-server", default=default_proxy_listen)
    subparser.add_argument("--real-kubectl", help="Path to real kubectl binary.")
    subparser.add_argument("--profile", help="Path to run profile YAML/JSON (CLI flags override profile values).")
    subparser.add_argument("--submit-timeout", type=int, default=20 * 60)
    subparser.add_argument("--setup-timeout", type=int, default=10 * 60)
    subparser.add_argument(
        "--setup-timeout-mode",
        choices=["fixed", "auto"],
        default="auto",
        help=(
            "How to interpret --setup-timeout: fixed uses the flag value as a hard cap; "
            "auto uses max(--setup-timeout, case auto budget)."
        ),
    )
    subparser.add_argument("--verify-timeout", type=int, default=20 * 60)
    subparser.add_argument("--cleanup-timeout", type=int, default=10 * 60)
    subparser.add_argument(
        "--max-attempts",
        type=int,
        help="Override max attempts (caps case/default attempts when set).",
    )
    subparser.add_argument(
        "--judge-mode",
        choices=["off", "post-run", "post-batch"],
        default="off",
        help=(
            "Trajectory judging mode: off (default), post-run (judge each completed run), "
            "post-batch (judge after all cases in batch)."
        ),
    )
    subparser.add_argument("--judge-model", help="Judge model name (OpenAI-compatible API).")
    subparser.add_argument("--judge-base-url", help="Judge API base URL (OpenAI-compatible).")
    subparser.add_argument("--judge-api-key", help="Judge API key (defaults to env variables).")
    subparser.add_argument(
        "--judge-timeout",
        type=int,
        default=120,
        help="Judge API timeout in seconds.",
    )
    subparser.add_argument(
        "--judge-max-retries",
        type=int,
        default=2,
        help="Judge API retry attempts.",
    )
    subparser.add_argument(
        "--judge-prompt-version",
        default="v1",
        help="Judge prompt version tag written with artifacts.",
    )
    subparser.add_argument(
        "--judge-include-outcome",
        action="store_true",
        help="Include final run outcome in judge input (disabled by default to reduce outcome bias).",
    )
    subparser.add_argument(
        "--judge-fail-open",
        dest="judge_fail_open",
        action="store_true",
        default=True,
        help="Continue run even if judge call fails (default).",
    )
    subparser.add_argument(
        "--judge-fail-closed",
        dest="judge_fail_open",
        action="store_false",
        help="Fail the orchestrator command if judge call fails.",
    )


def build_parser(*, default_proxy_listen):
    parser = argparse.ArgumentParser(description="Headless orchestrator for benchmark runs.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run a single benchmark case.")
    add_common_args(run_parser, default_proxy_listen=default_proxy_listen)

    batch_parser = sub.add_parser("batch", help="Run multiple cases sequentially.")
    add_common_args(batch_parser, default_proxy_listen=default_proxy_listen)
    batch_parser.add_argument("--results-json", help="Optional output file for results.")

    workflow_run_parser = sub.add_parser("workflow-run", help="Run a workflow benchmark chain.")
    add_common_args(workflow_run_parser, default_proxy_listen=default_proxy_listen)
    workflow_run_parser.set_defaults(agent_build=True)
    workflow_run_parser.add_argument("--workflow", help="Path to workflow YAML.")
    workflow_run_parser.add_argument(
        "--final-sweep-mode",
        choices=["inherit", "full", "off"],
        default="inherit",
        help="Override workflow spec final sweep mode (default: inherit from workflow YAML).",
    )
    workflow_run_parser.add_argument(
        "--stage-failure-mode",
        choices=["inherit", "continue", "terminate"],
        default="inherit",
        help=(
            "Override workflow spec stage failure mode "
            "(continue advances after non-retryable stage failures; "
            "terminate ends the workflow on first non-retryable stage failure)."
        ),
    )

    return parser


def run_parsed_args(
    args,
    *,
    default_proxy_listen,
    default_proxy_control,
    resolve_repo_root_fn,
    collect_case_ids_fn,
    normalize_control_url_fn,
    is_local_host_fn,
    proxy_control_running_fn,
    control_listen_from_url_fn,
    resolve_api_server_fn,
    start_local_proxy_fn,
    wait_for_proxy_fn,
    resolve_agent_defaults_fn,
    collect_llm_env_fn,
    ensure_proxy_control_fn,
    run_workflow_fn,
    run_case_fn,
    route_case_records_for_judging_fn,
    drain_pending_judge_records_fn,
    write_batch_judge_summary_fn,
):
    args._docker_ctx = {}

    repo_root = resolve_repo_root_fn()
    runs_dir = None
    local_proxy_proc = None
    built_image = None
    auto_proxy_enabled = os.environ.get("BENCHMARK_PROXY_AUTOSTART", "1").lower() not in (
        "0",
        "false",
        "no",
    )

    if args.proxy_server and args.proxy_server.startswith("http"):
        args.proxy_server = args.proxy_server.replace("https://", "").replace("http://", "")
    auto_proxy_requested = args.proxy_server == default_proxy_listen

    docker_ctx = {}
    try:
        built_image = resolve_agent_defaults_fn(args, repo_root)
        args._llm_env = collect_llm_env_fn(args, repo_root)
        if args.sandbox == "docker" and args.proxy_server == default_proxy_listen:
            args.proxy_server = "host.docker.internal:8081"
        if not os.environ.get("BENCHMARK_PROXY_CONTROL_URL"):
            os.environ["BENCHMARK_PROXY_CONTROL_URL"] = default_proxy_control
        if auto_proxy_enabled and auto_proxy_requested:
            control_url = os.environ.get("BENCHMARK_PROXY_CONTROL_URL")
            normalized = normalize_control_url_fn(control_url)
            parsed = urllib.parse.urlparse(normalized) if normalized else None
            host = parsed.hostname if parsed else "127.0.0.1"
            if is_local_host_fn(host) and not proxy_control_running_fn(control_url):
                control_listen = os.environ.get("BENCHMARK_PROXY_CONTROL_LISTEN") or control_listen_from_url_fn(
                    control_url
                )
                upstream = os.environ.get("BENCHMARK_PROXY_UPSTREAM") or resolve_api_server_fn(args.source_kubeconfig)
                log_path = repo_root / ".benchmark" / "proxy.log"
                local_proxy_proc = start_local_proxy_fn(
                    repo_root,
                    default_proxy_listen,
                    control_listen,
                    upstream,
                    log_path,
                )
                if not wait_for_proxy_fn(control_url, timeout=5.0):
                    raise RuntimeError("Failed to start local proxy.")

        from app.runner import BenchmarkApp
        from app.util import decode_case_id, ts_str

        app = BenchmarkApp()
        runs_dir = app.runs_dir
        judge_engine = None
        if getattr(args, "judge_mode", "off") != "off":
            from app.judge import TrajectoryJudge

            judge_engine = TrajectoryJudge.from_args(args)
        ensure_proxy_control_fn()

        if args.command == "workflow-run":
            try:
                outcome = run_workflow_fn(app, args)
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "status": "error",
                            "error": str(exc),
                            "workflow": args.workflow,
                        },
                        indent=2,
                    )
                )
                sys.exit(1)
            print(json.dumps([{"workflow": args.workflow, "result": outcome}], indent=2))
            return

        case_ids = collect_case_ids_fn(app, args)
        if not case_ids:
            print("No cases selected. Provide --case-id, --service/--case, --service, or --all.")
            sys.exit(1)

        results = []
        batch_index = []
        batch_dir = None
        pending_judge_records = []
        judged_runs = []
        if args.command == "batch":
            batch_dir = runs_dir / f"batch_{ts_str()}"
            batch_dir.mkdir(parents=True, exist_ok=True)
        for case_id in case_ids:
            outcome = run_case_fn(app, case_id, args)
            route_case_records_for_judging_fn(
                judge_engine,
                case_id,
                outcome,
                command=args.command,
                judge_mode=getattr(args, "judge_mode", "off"),
                pending_judge_records=pending_judge_records,
                judged_runs=judged_runs,
                decode_case_id=decode_case_id,
                fail_open=getattr(args, "judge_fail_open", True),
            )
            results.append({"case_id": case_id, "result": outcome})
            if batch_dir:
                try:
                    service, case, test_file = decode_case_id(case_id)
                except Exception:
                    service, case, test_file = None, None, None
                batch_index.append(
                    {
                        "case_id": case_id,
                        "service": service,
                        "case": case,
                        "test_file": test_file,
                        "status": outcome.get("status"),
                        "run_dir": outcome.get("run_dir"),
                        "metrics_path": outcome.get("metrics_path"),
                        "agent_usage_path": outcome.get("agent_usage_path"),
                        "token_usage_available": outcome.get("token_usage_available"),
                        "token_usage_total_tokens": outcome.get("token_usage_total_tokens"),
                    }
                )
            if args.command == "run":
                break

        drain_pending_judge_records_fn(
            judge_engine,
            pending_judge_records,
            judged_runs,
            decode_case_id=decode_case_id,
            fail_open=getattr(args, "judge_fail_open", True),
        )

        if args.command == "batch" and args.results_json:
            Path(args.results_json).write_text(json.dumps(results, indent=2))
        if batch_dir:
            index_path = batch_dir / "batch_index.json"
            index_path.write_text(json.dumps(batch_index, indent=2))
            print(f"[orchestrator] batch index: {index_path}", flush=True)
            write_batch_judge_summary_fn(judge_engine, batch_dir, judged_runs)
            print("Batch summary:", flush=True)
            for entry in batch_index:
                name = entry.get("case_id")
                if entry.get("service") and entry.get("case"):
                    name = f"{entry['service']}/{entry['case']}"
                status = entry.get("status")
                run_dir = entry.get("run_dir")
                print(f"- {name}: {status} ({run_dir})", flush=True)
        print(json.dumps(results, indent=2))
    finally:
        if local_proxy_proc and local_proxy_proc.poll() is None:
            local_proxy_proc.terminate()
        if built_image and args.agent_cleanup:
            subprocess.run(
                ["docker", "rmi", "-f", built_image],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def main(
    *,
    default_proxy_listen,
    default_proxy_control,
    resolve_repo_root_fn,
    collect_case_ids_fn,
    normalize_control_url_fn,
    is_local_host_fn,
    proxy_control_running_fn,
    control_listen_from_url_fn,
    resolve_api_server_fn,
    start_local_proxy_fn,
    wait_for_proxy_fn,
    resolve_agent_defaults_fn,
    collect_llm_env_fn,
    ensure_proxy_control_fn,
    run_workflow_fn,
    run_case_fn,
    route_case_records_for_judging_fn,
    drain_pending_judge_records_fn,
    write_batch_judge_summary_fn,
    argv=None,
):
    parser = build_parser(default_proxy_listen=default_proxy_listen)
    argv_tokens = list(argv if argv is not None else sys.argv[1:])
    explicit_cli_dests = _collect_explicit_cli_dests(parser, argv_tokens)
    args = parser.parse_args(argv_tokens)
    try:
        args = _apply_profile_overrides(args, parser, explicit_cli_dests)
    except ValueError as exc:
        parser.error(str(exc))

    if args.command == "workflow-run" and not str(getattr(args, "workflow", "") or "").strip():
        parser.error("workflow-run requires --workflow (or profile workflow setting)")

    return run_parsed_args(
        args,
        default_proxy_listen=default_proxy_listen,
        default_proxy_control=default_proxy_control,
        resolve_repo_root_fn=resolve_repo_root_fn,
        collect_case_ids_fn=collect_case_ids_fn,
        normalize_control_url_fn=normalize_control_url_fn,
        is_local_host_fn=is_local_host_fn,
        proxy_control_running_fn=proxy_control_running_fn,
        control_listen_from_url_fn=control_listen_from_url_fn,
        resolve_api_server_fn=resolve_api_server_fn,
        start_local_proxy_fn=start_local_proxy_fn,
        wait_for_proxy_fn=wait_for_proxy_fn,
        resolve_agent_defaults_fn=resolve_agent_defaults_fn,
        collect_llm_env_fn=collect_llm_env_fn,
        ensure_proxy_control_fn=ensure_proxy_control_fn,
        run_workflow_fn=run_workflow_fn,
        run_case_fn=run_case_fn,
        route_case_records_for_judging_fn=route_case_records_for_judging_fn,
        drain_pending_judge_records_fn=drain_pending_judge_records_fn,
        write_batch_judge_summary_fn=write_batch_judge_summary_fn,
    )
