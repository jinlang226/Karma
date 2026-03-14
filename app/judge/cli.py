import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from app.llm_config import collect_judge_env
from app.settings import ROOT

from .engine import TrajectoryJudge


def _add_common_flags(parser):
    parser.add_argument(
        "--judge-env-file",
        help=(
            "Path to env file with JUDGE_* or LLM_* keys. "
            "If omitted, auto-loads judge.env (or config/judge.env) when present."
        ),
    )
    parser.add_argument("--llm-env-file", help=argparse.SUPPRESS)
    parser.add_argument(
        "--agent",
        default="react",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--judge-model", help="Judge model name.")
    parser.add_argument("--judge-base-url", help="Judge API base URL.")
    parser.add_argument("--judge-api-key", help="Judge API key.")
    parser.add_argument("--judge-timeout", type=int, default=120, help="Judge timeout seconds.")
    parser.add_argument("--judge-max-retries", type=int, default=2, help="Judge retry attempts.")
    parser.add_argument("--judge-prompt-version", default="v1", help="Judge prompt version.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build judge input/prompt artifacts and print paths, but skip LLM calls.",
    )
    parser.add_argument(
        "--judge-include-outcome",
        action="store_true",
        help="Include final outcome fields in judge input.",
    )
    parser.add_argument(
        "--judge-fail-open",
        dest="judge_fail_open",
        action="store_true",
        default=True,
        help="Continue even if judge call fails (default).",
    )
    parser.add_argument(
        "--judge-fail-closed",
        dest="judge_fail_open",
        action="store_false",
        help="Exit non-zero on judge call failures.",
    )


def _load_batch_index(batch_dir):
    batch_root = Path(batch_dir)
    if not batch_root.is_absolute():
        batch_root = (ROOT / batch_root).resolve()
    index_path = batch_root / "batch_index.json"
    if not index_path.exists():
        raise RuntimeError(f"batch index not found: {index_path}")

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"invalid batch index format: {index_path}")
    normalized = _normalize_batch_rows(payload)
    if normalized:
        return batch_root, normalized

    discovered = _discover_runs_from_batch_window(batch_root, payload)
    if discovered:
        print(
            f"[judge] batch index has no run_dir rows; discovered {len(discovered)} run(s) from timestamp window",
            file=sys.stderr,
        )
    return batch_root, discovered


def _row_from_source(row, parent=None):
    parent = parent or {}
    run_dir = row.get("run_dir")
    if not run_dir:
        return None
    service = row.get("service") or parent.get("service")
    case = row.get("case") or parent.get("case")
    return {
        "run_dir": run_dir,
        "service": service,
        "case": case,
    }


def _normalize_batch_rows(rows):
    out = []
    seen = set()

    def add(item, parent=None):
        row = _row_from_source(item, parent=parent)
        if not row:
            return
        key = str(row.get("run_dir"))
        if key in seen:
            return
        seen.add(key)
        out.append(row)

    for row in rows:
        if not isinstance(row, dict):
            continue

        add(row)

        runs = row.get("runs")
        if isinstance(runs, list):
            for item in runs:
                if isinstance(item, dict):
                    add(item, parent=row)

        result = row.get("result")
        if isinstance(result, dict):
            add(result, parent=row)
            nested_runs = result.get("runs")
            if isinstance(nested_runs, list):
                for item in nested_runs:
                    if isinstance(item, dict):
                        add(item, parent=row)
    return out


def _parse_batch_ts(batch_name):
    if not str(batch_name).startswith("batch_"):
        return None
    ts = str(batch_name)[len("batch_") :]
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H-%M-%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except Exception:
        return None


def _discover_runs_from_batch_window(batch_root, index_rows):
    batch_ts = _parse_batch_ts(batch_root.name)
    if batch_ts is None:
        return []
    start_ts = int(batch_ts.timestamp())

    runs_root = batch_root.parent
    end_ts = None
    sibling_batches = sorted(
        [p for p in runs_root.iterdir() if p.is_dir() and p.name.startswith("batch_")],
        key=lambda p: p.name,
    )
    for sibling in sibling_batches:
        if sibling.name <= batch_root.name:
            continue
        sibling_ts = _parse_batch_ts(sibling.name)
        if sibling_ts is not None:
            end_ts = int(sibling_ts.timestamp())
            break

    allowed_pairs = {
        (str(row.get("service") or ""), str(row.get("case") or ""))
        for row in index_rows
        if isinstance(row, dict)
        and str(row.get("service") or "").strip()
        and str(row.get("case") or "").strip()
    }

    discovered = []
    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir() or run_dir.name.startswith("batch_"):
            continue
        meta_path = run_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        run_started = meta.get("setup_started_at_ts")
        if not isinstance(run_started, int):
            continue
        if run_started < start_ts:
            continue
        if end_ts is not None and run_started >= end_ts:
            continue
        service = str(meta.get("service") or "")
        case = str(meta.get("case") or "")
        if allowed_pairs and (service, case) not in allowed_pairs:
            continue
        discovered.append(
            {
                "run_dir": str(run_dir),
                "service": service or None,
                "case": case or None,
                "_started": run_started,
            }
        )

    discovered.sort(key=lambda row: (row.get("_started", 0), row.get("run_dir", "")))
    for row in discovered:
        row.pop("_started", None)
    return discovered


def _prepare_judge_args(args):
    judge_env_file = getattr(args, "judge_env_file", None) or getattr(args, "llm_env_file", None)
    legacy_agent = getattr(args, "agent", "react")
    judge_env, source = collect_judge_env(
        judge_env_file=judge_env_file,
        repo_root=ROOT,
        environ=dict(os.environ),
        allow_legacy_agent_env=True,
        legacy_agent=legacy_agent,
        return_source=True,
    )
    if source and str(source).startswith("legacy:"):
        print(
            f"[judge] warning: using deprecated agent env fallback ({source[7:]}); "
            "prefer --judge-env-file or repo judge.env",
            file=sys.stderr,
        )
    args._llm_env = judge_env
    return TrajectoryJudge.from_args(args)


def _run_single(args):
    judge = _prepare_judge_args(args)
    summary = judge.evaluate_run(
        run_dir=args.run_dir,
        service=getattr(args, "service", None),
        case=getattr(args, "case", None),
    )
    print(json.dumps(summary, indent=2))
    return 0


def _run_batch(args):
    batch_root, rows = _load_batch_index(args.batch_dir)
    judge = _prepare_judge_args(args)

    run_results = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        run_dir = row.get("run_dir")
        if not run_dir:
            print(f"[judge] skipping row without run_dir: {row}", file=sys.stderr)
            continue

        summary = judge.evaluate_run(
            run_dir=run_dir,
            service=row.get("service"),
            case=row.get("case"),
        )
        run_results.append(summary)
        line = (
            f"[judge] {summary.get('service')}/{summary.get('case')} "
            f"status={summary.get('judge_status')} score={summary.get('final_score')}"
        )
        if getattr(args, "dry_run", False):
            line += f" prompt={summary.get('prompt_path')}"
        print(line)

    artifacts = judge.write_batch_summary(batch_root, run_results)
    payload = {
        "batch_dir": str(batch_root),
        "runs_judged": len(run_results),
        **artifacts,
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Standalone trajectory judge runner.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Judge one run directory.")
    _add_common_flags(run_parser)
    run_parser.add_argument("--run-dir", required=True, help="Run directory path.")
    run_parser.add_argument("--service", help="Optional service override.")
    run_parser.add_argument("--case", help="Optional case override.")

    batch_parser = sub.add_parser("batch", help="Judge all runs in a batch directory.")
    _add_common_flags(batch_parser)
    batch_parser.add_argument("--batch-dir", required=True, help="Batch directory path.")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run_single(args)
    if args.command == "batch":
        return _run_batch(args)
    raise RuntimeError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
