#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _as_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_trailing_json_array(text: str) -> list[dict[str, Any]] | None:
    raw = str(text or "")
    if not raw.strip():
        return None
    decoder = json.JSONDecoder()
    starts = [idx for idx, ch in enumerate(raw) if ch == "["]
    for start in reversed(starts):
        snippet = raw[start:].lstrip()
        if not snippet:
            continue
        try:
            payload, _ = decoder.raw_decode(snippet)
        except Exception:
            continue
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict) and "workflow" in first and "result" in first:
                return payload
    return None


def _resolve_path(path_value: Any) -> Path | None:
    raw = str(path_value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _load_stage_index_by_id(workflow_path: Path) -> tuple[int, dict[str, int]]:
    payload = yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"workflow yaml must be an object: {workflow_path}")
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        raise ValueError(f"workflow spec must be an object: {workflow_path}")
    stages = spec.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError(f"workflow spec.stages must be a non-empty list: {workflow_path}")
    mapping: dict[str, int] = {}
    for index, stage in enumerate(stages, start=1):
        if not isinstance(stage, dict):
            raise ValueError(f"workflow stage must be an object: {workflow_path}#{index}")
        stage_id = str(stage.get("id") or "").strip()
        if not stage_id:
            stage_id = f"stage_{index}"
        mapping[stage_id] = index
    return len(stages), mapping


def _extract_failed_stage_info(
    result_payload: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    if not isinstance(result_payload, dict):
        return None, None, None, None

    stage_results_path = _resolve_path(result_payload.get("workflow_stage_results_path"))
    if stage_results_path is not None and stage_results_path.exists() and stage_results_path.is_file():
        try:
            for raw_line in stage_results_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    continue
                status = str(entry.get("status") or "").strip()
                if status != "passed":
                    stage_id = str(entry.get("stage_id") or "").strip() or None
                    reason = str(entry.get("reason") or "").strip() or None
                    return stage_id, status or None, reason, "workflow_stage_results"
        except Exception:
            pass

    workflow_state_path = _resolve_path(result_payload.get("workflow_state_path"))
    state = _read_json(workflow_state_path)
    if isinstance(state, dict):
        statuses = state.get("stage_statuses") if isinstance(state.get("stage_statuses"), list) else []
        for item in statuses:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip()
            if status and status != "passed":
                stage_id = str(item.get("stage_id") or "").strip() or None
                reason = str(item.get("reason") or "").strip() or None
                return stage_id, status, reason, "workflow_state"
        active_stage_id = str(state.get("active_stage_id") or "").strip() or None
        if active_stage_id:
            terminal_reason = str(result_payload.get("terminal_reason") or "").strip() or None
            return active_stage_id, "active_on_terminal", terminal_reason, "workflow_state_active_stage"

    terminal_reason = str(result_payload.get("terminal_reason") or "").strip() or None
    if terminal_reason:
        return None, "unknown", terminal_reason, "terminal_reason"
    return None, None, None, None


def _extract_active_stage_info(result_payload: dict[str, Any] | None) -> tuple[int | None, str | None]:
    if not isinstance(result_payload, dict):
        return None, None
    workflow_state_path = _resolve_path(result_payload.get("workflow_state_path"))
    state = _read_json(workflow_state_path)
    if not isinstance(state, dict):
        return None, None
    raw_index = state.get("active_stage_index")
    active_stage_index = raw_index if isinstance(raw_index, int) and raw_index > 0 else None
    active_stage_id = str(state.get("active_stage_id") or "").strip() or None
    return active_stage_index, active_stage_id


def _classify_outcome(
    *,
    parse_error: str | None,
    status: str,
    terminal_reason: str | None,
    failed_stage_reason: str | None,
    returncode: int,
) -> str:
    if parse_error:
        return "parse_error"
    if returncode != 0 and status == "process_error":
        return "process_error"
    if status == "passed":
        return "passed"
    if terminal_reason == "agent_exited":
        return "agent_exited"
    if terminal_reason == "submit_timeout":
        return "submit_timeout"
    if terminal_reason == "manual_cleanup":
        return "manual_cleanup"
    if failed_stage_reason == "stage_setup_failed" or terminal_reason == "next_stage_setup_failed":
        return "precondition_failed"
    if failed_stage_reason == "oracle_fail":
        return "oracle_fail"
    if terminal_reason:
        return f"terminal_{terminal_reason}"
    if status:
        return f"status_{status}"
    return "unknown_failure"


def _mean(values: list[int]) -> float | None:
    if not values:
        return None
    return float(sum(values)) / float(len(values))


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


@dataclass
class RunOutcome:
    attempt_index: int
    stage_count: int
    workflow_path: str
    command: list[str]
    returncode: int
    status: str
    passed: bool
    cleanup_status: str | None
    terminal_reason: str | None
    failed_stage_id: str | None
    failed_stage_status: str | None
    failed_stage_reason: str | None
    failed_stage_source: str | None
    active_stage_index: int | None
    active_stage_id: str | None
    failure_stage_index: int | None
    parse_error: str | None
    log_path: str
    result_payload: dict[str, Any] | None
    classification: str
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_index": self.attempt_index,
            "stage_count": self.stage_count,
            "workflow_path": self.workflow_path,
            "command": self.command,
            "returncode": self.returncode,
            "status": self.status,
            "passed": self.passed,
            "cleanup_status": self.cleanup_status,
            "terminal_reason": self.terminal_reason,
            "failed_stage_id": self.failed_stage_id,
            "failed_stage_status": self.failed_stage_status,
            "failed_stage_reason": self.failed_stage_reason,
            "failed_stage_source": self.failed_stage_source,
            "active_stage_index": self.active_stage_index,
            "active_stage_id": self.active_stage_id,
            "failure_stage_index": self.failure_stage_index,
            "parse_error": self.parse_error,
            "log_path": self.log_path,
            "result_payload": self.result_payload,
            "classification": self.classification,
            "dry_run": self.dry_run,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunOutcome":
        return cls(
            attempt_index=int(payload.get("attempt_index") or 0),
            stage_count=int(payload.get("stage_count") or 0),
            workflow_path=str(payload.get("workflow_path") or ""),
            command=list(payload.get("command") or []),
            returncode=int(payload.get("returncode") or 0),
            status=str(payload.get("status") or ""),
            passed=bool(payload.get("passed")),
            cleanup_status=(str(payload.get("cleanup_status")) if payload.get("cleanup_status") is not None else None),
            terminal_reason=(str(payload.get("terminal_reason")) if payload.get("terminal_reason") is not None else None),
            failed_stage_id=(str(payload.get("failed_stage_id")) if payload.get("failed_stage_id") is not None else None),
            failed_stage_status=(
                str(payload.get("failed_stage_status")) if payload.get("failed_stage_status") is not None else None
            ),
            failed_stage_reason=(
                str(payload.get("failed_stage_reason")) if payload.get("failed_stage_reason") is not None else None
            ),
            failed_stage_source=(
                str(payload.get("failed_stage_source")) if payload.get("failed_stage_source") is not None else None
            ),
            active_stage_index=(
                int(payload.get("active_stage_index")) if isinstance(payload.get("active_stage_index"), int) else None
            ),
            active_stage_id=(str(payload.get("active_stage_id")) if payload.get("active_stage_id") is not None else None),
            failure_stage_index=(
                int(payload.get("failure_stage_index")) if isinstance(payload.get("failure_stage_index"), int) else None
            ),
            parse_error=(str(payload.get("parse_error")) if payload.get("parse_error") is not None else None),
            log_path=str(payload.get("log_path") or ""),
            result_payload=(payload.get("result_payload") if isinstance(payload.get("result_payload"), dict) else None),
            classification=str(payload.get("classification") or ""),
            dry_run=bool(payload.get("dry_run")),
        )


class SingleStageReliabilityRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.work_dir = Path(args.work_dir).resolve()
        self.log_dir = self.work_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.work_dir / "history.jsonl"
        self.summary_path = self.work_dir / "summary.json"
        self.aggregate_csv = self.work_dir / "aggregate_runs.csv"
        self.history: list[RunOutcome] = []
        self.resume_skipped_lines = 0
        self.start_attempt = 1

        if not args.resume:
            self.history_path.unlink(missing_ok=True)
            self.summary_path.unlink(missing_ok=True)
            self.aggregate_csv.unlink(missing_ok=True)

        self.workflow_path = Path(args.workflow).resolve()
        self.stage_count, self.stage_index_by_id = _load_stage_index_by_id(self.workflow_path)
        if self.stage_count != 1:
            raise ValueError(
                f"--workflow must contain exactly one stage for this sweep (found {self.stage_count}): {self.workflow_path}"
            )

        if args.resume:
            existing_history, skipped_lines = self._load_existing_history()
            self.history.extend(existing_history)
            self.resume_skipped_lines = skipped_lines
            self.start_attempt = self._next_attempt_index()
            print(
                (
                    "[single-stage] resume loaded "
                    f"completed={self.start_attempt - 1} "
                    f"skipped_history_lines={self.resume_skipped_lines}"
                ),
                flush=True,
            )

    def _load_existing_history(self) -> tuple[list[RunOutcome], int]:
        if not self.history_path.exists() or not self.history_path.is_file():
            return [], 0
        loaded: list[RunOutcome] = []
        skipped = 0
        for raw_line in self.history_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                skipped += 1
                continue
            if not isinstance(payload, dict):
                skipped += 1
                continue
            try:
                loaded.append(RunOutcome.from_dict(payload))
            except Exception:
                skipped += 1
        loaded.sort(key=lambda item: item.attempt_index)
        return loaded, skipped

    def _next_attempt_index(self) -> int:
        max_attempt = 0
        for item in self.history:
            if isinstance(item.attempt_index, int) and item.attempt_index > max_attempt:
                max_attempt = item.attempt_index
        return max_attempt + 1

    def _base_cmd(self) -> list[str]:
        cmd = [
            self.args.python_bin,
            self.args.orchestrator,
            "workflow-run",
            "--workflow",
            str(self.workflow_path),
            "--sandbox",
            self.args.sandbox,
        ]
        if self.args.max_attempts is not None:
            cmd.extend(["--max-attempts", str(self.args.max_attempts)])
        if self.args.stage_failure_mode:
            cmd.extend(["--stage-failure-mode", self.args.stage_failure_mode])
        if self.args.final_sweep_mode:
            cmd.extend(["--final-sweep-mode", self.args.final_sweep_mode])
        for arg in self.args.orchestrator_arg:
            cmd.append(arg)
        return cmd

    def _run_once(self, *, attempt_index: int) -> RunOutcome:
        cmd = self._base_cmd()
        log_path = self.log_dir / f"run_{attempt_index:04d}.log"

        print(
            f"[single-stage] run={attempt_index}/{self.args.runs} stages={self.stage_count}",
            flush=True,
        )
        print(f"[single-stage] command: {' '.join(shlex.quote(part) for part in cmd)}", flush=True)

        if self.args.dry_run:
            payload = {
                "workflow": str(self.workflow_path),
                "result": {
                    "status": "dry_run",
                    "terminal_reason": "dry_run",
                    "cleanup_status": None,
                },
            }
            log_path.write_text(
                "[dry-run]\n" + " ".join(shlex.quote(part) for part in cmd) + "\n",
                encoding="utf-8",
            )
            outcome = RunOutcome(
                attempt_index=attempt_index,
                stage_count=self.stage_count,
                workflow_path=str(self.workflow_path),
                command=cmd,
                returncode=0,
                status="dry_run",
                passed=False,
                cleanup_status=None,
                terminal_reason="dry_run",
                failed_stage_id=None,
                failed_stage_status=None,
                failed_stage_reason=None,
                failed_stage_source=None,
                active_stage_index=None,
                active_stage_id=None,
                failure_stage_index=None,
                parse_error=None,
                log_path=str(log_path),
                result_payload=payload["result"],
                classification="dry_run",
                dry_run=True,
            )
            self._record(outcome)
            return outcome

        timeout = int(self.args.run_timeout_sec or 0)
        proc: subprocess.CompletedProcess[str] | None = None
        stdout = ""
        stderr = ""
        parse_error: str | None = None
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=timeout if timeout > 0 else None,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            proc = subprocess.CompletedProcess(cmd, returncode=124, stdout=stdout, stderr=stderr)
            parse_error = f"timeout after {timeout}s"
        except Exception as exc:  # pragma: no cover - defensive path
            proc = subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(exc))
            parse_error = f"exception: {exc}"

        combined = stdout
        if stderr:
            if combined and not combined.endswith("\n"):
                combined += "\n"
            combined += stderr
        log_path.write_text(combined, encoding="utf-8")

        result_payload: dict[str, Any] | None = None
        status = "process_error"
        terminal_reason: str | None = None
        cleanup_status: str | None = None
        failed_stage_id: str | None = None
        failed_stage_status: str | None = None
        failed_stage_reason: str | None = None
        failed_stage_source: str | None = None
        active_stage_index: int | None = None
        active_stage_id: str | None = None
        failure_stage_index: int | None = None

        parsed = _parse_trailing_json_array(combined)
        if parsed:
            first = parsed[0]
            result_obj = first.get("result") if isinstance(first, dict) else None
            if isinstance(result_obj, dict):
                result_payload = result_obj
                status = str(result_obj.get("status") or "").strip() or "unknown"
                terminal_reason = str(result_obj.get("terminal_reason") or "").strip() or None
                cleanup_status = str(result_obj.get("cleanup_status") or "").strip() or None
                (
                    failed_stage_id,
                    failed_stage_status,
                    failed_stage_reason,
                    failed_stage_source,
                ) = _extract_failed_stage_info(result_payload)
                active_stage_index, active_stage_id = _extract_active_stage_info(result_payload)
                if failed_stage_id and failed_stage_id in self.stage_index_by_id:
                    failure_stage_index = self.stage_index_by_id[failed_stage_id]
                elif active_stage_index is not None:
                    failure_stage_index = active_stage_index
            elif parse_error is None:
                parse_error = "result payload missing from orchestrator output"
        elif parse_error is None:
            parse_error = "unable to parse trailing orchestrator json output"

        passed = status == "passed"
        classification = _classify_outcome(
            parse_error=parse_error,
            status=status,
            terminal_reason=terminal_reason,
            failed_stage_reason=failed_stage_reason,
            returncode=proc.returncode,
        )

        outcome = RunOutcome(
            attempt_index=attempt_index,
            stage_count=self.stage_count,
            workflow_path=str(self.workflow_path),
            command=cmd,
            returncode=proc.returncode,
            status=status,
            passed=passed,
            cleanup_status=cleanup_status,
            terminal_reason=terminal_reason,
            failed_stage_id=failed_stage_id,
            failed_stage_status=failed_stage_status,
            failed_stage_reason=failed_stage_reason,
            failed_stage_source=failed_stage_source,
            active_stage_index=active_stage_index,
            active_stage_id=active_stage_id,
            failure_stage_index=failure_stage_index,
            parse_error=parse_error,
            log_path=str(log_path),
            result_payload=result_payload,
            classification=classification,
            dry_run=False,
        )
        self._record(outcome)
        return outcome

    def _record(self, outcome: RunOutcome) -> None:
        self.history.append(outcome)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(outcome.to_dict(), sort_keys=True) + "\n")

    def _write_aggregate_csv(self) -> None:
        with self.aggregate_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "attempt_index",
                    "stage_count",
                    "passed",
                    "status",
                    "classification",
                    "terminal_reason",
                    "cleanup_status",
                    "failure_stage_index",
                    "failed_stage_id",
                    "failed_stage_status",
                    "failed_stage_reason",
                    "failed_stage_source",
                    "active_stage_index",
                    "active_stage_id",
                    "returncode",
                    "parse_error",
                    "log_path",
                    "workflow_path",
                ]
            )
            for item in self.history:
                writer.writerow(
                    [
                        item.attempt_index,
                        item.stage_count,
                        item.passed,
                        item.status,
                        item.classification,
                        item.terminal_reason,
                        item.cleanup_status,
                        item.failure_stage_index,
                        item.failed_stage_id,
                        item.failed_stage_status,
                        item.failed_stage_reason,
                        item.failed_stage_source,
                        item.active_stage_index,
                        item.active_stage_id,
                        item.returncode,
                        item.parse_error,
                        item.log_path,
                        item.workflow_path,
                    ]
                )

    def _summary_stats(self) -> dict[str, Any]:
        total = len(self.history)
        pass_count = sum(1 for item in self.history if item.passed)
        fail_count = total - pass_count

        classification_counts = Counter(item.classification for item in self.history)
        terminal_reason_counts = Counter(item.terminal_reason or "" for item in self.history if item.terminal_reason)
        failed_stage_counts = Counter(item.failed_stage_id or "" for item in self.history if item.failed_stage_id)

        failed_stage_indexes = [
            int(item.failure_stage_index)
            for item in self.history
            if not item.passed and isinstance(item.failure_stage_index, int) and item.failure_stage_index > 0
        ]

        stage_reached_values: list[int] = []
        for item in self.history:
            if item.passed:
                stage_reached_values.append(self.stage_count)
                continue
            if isinstance(item.failure_stage_index, int) and item.failure_stage_index > 0:
                stage_reached_values.append(item.failure_stage_index)
                continue
            if isinstance(item.active_stage_index, int) and item.active_stage_index > 0:
                stage_reached_values.append(item.active_stage_index)

        return {
            "total_runs": total,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "pass_rate": (float(pass_count) / float(total)) if total else None,
            "avg_failed_stage_index": _mean(failed_stage_indexes),
            "median_failed_stage_index": _median(failed_stage_indexes),
            "avg_stage_reached": _mean(stage_reached_values),
            "median_stage_reached": _median(stage_reached_values),
            "classification_counts": _counter_dict(classification_counts),
            "terminal_reason_counts": _counter_dict(terminal_reason_counts),
            "failed_stage_counts": _counter_dict(failed_stage_counts),
        }

    def run(self) -> dict[str, Any]:
        target_runs = int(self.args.runs)
        start = self.start_attempt if self.args.resume else 1
        if start > target_runs:
            print(
                (
                    "[single-stage] resume already satisfied "
                    f"target={target_runs} completed={start - 1}; skipping execution"
                ),
                flush=True,
            )
        else:
            for attempt_index in range(start, target_runs + 1):
                self._run_once(attempt_index=attempt_index)

        self._write_aggregate_csv()
        summary_stats = self._summary_stats()
        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "workflow": {
                "path": str(self.workflow_path),
                "stage_count": self.stage_count,
            },
            "work_dir": str(self.work_dir),
            "history_path": str(self.history_path),
            "aggregate_runs_csv": str(self.aggregate_csv),
            "runs_target": int(self.args.runs),
            "dry_run": bool(self.args.dry_run),
            "resume": bool(self.args.resume),
            "resume_skipped_lines": int(self.resume_skipped_lines),
            "summary": summary_stats,
            "runs": [item.to_dict() for item in self.history],
        }
        self.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Runs one single-stage workflow repeatedly and summarizes pass/fail reliability."
        )
    )
    parser.add_argument(
        "--workflow",
        default="workflows/rabbitmq-blue-green-migration-single.yaml",
        help="Path to single-stage workflow yaml.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=50,
        help="How many workflow runs to execute (default: 50).",
    )
    parser.add_argument(
        "--work-dir",
        default=f".benchmark/rabbitmq-bluegreen-single-stage-{_utc_stamp()}",
        help="Output directory for logs/history/summary.",
    )
    parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Python executable used to invoke orchestrator.py.",
    )
    parser.add_argument(
        "--orchestrator",
        default="orchestrator.py",
        help="Path to orchestrator entrypoint.",
    )
    parser.add_argument(
        "--sandbox",
        default="docker",
        choices=["local", "docker"],
        help="Sandbox mode passed to workflow-run (default: docker).",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Optional override for --max-attempts in workflow-run.",
    )
    parser.add_argument(
        "--stage-failure-mode",
        default="terminate",
        choices=["continue", "terminate"],
        help="Override for workflow-run --stage-failure-mode (default: terminate).",
    )
    parser.add_argument(
        "--final-sweep-mode",
        default="off",
        choices=["inherit", "full", "off"],
        help="Override for workflow-run --final-sweep-mode (default: off).",
    )
    parser.add_argument(
        "--run-timeout-sec",
        type=int,
        default=0,
        help="Timeout per workflow run in seconds (0 means no timeout).",
    )
    parser.add_argument(
        "--orchestrator-arg",
        action="append",
        default=[],
        help="Extra raw arg forwarded to orchestrator workflow-run (repeatable).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=_as_bool_env("RESUME_SINGLE_STAGE_SWEEP", False),
        help="Keep existing history/summary and append new runs in work-dir.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=_as_bool_env("DRY_RUN_SINGLE_STAGE_SWEEP", False),
        help="Print and record planned commands without executing orchestrator.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be >= 1")
    if args.run_timeout_sec < 0:
        parser.error("--run-timeout-sec must be >= 0")
    if args.max_attempts is not None and args.max_attempts < 1:
        parser.error("--max-attempts must be >= 1")

    workflow = Path(args.workflow)
    if not workflow.exists():
        parser.error(f"--workflow not found: {workflow}")

    runner = SingleStageReliabilityRunner(args)
    summary = runner.run()
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"[single-stage] summary: {runner.summary_path}")
    print(f"[single-stage] runs csv: {runner.aggregate_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
