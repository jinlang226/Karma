import json
import threading
from copy import deepcopy
from urllib import error as urlerror
from urllib import request as urlrequest
from pathlib import Path
from subprocess import PIPE, run

from .decoys import build_decoy_commands, list_decoy_files, load_decoys, write_decoys_file
from .oracle import resolve_oracle_verify
from .settings import (
    MAX_ATTEMPTS,
    MAX_TIME_MINUTES,
    PROXY_CONTROL_TIMEOUT,
    PROXY_CONTROL_URL,
    RESOURCES_DIR,
    ROOT,
    RUNS_DIR,
)
from .test_schema import raise_for_legacy_test_yaml_keys
from .util import (
    decode_case_id,
    encode_case_id,
    is_valid_name,
    normalize_commands,
    normalize_metrics,
    read_yaml,
    sanitize_name,
    ts_epoch,
    ts_str,
    utc_now,
)
from .orchestrator_cli import (
    build_orchestrator_preview,
    get_orchestrator_cli_options,
)
from .runner_core.helpers import (
    default_timeout_sec_for_command,
    label_hash,
    resolve_step_timeout_sec,
    sanitize_label_value,
    split_command_tokens,
)
from .runner_core import command_runtime as command_runtime_core
from .runner_core import judge_jobs as judge_jobs_core
from .runner_core import manual_workflow_bridge as manual_workflow_bridge_core
from .runner_core import post_run as post_run_core
from .runner_core import run_flow as run_flow_core
from .runner_core import workflow_jobs as workflow_jobs_core


def _normalize_case_param_definitions(data):
    if not isinstance(data, dict):
        return {}
    params_block = data.get("params")
    if not isinstance(params_block, dict):
        return {}
    raw_defs = params_block.get("definitions")
    if not isinstance(raw_defs, dict):
        return {}
    out = {}
    for raw_name, raw_spec in raw_defs.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        if isinstance(raw_spec, dict):
            spec = deepcopy(raw_spec)
        else:
            spec = {"type": "string", "default": raw_spec}
        param_type = str(spec.get("type") or "string").strip().lower() or "string"
        row = {"type": param_type}
        if "default" in spec:
            row["default"] = spec.get("default")
        if bool(spec.get("required")):
            row["required"] = True
        if isinstance(spec.get("values"), list):
            row["values"] = deepcopy(spec.get("values"))
        if spec.get("min") is not None:
            row["min"] = spec.get("min")
        if spec.get("max") is not None:
            row["max"] = spec.get("max")
        if spec.get("pattern") is not None:
            row["pattern"] = str(spec.get("pattern"))
        if spec.get("description") is not None:
            row["description"] = str(spec.get("description"))
        out[name] = row
    return out


class BenchmarkApp:
    def __init__(self):
        self.runs_dir = RUNS_DIR
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.case_path_overrides = {}
        self.run_lock = threading.Lock()
        self.judge_lock = threading.Lock()
        self.judge_jobs = {}
        self.judge_job_order = []
        self.judge_event_seq = 0
        self.judge_event_limit = 500
        self.judge_event_history = []
        self.judge_event_cond = threading.Condition(self.judge_lock)
        self.workflow_lock = threading.Lock()
        self.workflow_jobs = {}
        self.workflow_job_order = []
        self.workflow_event_seq = 0
        self.workflow_event_limit = 800
        self.workflow_event_history = []
        self.workflow_event_cond = threading.Condition(self.workflow_lock)
        self.manual_workflow_session = manual_workflow_bridge_core.empty_manual_workflow_session()
        self._next_run_dir_override = None
        self.run_state = self._empty_run_state()
        self.cluster_ok, self.cluster_error = self._check_cluster()

    def _empty_run_state(self):
        return {
            "status": "idle",
            "case_id": None,
            "service": None,
            "case": None,
            "test_file": None,
            "run_dir": None,
            "max_attempts": None,
            "setup_log": None,
            "cleanup_log": None,
            "cleanup_status": None,
            "cleanup_started_at": None,
            "cleanup_finished_at": None,
            "verification_logs": [],
            "attempts": 0,
            "solve_started_at": None,
            "solve_pause_total_sec": 0,
            "solve_pause_started_at_ts": None,
            "solve_paused": False,
            "setup_started_at": None,
            "setup_finished_at": None,
            "finished_at": None,
            "cleanup_started_at_ts": None,
            "cleanup_finished_at_ts": None,
            "solve_started_at_ts": None,
            "setup_started_at_ts": None,
            "setup_finished_at_ts": None,
            "finished_at_ts": None,
            "last_error": None,
            "current_step": None,
            "metrics_path": None,
            "snapshot_pre": None,
            "snapshot_post": None,
            "snapshot_base": None,
            "snapshot_post_cleanup": None,
            "action_trace_log": None,
            "proxy_error": None,
            "verification_warnings": [],
            "setup_timeout_auto_sec": None,
            "setup_timeout_auto_breakdown": None,
            "setup_phase": None,
            "setup_warnings": [],
            "setup_checks_path": None,
            "defer_cleanup": False,
            "skip_precondition_unit_ids": [],
            "last_verification_kind": None,
            "last_verification_step": None,
            "resolved_params": {},
            "namespace_context": None,
            "namespace_lifecycle_owner": "orchestrator",
        }

    def _check_cluster(self):
        try:
            result = run(
                ["kubectl", "get", "nodes"],
                cwd=ROOT,
                stdout=PIPE,
                stderr=PIPE,
                text=True,
                check=True,
            )
            return True, result.stdout.strip()
        except Exception as exc:
            return False, str(exc)

    def list_services(self):
        services = []
        if not RESOURCES_DIR.exists():
            return services
        for entry in sorted(RESOURCES_DIR.iterdir()):
            if not entry.is_dir():
                continue
            if not is_valid_name(entry.name):
                continue
            cases = self.list_cases(entry.name)
            services.append({
                "name": entry.name,
                "label": entry.name,
                "count": len(cases),
            })
        return services

    def list_cases(self, service_name):
        if not is_valid_name(service_name):
            return []
        root = RESOURCES_DIR / service_name
        cases = []
        if not root.exists():
            return cases
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            case_name = entry.name
            if not is_valid_name(case_name):
                continue
            test_file = "test.yaml"
            resource_dir = entry / "resource"
            path = entry / test_file
            if resource_dir.is_dir() and path.is_file():
                cases.append(self._case_summary(service_name, case_name, test_file, path))
        return cases

    def _case_summary(self, service, case_name, test_file, path):
        return {
            "id": encode_case_id(service, case_name, test_file),
            "service": service,
            "case": case_name,
            "test_file": test_file,
            "display_name": case_name,
            "path": str(path.relative_to(ROOT)),
        }

    def get_case(self, case_id):
        try:
            service, case_name, test_file = decode_case_id(case_id)
        except Exception as exc:
            return {"error": f"Invalid case id: {exc}"}

        if not is_valid_name(service) or not is_valid_name(case_name):
            return {"error": "Invalid service or case name"}
        if "/" in case_name or "/" in test_file or ".." in case_name or ".." in test_file:
            return {"error": "Invalid case path"}
        if test_file != "test.yaml":
            return {"error": "Test file not allowed"}

        path = self._resolve_case_path(service, case_name, test_file)
        if not path or not path.exists():
            return {"error": "Case file not found"}
        resource_dir = path.parent / "resource"
        if not resource_dir.is_dir():
            return {"error": "Case missing resource directory"}

        data = read_yaml(path) or {}
        if data and data.get("_error"):
            return {"error": data.get("_error")}

        details = {
            "id": case_id,
            "service": service,
            "case": case_name,
            "test_file": test_file,
            "path": str(path.relative_to(ROOT)),
            "type": data.get("type"),
            "targetApp": data.get("targetApp"),
            "numAppInstance": data.get("numAppInstance"),
            "clusterType": data.get("clusterType"),
            "clusterProvider": data.get("clusterProvider"),
            "detailedInstructions": data.get("detailedInstructions", ""),
            "operatorContext": data.get("operatorContext", ""),
            "verification": data.get("verification", ""),
            "hasVerification": bool(resolve_oracle_verify(data).get("commands")),
            "hasCleanup": bool(data.get("cleanUpCommands")),
            "externalMetrics": data.get("externalMetrics", []),
            "params": {"definitions": _normalize_case_param_definitions(data)},
        }
        return details

    def _resolve_case_path(self, service, case_name, test_file):
        override_key = (service, case_name, test_file)
        override_path = self.case_path_overrides.get(override_key)
        if override_path:
            return Path(override_path)
        return RESOURCES_DIR / service / case_name / test_file

    def set_case_path_override(self, service, case_name, test_file, path):
        if not is_valid_name(service) or not is_valid_name(case_name):
            raise ValueError("Invalid service/case for override")
        if test_file != "test.yaml":
            raise ValueError("Only test.yaml overrides are supported")
        resolved = Path(path).resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"Override case path not found: {resolved}")
        self.case_path_overrides[(service, case_name, test_file)] = str(resolved)

    def clear_case_path_overrides(self):
        self.case_path_overrides = {}

    def start_run(
        self,
        case_id,
        max_attempts_override=None,
        defer_cleanup=False,
        skip_precondition_unit_ids=None,
        case_data_override=None,
        resolved_params=None,
        namespace_context=None,
        namespace_lifecycle_owner=None,
    ):
        with self.run_lock:
            run_dir_override = self._next_run_dir_override
            self._next_run_dir_override = None
            if self.run_state["status"] in ("setup_running", "ready", "verifying"):
                return {"error": "A run is already active"}
            if self.run_state.get("cleanup_status") in ("running", "failed"):
                return {"error": "Cleanup not finished"}
            if self._manual_workflow_start_enabled(
                defer_cleanup=defer_cleanup,
                skip_precondition_unit_ids=skip_precondition_unit_ids,
                case_data_override=case_data_override,
                resolved_params=resolved_params,
                namespace_context=namespace_context,
            ):
                return self._start_manual_workflow_run(
                    case_id,
                    max_attempts_override=max_attempts_override,
                )

            case = self.get_case(case_id)
            if case.get("error"):
                return case

            service, case_name, test_file = decode_case_id(case_id)
            path = self._resolve_case_path(service, case_name, test_file)
            if case_data_override is None:
                data = read_yaml(path) or {}
                if data.get("_error"):
                    return {"error": data.get("_error")}
            else:
                if not isinstance(case_data_override, dict):
                    return {"error": "case_data_override must be an object"}
                data = deepcopy(case_data_override)
            try:
                if case_data_override is None:
                    try:
                        schema_context = str(path.relative_to(ROOT))
                    except Exception:
                        schema_context = str(path)
                else:
                    schema_context = "case_data_override"
                raise_for_legacy_test_yaml_keys(
                    data,
                    context=schema_context,
                )
            except ValueError as exc:
                return {"error": str(exc)}

            max_attempts = self._resolve_max_attempts(data, max_attempts_override)
            started_at = utc_now()
            run_id = (
                f"{ts_str(started_at)}_{sanitize_name(service)}_"
                f"{sanitize_name(case_name)}_{sanitize_name(Path(test_file).stem)}"
            )
            if run_dir_override:
                try:
                    run_dir = Path(str(run_dir_override).strip())
                    if not run_dir.is_absolute():
                        run_dir = (ROOT / run_dir).resolve()
                    else:
                        run_dir = run_dir.resolve()
                    run_dir.relative_to(ROOT)
                except Exception:
                    return {"error": "run_dir_override must resolve inside repository root"}
            else:
                run_dir = self.runs_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            self.run_state = {
                "status": "setup_running",
                "case_id": case_id,
                "service": service,
                "case": case_name,
                "test_file": test_file,
                "run_dir": str(run_dir.relative_to(ROOT)),
                "max_attempts": max_attempts,
                "setup_log": str((run_dir / "preoperation.log").relative_to(ROOT)),
                "verification_logs": [],
                "attempts": 0,
                "solve_started_at": None,
                "solve_pause_total_sec": 0,
                "solve_pause_started_at_ts": None,
                "solve_paused": False,
                "setup_started_at": ts_str(started_at),
                "setup_started_at_ts": ts_epoch(started_at),
                "setup_finished_at": None,
                "setup_finished_at_ts": None,
                "finished_at": None,
                "finished_at_ts": None,
                "solve_started_at_ts": None,
                "last_error": None,
                "current_step": None,
                "metrics_path": None,
                "data": data,
                "external_metrics": normalize_metrics(data.get("externalMetrics")),
                "action_trace_log": None,
                "proxy_error": None,
                "verification_warnings": [],
                "setup_timeout_auto_sec": None,
                "setup_timeout_auto_breakdown": None,
                "setup_phase": "precondition_apply",
                "setup_warnings": [],
                "setup_checks_path": None,
                "defer_cleanup": bool(defer_cleanup),
                "skip_precondition_unit_ids": sorted(
                    {
                        str(item).strip()
                        for item in (skip_precondition_unit_ids or [])
                        if str(item).strip()
                    }
                ),
                "last_verification_kind": None,
                "last_verification_step": None,
                "resolved_params": deepcopy(resolved_params) if isinstance(resolved_params, dict) else {},
                "namespace_context": deepcopy(namespace_context) if isinstance(namespace_context, dict) else None,
                # Namespace lifecycle is orchestrator-owned for all supported paths.
                "namespace_lifecycle_owner": "orchestrator",
            }
            auto_timeout, breakdown = self._compute_setup_timeout_auto(data)
            self.run_state["setup_timeout_auto_sec"] = auto_timeout
            self.run_state["setup_timeout_auto_breakdown"] = breakdown
            self._set_action_trace(run_dir)
            self._start_proxy_trace(run_id, run_dir)
            self._write_meta()

            thread = threading.Thread(target=self._run_setup, daemon=True)
            thread.start()

        return {"status": "started"}

    def _command_list_budget_seconds(self, cmds, stage):
        return run_flow_core.command_list_budget_seconds(self, cmds, stage)

    def _extract_precondition_check_config(self, data):
        return run_flow_core.extract_precondition_check_config(data)

    def _derive_precondition_check_from_units(self, data, precondition_units=None):
        return run_flow_core.derive_precondition_check_from_units(self, data, precondition_units=precondition_units)

    def _resolve_precondition_check_config(self, data, precondition_units=None):
        return run_flow_core.resolve_precondition_check_config(self, data, precondition_units=precondition_units)

    def _normalize_setup_check_config(self, raw, default_mode="required"):
        return run_flow_core.normalize_setup_check_config(raw, default_mode=default_mode)

    def _estimate_check_budget_seconds(self, cfg, stage):
        return run_flow_core.estimate_check_budget_seconds(self, cfg, stage)

    def _resolve_precondition_units(self, data, raise_on_invalid=False):
        return run_flow_core.resolve_precondition_units(data, raise_on_invalid=raise_on_invalid)

    def _precondition_units_budget_seconds(self, units):
        return run_flow_core.precondition_units_budget_seconds(self, units)

    def _compute_setup_timeout_auto(self, data):
        return run_flow_core.compute_setup_timeout_auto(self, data)

    def _resolve_max_attempts(self, data, override):
        max_attempts = MAX_ATTEMPTS
        case_max = data.get("maxAttempts")
        if case_max is None:
            case_max = data.get("max_attempts")
        try:
            if case_max is not None:
                case_max = int(case_max)
        except (TypeError, ValueError):
            case_max = None
        if case_max and case_max > 0:
            max_attempts = case_max
        try:
            if override is not None:
                override = int(override)
        except (TypeError, ValueError):
            override = None
        if override and override > 0:
            max_attempts = min(max_attempts, override)
        return max_attempts

    def _sanitize_label_value(self, value):
        return sanitize_label_value(value)

    def _label_hash(self, value, length=16):
        return label_hash(value, length=length)

    def _manual_workflow_bridge_enabled(self):
        return manual_workflow_bridge_core.manual_workflow_bridge_enabled()

    def _manual_workflow_start_enabled(
        self,
        *,
        defer_cleanup=False,
        skip_precondition_unit_ids=None,
        case_data_override=None,
        resolved_params=None,
        namespace_context=None,
    ):
        if not self._manual_workflow_bridge_enabled():
            return False
        return manual_workflow_bridge_core.manual_bridge_start_eligible(
            defer_cleanup=defer_cleanup,
            skip_precondition_unit_ids=skip_precondition_unit_ids,
            case_data_override=case_data_override,
            resolved_params=resolved_params,
            namespace_context=namespace_context,
        )

    def _start_manual_workflow_run(self, case_id, *, max_attempts_override=None):
        case = self.get_case(case_id)
        if case.get("error"):
            return case
        try:
            service, case_name, test_file = decode_case_id(case_id)
        except Exception as exc:
            return {"error": f"Invalid case id: {exc}"}
        path = self._resolve_case_path(service, case_name, test_file)
        if path is None or not path.exists():
            return {"error": "Case file not found"}

        workflow_path = manual_workflow_bridge_core.write_manual_workflow_file(
            service=service,
            case_name=case_name,
            case_path=path.resolve(),
            max_attempts_override=max_attempts_override,
            root=ROOT,
        )
        start_payload = manual_workflow_bridge_core.manual_workflow_start_payload(
            self._rel_path(workflow_path),
            max_attempts_override=max_attempts_override,
        )
        started = self.start_workflow(start_payload)
        if "error" in started:
            return {"error": started.get("error")}
        job = started.get("job") if isinstance(started, dict) else None
        job_id = str((job or {}).get("id") or "").strip()
        if not job_id:
            return {"error": "manual workflow start failed: missing job id"}

        session = manual_workflow_bridge_core.empty_manual_workflow_session()
        session.update(
            {
                "active_job_id": job_id,
                "case_id": case_id,
                "service": service,
                "case": case_name,
                "test_file": test_file,
                "workflow_path": self._rel_path(workflow_path),
                "workflow_name": Path(workflow_path).stem,
            }
        )
        self.manual_workflow_session = session
        self.run_state = self._empty_run_state()
        return {"status": "started"}

    def start_manual_run(self, case_id, max_attempts_override=None):
        with self.run_lock:
            if self.run_state["status"] in ("setup_running", "ready", "verifying"):
                return {"error": "A run is already active"}
            if self.run_state.get("cleanup_status") in ("running", "failed"):
                return {"error": "Cleanup not finished"}
        session, _job_id, job = self._manual_workflow_lookup()
        if manual_workflow_bridge_core.has_active_manual_workflow_session(session):
            if isinstance(job, dict) and str(job.get("status") or "").strip().lower() == "running":
                return {"error": "A run is already active"}
            if not isinstance(job, dict):
                self._clear_manual_workflow_session()
        return self._start_manual_workflow_run(case_id, max_attempts_override=max_attempts_override)

    def _manual_workflow_session_snapshot(self):
        if isinstance(self.manual_workflow_session, dict):
            return deepcopy(self.manual_workflow_session)
        return {}

    def _manual_workflow_lookup(self):
        session = self._manual_workflow_session_snapshot()
        if not manual_workflow_bridge_core.has_active_manual_workflow_session(session):
            return session, "", None
        job_id = str(session.get("active_job_id") or "").strip()
        if not job_id:
            return session, "", None
        with self.workflow_lock:
            job = deepcopy(self.workflow_jobs.get(job_id))
        return session, job_id, job

    def _clear_manual_workflow_session(self):
        session = self._manual_workflow_session_snapshot()
        workflow_path = str(session.get("workflow_path") or "").strip()
        if workflow_path:
            path = Path(workflow_path)
            if not path.is_absolute():
                path = (ROOT / path).resolve()
            try:
                path.relative_to(ROOT)
            except Exception:
                path = None
            if path and path.is_file():
                path.unlink(missing_ok=True)
        self.manual_workflow_session = manual_workflow_bridge_core.empty_manual_workflow_session()

    def _manual_workflow_submit_bridge(self, require_flag=True):
        if require_flag and not self._manual_workflow_bridge_enabled():
            return None
        session, _job_id, job = self._manual_workflow_lookup()
        if not manual_workflow_bridge_core.has_active_manual_workflow_session(session):
            return None
        if not isinstance(job, dict):
            self._clear_manual_workflow_session()
            return {"error": "Manual workflow job not found"}
        if not manual_workflow_bridge_core.workflow_job_can_submit(job):
            return {"error": "Run is not ready for submission"}
        signal_path = manual_workflow_bridge_core.resolve_manual_submit_signal_path(
            session,
            job,
            root=ROOT,
        )
        if signal_path is None:
            return {"error": "Submit channel is not ready"}
        try:
            manual_workflow_bridge_core.write_manual_submit_signal(signal_path, payload="")
        except Exception as exc:
            return {"error": f"Failed to submit run: {exc}"}
        return {"status": "verifying"}

    def _manual_workflow_cleanup_bridge(self, require_flag=True):
        if require_flag and not self._manual_workflow_bridge_enabled():
            return None
        session, _job_id, job = self._manual_workflow_lookup()
        if not manual_workflow_bridge_core.has_active_manual_workflow_session(session):
            return None
        if not isinstance(job, dict):
            self._clear_manual_workflow_session()
            return {"status": "already_cleaned"}
        job_status = str(job.get("status") or "").strip().lower()
        if job_status in ("completed", "failed"):
            self._clear_manual_workflow_session()
            return {"status": "already_cleaned"}
        if job_status != "running":
            self._clear_manual_workflow_session()
            return {"status": "skipped"}
        if not manual_workflow_bridge_core.workflow_job_can_submit(job):
            return {"error": "Run is still in progress"}
        signal_path = manual_workflow_bridge_core.resolve_manual_submit_signal_path(
            session,
            job,
            root=ROOT,
        )
        if signal_path is None:
            return {"error": "Cleanup channel is not ready"}
        payload = json.dumps({"action": "cleanup", "reason": "manual_cleanup"})
        try:
            manual_workflow_bridge_core.write_manual_submit_signal(signal_path, payload=payload)
        except Exception as exc:
            return {"error": f"Failed to request cleanup: {exc}"}
        cleanup_log = manual_workflow_bridge_core.resolve_manual_cleanup_log_path(
            session,
            job,
            root=ROOT,
        )
        response = {"status": "cleaning"}
        if cleanup_log is not None:
            response["log"] = self._rel_path(cleanup_log)
        return response

    def submit_manual_run(self):
        bridged = self._manual_workflow_submit_bridge(require_flag=False)
        if bridged is not None:
            return bridged
        return {"error": "Run is not ready for submission"}

    def cleanup_manual_run(self):
        bridged = self._manual_workflow_cleanup_bridge(require_flag=False)
        if bridged is not None:
            return bridged
        return {"status": "skipped"}

    def _manual_workflow_run_status(self, require_flag=True):
        if require_flag and not self._manual_workflow_bridge_enabled():
            return None
        session, _job_id, job = self._manual_workflow_lookup()
        if not manual_workflow_bridge_core.has_active_manual_workflow_session(session):
            return None

        case_summary = None
        case_id = str(session.get("case_id") or "").strip()
        if case_id:
            service = str(session.get("service") or "").strip()
            case_name = str(session.get("case") or "").strip()
            test_file = str(session.get("test_file") or "test.yaml").strip() or "test.yaml"
            path = self._resolve_case_path(service, case_name, test_file)
            if path is not None:
                case_summary = self._case_summary(service, case_name, test_file, path)

        return manual_workflow_bridge_core.map_manual_session_to_run_status(
            session,
            job,
            root=ROOT,
            read_json_file=self._read_json_file,
            case_summary=case_summary,
            cluster_ok=self.cluster_ok,
            cluster_error=self.cluster_error,
        )

    def _run_status_payload(self, status):
        max_attempts = status.get("max_attempts") or MAX_ATTEMPTS
        max_time_seconds = MAX_TIME_MINUTES * 60
        elapsed = self._solve_elapsed_seconds()

        case_summary = None
        if status.get("case_id"):
            path = self._resolve_case_path(
                status.get("service"),
                status.get("case"),
                status.get("test_file"),
            )
            if path is not None:
                case_summary = self._case_summary(
                    status.get("service"),
                    status.get("case"),
                    status.get("test_file"),
                    path,
                )

        data = status.get("data") or {}
        has_verification = bool(resolve_oracle_verify(data).get("commands"))
        can_submit = status.get("status") in ("ready", "failed")

        return {
            "status": status.get("status"),
            "case": case_summary,
            "attempts": status.get("attempts"),
            "max_attempts": max_attempts,
            "elapsed_seconds": elapsed,
            "time_limit_seconds": max_time_seconds,
            "run_dir": status.get("run_dir"),
            "setup_log": status.get("setup_log"),
            "cleanup_log": status.get("cleanup_log"),
            "cleanup_status": status.get("cleanup_status"),
            "verification_logs": status.get("verification_logs"),
            "current_step": status.get("current_step"),
            "last_error": status.get("last_error"),
            "metrics_path": status.get("metrics_path"),
            "cluster_ok": self.cluster_ok,
            "cluster_error": self.cluster_error,
            "has_verification": has_verification,
            "can_submit": can_submit,
            "verification_warnings": status.get("verification_warnings") or [],
            "resolved_params": status.get("resolved_params") or {},
            "setup_timeout_auto_sec": status.get("setup_timeout_auto_sec"),
            "setup_timeout_auto_breakdown": status.get("setup_timeout_auto_breakdown"),
            "setup_phase": status.get("setup_phase"),
            "setup_warnings": status.get("setup_warnings") or [],
            "setup_checks_path": status.get("setup_checks_path"),
            "defer_cleanup": status.get("defer_cleanup"),
            "skip_precondition_unit_ids": status.get("skip_precondition_unit_ids") or [],
            "last_verification_kind": status.get("last_verification_kind"),
            "last_verification_step": status.get("last_verification_step"),
            "namespace_context": status.get("namespace_context") or {},
            "namespace_lifecycle_owner": status.get("namespace_lifecycle_owner") or "orchestrator",
        }

    def manual_run_status(self):
        bridged = self._manual_workflow_run_status(require_flag=False)
        if bridged is not None:
            return bridged
        with self.run_lock:
            status = dict(self.run_state)
        if not status:
            status = self._empty_run_state()
        return self._run_status_payload(status)

    def _set_setup_phase(self, phase):
        run_flow_core.set_setup_phase(self, phase)

    def _record_setup_warning(self, warning):
        run_flow_core.record_setup_warning(self, warning)

    def _write_setup_checks_summary(self, records):
        run_flow_core.write_setup_checks_summary(self, records)

    def _fail_setup(self, reason=None):
        run_flow_core.fail_setup(self, reason=reason)

    def _run_setup_check_loop(self, check_id, cfg, log_path, stage, records):
        return run_flow_core.run_setup_check_loop(self, check_id, cfg, log_path, stage, records)

    def _run_precondition_check(self, records, precondition_units=None):
        return run_flow_core.run_precondition_check(self, records, precondition_units=precondition_units)

    def _run_probe_command_list(self, cmds, log_path, stage, label):
        return run_flow_core.run_probe_command_list(self, cmds, log_path, stage, label)

    def _run_precondition_units(self, units, log_path):
        return run_flow_core.run_precondition_units(self, units, log_path)

    def _run_setup(self):
        run_flow_core.run_setup(self)

    def submit_run(self):
        bridged = self._manual_workflow_submit_bridge()
        if bridged is not None:
            return bridged
        return run_flow_core.submit_run(self)

    def abort_run(self, reason=None, exit_code=None):
        with self.run_lock:
            if self.run_state["status"] not in ("ready", "failed"):
                return {"error": "Run is not abortable"}
            detail = reason or "Agent aborted"
            if exit_code is not None:
                detail = f"{detail} (exit_code={exit_code})"
            self._resume_solve_timer()
            self.run_state["status"] = "auto_failed"
            self._set_timestamp("finished_at")
            self.run_state["last_error"] = detail
            self._write_meta()
            self._stop_proxy_trace()
            self._maybe_compute_metrics()
            self._maybe_start_cleanup()
            return {"status": "aborted", "error": detail}

    def abort_active_run(self, reason=None, exit_code=None):
        with self.run_lock:
            if self.run_state["status"] not in ("setup_running", "ready", "verifying", "failed"):
                return {"error": "Run is not active"}
            detail = reason or "Run aborted"
            if exit_code is not None:
                detail = f"{detail} (exit_code={exit_code})"
            self._resume_solve_timer()
            self.run_state["status"] = "auto_failed"
            self._set_timestamp("finished_at")
            self.run_state["last_error"] = detail
            self._write_meta()
            self._stop_proxy_trace()
            self._maybe_compute_metrics()
            self._maybe_start_cleanup()
            return {"status": "aborted", "error": detail}

    def finalize_active_run_without_submit(self, status="passed", reason=None):
        with self.run_lock:
            if self.run_state["status"] not in ("ready", "failed"):
                return {"error": "Run is not finalizable"}

            final_status = "passed" if str(status).strip().lower() == "passed" else "failed"
            self._resume_solve_timer()
            self.run_state["status"] = final_status
            if final_status == "passed":
                self._set_timestamp("finished_at")
            else:
                self.run_state["finished_at"] = None
                self.run_state["finished_at_ts"] = None
                if reason:
                    self.run_state["last_error"] = str(reason)
            self._write_meta()
            self._stop_proxy_trace()
            self._maybe_compute_metrics()
            self._maybe_start_cleanup()
            return {"status": final_status}

    def _run_verification(self, wait_cmds, before_cmds, verify_cmds, after_cmds, after_failure_mode, log_path, attempt):
        run_flow_core.run_verification(
            self,
            wait_cmds,
            before_cmds,
            verify_cmds,
            after_cmds,
            after_failure_mode,
            log_path,
            attempt,
        )

    def cleanup_run(self):
        bridged = self._manual_workflow_cleanup_bridge()
        if bridged is not None:
            return bridged
        return post_run_core.cleanup_run(self)

    def _split_command_tokens(self, command):
        return split_command_tokens(command)

    def _namespace_context(self):
        return command_runtime_core.namespace_context(self)

    def _namespace_env(self):
        return command_runtime_core.namespace_env(self)

    def _namespace_tokens(self):
        return command_runtime_core.namespace_tokens(self)

    def _namespace_for_item(self, item):
        return command_runtime_core.namespace_for_item(self, item)

    def _render_command_namespace_placeholders(self, command):
        return command_runtime_core.render_command_namespace_placeholders(self, command)

    def _inject_kubectl_namespace(self, command, namespace_value):
        return command_runtime_core.inject_kubectl_namespace(command, namespace_value)

    def _prepare_exec_item(self, item):
        return command_runtime_core.prepare_exec_item(self, item)

    def _render_manifest_paths(self, command):
        return command_runtime_core.render_manifest_paths(self, command)

    def _render_namespace_placeholders_value(self, value):
        return command_runtime_core.render_namespace_placeholders_value(self, value)

    def _default_timeout_sec_for_command(self, command, stage):
        return default_timeout_sec_for_command(command, stage)

    def _resolve_step_timeout_sec(self, item, stage):
        return resolve_step_timeout_sec(item, stage)

    def _run_command_list(self, cmds, log_path, stage):
        return command_runtime_core.run_command_list(self, cmds, log_path, stage)

    def _run_command_list_stateless(self, cmds, log_path, stage="cleanup"):
        return command_runtime_core.run_command_list_stateless(self, cmds, log_path, stage=stage)

    def _is_cleanup_deferred(self):
        return post_run_core.is_cleanup_deferred(self)

    def _maybe_start_cleanup(self):
        post_run_core.maybe_start_cleanup(self)

    def _run_cleanup_async(self, cmds, log_path, context=None):
        post_run_core.run_cleanup_async(self, cmds, log_path, context=context)

    def _append_log(self, path, text):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    def _set_timestamp(self, key):
        now = utc_now()
        self.run_state[key] = ts_str(now)
        self.run_state[f"{key}_ts"] = ts_epoch(now)

    def _pause_solve_timer(self):
        post_run_core.pause_solve_timer(self)

    def _resume_solve_timer(self):
        post_run_core.resume_solve_timer(self)

    def _write_meta(self):
        run_dir = self.run_state.get("run_dir")
        if not run_dir:
            return
        meta_path = ROOT / run_dir / "meta.json"
        payload = {
            "service": self.run_state.get("service"),
            "case": self.run_state.get("case"),
            "test_file": self.run_state.get("test_file"),
            "status": self.run_state.get("status"),
            "attempts": self.run_state.get("attempts"),
            "max_attempts": self.run_state.get("max_attempts"),
            "setup_started_at": self.run_state.get("setup_started_at"),
            "setup_started_at_ts": self.run_state.get("setup_started_at_ts"),
            "setup_finished_at": self.run_state.get("setup_finished_at"),
            "setup_finished_at_ts": self.run_state.get("setup_finished_at_ts"),
            "solve_started_at": self.run_state.get("solve_started_at"),
            "solve_started_at_ts": self.run_state.get("solve_started_at_ts"),
            "solve_pause_total_sec": self.run_state.get("solve_pause_total_sec"),
            "solve_pause_started_at_ts": self.run_state.get("solve_pause_started_at_ts"),
            "solve_paused": self.run_state.get("solve_paused"),
            "finished_at": self.run_state.get("finished_at"),
            "finished_at_ts": self.run_state.get("finished_at_ts"),
            "run_dir": self.run_state.get("run_dir"),
            "setup_log": self.run_state.get("setup_log"),
            "cleanup_log": self.run_state.get("cleanup_log"),
            "cleanup_status": self.run_state.get("cleanup_status"),
            "cleanup_started_at": self.run_state.get("cleanup_started_at"),
            "cleanup_started_at_ts": self.run_state.get("cleanup_started_at_ts"),
            "cleanup_finished_at": self.run_state.get("cleanup_finished_at"),
            "cleanup_finished_at_ts": self.run_state.get("cleanup_finished_at_ts"),
            "verification_logs": self.run_state.get("verification_logs"),
            "last_error": self.run_state.get("last_error"),
            "metrics_path": self.run_state.get("metrics_path"),
            "snapshot_pre": self.run_state.get("snapshot_pre"),
            "snapshot_post": self.run_state.get("snapshot_post"),
            "snapshot_base": self.run_state.get("snapshot_base"),
            "snapshot_post_cleanup": self.run_state.get("snapshot_post_cleanup"),
            "action_trace_log": self.run_state.get("action_trace_log"),
            "proxy_error": self.run_state.get("proxy_error"),
            "verification_warnings": self.run_state.get("verification_warnings"),
            "setup_timeout_auto_sec": self.run_state.get("setup_timeout_auto_sec"),
            "setup_timeout_auto_breakdown": self.run_state.get("setup_timeout_auto_breakdown"),
            "setup_phase": self.run_state.get("setup_phase"),
            "setup_warnings": self.run_state.get("setup_warnings"),
            "setup_checks_path": self.run_state.get("setup_checks_path"),
            "defer_cleanup": self.run_state.get("defer_cleanup"),
            "skip_precondition_unit_ids": self.run_state.get("skip_precondition_unit_ids") or [],
            "last_verification_kind": self.run_state.get("last_verification_kind"),
            "last_verification_step": self.run_state.get("last_verification_step"),
        }
        meta_path.write_text(json.dumps(payload, indent=2))

    def _maybe_compute_metrics(self):
        post_run_core.maybe_compute_metrics(self)

    def _capture_snapshot_file(self, label):
        return post_run_core.capture_snapshot_file(self, label)

    def _needs_snapshot(self):
        return post_run_core.needs_snapshot(self)

    def _needs_residual_drift(self):
        return post_run_core.needs_residual_drift(self)

    def _write_metrics(self, results, run_dir=None):
        post_run_core.write_metrics(self, results, run_dir=run_dir)

    def _post_cleanup_metrics_from_state(self):
        post_run_core.post_cleanup_metrics_from_state(self)

    def _post_cleanup_metrics_from_context(self, context):
        post_run_core.post_cleanup_metrics_from_context(self, context)

    def _start_proxy_trace(self, run_id, run_dir):
        if not PROXY_CONTROL_URL:
            return
        trace_path = run_dir / "action_trace.jsonl"
        payload = {
            "run_id": str(run_id),
            "log_path": str(trace_path),
        }
        try:
            url = PROXY_CONTROL_URL.rstrip("/") + "/start"
            data = json.dumps(payload).encode("utf-8")
            req = urlrequest.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=PROXY_CONTROL_TIMEOUT) as resp:
                resp.read()
            self.run_state["proxy_error"] = None
        except (urlerror.URLError, ValueError, OSError) as exc:
            self.run_state["proxy_error"] = str(exc)

    def _stop_proxy_trace(self):
        self._clear_action_trace()
        if not PROXY_CONTROL_URL:
            return
        try:
            url = PROXY_CONTROL_URL.rstrip("/") + "/stop"
            req = urlrequest.Request(
                url,
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=PROXY_CONTROL_TIMEOUT) as resp:
                resp.read()
        except (urlerror.URLError, ValueError, OSError) as exc:
            self.run_state["proxy_error"] = str(exc)

    def _action_trace_path(self):
        path = self.run_state.get("action_trace_log")
        if not path:
            return None
        return ROOT / path

    def _active_trace_file(self):
        return ROOT / ".benchmark" / "active_trace_path"

    def _set_action_trace(self, run_dir):
        trace_path = Path(run_dir) / "action_trace.jsonl"
        if not trace_path.is_absolute():
            trace_path = ROOT / trace_path
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.touch(exist_ok=True)
        self.run_state["action_trace_log"] = str(trace_path.relative_to(ROOT))
        active_file = self._active_trace_file()
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(str(trace_path))

    def _clear_action_trace(self):
        active_file = self._active_trace_file()
        try:
            if active_file.exists():
                active_file.unlink()
        except OSError:
            pass

    def _apply_decoys_if_needed(self):
        metrics = self.run_state.get("external_metrics") or []
        if "decoy_integrity" not in metrics:
            return True
        run_dir = self.run_state.get("run_dir")
        if not run_dir:
            return True
        case_dir = RESOURCES_DIR / self.run_state.get("service", "") / self.run_state.get("case", "")
        decoy_files = list_decoy_files(case_dir)
        decoy_log = ROOT / run_dir / "decoy_setup.log"
        if not decoy_files:
            write_decoys_file(ROOT / run_dir, [])
            return True
        commands = build_decoy_commands(decoy_files, "apply")
        ok = self._run_command_list(commands, decoy_log, stage="decoy")
        decoys = load_decoys(decoy_files)
        write_decoys_file(ROOT / run_dir, decoys)
        return ok

    def _decoy_cleanup_commands(self):
        metrics = self.run_state.get("external_metrics") or []
        if "decoy_integrity" not in metrics:
            return []
        case_dir = RESOURCES_DIR / self.run_state.get("service", "") / self.run_state.get("case", "")
        decoy_files = list_decoy_files(case_dir)
        if not decoy_files:
            return []
        return build_decoy_commands(decoy_files, "delete")

    def _auto_fail_if_limits_exceeded(self):
        max_attempts = self.run_state.get("max_attempts") or MAX_ATTEMPTS
        max_time_seconds = MAX_TIME_MINUTES * 60
        attempts = self.run_state.get("attempts", 0)
        elapsed = self._solve_elapsed_seconds()

        if attempts >= max_attempts:
            self.run_state["status"] = "auto_failed"
            self._set_timestamp("finished_at")
            self.run_state["last_error"] = "Maximum attempts reached"
            self._write_meta()
            self._stop_proxy_trace()
            self._maybe_compute_metrics()
            self._maybe_start_cleanup()
            return True
        if elapsed > max_time_seconds:
            self.run_state["status"] = "auto_failed"
            self._set_timestamp("finished_at")
            self.run_state["last_error"] = "Time limit exceeded"
            self._write_meta()
            self._stop_proxy_trace()
            self._maybe_compute_metrics()
            self._maybe_start_cleanup()
            return True
        return False

    def _solve_elapsed_seconds(self):
        return post_run_core.solve_elapsed_seconds(self)

    def run_status(self):
        bridged = self._manual_workflow_run_status()
        if bridged is not None:
            return bridged
        with self.run_lock:
            if self.run_state["status"] in ("ready", "failed"):
                self._auto_fail_if_limits_exceeded()
            status = dict(self.run_state)
        return self._run_status_payload(status)

    def run_metrics(self):
        with self.run_lock:
            metrics_path = self.run_state.get("metrics_path")
        if not metrics_path:
            return {"status": "pending"}
        full_path = ROOT / metrics_path
        if not full_path.exists():
            return {"status": "missing", "path": str(metrics_path)}
        try:
            return json.loads(full_path.read_text())
        except Exception as exc:
            return {"error": str(exc)}

    def proxy_status(self):
        if not PROXY_CONTROL_URL:
            return {"status": "disabled"}
        try:
            url = PROXY_CONTROL_URL.rstrip("/") + "/status"
            with urlrequest.urlopen(url, timeout=PROXY_CONTROL_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                return {
                    "status": "error",
                    "error": "invalid proxy status response",
                    "control_url": PROXY_CONTROL_URL,
                }
            payload.setdefault("status", "ok")
            payload["control_url"] = PROXY_CONTROL_URL
            return payload
        except (urlerror.URLError, ValueError, OSError) as exc:
            return {
                "status": "error",
                "error": str(exc),
                "control_url": PROXY_CONTROL_URL,
            }

    def orchestrator_options(self):
        return get_orchestrator_cli_options()

    def orchestrator_preview(self, payload):
        return build_orchestrator_preview(payload or {})

    def _read_json_file(self, path):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _rel_path(self, path):
        p = Path(path)
        try:
            return str(p.resolve().relative_to(ROOT))
        except Exception:
            return str(p)

    def list_workflow_files(self):
        return workflow_jobs_core.list_workflow_files(self)

    def workflow_preview(self, payload):
        return workflow_jobs_core.workflow_preview(self, payload)

    def workflow_import(self, payload):
        return workflow_jobs_core.workflow_import(self, payload)

    def get_workflow_stream_snapshot(self):
        return workflow_jobs_core.get_workflow_stream_snapshot(self)

    def get_workflow_events_since(self, since_seq, timeout_sec=15.0):
        return workflow_jobs_core.get_workflow_events_since(self, since_seq, timeout_sec=timeout_sec)

    def start_workflow(self, payload):
        return workflow_jobs_core.start_workflow(self, payload)

    def submit_workflow_job(self, job_id):
        return workflow_jobs_core.submit_workflow_job(self, job_id)

    def cleanup_workflow_job(self, job_id):
        return workflow_jobs_core.cleanup_workflow_job(self, job_id)

    def get_workflow_job_prompt(self, job_id, max_chars=None):
        return workflow_jobs_core.get_workflow_job_prompt(self, job_id, max_chars=max_chars)

    def list_workflow_jobs(self):
        return workflow_jobs_core.list_workflow_jobs(self)

    def get_workflow_job(self, job_id):
        return workflow_jobs_core.get_workflow_job(self, job_id)

    def list_judge_runs(self):
        return judge_jobs_core.list_judge_runs(self)

    def list_judge_batches(self):
        return judge_jobs_core.list_judge_batches(self)

    def judge_preview(self, payload):
        return judge_jobs_core.judge_preview(self, payload)

    def get_judge_stream_snapshot(self):
        return judge_jobs_core.get_judge_stream_snapshot(self)

    def get_judge_events_since(self, since_seq, timeout_sec=15.0):
        return judge_jobs_core.get_judge_events_since(self, since_seq, timeout_sec=timeout_sec)

    def start_judge(self, payload):
        return judge_jobs_core.start_judge(self, payload)

    def list_judge_jobs(self):
        return judge_jobs_core.list_judge_jobs(self)

    def get_judge_job(self, job_id):
        return judge_jobs_core.get_judge_job(self, job_id)
