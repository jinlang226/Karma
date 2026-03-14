#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _sanitize_name(value: str) -> str:
    text = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")
    if not text:
        text = "workflow"
    if len(text) <= 63:
        return text
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{text[:54]}-{digest}"


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"workflow yaml must be an object: {path}")
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        raise ValueError(f"workflow spec must be an object: {path}")
    stages = spec.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError(f"workflow spec.stages must be a non-empty list: {path}")
    return payload


def _scaled_workflow(base_workflow: dict[str, Any], factor: int, run_idx: int) -> dict[str, Any]:
    if factor < 1:
        raise ValueError("factor must be >= 1")
    out = copy.deepcopy(base_workflow)
    metadata = out.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        out["metadata"] = metadata
    spec = out.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("workflow spec must be an object")
    base_stages = list(spec.get("stages") or [])
    expanded: list[dict[str, Any]] = []
    for rep in range(1, factor + 1):
        for idx, stage in enumerate(base_stages, start=1):
            if not isinstance(stage, dict):
                raise ValueError("workflow stage must be an object")
            stage_copy = copy.deepcopy(stage)
            orig_id = str(stage_copy.get("id") or f"stage_{idx}").strip()
            stage_copy["id"] = f"r{rep:03d}_{orig_id}"
            expanded.append(stage_copy)
    spec["stages"] = expanded

    base_name = str(metadata.get("name") or "workflow").strip()
    metadata["name"] = _sanitize_name(f"{base_name}-x{factor}-run{run_idx}")
    return out


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(payload, sort_keys=False)
    path.write_text(text, encoding="utf-8")


def _parse_trailing_json_array(text: str) -> list[dict[str, Any]] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidate_positions = [idx for idx, ch in enumerate(raw) if ch == "["]
    for start in reversed(candidate_positions):
        snippet = raw[start:].strip()
        try:
            payload = json.loads(snippet)
        except Exception:
            continue
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict) and "result" in first:
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


def _extract_failed_stage_info(result_payload: dict[str, Any] | None) -> tuple[str | None, str | None, str | None, str | None]:
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


@dataclass
class RunOutcome:
    run_index: int
    factor: int
    stage_count: int
    attempt_kind: str
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
    parse_error: str | None
    log_path: str
    result_payload: dict[str, Any] | None
    classification: str = "countable_failure"
    counted_for_limit: bool = True
    agent_exit_rerun_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_index": self.run_index,
            "factor": self.factor,
            "stage_count": self.stage_count,
            "attempt_kind": self.attempt_kind,
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
            "parse_error": self.parse_error,
            "log_path": self.log_path,
            "result_payload": self.result_payload,
            "classification": self.classification,
            "counted_for_limit": self.counted_for_limit,
            "agent_exit_rerun_index": self.agent_exit_rerun_index,
        }


class AgentLimitSearch:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.base_workflow_path = Path(args.base_workflow).resolve()
        self.base_workflow = _load_yaml(self.base_workflow_path)
        self.base_stage_count = len((self.base_workflow.get("spec") or {}).get("stages") or [])
        self.work_dir = Path(args.work_dir).resolve()
        self.generated_dir = self.work_dir / "generated_workflows"
        self.log_dir = self.work_dir / "logs"
        self.summary_path = self.work_dir / "summary.json"
        self.history_path = self.work_dir / "history.jsonl"
        self.env_overrides = self._parse_env_overrides(args.env or [])
        self.run_index = 0
        self.history: list[RunOutcome] = []

        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.history_path.unlink(missing_ok=True)
        self.summary_path.unlink(missing_ok=True)

    @staticmethod
    def _parse_env_overrides(entries: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for item in entries:
            text = str(item or "").strip()
            if not text:
                continue
            if "=" not in text:
                raise ValueError(f"--env must be KEY=VALUE, got: {item}")
            key, value = text.split("=", 1)
            key = key.strip()
            if not key:
                raise ValueError(f"--env must include key before '=': {item}")
            out[key] = value
        return out

    @staticmethod
    def _classify_outcome(
        *,
        passed: bool,
        status: str,
        parse_error: str | None,
        cleanup_status: str | None,
        terminal_reason: str | None,
        failed_stage_reason: str | None,
        result_payload: dict[str, Any] | None,
    ) -> str:
        if parse_error:
            return "infra_abort"
        if status in {"parse_error", "process_error", "timeout"}:
            return "infra_abort"
        if cleanup_status and cleanup_status != "done":
            return "infra_abort"
        if passed:
            return "passed"
        terminal_base_status = None
        if isinstance(result_payload, dict):
            terminal_base_status = str(result_payload.get("terminal_base_status") or "").strip() or None
        if (
            failed_stage_reason == "stage_setup_failed"
            or terminal_reason == "next_stage_setup_failed"
            or terminal_base_status == "setup_failed"
        ):
            return "precondition_failure"
        if terminal_reason == "agent_exited":
            agent_exit_code = None
            if isinstance(result_payload, dict):
                raw = result_payload.get("agent_exit_code")
                if isinstance(raw, int):
                    agent_exit_code = raw
                elif raw not in (None, ""):
                    try:
                        agent_exit_code = int(str(raw).strip())
                    except (TypeError, ValueError):
                        agent_exit_code = None
            if agent_exit_code == 0:
                return "agent_give_up"
            return "agent_runtime_error"
        return "countable_failure"

    def _run_once(self, factor: int, attempt_kind: str) -> RunOutcome:
        self.run_index += 1
        stage_count = self.base_stage_count * factor
        workflow_payload = _scaled_workflow(self.base_workflow, factor=factor, run_idx=self.run_index)
        workflow_file = self.generated_dir / f"workflow_x{factor}_run{self.run_index}_{attempt_kind}.yaml"
        _write_yaml(workflow_file, workflow_payload)

        cmd = [
            self.args.python_bin,
            self.args.orchestrator_bin,
            "workflow-run",
            "--workflow",
            str(workflow_file),
            "--sandbox",
            self.args.sandbox,
            "--stage-failure-mode",
            self.args.stage_failure_mode,
            "--max-attempts",
            str(self.args.max_attempts),
            "--final-sweep-mode",
            self.args.final_sweep_mode,
        ]
        cmd.extend(self.args.orchestrator_arg or [])
        log_path = self.log_dir / f"run_{self.run_index:04d}_x{factor}_{attempt_kind}.log"

        print(
            f"[agent-limit] run={self.run_index} factor={factor} stages={stage_count} "
            f"attempt={attempt_kind} workflow={workflow_file}"
        )
        print(f"[agent-limit] command: {shlex.join(cmd)}")

        if self.args.dry_run:
            outcome = RunOutcome(
                run_index=self.run_index,
                factor=factor,
                stage_count=stage_count,
                attempt_kind=attempt_kind,
                workflow_path=str(workflow_file),
                command=list(cmd),
                returncode=0,
                status="dry_run",
                passed=True,
                cleanup_status=None,
                terminal_reason=None,
                failed_stage_id=None,
                failed_stage_status=None,
                failed_stage_reason=None,
                failed_stage_source=None,
                active_stage_index=None,
                active_stage_id=None,
                parse_error=None,
                log_path=str(log_path),
                result_payload=None,
                classification="passed",
            )
            return outcome

        env = os.environ.copy()
        env.update(self.env_overrides)
        timeout = self.args.run_timeout_sec if self.args.run_timeout_sec > 0 else None
        parse_error = None
        payload = None
        result = None
        status = "workflow_fatal"
        passed = False
        cleanup_status = None
        terminal_reason = None
        failed_stage_id = None
        failed_stage_status = None
        failed_stage_reason = None
        failed_stage_source = None
        active_stage_index = None
        active_stage_id = None

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(Path.cwd()),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output_text = (proc.stdout or "") + (proc.stderr or "")
            log_path.write_text(output_text, encoding="utf-8")
            payload = _parse_trailing_json_array(output_text)
            if payload:
                first = payload[0] if payload else {}
                result = first.get("result") if isinstance(first, dict) else None
            if isinstance(result, dict):
                status = str(result.get("status") or "workflow_fatal")
                cleanup_status = result.get("cleanup_status")
                terminal_reason = result.get("terminal_reason")
                passed = status == "passed"
                (
                    failed_stage_id,
                    failed_stage_status,
                    failed_stage_reason,
                    failed_stage_source,
                ) = _extract_failed_stage_info(result)
                active_stage_index, active_stage_id = _extract_active_stage_info(result)
            else:
                parse_error = "unable to parse workflow result JSON"
                if proc.returncode == 0:
                    status = "parse_error"
                else:
                    status = "process_error"
        except subprocess.TimeoutExpired as exc:
            output_text = (exc.stdout or "") + (exc.stderr or "")
            log_path.write_text(output_text, encoding="utf-8")
            proc = subprocess.CompletedProcess(exc.cmd, returncode=124, stdout=exc.stdout, stderr=exc.stderr)
            parse_error = f"timeout after {self.args.run_timeout_sec}s"
            status = "timeout"

        outcome = RunOutcome(
            run_index=self.run_index,
            factor=factor,
            stage_count=stage_count,
            attempt_kind=attempt_kind,
            workflow_path=str(workflow_file),
            command=list(cmd),
            returncode=int(proc.returncode),
            status=status,
            passed=bool(passed),
            cleanup_status=cleanup_status,
            terminal_reason=terminal_reason,
            failed_stage_id=failed_stage_id,
            failed_stage_status=failed_stage_status,
            failed_stage_reason=failed_stage_reason,
            failed_stage_source=failed_stage_source,
            active_stage_index=active_stage_index,
            active_stage_id=active_stage_id,
            parse_error=parse_error,
            log_path=str(log_path),
            result_payload=result if isinstance(result, dict) else None,
            classification=self._classify_outcome(
                passed=bool(passed),
                status=status,
                parse_error=parse_error,
                cleanup_status=cleanup_status,
                terminal_reason=terminal_reason,
                failed_stage_reason=failed_stage_reason,
                result_payload=result if isinstance(result, dict) else None,
            ),
        )
        return outcome

    @staticmethod
    def _print_outcome_summary(outcome: RunOutcome) -> None:
        stage_index = outcome.active_stage_index if outcome.active_stage_index else "?"
        stage_total = outcome.stage_count if outcome.stage_count else "?"
        stage_id = outcome.failed_stage_id or outcome.active_stage_id or "-"
        terminal = outcome.terminal_reason or "-"
        print(
            f"[agent-limit] outcome run={outcome.run_index} factor={outcome.factor} "
            f"class={outcome.classification} counted={str(outcome.counted_for_limit).lower()} "
            f"stage={stage_index}/{stage_total} stage_id={stage_id} terminal={terminal}"
        )

    def _run_until_countable(self, *, factor: int, attempt_kind: str) -> tuple[RunOutcome | None, str | None]:
        first = self._run_once(factor=factor, attempt_kind=attempt_kind)
        first.agent_exit_rerun_index = 1
        if first.classification != "agent_runtime_error":
            first.counted_for_limit = True
            self._record_outcome(first)
            self._print_outcome_summary(first)
            return first, None

        first.counted_for_limit = False
        self._record_outcome(first)
        self._print_outcome_summary(first)
        second = self._run_once(factor=factor, attempt_kind=attempt_kind)
        second.agent_exit_rerun_index = 2
        if second.classification == "agent_runtime_error":
            second.counted_for_limit = False
            self._record_outcome(second)
            self._print_outcome_summary(second)
            return None, "agent_exited_twice_abort"
        second.counted_for_limit = True
        self._record_outcome(second)
        self._print_outcome_summary(second)
        return second, None

    def _record_outcome(self, outcome: RunOutcome) -> None:
        self.history.append(outcome)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(outcome.to_dict(), sort_keys=True) + "\n")

    def run_search(self) -> dict[str, Any]:
        factor = max(1, int(self.args.initial_factor))
        last_stable_factor = 0
        stop_reason = "max_limits_reached"
        limit_factor = None

        for _step in range(int(self.args.max_search_steps)):
            if factor > int(self.args.max_factor):
                stop_reason = "max_factor_reached"
                break
            stage_count = self.base_stage_count * factor
            if self.args.max_stage_count > 0 and stage_count > self.args.max_stage_count:
                stop_reason = "max_stage_count_reached"
                break

            first, stop = self._run_until_countable(factor=factor, attempt_kind="primary")
            if stop:
                stop_reason = stop
                limit_factor = factor
                break
            if first is None:
                stop_reason = "countable_run_missing"
                break
            if first.classification == "infra_abort":
                stop_reason = "infra_abort"
                break
            if first.classification == "precondition_failure":
                stop_reason = "precondition_failed_abort"
                break
            if first.passed:
                last_stable_factor = factor
                factor *= 2
                continue

            retry, stop = self._run_until_countable(factor=factor, attempt_kind="retry")
            if stop:
                stop_reason = stop
                limit_factor = factor
                break
            if retry is None:
                stop_reason = "countable_run_missing"
                break
            if retry.classification == "infra_abort":
                stop_reason = "infra_abort"
                break
            if retry.classification == "precondition_failure":
                stop_reason = "precondition_failed_abort"
                break
            if not retry.passed:
                stop_reason = "failed_twice_same_factor"
                limit_factor = factor
                break

            confirm, stop = self._run_until_countable(factor=factor, attempt_kind="confirm")
            if stop:
                stop_reason = stop
                limit_factor = factor
                break
            if confirm is None:
                stop_reason = "countable_run_missing"
                break
            if confirm.classification == "infra_abort":
                stop_reason = "infra_abort"
                break
            if confirm.classification == "precondition_failure":
                stop_reason = "precondition_failed_abort"
                break
            if confirm.passed:
                last_stable_factor = factor
                factor *= 2
                continue

            stop_reason = "retry_passed_but_confirm_failed"
            limit_factor = factor
            break

        limit_valid = bool(limit_factor) and stop_reason in {
            "failed_twice_same_factor",
            "retry_passed_but_confirm_failed",
        }

        summary = {
            "base_workflow": str(self.base_workflow_path),
            "base_stage_count": self.base_stage_count,
            "initial_factor": self.args.initial_factor,
            "max_factor": self.args.max_factor,
            "max_stage_count": self.args.max_stage_count,
            "max_search_steps": self.args.max_search_steps,
            "run_timeout_sec": self.args.run_timeout_sec,
            "dry_run": bool(self.args.dry_run),
            "last_stable_factor": last_stable_factor,
            "last_stable_stage_count": self.base_stage_count * last_stable_factor if last_stable_factor else 0,
            "limit_factor": limit_factor if limit_valid else None,
            "limit_stage_count": self.base_stage_count * limit_factor if (limit_valid and limit_factor) else None,
            "limit_valid": limit_valid,
            "stop_reason": stop_reason,
            "history_path": str(self.history_path),
            "counted_run_count": sum(1 for item in self.history if item.counted_for_limit),
            "uncounted_run_count": sum(1 for item in self.history if not item.counted_for_limit),
            "runs": [item.to_dict() for item in self.history],
        }
        self.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Temporary agent-limit search driver. "
            "Runs a workflow, doubles stage count on pass, retries once on countable fail, "
            "aborts immediately on precondition/setup failures, and aborts if agent exits twice consecutively."
        )
    )
    parser.add_argument(
        "--base-workflow",
        default="workflows/rabbitmq-two-cycle-xy-rotation.yaml",
        help="Base workflow YAML to replicate.",
    )
    parser.add_argument(
        "--work-dir",
        default=".benchmark/agent-limit-search",
        help="Directory to write generated workflows and run history.",
    )
    parser.add_argument("--initial-factor", type=int, default=1, help="Initial stage replication factor.")
    parser.add_argument("--max-factor", type=int, default=1024, help="Upper bound factor guardrail.")
    parser.add_argument(
        "--max-stage-count",
        type=int,
        default=0,
        help="Optional max stage count guardrail (0 disables this guardrail).",
    )
    parser.add_argument(
        "--max-search-steps",
        type=int,
        default=32,
        help="Maximum doubling iterations before stop.",
    )
    parser.add_argument(
        "--run-timeout-sec",
        type=int,
        default=0,
        help="Per workflow run timeout in seconds (0 disables timeout).",
    )
    parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Python interpreter used to invoke orchestrator.",
    )
    parser.add_argument(
        "--orchestrator-bin",
        default="orchestrator.py",
        help="Orchestrator entrypoint path.",
    )
    parser.add_argument(
        "--sandbox",
        default="docker",
        choices=["local", "docker"],
        help="Sandbox mode used by workflow-run (default: docker).",
    )
    parser.add_argument(
        "--stage-failure-mode",
        default="terminate",
        choices=["continue", "terminate"],
        help="Workflow stage failure mode override.",
    )
    parser.add_argument(
        "--final-sweep-mode",
        default="off",
        choices=["inherit", "full", "off"],
        help="Workflow final sweep mode override for speed.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=1,
        help="Per-stage max attempts override used in workflow-run.",
    )
    parser.add_argument(
        "--orchestrator-arg",
        action="append",
        default=[],
        help="Extra arg forwarded to orchestrator workflow-run (repeatable).",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="Environment override in KEY=VALUE format (repeatable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate workflows and print commands without executing orchestrator.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.initial_factor < 1:
        parser.error("--initial-factor must be >= 1")
    if args.max_factor < 1:
        parser.error("--max-factor must be >= 1")
    if args.max_search_steps < 1:
        parser.error("--max-search-steps must be >= 1")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be >= 1")
    if args.run_timeout_sec < 0:
        parser.error("--run-timeout-sec must be >= 0")
    if "--sandbox" in args.orchestrator_arg:
        parser.error("Do not pass --sandbox via --orchestrator-arg; use --sandbox directly")

    runner = AgentLimitSearch(args)
    summary = runner.run_search()

    print(json.dumps(summary, indent=2, sort_keys=True))
    limit = summary.get("limit_factor")
    if limit:
        print(
            f"[agent-limit] limit discovered at factor={limit} "
            f"(stages={summary.get('limit_stage_count')}) stop={summary.get('stop_reason')}"
        )
        return 0
    print(
        f"[agent-limit] search completed without hard limit. "
        f"last_stable_factor={summary.get('last_stable_factor')} stop={summary.get('stop_reason')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
