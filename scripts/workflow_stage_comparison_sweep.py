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
    if failed_stage_reason == "precondition_failed":
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
    workflow_kind: str
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
            "workflow_kind": self.workflow_kind,
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


class SweepRunner:
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
        self.single_start_attempt = 1
        self.three_stage_start_attempt = 1

        if not args.resume:
            self.history_path.unlink(missing_ok=True)
            self.summary_path.unlink(missing_ok=True)
            self.aggregate_csv.unlink(missing_ok=True)

        self.single_workflow_path = Path(args.single_workflow).resolve()
        self.multi_workflow_path = Path(args.multi_workflow).resolve()
        self.single_stage_count, self.single_stage_index = _load_stage_index_by_id(self.single_workflow_path)
        self.multi_stage_count, self.multi_stage_index = _load_stage_index_by_id(self.multi_workflow_path)

        if args.resume:
            existing_history, skipped_lines = self._load_existing_history()
            self.history.extend(existing_history)
            self.resume_skipped_lines = skipped_lines
            self.single_start_attempt = self._next_attempt_index("single")
            self.three_stage_start_attempt = self._next_attempt_index("three_stage")
            print(
                (
                    "[stage-compare] resume loaded "
                    f"single_completed={self.single_start_attempt - 1} "
                    f"three_stage_completed={self.three_stage_start_attempt - 1} "
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
                loaded.append(RunOutcome(**payload))
            except Exception:
                skipped += 1
        return loaded, skipped

    def _next_attempt_index(self, workflow_kind: str) -> int:
        max_attempt = 0
        for item in self.history:
            if item.workflow_kind != workflow_kind:
                continue
            if isinstance(item.attempt_index, int) and item.attempt_index > max_attempt:
                max_attempt = item.attempt_index
        return max_attempt + 1

    def _base_cmd(self, workflow_path: Path) -> list[str]:
        cmd = [
            self.args.python_bin,
            self.args.orchestrator,
            "workflow-run",
            "--workflow",
            str(workflow_path),
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

    def _run_once(
        self,
        *,
        workflow_kind: str,
        workflow_path: Path,
        stage_count: int,
        stage_index_by_id: dict[str, int],
        attempt_index: int,
    ) -> RunOutcome:
        cmd = self._base_cmd(workflow_path)
        log_path = self.log_dir / f"{workflow_kind}_run_{attempt_index:04d}.log"

        print(
            f"[stage-compare] {workflow_kind} run={attempt_index}/{self.args.runs_per_workflow} "
            f"stages={stage_count}",
            flush=True,
        )
        print(f"[stage-compare] command: {' '.join(shlex.quote(part) for part in cmd)}", flush=True)

        if self.args.dry_run:
            payload = {
                "workflow": str(workflow_path),
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
                workflow_kind=workflow_kind,
                attempt_index=attempt_index,
                stage_count=stage_count,
                workflow_path=str(workflow_path),
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
                failed_stage_id, failed_stage_status, failed_stage_reason, failed_stage_source = _extract_failed_stage_info(
                    result_payload
                )
                active_stage_index, active_stage_id = _extract_active_stage_info(result_payload)
                if failed_stage_id and failed_stage_id in stage_index_by_id:
                    failure_stage_index = stage_index_by_id[failed_stage_id]
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
            workflow_kind=workflow_kind,
            attempt_index=attempt_index,
            stage_count=stage_count,
            workflow_path=str(workflow_path),
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

    def _summarize_workflow_kind(
        self,
        *,
        workflow_kind: str,
        stage_count: int,
    ) -> dict[str, Any]:
        runs = [item for item in self.history if item.workflow_kind == workflow_kind]
        total = len(runs)
        pass_count = sum(1 for item in runs if item.passed)
        fail_count = total - pass_count

        classification_counts = Counter(item.classification for item in runs)
        terminal_reason_counts = Counter(item.terminal_reason or "" for item in runs if item.terminal_reason)
        failed_stage_counts = Counter(item.failed_stage_id or "" for item in runs if item.failed_stage_id)

        failed_stage_indexes = [
            int(item.failure_stage_index)
            for item in runs
            if not item.passed and isinstance(item.failure_stage_index, int) and item.failure_stage_index > 0
        ]

        stage_reached_values: list[int] = []
        for item in runs:
            if item.passed:
                stage_reached_values.append(stage_count)
                continue
            if isinstance(item.failure_stage_index, int) and item.failure_stage_index > 0:
                stage_reached_values.append(item.failure_stage_index)
                continue
            if isinstance(item.active_stage_index, int) and item.active_stage_index > 0:
                stage_reached_values.append(item.active_stage_index)

        return {
            "workflow_kind": workflow_kind,
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

    def _write_aggregate_csv(self) -> None:
        with self.aggregate_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "workflow_kind",
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
                        item.workflow_kind,
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

    def run(self) -> dict[str, Any]:
        target_runs = int(self.args.runs_per_workflow)
        single_start = self.single_start_attempt if self.args.resume else 1
        three_stage_start = self.three_stage_start_attempt if self.args.resume else 1

        if single_start > target_runs:
            print(
                (
                    "[stage-compare] resume single already satisfied "
                    f"target={target_runs} completed={single_start - 1}; skipping single workflow runs"
                ),
                flush=True,
            )
        else:
            for attempt_index in range(single_start, target_runs + 1):
                self._run_once(
                    workflow_kind="single",
                    workflow_path=self.single_workflow_path,
                    stage_count=self.single_stage_count,
                    stage_index_by_id=self.single_stage_index,
                    attempt_index=attempt_index,
                )

        if three_stage_start > target_runs:
            print(
                (
                    "[stage-compare] resume three_stage already satisfied "
                    f"target={target_runs} completed={three_stage_start - 1}; skipping three_stage workflow runs"
                ),
                flush=True,
            )
        else:
            for attempt_index in range(three_stage_start, target_runs + 1):
                self._run_once(
                    workflow_kind="three_stage",
                    workflow_path=self.multi_workflow_path,
                    stage_count=self.multi_stage_count,
                    stage_index_by_id=self.multi_stage_index,
                    attempt_index=attempt_index,
                )

        single_summary = self._summarize_workflow_kind(workflow_kind="single", stage_count=self.single_stage_count)
        multi_summary = self._summarize_workflow_kind(workflow_kind="three_stage", stage_count=self.multi_stage_count)
        self._write_aggregate_csv()

        single_pass_rate = single_summary.get("pass_rate")
        multi_pass_rate = multi_summary.get("pass_rate")
        pass_rate_delta = None
        if isinstance(single_pass_rate, float) and isinstance(multi_pass_rate, float):
            pass_rate_delta = multi_pass_rate - single_pass_rate

        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "work_dir": str(self.work_dir),
            "history_path": str(self.history_path),
            "aggregate_runs_csv": str(self.aggregate_csv),
            "runs_per_workflow": int(self.args.runs_per_workflow),
            "dry_run": bool(self.args.dry_run),
            "single_workflow": {
                "path": str(self.single_workflow_path),
                "stage_count": self.single_stage_count,
                "summary": single_summary,
            },
            "three_stage_workflow": {
                "path": str(self.multi_workflow_path),
                "stage_count": self.multi_stage_count,
                "summary": multi_summary,
            },
            "comparison": {
                "pass_rate_delta_three_minus_single": pass_rate_delta,
                "avg_stage_reached_delta_three_minus_single": (
                    (multi_summary.get("avg_stage_reached") - single_summary.get("avg_stage_reached"))
                    if isinstance(multi_summary.get("avg_stage_reached"), float)
                    and isinstance(single_summary.get("avg_stage_reached"), float)
                    else None
                ),
                "avg_failed_stage_index_delta_three_minus_single": (
                    (multi_summary.get("avg_failed_stage_index") - single_summary.get("avg_failed_stage_index"))
                    if isinstance(multi_summary.get("avg_failed_stage_index"), float)
                    and isinstance(single_summary.get("avg_failed_stage_index"), float)
                    else None
                ),
            },
            "runs": [item.to_dict() for item in self.history],
        }
        self.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Runs two workflows repeatedly (single-stage and three-stage) and compares reliability."
        )
    )
    parser.add_argument(
        "--single-workflow",
        default="workflows/mongodb-rbac-reset-script-single.yaml",
        help="Path to the single-stage workflow yaml.",
    )
    parser.add_argument(
        "--multi-workflow",
        default="workflows/mongodb-rbac-reset-script-after-rbac-setup.yaml",
        help="Path to the three-stage workflow yaml.",
    )
    parser.add_argument(
        "--runs-per-workflow",
        type=int,
        default=10,
        help="How many runs to execute for each workflow (default: 10).",
    )
    parser.add_argument(
        "--work-dir",
        default=f".benchmark/workflow-stage-comparison-{_utc_stamp()}",
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
        default=None,
        help="Optional override for --stage-failure-mode in workflow-run.",
    )
    parser.add_argument(
        "--final-sweep-mode",
        default=None,
        help="Optional override for --final-sweep-mode in workflow-run.",
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
        default=_as_bool_env("RESUME_STAGE_COMPARE", False),
        help="Keep existing history/summary and append new runs in work-dir.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=_as_bool_env("DRY_RUN_STAGE_COMPARE", False),
        help="Print and record planned commands without executing orchestrator.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.runs_per_workflow < 1:
        parser.error("--runs-per-workflow must be >= 1")
    if args.run_timeout_sec < 0:
        parser.error("--run-timeout-sec must be >= 0")
    if args.max_attempts is not None and args.max_attempts < 1:
        parser.error("--max-attempts must be >= 1")

    single = Path(args.single_workflow)
    multi = Path(args.multi_workflow)
    if not single.exists():
        parser.error(f"--single-workflow not found: {single}")
    if not multi.exists():
        parser.error(f"--multi-workflow not found: {multi}")

    runner = SweepRunner(args)
    summary = runner.run()
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"[stage-compare] summary: {runner.summary_path}")
    print(f"[stage-compare] runs csv: {runner.aggregate_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
