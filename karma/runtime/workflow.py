"""
Workflow loop, stage advance, state publication, and final sweeps.

:func:`run_workflow_loop` is the outermost execution loop. It iterates
over workflow rows, calls ``runtime.case.run_stage`` for each stage,
manages retries and fail-fast decisions, publishes workflow state after
each stage, and runs final sweeps on completion.

Final sweeps:

*Regression sweep*
    Re-runs the oracle for all completed stages to detect regressions
    introduced by later stages. Executed only when the workflow completes
    successfully with more than one stage.

*Adversary cleanup sweep*
    Lifts any adversary injections whose ``lift_at_stage`` never ran due
    to an early workflow exit, ensuring no adversarial conditions are left
    in the cluster.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .case import run_stage
from ..oracle import run_regression_sweep
from ..adversary import collect_pending_lift_units
from .. import protocol


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_workflow_state(run_dir: Path, state: dict[str, Any]) -> None:
    """Write *state* to the workflow state file atomically.

    Writes to a ``.tmp`` file then renames to avoid partial reads by the
    HTTP SSE path.
    """
    path = protocol.workflow_state_path(run_dir)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2))
        tmp.rename(path)
    except Exception:
        pass


def _should_retry(stage_result: dict[str, Any], retries_remaining: int) -> bool:
    """Return ``True`` when the stage should be retried.

    Retry is permitted on ``"error"`` and ``"timeout"`` statuses only.
    A ``"fail"`` verdict from the oracle is deterministic and is not
    retried.
    """
    if retries_remaining <= 0:
        return False
    return stage_result.get("status") in ("error", "timeout")


def _run_final_regression_sweep(
    rows: list[dict[str, Any]],
    stage_results: list[dict[str, Any]],
    *,
    run_dir: Path,
) -> dict[str, Any]:
    """Re-run the oracle for all completed stages and return a regression summary.

    A regression is a stage whose oracle passed during the run but fails
    when re-evaluated after later stages may have altered cluster state.

    Returns
    -------
    dict
        Map of ``stage_id`` to regression verdict dict.
    """
    from ..definitions.cases import normalize_oracle_config
    from ..definitions.workflows import resolve_workflow_rows

    completed_rows = [
        row for row in rows
        if row.get("stage_id") in {r.get("stage_id") for r in stage_results if r.get("status") == "pass"}
    ]
    if len(completed_rows) <= 1:
        return {}

    oracle_configs = [(r["stage_id"], r.get("case", {}).get("oracle") or {}) for r in completed_rows]
    role_bindings_map = {r["stage_id"]: {} for r in completed_rows}

    return run_regression_sweep(
        oracle_configs,
        role_bindings_map=role_bindings_map,
        run_dir=run_dir,
    )


def _run_adversary_cleanup_sweep(
    all_injections: list[dict[str, Any]],
    *,
    deployed_scenario_ids: set[str],
    completed_stage_ids: set[str],
    environment: Any,
    run_dir: Path,
) -> dict[str, Any]:
    """Lift pending adversary injections and return a cleanup summary.

    Returns
    -------
    dict
        Keys: ``lifted`` (list of scenario IDs that were lifted),
        ``errors`` (list of error strings for failed lifts).
    """
    pending = collect_pending_lift_units(
        all_injections,
        deployed_scenario_ids=deployed_scenario_ids,
        completed_stage_ids=completed_stage_ids,
    )
    if not pending:
        return {"lifted": [], "errors": []}

    lifted_ids: list[str] = []
    errors: list[str] = []
    adv_log = run_dir / "adversary_cleanup.log"
    for unit in pending:
        from ..adversary.runtime import lift as adv_lift
        result = adv_lift([unit], role_bindings={}, log_path=adv_log, env_vars=None)
        if result.get("ok"):
            lifted_ids.extend(result.get("lifted_ids") or [])
        else:
            errors.append(f"cleanup lift failed: {result.get('output', '')[:200]}")
    return {"lifted": lifted_ids, "errors": errors}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_workflow_loop(
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    run_dir: Path,
    resources_dir: Path,
    agent_meta: dict[str, Any],
    sandbox_mode: str,
    environment: Any,
    prompt_mode: str,
    on_stage_complete: Any | None = None,
) -> dict[str, Any]:
    """Drive the workflow execution loop and return the final run result.

    Iterates over *rows* in order, calling ``runtime.case.run_stage`` for
    each. Handles per-stage retries and fail-fast decisions. Publishes
    workflow state to disk after each stage so that the HTTP SSE path can
    stream progress. Runs final sweeps after the loop completes.

    Parameters
    ----------
    rows:
        Ordered workflow row list from
        ``definitions.workflows.resolve_workflow_rows``.
    run_id:
        Unique identifier for this run.
    run_dir:
        Root directory for all run artifacts.
    resources_dir:
        Root resources directory forwarded to ``run_stage``.
    agent_meta:
        Agent launch metadata from ``agents.registry.resolve_agent``.
    sandbox_mode:
        ``"local"`` or ``"docker"``.
    environment:
        Initialized environment provider from ``environments.registry``.
    prompt_mode:
        One of the prompt modes defined in ``definitions.prompts``.
    on_stage_complete:
        Optional callable invoked with the stage result dict after each
        stage (including retries). Used by the HTTP SSE path to push
        progress events to the UI.

    Returns
    -------
    dict
        Keys: ``run_id``, ``status`` (``"complete"``, ``"failed"``, or
        ``"error"``), ``stages`` (list[dict]),
        ``regression_sweep`` (dict or ``None``),
        ``adversary_cleanup`` (dict or ``None``),
        ``duration_sec`` (float).
    """
    start_time = time.monotonic()
    stage_results: list[dict[str, Any]] = []
    stage_prompts: list[str] = []
    completed_stage_ids: set[str] = set()
    deployed_scenario_ids: set[str] = set()

    all_injections = [
        inj
        for row in rows
        for inj in (row.get("adversary_deploy") or [])
    ]

    workflow_status = "complete"

    for idx, row in enumerate(rows):
        stage_id = row["stage_id"]
        retries = row.get("retries", 0)
        stage_result: dict[str, Any] = {}

        for attempt in range(retries + 1):
            stage_result = run_stage(
                row,
                run_dir=run_dir,
                resources_dir=resources_dir,
                agent_meta=agent_meta,
                sandbox_mode=sandbox_mode,
                environment=environment,
                prior_stage_ids=list(completed_stage_ids),
                stage_prompts=stage_prompts,
                prompt_mode=prompt_mode,
            )

            if on_stage_complete is not None:
                try:
                    on_stage_complete(stage_result)
                except Exception:
                    pass

            if not _should_retry(stage_result, retries - attempt):
                break

        stage_results.append(stage_result)

        if stage_result.get("status") == "pass":
            completed_stage_ids.add(stage_id)
        else:
            workflow_status = "failed"
            break

        _write_workflow_state(run_dir, {
            "run_id": run_id,
            "status": "running",
            "completed_stages": list(completed_stage_ids),
            "stage_results": stage_results,
        })

    regression_sweep = None
    if workflow_status == "complete" and len(completed_stage_ids) > 1:
        regression_sweep = _run_final_regression_sweep(
            rows, stage_results, run_dir=run_dir
        )

    adversary_cleanup = None
    if deployed_scenario_ids:
        adversary_cleanup = _run_adversary_cleanup_sweep(
            all_injections,
            deployed_scenario_ids=deployed_scenario_ids,
            completed_stage_ids=completed_stage_ids,
            environment=environment,
            run_dir=run_dir,
        )

    result = {
        "run_id": run_id,
        "status": workflow_status,
        "stages": stage_results,
        "regression_sweep": regression_sweep,
        "adversary_cleanup": adversary_cleanup,
        "duration_sec": time.monotonic() - start_time,
    }

    try:
        protocol.run_meta_path(run_dir).write_text(json.dumps(result, indent=2))
    except Exception:
        pass

    _write_workflow_state(run_dir, {
        "run_id": run_id,
        "status": workflow_status,
        "completed_stages": list(completed_stage_ids),
        "stage_results": stage_results,
    })

    return result
