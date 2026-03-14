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


def _build_fixed_stage_workflow(
    base_workflow: dict[str, Any],
    *,
    base_workflow_dir: Path,
    target_stage_count: int,
    attempt_index: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    if target_stage_count < 1:
        raise ValueError("target_stage_count must be >= 1")
    out = copy.deepcopy(base_workflow)
    metadata = out.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        out["metadata"] = metadata
    spec = out.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("workflow spec must be an object")
    base_stages = list(spec.get("stages") or [])
    if not base_stages:
        raise ValueError("workflow spec.stages must be non-empty")

    expanded: list[dict[str, Any]] = []
    stage_index_by_id: dict[str, int] = {}
    for index in range(target_stage_count):
        base_stage = base_stages[index % len(base_stages)]
        if not isinstance(base_stage, dict):
            raise ValueError("workflow stage must be an object")
        stage_copy = copy.deepcopy(base_stage)
        cycle = (index // len(base_stages)) + 1
        orig_id = str(stage_copy.get("id") or f"stage_{(index % len(base_stages)) + 1}").strip()
        stage_id = f"r{cycle:03d}_{orig_id}"
        stage_copy["id"] = stage_id
        # Generated workflow files are written under work_dir; normalize relative
        # case_path now so they still resolve to the original workflow location.
        case_path = stage_copy.get("case_path")
        if isinstance(case_path, str):
            case_path_text = case_path.strip()
            if case_path_text and not Path(case_path_text).is_absolute():
                stage_copy["case_path"] = str((base_workflow_dir / case_path_text).resolve())
        expanded.append(stage_copy)
        stage_index_by_id[stage_id] = index + 1
    spec["stages"] = expanded

    base_name = str(metadata.get("name") or "workflow").strip()
    metadata["name"] = _sanitize_name(f"{base_name}-fixed{target_stage_count}-attempt{attempt_index}")
    return out, stage_index_by_id


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
    parse_error: str | None
    log_path: str
    result_payload: dict[str, Any] | None
    classification: str
    retryable: bool
    hard_stop: bool
    failure_stage_index: int | None

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
            "parse_error": self.parse_error,
            "log_path": self.log_path,
            "result_payload": self.result_payload,
            "classification": self.classification,
            "retryable": self.retryable,
            "hard_stop": self.hard_stop,
            "failure_stage_index": self.failure_stage_index,
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
            parse_error=(str(payload.get("parse_error")) if payload.get("parse_error") is not None else None),
            log_path=str(payload.get("log_path") or ""),
            result_payload=(payload.get("result_payload") if isinstance(payload.get("result_payload"), dict) else None),
            classification=str(payload.get("classification") or ""),
            retryable=bool(payload.get("retryable")),
            hard_stop=bool(payload.get("hard_stop")),
            failure_stage_index=(
                int(payload.get("failure_stage_index")) if isinstance(payload.get("failure_stage_index"), int) else None
            ),
        )


class FixedStageReliabilityRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.base_workflow_path = Path(args.base_workflow).resolve()
        self.base_workflow = _load_yaml(self.base_workflow_path)
        self.work_dir = Path(args.work_dir).resolve()
        self.generated_dir = self.work_dir / "generated_workflows"
        self.log_dir = self.work_dir / "logs"
        self.summary_path = self.work_dir / "summary.json"
        self.history_path = self.work_dir / "history.jsonl"
        self.env_overrides = self._parse_env_overrides(args.env or [])
        self.history: list[RunOutcome] = []

        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if bool(self.args.resume):
            self._load_history_for_resume()
        else:
            self.history_path.unlink(missing_ok=True)
            self.summary_path.unlink(missing_ok=True)

    def _load_history_for_resume(self) -> None:
        if not self.history_path.exists() or not self.history_path.is_file():
            return
        loaded: list[RunOutcome] = []
        for raw_line in self.history_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception as exc:
                raise ValueError(f"invalid history JSON line in {self.history_path}: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"invalid history entry in {self.history_path}: expected object")
            outcome = RunOutcome.from_dict(data)
            classification, retryable, hard_stop = self._classify_outcome(
                passed=bool(outcome.passed),
                status=outcome.status,
                parse_error=outcome.parse_error,
                cleanup_status=outcome.cleanup_status,
                terminal_reason=outcome.terminal_reason,
                failed_stage_reason=outcome.failed_stage_reason,
                result_payload=outcome.result_payload,
                precondition_hard_stop=bool(self.args.precondition_hard_stop),
            )
            outcome.classification = classification
            outcome.retryable = retryable
            outcome.hard_stop = hard_stop
            loaded.append(outcome)
        loaded.sort(key=lambda item: item.attempt_index)
        self.history = loaded
        if loaded:
            print(
                f"[fixed-50] resume loaded attempts={len(loaded)} from {self.history_path}"
            )

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
        precondition_hard_stop: bool,
    ) -> tuple[str, bool, bool]:
        if parse_error:
            return "infra_abort", False, True
        if status in {"parse_error", "process_error", "timeout"}:
            return "infra_abort", False, True
        if cleanup_status and cleanup_status != "done":
            return "infra_abort", False, True
        if passed:
            return "passed", False, False

        terminal_base_status = None
        if isinstance(result_payload, dict):
            terminal_base_status = str(result_payload.get("terminal_base_status") or "").strip() or None

        if (
            failed_stage_reason == "stage_setup_failed"
            or terminal_reason == "next_stage_setup_failed"
            or terminal_base_status == "setup_failed"
        ):
            if precondition_hard_stop:
                return "precondition_failure", False, True
            return "precondition_failure", True, False

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
                return "agent_give_up", True, False
            return "agent_runtime_error", False, True

        if failed_stage_reason == "oracle_fail":
            return "oracle_fail", True, False

        if terminal_reason == "submit_timeout" or terminal_base_status == "submit_timeout":
            return "agent_give_up", True, False

        return "retryable_failure", True, False

    @staticmethod
    def _resolve_failure_stage_index(
        *,
        active_stage_index: int | None,
        failed_stage_id: str | None,
        stage_index_by_id: dict[str, int],
    ) -> int | None:
        if active_stage_index and active_stage_index > 0:
            return active_stage_index
        if failed_stage_id and failed_stage_id in stage_index_by_id:
            return stage_index_by_id[failed_stage_id]
        return None

    def _run_once(self, *, attempt_index: int) -> RunOutcome:
        workflow_payload, stage_index_by_id = _build_fixed_stage_workflow(
            self.base_workflow,
            base_workflow_dir=self.base_workflow_path.parent,
            target_stage_count=self.args.target_stage_count,
            attempt_index=attempt_index,
        )
        stage_count = len((workflow_payload.get("spec") or {}).get("stages") or [])
        workflow_file = self.generated_dir / f"workflow_fixed{stage_count}_attempt{attempt_index}.yaml"
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
        log_path = self.log_dir / f"run_{attempt_index:04d}.log"

        print(
            f"[fixed-50] attempt={attempt_index}/{self.args.max_reruns} "
            f"stages={stage_count} workflow={workflow_file}"
        )
        print(f"[fixed-50] command: {shlex.join(cmd)}")

        if self.args.dry_run:
            classification, retryable, hard_stop = "passed", False, False
            return RunOutcome(
                attempt_index=attempt_index,
                stage_count=stage_count,
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
                classification=classification,
                retryable=retryable,
                hard_stop=hard_stop,
                failure_stage_index=None,
            )

        env = os.environ.copy()
        env.update(self.env_overrides)
        timeout = self.args.run_timeout_sec if self.args.run_timeout_sec > 0 else None

        parse_error = None
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
        result_payload: dict[str, Any] | None = None

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
            parsed = _parse_trailing_json_array(output_text)
            if parsed:
                first = parsed[0] if parsed else {}
                result = first.get("result") if isinstance(first, dict) else None
                if isinstance(result, dict):
                    result_payload = result
            if isinstance(result_payload, dict):
                status = str(result_payload.get("status") or "workflow_fatal")
                cleanup_status = result_payload.get("cleanup_status")
                terminal_reason = result_payload.get("terminal_reason")
                passed = status == "passed"
                (
                    failed_stage_id,
                    failed_stage_status,
                    failed_stage_reason,
                    failed_stage_source,
                ) = _extract_failed_stage_info(result_payload)
                active_stage_index, active_stage_id = _extract_active_stage_info(result_payload)
            else:
                parse_error = "unable to parse workflow result JSON"
                status = "parse_error" if proc.returncode == 0 else "process_error"
        except subprocess.TimeoutExpired as exc:
            output_text = (exc.stdout or "") + (exc.stderr or "")
            log_path.write_text(output_text, encoding="utf-8")
            proc = subprocess.CompletedProcess(exc.cmd, returncode=124, stdout=exc.stdout, stderr=exc.stderr)
            parse_error = f"timeout after {self.args.run_timeout_sec}s"
            status = "timeout"

        classification, retryable, hard_stop = self._classify_outcome(
            passed=bool(passed),
            status=status,
            parse_error=parse_error,
            cleanup_status=cleanup_status,
            terminal_reason=terminal_reason,
            failed_stage_reason=failed_stage_reason,
            result_payload=result_payload,
            precondition_hard_stop=bool(self.args.precondition_hard_stop),
        )
        failure_stage_index = self._resolve_failure_stage_index(
            active_stage_index=active_stage_index,
            failed_stage_id=failed_stage_id,
            stage_index_by_id=stage_index_by_id,
        )

        return RunOutcome(
            attempt_index=attempt_index,
            stage_count=stage_count,
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
            result_payload=result_payload,
            classification=classification,
            retryable=retryable,
            hard_stop=hard_stop,
            failure_stage_index=failure_stage_index,
        )

    def _record_outcome(self, outcome: RunOutcome) -> None:
        self.history.append(outcome)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(outcome.to_dict(), sort_keys=True) + "\n")

    @staticmethod
    def _avg(values: list[int]) -> float | None:
        if not values:
            return None
        return float(sum(values)) / float(len(values))

    def run(self) -> dict[str, Any]:
        initial_history_count = len(self.history)
        failed_stage_indices: list[int] = []
        failed_stage_ids: list[str] = []
        missing_stage_index_count = 0
        stop_reason = "max_reruns_exhausted"
        status = "failed_after_max_reruns"
        matrix_pause_required = False
        pause_classification = None
        complete_50 = False

        for existing in self.history:
            if existing.passed:
                stop_reason = "complete_50"
                status = "complete_50"
                complete_50 = True
                break
            if existing.hard_stop:
                stop_reason = f"{existing.classification}_abort"
                status = "matrix_pause_required"
                matrix_pause_required = True
                pause_classification = existing.classification
                break
            if existing.failure_stage_index is None:
                missing_stage_index_count += 1
            else:
                failed_stage_indices.append(existing.failure_stage_index)
            if existing.failed_stage_id:
                failed_stage_ids.append(existing.failed_stage_id)

        start_attempt = len(self.history) + 1
        if bool(self.args.resume):
            if complete_50:
                print(
                    f"[fixed-50] resume no-op: already complete "
                    f"attempts_used={len(self.history)}"
                )
            elif matrix_pause_required:
                print(
                    f"[fixed-50] resume no-op: prior hard-stop "
                    f"class={pause_classification} attempts_used={len(self.history)}"
                )
            elif start_attempt > int(self.args.max_reruns):
                print(
                    f"[fixed-50] resume no-op: attempts already exhausted "
                    f"attempts_used={len(self.history)} max_reruns={self.args.max_reruns}"
                )
            elif self.history:
                print(
                    f"[fixed-50] resume start-attempt={start_attempt}/{self.args.max_reruns} "
                    f"existing_attempts={len(self.history)}"
                )

        for attempt_index in range(start_attempt, int(self.args.max_reruns) + 1):
            outcome = self._run_once(attempt_index=attempt_index)
            self._record_outcome(outcome)

            stage_idx = outcome.failure_stage_index if outcome.failure_stage_index else "?"
            stage_id = outcome.failed_stage_id or outcome.active_stage_id or "-"
            terminal = outcome.terminal_reason or "-"
            print(
                f"[fixed-50] outcome attempt={attempt_index} class={outcome.classification} "
                f"retryable={str(outcome.retryable).lower()} hard_stop={str(outcome.hard_stop).lower()} "
                f"stage={stage_idx}/{outcome.stage_count} stage_id={stage_id} terminal={terminal}"
            )

            if outcome.passed:
                stop_reason = "complete_50"
                status = "complete_50"
                complete_50 = True
                break

            if outcome.hard_stop:
                stop_reason = f"{outcome.classification}_abort"
                status = "matrix_pause_required"
                matrix_pause_required = True
                pause_classification = outcome.classification
                break

            if outcome.failure_stage_index is None:
                missing_stage_index_count += 1
            else:
                failed_stage_indices.append(outcome.failure_stage_index)
            if outcome.failed_stage_id:
                failed_stage_ids.append(outcome.failed_stage_id)

        avg_failed_stage_index = self._avg(failed_stage_indices)
        summary = {
            "base_workflow": str(self.base_workflow_path),
            "target_stage_count": int(self.args.target_stage_count),
            "max_reruns": int(self.args.max_reruns),
            "resume": bool(self.args.resume),
            "resume_loaded_attempts": initial_history_count,
            "precondition_hard_stop": bool(self.args.precondition_hard_stop),
            "run_timeout_sec": int(self.args.run_timeout_sec),
            "dry_run": bool(self.args.dry_run),
            "status": status,
            "complete_50": complete_50,
            "stop_reason": stop_reason,
            "matrix_pause_required": matrix_pause_required,
            "pause_classification": pause_classification,
            "attempts_used": len(self.history),
            "average_failed_stage_index": avg_failed_stage_index,
            "failed_stage_index_samples": failed_stage_indices,
            "failed_stage_id_samples": failed_stage_ids,
            "missing_stage_index_count": missing_stage_index_count,
            "history_path": str(self.history_path),
            "runs": [item.to_dict() for item in self.history],
        }
        self.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fixed-stage model reliability runner. "
            "Runs one fixed-size workflow per attempt, retries retryable failures up to max-reruns, "
            "and hard-stops on infra/agent-runtime errors (and optionally precondition failures)."
        )
    )
    parser.add_argument(
        "--base-workflow",
        default="workflows/rabbitmq-two-cycle-xy-rotation.yaml",
        help="Base workflow YAML to replicate.",
    )
    parser.add_argument(
        "--work-dir",
        default=".benchmark/agent-fixed-stage-reliability",
        help="Directory to write generated workflows and run history.",
    )
    parser.add_argument(
        "--target-stage-count",
        type=int,
        default=50,
        help="Fixed workflow stage count per attempt.",
    )
    parser.add_argument(
        "--max-reruns",
        type=int,
        default=5,
        help="Maximum attempts per model before declaring failed_after_max_reruns.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume from existing history.jsonl in --work-dir. "
            "Only remaining attempts up to --max-reruns will run."
        ),
    )
    parser.add_argument(
        "--precondition-hard-stop",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "If enabled, precondition/setup failures hard-stop the model run (matrix pause). "
            "Disable to treat precondition failures as retryable."
        ),
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

    if args.target_stage_count < 1:
        parser.error("--target-stage-count must be >= 1")
    if args.max_reruns < 1:
        parser.error("--max-reruns must be >= 1")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be >= 1")
    if args.run_timeout_sec < 0:
        parser.error("--run-timeout-sec must be >= 0")
    if "--sandbox" in args.orchestrator_arg:
        parser.error("Do not pass --sandbox via --orchestrator-arg; use --sandbox directly")

    runner = FixedStageReliabilityRunner(args)
    summary = runner.run()
    print(json.dumps(summary, indent=2, sort_keys=True))

    if summary.get("matrix_pause_required"):
        print(
            "[fixed-50] matrix pause required: "
            f"reason={summary.get('stop_reason')} "
            f"class={summary.get('pause_classification')} "
            f"attempts_used={summary.get('attempts_used')}"
        )
        return 2
    if summary.get("complete_50"):
        print(
            "[fixed-50] completed fixed-stage workflow: "
            f"stages={summary.get('target_stage_count')} attempts={summary.get('attempts_used')}"
        )
        return 0
    print(
        "[fixed-50] failed after max reruns: "
        f"attempts={summary.get('attempts_used')} "
        f"avg_failed_stage={summary.get('average_failed_stage_index')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
