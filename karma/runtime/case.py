"""
Single-stage lifecycle execution.

:func:`run_stage` is the innermost execution unit called by
``runtime.workflow`` for each stage in the workflow loop.

Stage execution steps:

1. Create the stage directory via ``protocol``.
2. Launch the kubectl proxy via ``transport.k8s.backend``.
3. Bind namespace roles and create namespaces via the environment provider.
4. Run precondition units.
5. Plant decoys.
6. Run adversary deploy units.
7. Write the agent credential bundle (kubeconfig, env vars).
8. Render and write the stage prompt.
9. Launch the agent via ``sandbox``.
10. Poll for ``submit.txt`` or wait for the agent timeout.
11. Terminate the agent process.
12. Collect evidence via ``evidence``.
13. Run the oracle via ``oracle``.
14. Run adversary lift units.
15. Write stage metadata and outcome.
16. Tear down the proxy and clean up namespaces.

:func:`run_stage` never raises. All errors are captured in the returned
stage result dict under the ``"error"`` key so that ``runtime.workflow``
can decide whether to retry or advance.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..transport.k8s.backend import launch_proxy, write_agent_bundle
from ..environments.registry import get_environment
from ..sandbox import launch_agent, cleanup_agent
from ..oracle import run_oracle
from ..evidence import collect_evidence
from ..adversary import deploy as adversary_deploy, lift as adversary_lift, report as adversary_report
from ..definitions.cases import discover_case_decoys
from .. import protocol
from .._warn import warn


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_operation_units(
    units: list[dict[str, Any]],
    *,
    role_bindings: dict[str, str],
    log_path: Path,
    env_vars: dict[str, str] | None = None,
    label: str = "operation",
    phase_timeout_sec: float | None = None,
) -> dict[str, Any]:
    """Execute a list of probe/apply/verify operation units in order.

    All units must be in the canonical format produced by
    ``definitions.cases.normalize_precondition_units``. No format
    detection or legacy field handling is performed here; the legacy
    execution branch from ``run_flow.py`` is not carried forward.

    For each unit, the probe tests whether the target state already holds:

    * probe **passes** -> state already present, apply is skipped.
    * probe **fails** + ``on_probe_fail="skip"`` (the default, used by setup
      units) -> the "not yet present" signal is expected, so apply runs to
      establish the state, then verify.
    * probe **fails** + ``on_probe_fail="error"`` -> the failure is fatal
      (e.g. a readiness gate the apply cannot satisfy), so the unit fails.

    Retries verify up to ``verify_retries`` times with
    ``verify_interval_sec`` between attempts. All command output is
    appended to *log_path*.

    ``phase_timeout_sec`` bounds the wall-clock time of the whole phase
    (``None`` = unbounded, the default for callers that do not opt in).
    When set, each command's timeout is capped to the remaining budget and
    the phase aborts as soon as the deadline passes -- this is what makes
    ``--setup-timeout`` (``settings.precondition_timeout_sec``) actually
    bound the precondition phase, mirroring the oracle's wall-clock cap.

    Returns
    -------
    dict
        Keys: ``ok`` (bool), ``units`` (list[dict] of per-unit outcomes),
        ``output`` (str), ``timed_out`` (bool -- True if the phase budget
        was exhausted).
    """
    import os
    import subprocess
    from ..settings import settings as _settings

    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **role_bindings, **(env_vars or {})}
    all_output: list[str] = []
    unit_outcomes: list[dict[str, Any]] = []
    overall_ok = True

    # Wall-clock deadline for the whole phase. ``None`` means unbounded.
    deadline = (time.monotonic() + phase_timeout_sec) if phase_timeout_sec else None
    timed_out = False

    def _exec(cmd_entry: dict[str, Any], default_to: int, phase: str, unit_id: str):
        """Run one command, capping its timeout to the remaining phase budget.

        Returns the subprocess return code, or ``None`` if the command did
        not complete (error or timeout). A timeout that occurs once the phase
        budget is the binding constraint sets the enclosing ``timed_out`` flag.
        """
        nonlocal timed_out
        cmd = cmd_entry["command"]
        base_to = cmd_entry.get("timeout_sec") or default_to
        cmd_to = base_to
        capped = False  # True once the phase budget is the binding constraint.
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                marker = "[setup timeout: phase budget exhausted]"
                all_output.append(f"$ {cmd}\n{marker}\n")
                with log_path.open("a") as fh:
                    fh.write(f"[{label}:{unit_id}:{phase}] $ {cmd}\n{marker}\n")
                return None
            cmd_to = max(1, min(base_to, int(remaining)))
            capped = cmd_to < base_to
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, env=env, timeout=cmd_to
            )
            out = proc.stdout + proc.stderr
            all_output.append(f"$ {cmd}\n{out}")
            with log_path.open("a") as fh:
                fh.write(f"[{label}:{unit_id}:{phase}] $ {cmd}\n{out}\n")
            if cmd_entry.get("sleep"):
                time.sleep(cmd_entry["sleep"])
            return proc.returncode
        except subprocess.TimeoutExpired:
            # A timeout while the budget was capping this command (or the
            # deadline has since passed) is a phase timeout; a timeout at the
            # command's own declared limit is an ordinary command failure.
            if capped or (deadline is not None and time.monotonic() >= deadline):
                timed_out = True
                marker = f"[setup timeout after {cmd_to}s]"
            else:
                marker = f"[timed out after {cmd_to}s]"
            all_output.append(f"$ {cmd}\n{marker}\n")
            with log_path.open("a") as fh:
                fh.write(f"[{label}:{unit_id}:{phase}] $ {cmd}\n{marker}\n")
            return None
        except Exception as exc:
            all_output.append(f"$ {cmd}\n[error: {exc}]\n")
            with log_path.open("a") as fh:
                fh.write(f"[{label}:{unit_id}:{phase}] $ {cmd}\n[error: {exc}]\n")
            return None

    for unit in units or []:
        if timed_out:
            break
        unit_id = unit.get("id") or unit.get("name") or "unknown"
        on_fail = unit.get("on_probe_fail", "skip")

        # Probe
        probe_ok = True
        for cmd_entry in unit.get("probe_commands") or []:
            rc = _exec(cmd_entry, 30, "probe", unit_id)
            if rc != 0:
                probe_ok = False
                break
        if timed_out:
            overall_ok = False
            unit_outcomes.append({"id": unit_id, "ok": False, "phase": "probe", "timed_out": True})
            break

        if probe_ok:
            # Target state already present -> no apply needed.
            unit_outcomes.append({"id": unit_id, "ok": True, "skipped": True})
            continue
        if on_fail == "error":
            # Probe failure is fatal: apply cannot establish the state
            # (e.g. a readiness gate), so the precondition fails.
            overall_ok = False
            unit_outcomes.append({"id": unit_id, "ok": False, "phase": "probe"})
            continue
        # on_fail == "skip" (default): the probe failing is the expected
        # "not yet present" signal -> fall through to apply to establish it.

        # Apply
        apply_ok = True
        for cmd_entry in unit.get("apply_commands") or []:
            rc = _exec(cmd_entry, _settings.command_timeout_sec, "apply", unit_id)
            if rc != 0:
                apply_ok = False
                break
        if timed_out:
            overall_ok = False
            unit_outcomes.append({"id": unit_id, "ok": False, "phase": "apply", "timed_out": True})
            break
        if not apply_ok:
            overall_ok = False
            unit_outcomes.append({"id": unit_id, "ok": False, "phase": "apply"})
            continue

        # Verify
        retries = unit.get("verify_retries") or 1
        interval = unit.get("verify_interval_sec") or 0.0
        verify_ok = False
        for _attempt in range(retries):
            if _attempt > 0:
                time.sleep(interval)
            verify_ok = True
            for cmd_entry in unit.get("verify_commands") or []:
                rc = _exec(cmd_entry, 30, "verify", unit_id)
                if rc != 0:
                    verify_ok = False
                    break
            if verify_ok or timed_out:
                break
        if timed_out:
            overall_ok = False
            unit_outcomes.append({"id": unit_id, "ok": False, "phase": "verify", "timed_out": True})
            break

        unit_outcomes.append({"id": unit_id, "ok": verify_ok, "phase": "verify"})
        if not verify_ok:
            overall_ok = False

    return {
        "ok": overall_ok,
        "units": unit_outcomes,
        "output": "".join(all_output),
        "timed_out": timed_out,
    }


def _command_list_budget_seconds(
    commands: list[dict[str, Any]] | None, default_to: int
) -> int:
    """Worst-case wall-clock budget for a command list.

    Sum of each command's declared (or default) timeout plus any post-command
    sleep. Mirrors the old ``_command_list_budget_seconds``.
    """
    total = 0
    for entry in commands or []:
        total += int(entry.get("timeout_sec") or default_to) + int(entry.get("sleep") or 0)
    return total


def _precondition_auto_budget_seconds(units: list[dict[str, Any]]) -> int:
    """Computed wall-clock budget for the precondition phase (``auto`` mode).

    Per unit: probe + apply + verify*retries + the inter-retry interval gaps,
    using the same per-command timeout defaults the runner applies (probe/verify
    30s, apply ``command_timeout_sec``), plus a fixed slack. Used as the floor in
    ``setup_timeout_mode == "auto"`` so a legitimately slow precondition is not
    killed by a too-small ``--setup-timeout``. Ported from the old
    ``precondition_units_budget_seconds`` / ``compute_setup_timeout_auto``.
    """
    from ..settings import settings as _settings
    total = 0
    for unit in units or []:
        probe = _command_list_budget_seconds(unit.get("probe_commands"), 30)
        apply_ = _command_list_budget_seconds(
            unit.get("apply_commands"), _settings.command_timeout_sec
        )
        verify_once = _command_list_budget_seconds(unit.get("verify_commands"), 30)
        retries = max(1, int(unit.get("verify_retries") or 1))
        interval = max(0, int(float(unit.get("verify_interval_sec") or 0)))
        total += probe + apply_ + verify_once * retries + interval * max(0, retries - 1)
    return total + 60  # slack, matching the old auto budget


def _param_env_vars(params: dict[str, Any] | None) -> dict[str, str]:
    """Return ``BENCH_PARAM_<KEY>`` environment variables for resolved params.

    Keys are uppercased with non-alphanumerics replaced by underscores.
    Bools render as ``true``/``false``, ``None`` as empty string, and
    dict/list values as compact JSON, matching the legacy command contract.
    """
    import json
    import re as _re

    out: dict[str, str] = {}
    for key, value in (params or {}).items():
        key_name = _re.sub(r"[^A-Z0-9]", "_", str(key).upper())
        if not key_name:
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif value is None:
            rendered = ""
        elif isinstance(value, (dict, list)):
            rendered = json.dumps(value, sort_keys=True)
        else:
            rendered = str(value)
        out["BENCH_PARAM_" + key_name] = rendered
    return out


def _wait_for_submit(
    submit_path: Path,
    *,
    agent_timeout_sec: int,
    poll_interval_sec: float = 1.0,
    agent_process: Any = None,
) -> tuple[bool, str | None, bool]:
    """Poll for the agent's submit file until it appears, the agent exits, or
    the timeout expires.

    When *agent_process* is given, a process that terminates before writing
    ``submit.txt`` ends the wait immediately rather than burning the full
    timeout (a crashed/early-exiting agent).

    Returns
    -------
    tuple[bool, str | None, bool]
        ``(submitted, content, agent_exited)``. *submitted* is ``True`` when
        the file appeared (with its text in *content*). *agent_exited* is
        ``True`` when the process died before submitting. ``(False, None,
        False)`` is a genuine timeout.
    """
    deadline = time.monotonic() + agent_timeout_sec
    while time.monotonic() < deadline:
        if submit_path.exists():
            try:
                return True, submit_path.read_text(), False
            except Exception:
                pass
        if agent_process is not None and not agent_process.is_running():
            # Agent exited; give submit.txt one last chance (race on the final
            # write) before declaring an early exit.
            if submit_path.exists():
                try:
                    return True, submit_path.read_text(), False
                except Exception:
                    pass
            return False, None, True
        time.sleep(poll_interval_sec)
    return False, None, False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_stage(
    row: dict[str, Any],
    *,
    run_dir: Path,
    resources_dir: Path,
    agent_meta: dict[str, Any],
    sandbox_mode: str,
    environment: Any,
    prior_stage_ids: list[str],
    stage_prompts: list[str],
    prompt_mode: str,
    defer_cleanup: bool = False,
    sandbox_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one workflow stage and return its result dict.

    Parameters
    ----------
    row:
        Workflow row dict from ``definitions.workflows.resolve_workflow_rows``.
    run_dir:
        Root directory of the current run.
    resources_dir:
        Root resources directory.
    agent_meta:
        Agent launch metadata from ``agents.registry.resolve_agent``.
    sandbox_mode:
        ``"local"`` or ``"docker"``.
    environment:
        Initialized environment provider from ``environments.registry``.
    prior_stage_ids:
        IDs of stages that completed successfully before this one.
    stage_prompts:
        Rendered prompt strings for all stages up to and including this one.
    prompt_mode:
        One of the prompt modes defined in ``definitions.prompts``.
    defer_cleanup:
        When ``True``, the stage does not tear down its namespaces; the
        caller (the workflow loop) owns namespace teardown so that cluster
        state survives across stages and the final regression sweep can
        re-evaluate it against the live cluster. The proxy and agent are
        always cleaned up per stage regardless.

    Returns
    -------
    dict
        Keys: ``stage_id``, ``status`` (``"pass"``, ``"fail"``,
        ``"timeout"``, or ``"error"``), ``oracle_verdict``
        (``"pass"``, ``"fail"``, ``"error"``, or ``None``),
        ``submitted`` (bool), ``duration_sec`` (float),
        ``error`` (str or ``None``), ``evidence_path`` (str),
        ``oracle_path`` (str).
    """
    stage_id = row["stage_id"]
    stage_dir = protocol.ensure_stage_dir(run_dir, stage_id)
    start_time = time.monotonic()

    proxy_handle = None
    agent_process = None
    role_bindings: dict[str, str] = {}
    ns_baseline: set[str] = set()

    try:
        from ..definitions.prompts import render_stage_prompt, assemble_agent_prompt

        # Step 1: launch proxy. In docker mode the agent container reaches the
        # proxy via host.docker.internal, so it must bind all interfaces (a
        # 127.0.0.1-only listener refuses the container's connection).
        proxy_handle = launch_proxy(
            run_dir=stage_dir,
            bind_host="0.0.0.0" if sandbox_mode == "docker" else "127.0.0.1",
        )

        # Step 2: bind namespace roles
        ns_roles = row.get("namespace_roles") or ["default"]
        role_bindings = environment.bind_namespace_roles(ns_roles, run_dir.name)
        environment.ensure_namespaces(role_bindings, run_dir=stage_dir)
        # Snapshot namespaces (incl. the role namespaces just created) so the
        # teardown can remove any literal namespaces the case creates in its
        # preconditions (mongodb, cockroachdb, ...) which the per-role cleanup
        # does not cover.
        if hasattr(environment, "list_namespaces"):
            try:
                ns_baseline = environment.list_namespaces()
            except Exception:
                ns_baseline = set()

        # Step 3: run precondition units
        precond_log = protocol.stage_precondition_log_path(run_dir, stage_id)
        case = row.get("case") or {}
        precond_units = case.get("precondition_units") or []
        # Namespace ($BENCH_NAMESPACE, $BENCH_NS_<ROLE>) and param
        # ($BENCH_PARAM_<KEY>) env vars that case/scenario commands reference.
        param_env = _param_env_vars(case.get("params"))
        command_env = {**environment.build_namespace_env_vars(role_bindings), **param_env}
        from ..settings import settings as _settings
        # "fixed" caps at the literal precondition timeout; "auto" floors it at
        # the per-case computed budget so a slow-but-legitimate precondition is
        # not killed (restores the old --setup-timeout-mode behaviour).
        if (_settings.setup_timeout_mode or "auto") == "auto":
            phase_timeout = max(
                _settings.precondition_timeout_sec,
                _precondition_auto_budget_seconds(precond_units),
            )
        else:
            phase_timeout = _settings.precondition_timeout_sec
        precond_result = _run_operation_units(
            precond_units,
            role_bindings=role_bindings,
            log_path=precond_log,
            env_vars=command_env,
            label="precondition",
            phase_timeout_sec=phase_timeout,
        )
        if not precond_result["ok"]:
            # A budget-exhausted phase (``--setup-timeout``) is reported
            # distinctly from a genuine precondition command failure.
            timed_out = precond_result.get("timed_out")
            error_msg = (
                f"setup timeout: preconditions exceeded "
                f"{int(_settings.precondition_timeout_sec)}s"
                if timed_out
                else "precondition units failed"
            )
            return {
                "stage_id": stage_id,
                "status": "timeout" if timed_out else "error",
                "oracle_verdict": None,
                "submitted": False,
                "duration_sec": time.monotonic() - start_time,
                "error": error_msg,
                "evidence_path": str(protocol.stage_evidence_path(run_dir, stage_id)),
                "oracle_path": str(protocol.stage_oracle_path(run_dir, stage_id)),
            }

        # Step 4: plant decoys -- explicit `decoys:` entries plus any manifests
        # discovered under the case's decoy/ directory.
        decoy_configs = list(case.get("decoys") or [])
        decoy_configs += discover_case_decoys(
            resources_dir, row.get("service"), row.get("case_name")
        )
        if decoy_configs:
            environment.plant_decoys(decoy_configs, role_bindings, resources_dir=resources_dir, run_dir=stage_dir)

        # Step 5: adversary deploy
        adv_deploy_units = row.get("adversary_deploy") or []
        adv_log = protocol.stage_adversary_log_path(run_dir, stage_id)
        env_vars_adv = {
            **environment.build_env_vars(role_bindings, proxy_port=proxy_handle.port),
            **param_env,
        }
        deploy_result = adversary_deploy(adv_deploy_units, role_bindings=role_bindings, log_path=adv_log, env_vars=env_vars_adv)

        # Step 6: write agent bundle
        opts = sandbox_options or {}
        src_kc = opts.get("source_kubeconfig")
        agent_kubeconfig = write_agent_bundle(
            proxy_handle,
            run_dir=run_dir,
            namespace_env_vars=env_vars_adv,
            source_kubeconfig=Path(src_kc) if src_kc else None,
            docker=(sandbox_mode == "docker"),
        )

        # Step 7: render and write prompt
        rendered_prompt = render_stage_prompt(case, row, {"id": run_dir.name}, variables=env_vars_adv)
        stage_prompts_up = list(stage_prompts) + [rendered_prompt]
        adversary_hint = row.get("adversary_hint")
        final_prompt = assemble_agent_prompt(
            stage_prompts_up,
            len(stage_prompts_up) - 1,
            prompt_mode,
            adversary_hint=adversary_hint,
        )
        protocol.stage_prompt_path(run_dir, stage_id).write_text(final_prompt)

        # Step 8: launch agent
        #
        # A no-agent run -- `resolve_agent(None, sandbox_mode="local")` returns a
        # descriptor with no folder/entrypoint -- stands the scenario up and lets
        # the oracle verify the preconditioned state without launching anything
        # (e.g. `run-case` with no --agent, or an operator-driven manual run).
        # In that case there is nothing to launch or wait on, so the stage
        # outcome is determined solely by the oracle verdict.
        # In docker mode the kubeconfig is bind-mounted at /root/.kube/config
        # (see write_agent_bundle/launch_agent), so KUBECONFIG must point there,
        # not at the host path the container cannot see.
        kubeconfig_env = (
            "/root/.kube/config" if sandbox_mode == "docker" else str(agent_kubeconfig)
        )
        agent_env_vars = {**env_vars_adv, "KUBECONFIG": kubeconfig_env}
        # A per-run launch command (--agent-cmd) is itself an agent to launch,
        # even when no agent is registered (folder/entrypoint absent).
        command_override = opts.get("agent_cmd")
        no_agent = not (
            agent_meta.get("folder") or agent_meta.get("entrypoint") or command_override
        )
        if no_agent:
            submitted = False
            stage_status_before_oracle = "running"
        else:
            agent_process = launch_agent(
                agent_meta,
                sandbox_mode=sandbox_mode,
                env_vars=agent_env_vars,
                run_dir=stage_dir,
                agent_timeout_sec=row.get("agent_timeout_sec") or 900,
                kubeconfig_path=agent_kubeconfig,
                extra_mounts=opts.get("extra_mounts"),
                command_override=command_override,
            )

            # Step 9: wait for submit, agent exit, or timeout
            submit_path = protocol.stage_submit_path(run_dir, stage_id)
            submitted, _submit_content, agent_exited = _wait_for_submit(
                submit_path,
                agent_timeout_sec=row.get("agent_timeout_sec") or 900,
                agent_process=agent_process,
            )
            agent_process.terminate()
            # A crashed/early-exiting agent is not a timeout; let the oracle
            # decide pass/fail rather than forcing the "timeout" status.
            stage_status_before_oracle = (
                "running" if (submitted or agent_exited) else "timeout"
            )

        # Step 10: collect evidence
        evidence = collect_evidence(
            run_dir=run_dir,
            stage_id=stage_id,
            case=case,
            role_bindings=role_bindings,
        )

        # Step 11: run oracle
        oracle_config = case.get("oracle") or {}
        oracle_result = run_oracle(
            oracle_config,
            role_bindings=role_bindings,
            run_dir=run_dir,
            stage_id=stage_id,
            env_vars=env_vars_adv,
        )
        oracle_verdict = oracle_result.get("verdict")

        # Step 12: adversary lift
        adv_lift_units = row.get("adversary_lift") or []
        lift_result = adversary_lift(adv_lift_units, role_bindings=role_bindings, log_path=adv_log, env_vars=env_vars_adv)
        if deploy_result.get("deployed_ids"):
            adversary_report(
                str(row.get("stage_id")),
                deploy_result,
                lift_result,
                run_dir=run_dir,
                stage_id=stage_id,
            )

        if oracle_verdict == "pass":
            final_status = "pass"
        elif stage_status_before_oracle == "timeout":
            final_status = "timeout"
        else:
            final_status = oracle_verdict or "error"

        return {
            "stage_id": stage_id,
            "status": final_status,
            "oracle_verdict": oracle_verdict,
            "submitted": submitted,
            "duration_sec": time.monotonic() - start_time,
            "error": None,
            "evidence_path": str(protocol.stage_evidence_path(run_dir, stage_id)),
            "oracle_path": str(protocol.stage_oracle_path(run_dir, stage_id)),
        }

    except Exception as exc:
        return {
            "stage_id": stage_id,
            "status": "error",
            "oracle_verdict": None,
            "submitted": False,
            "duration_sec": time.monotonic() - start_time,
            "error": str(exc),
            "evidence_path": str(protocol.stage_evidence_path(run_dir, stage_id)),
            "oracle_path": str(protocol.stage_oracle_path(run_dir, stage_id)),
        }
    finally:
        if agent_process is not None:
            cleanup_agent(agent_process)
        if proxy_handle is not None:
            proxy_handle.teardown()
        if not defer_cleanup and role_bindings and environment is not None:
            try:
                environment.cleanup_namespaces(role_bindings, run_dir=stage_dir)
            except Exception as exc:
                warn(f"failed to delete stage namespaces: {exc}")
        # Also remove any literal namespaces the case created in preconditions
        # (deferred to the workflow loop for multi-stage runs that share state).
        if not defer_cleanup and ns_baseline and hasattr(environment, "cleanup_created_namespaces"):
            try:
                environment.cleanup_created_namespaces(ns_baseline, run_dir=stage_dir)
            except Exception:
                pass
