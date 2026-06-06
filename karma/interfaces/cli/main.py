"""
CLI parsing, request normalization, and result output.

Defines all CLI subcommands and delegates execution to ``runtime.service``
and ``judge.engine``. No orchestration logic lives here.

Entrypoint::

    python orchestrator.py [args...]
    python -m karma.interfaces.cli.main [args...]

Subcommands:

``run-workflow``
    Run a workflow YAML file end to end.
``run-case``
    Run a single case directly without a workflow file.
``manual``
    Set a case up, hand the namespace to a human operator to act on by
    hand, then verify on demand and tear down.
``judge``
    Run the judge on an existing run directory.
``info``
    Print the registry of available agents, metrics, and providers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .profiles import load_profile, merge_profile
from ...runtime.service import run_workflow, run_case
from ...agents.registry import list_agents
from ...metrics import list_metrics
from ...definitions.workflows import load_workflow_file, normalize_workflow


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Return the top-level argument parser with all subcommands registered."""
    parser = argparse.ArgumentParser(
        prog="karma",
        description="KARMA Kubernetes microservice agent benchmark framework.",
    )
    sub = parser.add_subparsers(dest="command")

    wf = sub.add_parser("run-workflow", help="Run a workflow YAML file.")
    wf.add_argument("workflow", help="Path to the workflow YAML file.")
    wf.add_argument("--agent", default=None)
    wf.add_argument("--sandbox", default="local", choices=["local", "docker"])
    wf.add_argument("--runs-dir", default="runs")
    wf.add_argument("--resources-dir", default="resources")
    wf.add_argument("--profile", default=None)
    wf.add_argument("--dry-run", action="store_true",
                    help="Resolve rows and print without running.")
    wf.add_argument("--judge", action="store_true",
                    help="Run the LLM judge on every stage after the run completes.")
    wf.add_argument("--output", default="text", choices=["text", "json"])

    rc = sub.add_parser("run-case", help="Run a single case.")
    rc.add_argument("service")
    rc.add_argument("case")
    rc.add_argument("--agent", default=None)
    rc.add_argument("--sandbox", default="local", choices=["local", "docker"])
    rc.add_argument("--runs-dir", default="runs")
    rc.add_argument("--resources-dir", default="resources")
    rc.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    rc.add_argument("--timeout", type=int, default=900)
    rc.add_argument("--judge", action="store_true",
                    help="Run the LLM judge on every stage after the run completes.")
    rc.add_argument("--profile", default=None)
    rc.add_argument("--output", default="text", choices=["text", "json"])

    mn = sub.add_parser(
        "manual",
        help="Set up a case for hands-on operation, then verify and clean up.",
    )
    mn.add_argument("service")
    mn.add_argument("case")
    mn.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    mn.add_argument("--runs-dir", default="runs")
    mn.add_argument("--resources-dir", default="resources")
    mn.add_argument("--profile", default=None)

    jg = sub.add_parser("judge", help="Run the judge on an existing run directory.")
    jg.add_argument("run_dir")
    jg.add_argument("--stage", default=None)
    jg.add_argument("--model", default=None)
    jg.add_argument("--base-url", default=None,
                    help="OpenAI-compatible base URL for the judge LLM.")
    jg.add_argument("--api-key", default=None,
                    help="API key for the judge LLM (else from the environment).")
    jg.add_argument("--timeout", type=int, default=None,
                    help="Per-request judge LLM timeout in seconds.")
    jg.add_argument("--dry-run", action="store_true")
    jg.add_argument("--output", default="text", choices=["text", "json"])

    inf = sub.add_parser("info", help="Print registry info.")
    inf.add_argument("--agents", action="store_true")
    inf.add_argument("--metrics", action="store_true")

    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_param_overrides(param_args: list[str]) -> dict[str, Any]:
    """Parse ``KEY=VALUE`` strings into a parameter overrides dict.

    Values that are valid JSON are decoded as their native Python type;
    all others are kept as strings.

    Raises
    ------
    ValueError
        When any entry is not in ``KEY=VALUE`` format.
    """
    result: dict[str, Any] = {}
    for item in param_args:
        if "=" not in item:
            raise ValueError(
                f"invalid --param format (expected KEY=VALUE): {item!r}"
            )
        key, _, raw_value = item.partition("=")
        try:
            value: Any = json.loads(raw_value)
        except Exception:
            value = raw_value
        result[key.strip()] = value
    return result


def _maybe_inline_judge(
    result: dict[str, Any], runs_dir: Path, output_format: str
) -> None:
    """Run the judge on every stage of a just-completed run and print it.

    Restores the old inline post-run judging: ``run-case``/``run-workflow``
    with ``--judge`` score the run immediately instead of requiring a
    separate ``judge`` invocation.
    """
    from ...judge.engine import run_judge_batch

    run_id = result.get("run_id")
    if not run_id:
        return
    try:
        batch = run_judge_batch(runs_dir / str(run_id))
    except Exception as exc:
        print(f"inline judge failed: {exc}", file=sys.stderr)
        return
    if output_format == "json":
        print(json.dumps(batch, indent=2))
    else:
        print("judge:")
        for sid, res in batch.items():
            print(f"  stage {sid}: {res.get('verdict')} (score {res.get('score')})")


def _print_result(result: dict[str, Any], output_format: str) -> None:
    """Print *result* to stdout in *output_format* (``"text"`` or ``"json"``)."""
    if output_format == "json":
        print(json.dumps(result, indent=2))
        return
    status = result.get("status", "unknown")
    run_id = result.get("run_id", "?")
    duration = result.get("duration_sec", 0.0)
    print(f"run {run_id}: {status} ({duration:.1f}s)")
    for stage in result.get("stages") or []:
        sid = stage.get("stage_id", "?")
        st = stage.get("status", "?")
        verdict = stage.get("oracle_verdict", "?")
        print(f"  stage {sid}: {st} (oracle: {verdict})")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_run_workflow(args: argparse.Namespace) -> None:
    """Handle the ``run-workflow`` subcommand."""
    profile = load_profile(args.profile) if args.profile else {}
    merged = merge_profile(vars(args), profile)
    resources_dir = Path(merged.get("resources_dir", "resources"))
    runs_dir = Path(merged.get("runs_dir", "runs"))

    raw = load_workflow_file(Path(args.workflow))
    workflow = normalize_workflow(raw, resources_dir=resources_dir)

    if args.dry_run:
        print(json.dumps(workflow, indent=2, default=str))
        return

    result = run_workflow(
        workflow,
        runs_dir=runs_dir,
        resources_dir=resources_dir,
        agent_name=merged.get("agent"),
        sandbox_mode=merged.get("sandbox", "local"),
    )
    _print_result(result, args.output)
    if getattr(args, "judge", False):
        _maybe_inline_judge(result, runs_dir, args.output)


def _cmd_run_case(args: argparse.Namespace) -> None:
    """Handle the ``run-case`` subcommand."""
    profile = load_profile(args.profile) if args.profile else {}
    merged = merge_profile(vars(args), profile)
    param_overrides = _parse_param_overrides(args.param)
    resources_dir = Path(merged.get("resources_dir", "resources"))
    runs_dir = Path(merged.get("runs_dir", "runs"))

    result = run_case(
        args.service,
        args.case,
        runs_dir=runs_dir,
        resources_dir=resources_dir,
        param_overrides=param_overrides,
        agent_name=merged.get("agent"),
        sandbox_mode=merged.get("sandbox", "local"),
        agent_timeout_sec=args.timeout,
    )
    _print_result(result, args.output)
    if getattr(args, "judge", False):
        _maybe_inline_judge(result, runs_dir, args.output)


def _cmd_manual(args: argparse.Namespace) -> None:
    """Handle the ``manual`` subcommand.

    Sets the scenario up (proxy, namespaces, preconditions, decoys, bundle),
    pauses for a human operator to do the task by hand against the printed
    namespace, then verifies on demand (re-runnable) and tears down. Drives
    the same ``runtime.manual`` lifecycle the HTTP API exposes -- the whole
    session lives in this one process, so the in-memory state survives.
    """
    import time
    from ...runtime import manual

    profile = load_profile(args.profile) if args.profile else {}
    merged = merge_profile(vars(args), profile)
    param_overrides = _parse_param_overrides(args.param)
    resources_dir = Path(merged.get("resources_dir", "resources"))
    runs_dir = Path(merged.get("runs_dir", "runs"))

    run_id = manual.start_manual_run(
        args.service,
        args.case,
        runs_dir=runs_dir,
        resources_dir=resources_dir,
        param_overrides=param_overrides,
    )
    print(f"manual run {run_id}: setting up scenario...")

    status = manual.get_manual_status(run_id) or {}
    while status.get("status") == "setup_running":
        time.sleep(1.5)
        status = manual.get_manual_status(run_id) or {}

    if status.get("status") == "setup_failed":
        print(f"setup failed: {status.get('error')}", file=sys.stderr)
        manual.cleanup_manual_run(run_id)
        sys.exit(1)

    print("\nready -- operate the cluster by hand, then verify:")
    for role, ns in (status.get("namespace_bindings") or {}).items():
        print(f"  namespace[{role}] = {ns}")
    if status.get("kubeconfig_path"):
        print(f"  export KUBECONFIG={status['kubeconfig_path']}")
    if status.get("prompt_path"):
        print(f"  task prompt: {status['prompt_path']}")

    try:
        while True:
            choice = input("\n[Enter] verify  /  q then Enter to quit: ").strip().lower()
            if choice == "q":
                break
            result = manual.submit_manual_run(run_id)
            print(
                f"  {result.get('status')} "
                f"(oracle: {result.get('oracle_verdict')}, "
                f"attempt {result.get('attempts')})"
            )
            if result.get("status") == "passed":
                break
    finally:
        manual.cleanup_manual_run(run_id)
        print("cleaned up.")


def _cmd_judge(args: argparse.Namespace) -> None:
    """Handle the ``judge`` subcommand."""
    from ...judge.engine import run_judge, run_judge_batch

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        raise RuntimeError(f"run directory not found: {run_dir}")

    judge_kwargs = {
        "judge_model": args.model,
        "judge_base_url": args.base_url,
        "judge_api_key": args.api_key,
        "judge_timeout_sec": args.timeout,
    }
    if args.stage:
        result = run_judge(run_dir, args.stage, dry_run=args.dry_run, **judge_kwargs)
    else:
        result = run_judge_batch(run_dir, dry_run=args.dry_run, **judge_kwargs)
    _print_result(result, args.output)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """Parse *argv* and dispatch to the appropriate subcommand handler.

    Exits with code 1 on error and code 0 on success.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "info":
        if args.agents or not args.metrics:
            print("agents:", ", ".join(list_agents()))
        if args.metrics or not args.agents:
            print("metrics:", ", ".join(list_metrics()))
        sys.exit(0)

    try:
        if args.command == "run-workflow":
            _cmd_run_workflow(args)
        elif args.command == "run-case":
            _cmd_run_case(args)
        elif args.command == "manual":
            _cmd_manual(args)
        elif args.command == "judge":
            _cmd_judge(args)
        else:
            parser.print_help()
            sys.exit(1)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
