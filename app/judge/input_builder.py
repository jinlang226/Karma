import json
from pathlib import Path

import yaml

from app.settings import RESOURCES_DIR, ROOT


class JudgeInputBuilder:
    def __init__(
        self,
        root=ROOT,
        include_outcome=False,
    ):
        self.root = Path(root)
        self.include_outcome = bool(include_outcome)

    def build(self, run_dir, rubric):
        run_root = Path(run_dir)
        if not run_root.is_absolute():
            run_root = (self.root / run_root).resolve()
        warnings = []

        meta = self._read_json(run_root / "meta.json") or {}
        external_metrics = self._read_json(run_root / "external_metrics.json") or {}
        agent_usage = self._read_json(run_root / "agent_usage.json")

        workflow_context = self._build_workflow_context(run_root, meta, warnings)
        service = str(meta.get("service") or "").strip()
        case = str(meta.get("case") or "").strip()
        if not service:
            service = str(workflow_context.get("service_hint") or "").strip()
        if not case:
            case = str(workflow_context.get("case_hint") or "").strip()
        problem_statement = self._load_problem_statement(
            service=service,
            case=case,
            test_file=meta.get("test_file"),
            warnings=warnings,
        )
        if not problem_statement:
            problem_statement = str(workflow_context.get("problem_statement") or "").strip()
            if problem_statement and workflow_context.get("workflow_enabled"):
                warnings[:] = [w for w in warnings if w != "test.yaml not found for case context"]

        if agent_usage is None:
            fallback_usage = external_metrics.get("agent_token_usage")
            if isinstance(fallback_usage, dict):
                agent_usage = fallback_usage
                warnings.append("agent_usage.json missing; used external_metrics.agent_token_usage fallback")
            else:
                warnings.append("agent usage metrics missing")
        agent_usage = self._normalize_agent_usage(agent_usage)

        agent_log_path = run_root / "agent.log"
        if not agent_log_path.exists():
            warnings.append("agent.log missing")
            agent_log_text = ""
            agent_log_line_count = 0
        else:
            agent_log_text = agent_log_path.read_text(encoding="utf-8", errors="replace")
            agent_log_line_count = len(agent_log_text.splitlines())

        solve_duration_sec = self._duration_seconds(meta.get("solve_started_at_ts"), meta.get("finished_at_ts"))
        setup_duration_sec = self._duration_seconds(meta.get("setup_started_at_ts"), meta.get("setup_finished_at_ts"))
        pause_total = int(meta.get("solve_pause_total_sec") or 0)
        efficiency_facts = self._build_efficiency_facts(
            external_metrics=external_metrics,
            agent_usage=agent_usage,
            solve_duration_sec=solve_duration_sec,
            verification_attempts=len(meta.get("verification_logs") or []),
        )

        run_rel = str(run_root.relative_to(self.root)) if run_root.is_relative_to(self.root) else str(run_root)
        meta_block = {
            "run_id": run_root.name,
            "service": service,
            "case": case,
            "test_file": meta.get("test_file"),
            "attempts": meta.get("attempts"),
            "max_attempts": meta.get("max_attempts"),
            "run_dir": run_rel,
        }
        if self.include_outcome:
            meta_block["status"] = meta.get("status")

        packet = {
            "schema_version": "judge_input.v1",
            "meta": meta_block,
            "rubric_context": {
                "rubric_id": rubric.get("rubric_id"),
                "rubric_version": rubric.get("rubric_version"),
                "question_ids": [item.get("id") for item in (rubric.get("questions") or [])],
            },
            "case_context": {
                "problem_statement": problem_statement,
            },
            "workflow_context": workflow_context,
            "objective_metrics": {
                "external_metrics": external_metrics,
                "derived_signals": {
                    "verification_attempts": len(meta.get("verification_logs") or []),
                    "solve_duration_sec": solve_duration_sec,
                    "setup_duration_sec": setup_duration_sec,
                    "solve_pause_total_sec": pause_total,
                    "agent_log_line_count": agent_log_line_count,
                },
            },
            "blocks": {
                "agent_log": {
                    "path": self._rel(agent_log_path),
                    "line_count": agent_log_line_count,
                    "text": agent_log_text,
                    "text_numbered": self._number_lines(agent_log_text),
                },
                "external_metrics": external_metrics,
                "agent_usage": agent_usage,
                "efficiency_facts": efficiency_facts,
                "workflow_state": workflow_context.get("workflow_state") or {},
                "workflow_stage_results": workflow_context.get("stage_results") or {},
                "workflow_submit_results": workflow_context.get("submit_results") or {},
                "workflow_final_sweep": workflow_context.get("final_sweep") or {},
                "workflow_efficiency_facts": workflow_context.get("efficiency_facts") or {},
            },
            "warnings": warnings,
        }

        return packet, {
            "service": service,
            "case": case,
            "status": meta.get("status"),
            "warnings": warnings,
        }

    def _read_json(self, path):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_yaml(self, path):
        try:
            return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _rel(self, path):
        p = Path(path)
        if p.is_relative_to(self.root):
            return str(p.relative_to(self.root))
        return str(p)

    def _duration_seconds(self, start, end):
        try:
            start_i = int(start)
            end_i = int(end)
        except Exception:
            return None
        if end_i < start_i:
            return None
        return end_i - start_i

    def _number_lines(self, text):
        lines = str(text or "").splitlines()
        if not lines:
            return ""
        return "\n".join(f"L{i:06d} {line}" for i, line in enumerate(lines, start=1))

    def _build_efficiency_facts(self, external_metrics, agent_usage, solve_duration_sec, verification_attempts):
        out = {
            "solve_duration_sec": solve_duration_sec,
            "verification_attempts": verification_attempts,
        }
        rw = (external_metrics or {}).get("read_write_ratio") or {}
        ttfm = (external_metrics or {}).get("time_to_first_mutation") or {}
        for key in (
            "total_commands",
            "read_count",
            "write_count",
            "exec_count",
            "retry_count",
            "read_write_ratio",
        ):
            if key in rw:
                out[key] = rw.get(key)
        for key in ("time_to_first_mutation_seconds", "time_to_success_seconds"):
            if key in ttfm:
                out[key] = ttfm.get(key)

        usage_totals = {}
        if isinstance(agent_usage, dict):
            usage_totals = agent_usage.get("totals") if isinstance(agent_usage.get("totals"), dict) else {}
        if not usage_totals and isinstance(agent_usage, dict):
            usage_totals = agent_usage

        token_map = {
            "total_tokens": usage_totals.get("total_tokens"),
            "input_tokens": usage_totals.get("input_tokens"),
            "cached_input_tokens": usage_totals.get("cached_input_tokens"),
            "output_tokens": usage_totals.get("output_tokens"),
            "reasoning_output_tokens": usage_totals.get("reasoning_output_tokens"),
        }
        for key, value in token_map.items():
            if value is not None:
                out[key] = value

        return {key: value for key, value in out.items() if value is not None}

    def _normalize_agent_usage(self, agent_usage):
        if not isinstance(agent_usage, dict):
            return agent_usage
        if isinstance(agent_usage.get("totals"), dict):
            return agent_usage

        token_keys = (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "total_tokens",
        )
        totals = {}
        for key in token_keys:
            if key in agent_usage:
                totals[key] = agent_usage.get(key)
        if not totals:
            return agent_usage
        out = dict(agent_usage)
        out["totals"] = totals
        return out

    def _resolve_test_yaml_path(self, service, case, test_file):
        if test_file:
            candidate = (self.root / str(test_file)).resolve()
            if candidate.exists():
                return candidate

        if service and case:
            candidate = RESOURCES_DIR / service / case / "test.yaml"
            if candidate.exists():
                return candidate
        return None

    def _load_problem_statement(self, service, case, test_file, warnings):
        test_yaml = self._resolve_test_yaml_path(service, case, test_file)
        if not test_yaml:
            warnings.append("test.yaml not found for case context")
            return ""

        payload = self._read_yaml(test_yaml)
        if not isinstance(payload, dict):
            warnings.append(f"unable to parse case test.yaml: {test_yaml}")
            return ""

        for key in ("detailedInstructions", "prompt", "description"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        warnings.append(f"no problem statement fields found in {test_yaml}")
        return ""

    def _build_workflow_context(self, run_root, meta, warnings):
        state = self._read_json(run_root / "workflow_state.json") or {}
        submit_rows = self._read_jsonl(run_root / "submit_results.log")
        stage_rows = self._read_jsonl(run_root / "workflow_stage_results.jsonl")
        final_sweep = self._read_json(run_root / "workflow_final_sweep.json") or {}

        is_workflow = bool(state) or bool(stage_rows) or bool(submit_rows) or bool(final_sweep)
        if not is_workflow:
            # Canonical shape: single run is represented as a 1-stage workflow.
            stage_id = str(meta.get("case") or "stage_1")
            single_status = str(meta.get("status") or "").strip() or "unknown"
            attempts = int(meta.get("attempts") or 0)
            stage_entry = {
                "stage_id": stage_id,
                "status": single_status,
                "attempts": attempts,
                "reason": "single_run",
                "run_dir": self._rel(run_root),
                "service": str(meta.get("service") or "").strip(),
                "case": str(meta.get("case") or "").strip(),
            }
            stage_map = {stage_id: stage_entry}
            return {
                "workflow_enabled": False,
                "workflow_id": run_root.name,
                "mode": "single",
                "stage_total": 1,
                "active_stage_id": stage_id,
                "active_stage_index": 1,
                "terminal": True,
                "terminal_reason": "single_run_complete",
                "solve_status": single_status,
                "service_hint": stage_entry["service"],
                "case_hint": stage_entry["case"],
                "problem_statement": "",
                "workflow_state": {},
                "stage_results": stage_map,
                "submit_results": {},
                "final_sweep": {},
                "efficiency_facts": {
                    "stage_total": 1,
                    "total_stage_attempts": max(1, attempts),
                    "total_retries": max(0, attempts - 1),
                },
            }

        stage_map = {}
        for row in stage_rows:
            if not isinstance(row, dict):
                continue
            stage_id = str(row.get("stage_id") or "").strip()
            if not stage_id:
                continue
            stage_map[stage_id] = {
                "stage_id": stage_id,
                "status": str(row.get("status") or "").strip(),
                "attempts": int(row.get("attempt") or row.get("attempts") or 0),
                "reason": str(row.get("reason") or "").strip(),
                "run_dir": str(row.get("run_dir") or "").strip(),
                "service": "",
                "case": "",
            }

        submit_map = {}
        for idx, row in enumerate(submit_rows, start=1):
            if not isinstance(row, dict):
                continue
            submit_map[f"event_{idx:03d}"] = row

        # Attach service/case per stage from per-stage run meta when available.
        service_hint = str(meta.get("service") or "").strip()
        case_hint = str(meta.get("case") or "").strip()
        for stage_id, entry in stage_map.items():
            run_dir = entry.get("run_dir")
            if not run_dir:
                continue
            stage_run_root = Path(run_dir)
            if not stage_run_root.is_absolute():
                stage_run_root = (self.root / stage_run_root).resolve()
            stage_meta = self._read_json(stage_run_root / "meta.json") or {}
            entry["service"] = str(stage_meta.get("service") or "").strip()
            entry["case"] = str(stage_meta.get("case") or "").strip()
            if not service_hint and entry["service"]:
                service_hint = entry["service"]
            if not case_hint and entry["case"]:
                case_hint = entry["case"]

        stage_total = int(state.get("stage_total") or 0)
        if stage_total <= 0:
            stage_total = len(stage_map) or 1

        # Build workflow-level efficiency facts from stage run metadata/metrics.
        total_attempts = 0
        total_retries = 0
        total_solve_duration = 0
        total_setup_duration = 0
        total_commands = 0
        total_tokens = 0
        with_stage_metrics = 0
        with_stage_usage = 0
        with_stage_commands = 0

        for entry in stage_map.values():
            attempts = int(entry.get("attempts") or 0)
            if attempts > 0:
                total_attempts += attempts
                total_retries += max(0, attempts - 1)
            run_dir = entry.get("run_dir")
            if not run_dir:
                continue
            stage_run_root = Path(run_dir)
            if not stage_run_root.is_absolute():
                stage_run_root = (self.root / stage_run_root).resolve()
            stage_meta = self._read_json(stage_run_root / "meta.json") or {}
            stage_metrics = self._read_json(stage_run_root / "external_metrics.json") or {}
            stage_usage = self._read_json(stage_run_root / "agent_usage.json") or {}
            stage_usage = self._normalize_agent_usage(stage_usage if isinstance(stage_usage, dict) else None)
            if not stage_usage and isinstance(stage_metrics, dict):
                fallback = stage_metrics.get("agent_token_usage")
                if isinstance(fallback, dict):
                    stage_usage = self._normalize_agent_usage(fallback)

            solve_sec = self._duration_seconds(stage_meta.get("solve_started_at_ts"), stage_meta.get("finished_at_ts"))
            setup_sec = self._duration_seconds(stage_meta.get("setup_started_at_ts"), stage_meta.get("setup_finished_at_ts"))
            if solve_sec is not None:
                total_solve_duration += int(solve_sec)
                with_stage_metrics += 1
            if setup_sec is not None:
                total_setup_duration += int(setup_sec)

            rw = stage_metrics.get("read_write_ratio") if isinstance(stage_metrics, dict) else {}
            if isinstance(rw, dict):
                cmds = rw.get("total_commands")
                try:
                    if cmds is not None:
                        total_commands += int(cmds)
                        with_stage_commands += 1
                except Exception:
                    pass

            totals = stage_usage.get("totals") if isinstance(stage_usage, dict) else {}
            if isinstance(totals, dict):
                tok = totals.get("total_tokens")
                try:
                    if tok is not None:
                        total_tokens += int(tok)
                        with_stage_usage += 1
                except Exception:
                    pass

        # Fall back to run-level attempts for safety.
        if total_attempts <= 0:
            total_attempts = int(meta.get("attempts") or 0)
            total_retries = max(0, total_attempts - 1)

        workflow_name = str(state.get("workflow_name") or run_root.name).strip() or run_root.name
        prompt_mode = str(state.get("prompt_mode") or "workflow").strip() or "workflow"

        problem_statement = ""
        prompt_path = run_root / "agent_bundle" / "PROMPT.md"
        if prompt_path.exists():
            try:
                text = prompt_path.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    problem_statement = text
            except Exception:
                pass
        if not problem_statement:
            warnings.append("workflow prompt not found for case context fallback")

        return {
            "workflow_enabled": True,
            "workflow_id": workflow_name,
            "mode": prompt_mode,
            "stage_total": stage_total,
            "active_stage_id": str(state.get("active_stage_id") or "").strip(),
            "active_stage_index": int(state.get("active_stage_index") or 0),
            "terminal": bool(state.get("terminal")),
            "terminal_reason": str(state.get("terminal_reason") or "").strip(),
            "solve_status": str(state.get("solve_status") or "").strip(),
            "service_hint": service_hint,
            "case_hint": case_hint,
            "problem_statement": problem_statement,
            "workflow_state": state,
            "stage_results": stage_map,
            "submit_results": submit_map,
            "final_sweep": final_sweep,
            "efficiency_facts": {
                "stage_total": stage_total,
                "total_stage_attempts": total_attempts,
                "total_retries": total_retries,
                "total_solve_duration_sec": total_solve_duration if with_stage_metrics else None,
                "total_setup_duration_sec": total_setup_duration if stage_map else None,
                "total_commands": total_commands if with_stage_commands else None,
                "total_tokens": total_tokens if with_stage_usage else None,
            },
        }

    def _read_jsonl(self, path):
        try:
            p = Path(path)
            if not p.exists():
                return []
            rows = []
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rows.append(json.loads(raw))
                except Exception:
                    continue
            return rows
        except Exception:
            return []
