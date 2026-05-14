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
from .. import protocol


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
) -> dict[str, Any]:
    """Execute a list of probe/apply/verify operation units in order.

    All units must be in the canonical format produced by
    ``definitions.cases.normalize_precondition_units``. No format
    detection or legacy field handling is performed here; the legacy
    execution branch from ``run_flow.py`` is not carried forward.

    For each unit: runs probe commands first; when the probe passes, runs
    apply then verify. Respects the ``on_probe_fail`` policy (``"error"``
    or ``"skip"``). Retries verify up to ``verify_retries`` times with
    ``verify_interval_sec`` between attempts. All command output is
    appended to *log_path*.

    Returns
    -------
    dict
        Keys: ``ok`` (bool), ``units`` (list[dict] of per-unit outcomes),
        ``output`` (str).
    """
    import os
    import subprocess

    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **role_bindings, **(env_vars or {})}
    all_output: list[str] = []
    unit_outcomes: list[dict[str, Any]] = []
    overall_ok = True

    for unit in units or []:
        unit_id = unit.get("id") or unit.get("name") or "unknown"
        on_fail = unit.get("on_probe_fail", "error")

        # Probe
        probe_ok = True
        for cmd_entry in unit.get("probe_commands") or []:
            cmd = cmd_entry["command"]
            to = cmd_entry.get("timeout_sec") or 30
            try:
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, env=env, timeout=to
                )
                out = proc.stdout + proc.stderr
                all_output.append(f"$ {cmd}\n{out}")
                with log_path.open("a") as fh:
                    fh.write(f"[{label}:{unit_id}:probe] $ {cmd}\n{out}\n")
                if cmd_entry.get("sleep"):
                    time.sleep(cmd_entry["sleep"])
                if proc.returncode != 0:
                    probe_ok = False
                    break
            except Exception as exc:
                probe_ok = False
                all_output.append(f"$ {cmd}\n[error: {exc}]\n")
                break

        if probe_ok:
            # Condition already satisfied
            unit_outcomes.append({"id": unit_id, "ok": True, "skipped": True})
            continue
        if not probe_ok and on_fail == "skip":
            unit_outcomes.append({"id": unit_id, "ok": True, "skipped": True})
            continue

        # Apply
        apply_ok = True
        for cmd_entry in unit.get("apply_commands") or []:
            cmd = cmd_entry["command"]
            to = cmd_entry.get("timeout_sec") or 120
            try:
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, env=env, timeout=to
                )
                out = proc.stdout + proc.stderr
                all_output.append(f"$ {cmd}\n{out}")
                with log_path.open("a") as fh:
                    fh.write(f"[{label}:{unit_id}:apply] $ {cmd}\n{out}\n")
                if cmd_entry.get("sleep"):
                    time.sleep(cmd_entry["sleep"])
                if proc.returncode != 0:
                    apply_ok = False
                    break
            except Exception as exc:
                apply_ok = False
                all_output.append(f"[apply error: {exc}]\n")
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
                cmd = cmd_entry["command"]
                to = cmd_entry.get("timeout_sec") or 30
                try:
                    proc = subprocess.run(
                        cmd, shell=True, capture_output=True, text=True, env=env, timeout=to
                    )
                    out = proc.stdout + proc.stderr
                    all_output.append(f"$ {cmd}\n{out}")
                    with log_path.open("a") as fh:
                        fh.write(f"[{label}:{unit_id}:verify] $ {cmd}\n{out}\n")
                    if proc.returncode != 0:
                        verify_ok = False
                        break
                except Exception:
                    verify_ok = False
                    break
            if verify_ok:
                break

        unit_outcomes.append({"id": unit_id, "ok": verify_ok, "phase": "verify"})
        if not verify_ok:
            overall_ok = False

    return {"ok": overall_ok, "units": unit_outcomes, "output": "".join(all_output)}


def _wait_for_submit(
    submit_path: Path,
    *,
    agent_timeout_sec: int,
    poll_interval_sec: float = 1.0,
) -> tuple[bool, str | None]:
    """Poll for the agent's submit file until it appears or the timeout expires.

    Returns
    -------
    tuple[bool, str | None]
        ``(submitted, content)`` where *submitted* is ``True`` when the
        file appeared and *content* is its text. Returns
        ``(False, None)`` on timeout.
    """
    deadline = time.monotonic() + agent_timeout_sec
    while time.monotonic() < deadline:
        if submit_path.exists():
            try:
                return True, submit_path.read_text()
            except Exception:
                pass
        time.sleep(poll_interval_sec)
    return False, None


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

    try:
        from ..definitions.prompts import render_stage_prompt, assemble_agent_prompt

        # Step 1: launch proxy
        proxy_handle = launch_proxy(run_dir=stage_dir)

        # Step 2: bind namespace roles
        ns_roles = row.get("namespace_roles") or ["default"]
        role_bindings = environment.bind_namespace_roles(ns_roles, run_dir.name)
        environment.ensure_namespaces(role_bindings, run_dir=stage_dir)

        # Step 3: run precondition units
        precond_log = protocol.stage_precondition_log_path(run_dir, stage_id)
        case = row.get("case") or {}
        precond_units = case.get("precondition_units") or []
        precond_result = _run_operation_units(
            precond_units,
            role_bindings=role_bindings,
            log_path=precond_log,
            label="precondition",
        )
        if not precond_result["ok"]:
            return {
                "stage_id": stage_id,
                "status": "error",
                "oracle_verdict": None,
                "submitted": False,
                "duration_sec": time.monotonic() - start_time,
                "error": "precondition units failed",
                "evidence_path": str(protocol.stage_evidence_path(run_dir, stage_id)),
                "oracle_path": str(protocol.stage_oracle_path(run_dir, stage_id)),
            }

        # Step 4: plant decoys
        decoy_configs = case.get("decoys") or []
        if decoy_configs:
            environment.plant_decoys(decoy_configs, role_bindings, resources_dir=resources_dir, run_dir=stage_dir)

        # Step 5: adversary deploy
        adv_deploy_units = row.get("adversary_deploy") or []
        adv_log = protocol.stage_adversary_log_path(run_dir, stage_id)
        env_vars_adv = environment.build_env_vars(role_bindings, proxy_port=proxy_handle.port)
        deploy_result = adversary_deploy(adv_deploy_units, role_bindings=role_bindings, log_path=adv_log, env_vars=env_vars_adv)

        # Step 6: write agent bundle
        agent_kubeconfig = write_agent_bundle(
            proxy_handle,
            run_dir=run_dir,
            namespace_env_vars=env_vars_adv,
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
        agent_env_vars = {**env_vars_adv, "KUBECONFIG": str(agent_kubeconfig)}
        agent_process = launch_agent(
            agent_meta,
            sandbox_mode=sandbox_mode,
            env_vars=agent_env_vars,
            run_dir=stage_dir,
            agent_timeout_sec=row.get("agent_timeout_sec") or 900,
            kubeconfig_path=agent_kubeconfig,
        )

        # Step 9: wait for submit or timeout
        submit_path = protocol.stage_submit_path(run_dir, stage_id)
        submitted, submit_content = _wait_for_submit(
            submit_path,
            agent_timeout_sec=row.get("agent_timeout_sec") or 900,
        )
        agent_process.terminate()
        stage_status_before_oracle = "timeout" if not submitted else "running"

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
        if role_bindings and environment is not None:
            try:
                environment.cleanup_namespaces(role_bindings, run_dir=stage_dir)
            except Exception:
                pass
