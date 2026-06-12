"""
Read-only catalog and listing queries for the HTTP interface.

Backs the browse-oriented endpoints: the service/case catalog, per-case
detail, the run history, and the workflow file list. Everything here is a
pure filesystem read built on the ``definitions`` loaders -- no state is
mutated and no cluster is touched, with the single exception of the
best-effort :func:`cluster_status` probe which shells out to
``kubectl cluster-info`` so the UI can warn when no cluster is reachable.

Keeping these queries in their own module keeps ``server.py`` thin: the
route functions there call into this module and serialize the result.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

# Short cache for the cluster probe so /api/services (polled by the UI banner)
# does not shell out to kubectl on every request.
_CLUSTER_TTL_SEC = 5.0
_cluster_cache: dict[str, Any] = {"ts": 0.0, "value": None}

from ...definitions.cases import load_case_file, normalize_case
from ...definitions.workflows import load_workflow_file, normalize_workflow


def _read_json(path: Path) -> dict[str, Any] | None:
    """Return the parsed JSON object at *path*, or ``None`` when absent/invalid."""
    try:
        if path.exists():
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else None
    except Exception:
        return None
    return None


def list_services(resources_dir: Path) -> list[dict[str, Any]]:
    """Return the service catalog: one entry per service directory.

    Each entry carries the service name, the count of cases it defines,
    and the sorted case-name list. A *case* is any direct subdirectory
    that contains a ``test.yaml``.
    """
    result: list[dict[str, Any]] = []
    if not resources_dir.exists():
        return result
    for svc_dir in sorted(resources_dir.iterdir()):
        if not svc_dir.is_dir():
            continue
        cases = sorted(
            d.name
            for d in svc_dir.iterdir()
            if d.is_dir() and (d / "test.yaml").exists()
        )
        result.append(
            {"name": svc_dir.name, "case_count": len(cases), "cases": cases}
        )
    return result


def list_cases_by_service(resources_dir: Path) -> dict[str, list[str]]:
    """Return a ``{service: [case_name, ...]}`` map across all services."""
    return {svc["name"]: svc["cases"] for svc in list_services(resources_dir)}


def get_case_detail(
    resources_dir: Path, service: str, case_name: str
) -> dict[str, Any]:
    """Return the full detail descriptor for one case.

    Loads and normalizes ``test.yaml`` so the UI case-detail view and the
    workflow param-override editor share one source of truth for the
    prompt, declared params (name/default/description), namespace
    contract, decoys, metrics, tags, and an oracle summary.

    Raises
    ------
    RuntimeError
        When the case file is missing or fails schema validation. The
        caller maps this to an HTTP 404/400.
    """
    case_data = load_case_file(resources_dir, service, case_name)
    normalized = normalize_case(case_data, service, case_name)

    params: list[dict[str, Any]] = []
    for name, pdef in (case_data.get("params") or {}).items():
        if isinstance(pdef, dict):
            params.append(
                {
                    "name": name,
                    "default": pdef.get("default"),
                    "description": pdef.get("description", ""),
                }
            )
        else:
            params.append({"name": name, "default": pdef, "description": ""})

    oracle = normalized.get("oracle") or {}
    return {
        "service": service,
        "case_name": case_name,
        "prompt": normalized.get("prompt", ""),
        "params": params,
        "namespace_contract": normalized.get("namespace_contract"),
        "precondition_count": len(normalized.get("precondition_units") or []),
        "decoys": normalized.get("decoys") or [],
        "metrics": normalized.get("metrics") or [],
        "tags": normalized.get("tags") or [],
        "oracle": {
            "verify_command_count": len(oracle.get("verify_commands") or []),
            "has_script": bool(oracle.get("script_path")),
        },
        "warnings": normalized.get("warnings") or [],
    }


def list_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Return the run history, newest first.

    Each entry summarizes one run directory: its status (from
    ``workflow_state.json`` or ``run.json``), stage count, and -- when any
    stage has a ``judge.json`` -- the mean judge score so the Judge view
    can list scored runs without re-reading every artifact.
    """
    runs: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return runs

    def _run_ts(p):
        m = re.search(r"(\d{8}_\d{6})", p.name)
        return m.group(1) if m else p.name
    # Chronological, newest first (sort by the run_id timestamp, not the name --
    # the name leads with the service so a name sort groups by service instead).
    for run_dir in sorted(runs_dir.iterdir(), key=_run_ts, reverse=True):
        if not run_dir.is_dir():
            continue
        state = _read_json(run_dir / "workflow_state.json")
        meta = _read_json(run_dir / "run.json")
        data = state or meta or {}

        entry: dict[str, Any] = {
            "run_id": run_dir.name,
            "path": str(run_dir),
            "status": data.get("status", "unknown"),
            "judged": False,
        }

        # Per-stage pass/fail progress + the submitted config, so the Results
        # list can show "2/3 passed" and which agent/target ran.
        stages = data.get("stages") or data.get("stage_results") or []
        entry["passed"] = sum(1 for s in stages if s.get("status") == "pass")
        entry["failed"] = sum(
            1 for s in stages if s.get("status") in ("fail", "failed", "error", "timeout")
        )
        cfg = _read_json(run_dir / "config.json") or {}
        entry["agent"] = cfg.get("agent")
        entry["sandbox"] = cfg.get("sandbox")
        # Service/case for the UI to split the run name (service can contain
        # hyphens, so the run_id alone can't be parsed reliably).
        entry["service"] = cfg.get("service")
        entry["case_name"] = cfg.get("case_name")
        # The workflow's true total stage count (config), so a running run shows
        # "1/5" rather than "1/2" (only 2 stage dirs created so far).
        entry["stage_total"] = cfg.get("stage_total")
        entry["target"] = (
            cfg.get("workflow_id")
            or (f"{cfg.get('service')}/{cfg.get('case_name')}" if cfg.get("service") else None)
        )

        stages_dir = run_dir / "stages"
        if stages_dir.exists():
            # Exclude regression-sweep dirs ("<stage>__regression") from the
            # count -- they are oracle re-runs, not workflow stages.
            stage_ids = sorted(
                d.name for d in stages_dir.iterdir()
                if d.is_dir() and not d.name.endswith("__regression")
            )
            entry["stage_count"] = len(stage_ids)
        # Prefer the run-level judge.json; fall back to the per-stage mean.
        run_judge = _read_json(run_dir / "judge.json")
        if run_judge and isinstance(run_judge.get("score"), (int, float)):
            entry["judged"] = True
            entry["judge_score"] = round(float(run_judge["score"]), 1)
        elif stages_dir.exists():
            scores: list[float] = []
            for sid in stage_ids:
                jd = _read_json(stages_dir / sid / "judge.json")
                if jd and isinstance(jd.get("score"), (int, float)):
                    scores.append(float(jd["score"]))
            if scores:
                entry["judged"] = True
                entry["judge_score"] = round(sum(scores) / len(scores), 1)
        runs.append(entry)
    return runs


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _tail(path: Path, max_bytes: int = 16384) -> str | None:
    """Return up to the last *max_bytes* of a text file, or None if absent."""
    try:
        data = path.read_text(errors="replace")
    except Exception:
        return None
    if len(data) > max_bytes:
        return "...(earlier output truncated)...\n" + data[-max_bytes:]
    return data


def get_stage_detail(runs_dir: Path, run_id: str, stage_id: str) -> dict[str, Any]:
    """Return one stage's artifacts for the UI: its status/error plus the
    precondition log (the command that triggered a setup failure), the oracle
    result (expected vs actual), and the agent log.

    Raises
    ------
    RuntimeError
        When the ids are unsafe or the run/stage directory is missing.
    """
    if not (_SAFE_ID.match(run_id) and _SAFE_ID.match(stage_id)) \
            or ".." in run_id or ".." in stage_id:
        raise RuntimeError("invalid run or stage id")
    run_dir = runs_dir / run_id
    sdir = run_dir / "stages" / stage_id
    if not sdir.is_dir():
        raise RuntimeError(f"stage not found: {run_id}/{stage_id}")

    status = error = oracle_verdict = None
    meta = _read_json(run_dir / "run.json") or _read_json(run_dir / "workflow_state.json") or {}
    for s in (meta.get("stages") or []):
        if s.get("stage_id") == stage_id:
            status = s.get("status")
            error = s.get("error")
            oracle_verdict = s.get("oracle_verdict")
            break

    return {
        "run_id": run_id,
        "stage_id": stage_id,
        "status": status,
        "error": error,
        "oracle_verdict": oracle_verdict,
        "precondition_log": _tail(sdir / "precondition.log"),
        "oracle": _read_json(sdir / "oracle.json"),
        "agent_log": _tail(sdir / "agent.log"),
        "prompt": _tail(sdir / "prompt.txt", max_bytes=4096),
    }


def get_run_detail(runs_dir: Path, run_id: str) -> dict[str, Any]:
    """Return a run's header detail for the Results view: status, the submitted
    config, and the per-stage status list. Drill into a stage via
    :func:`get_stage_detail`.

    Raises
    ------
    RuntimeError
        When the id is unsafe or the run directory is missing.
    """
    if not _SAFE_ID.match(run_id) or ".." in run_id:
        raise RuntimeError("invalid run id")
    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        raise RuntimeError(f"run not found: {run_id}")
    meta = _read_json(run_dir / "run.json") or _read_json(run_dir / "workflow_state.json") or {}
    config = _read_json(run_dir / "config.json") or {}
    raw_stages = meta.get("stages") or meta.get("stage_results") or []
    stages = [{
        "stage_id": s.get("stage_id"),
        "status": s.get("status"),
        "oracle_verdict": s.get("oracle_verdict"),
        "error": s.get("error"),
    } for s in raw_stages]
    detail: dict[str, Any] = {
        "run_id": run_id,
        "status": meta.get("status", "unknown"),
        "config": config,
        "stages": stages,
        "duration_sec": meta.get("duration_sec"),
        # Final-sweep artifacts (multi-stage workflows only): the post-run oracle
        # re-run per passed stage, and the adversary lift/cleanup report.
        "regression_sweep": meta.get("regression_sweep"),
        "adversary_cleanup": meta.get("adversary_cleanup"),
    }
    # Mean judge score across stages (0-100, 0.1 precision), so the detail header
    # can show the run's test score without the caller re-reading judge artifacts.
    # Prefer the run-level judge.json (objective stage-pass score + regression
    # adjudication); fall back to the mean of legacy per-stage judge.json.
    run_judge = _read_json(run_dir / "judge.json")
    judge_log_path = run_dir / "judge.log"
    if judge_log_path.exists():
        try:
            detail["judge_log"] = judge_log_path.read_text()
        except Exception:
            pass
    if run_judge and isinstance(run_judge.get("score"), (int, float)):
        detail["judged"] = True
        detail["judge_score"] = round(float(run_judge["score"]), 1)
        detail["score_max"] = float(run_judge.get("score_max") or 100.0)
        detail["judge_breakdown"] = {
            k: run_judge.get(k)
            for k in (
                "summary", "total_stages", "passed_stages", "base_score",
                "regression_failures", "legitimate_regressions", "regressions",
            )
        }
    else:
        stages_dir = run_dir / "stages"
        if stages_dir.exists():
            scores: list[float] = []
            for sid in (s.get("stage_id") for s in raw_stages):
                if not sid:
                    continue
                jd = _read_json(stages_dir / str(sid) / "judge.json")
                if jd and isinstance(jd.get("score"), (int, float)):
                    scores.append(float(jd["score"]))
            if scores:
                detail["judged"] = True
                detail["judge_score"] = round(sum(scores) / len(scores), 1)
                detail["score_max"] = 100.0
    return detail


# Subfolder under workflows/ where UI-built workflows are saved.
_UI_SUBDIR = "ui"


def get_workflow_detail(
    workflows_dir: Path, resources_dir: Path, name: str
) -> dict[str, Any]:
    """Return the full normalized workflow for a saved file (its stages with
    service/case/param_overrides), for the detail + customize view. Path-safe:
    *name* is a relative path (e.g. ``suite/foo.yaml``) or bare basename
    confined to workflows_dir (block traversal outside the tree).

    Raises
    ------
    RuntimeError
        When the name is unsafe or no matching file exists.
    """
    if ".." in name or name.startswith("/") or name.startswith("\\"):
        raise RuntimeError("invalid workflow name")
    base = workflows_dir.resolve()
    # Accept a full relative path (e.g. "suite/foo.yaml", "ui/foo.yaml") or a
    # bare basename (searched at the top level and under ui/ for back-compat).
    candidates = [workflows_dir / name]
    if "/" not in name:
        candidates.append(workflows_dir / _UI_SUBDIR / name)
    for cand in candidates:
        target = cand.resolve()
        if (target == base or base in target.parents) and target.is_file():
            return normalize_workflow(load_workflow_file(target), resources_dir=resources_dir)
    raise RuntimeError(f"workflow not found: {name}")


def list_workflow_files(
    workflows_dir: Path, resources_dir: Path
) -> list[dict[str, Any]]:
    """Return one entry per ``*.yaml`` anywhere under *workflows_dir*.

    Recurses the whole tree (top level, ``ui/``, ``suite/``, and any other
    subfolder) so every workflow file is listed, not just the top level. Each
    entry carries its ``name`` (path relative to workflows_dir, e.g.
    ``suite/foo.yaml``) and ``dir`` (the relative subfolder, ``""`` at the top
    level) so the UI can preserve the directory hierarchy. Each workflow is
    loaded and normalized so the UI can show its id, stage count, and prompt
    mode, plus an ``ok`` flag and validation errors for files that fail to parse.
    """
    result: list[dict[str, Any]] = []
    if not workflows_dir.exists():
        return result
    paths = sorted(p for p in workflows_dir.rglob("*.yaml") if p.is_file())
    for wf in paths:
        try:
            rel = str(wf.relative_to(workflows_dir))
            subdir = str(wf.parent.relative_to(workflows_dir))
        except ValueError:
            rel, subdir = wf.name, ""
        if subdir == ".":
            subdir = ""
        entry: dict[str, Any] = {"path": str(wf), "name": rel, "dir": subdir,
                                 "ok": True, "errors": []}
        try:
            raw = load_workflow_file(wf)
            norm = normalize_workflow(raw, resources_dir=resources_dir)
            entry["id"] = norm.get("id")
            entry["stage_count"] = len(norm.get("stages") or [])
            entry["prompt_mode"] = norm.get("prompt_mode")
            entry["adversary_count"] = len(norm.get("adversary") or [])
        except Exception as exc:
            entry["ok"] = False
            entry["errors"] = [str(exc)]
        result.append(entry)
    return result


def save_workflow(
    workflows_dir: Path, resources_dir: Path, yaml_text: str, name: str | None
) -> dict[str, Any]:
    """Validate and save a builder workflow to ``workflows/ui/<name>.yaml``.

    The YAML is parsed and normalized first (so an invalid workflow is
    never written). The file name derives from *name* (or the workflow id),
    sanitized to a single safe path segment; saving with the same name
    overwrites (upsert). Returns ``{ok, path, name}``.

    Raises
    ------
    ValueError
        When the YAML is unparseable or fails workflow validation.
    """
    import re
    import yaml as _yaml

    try:
        raw = _yaml.safe_load(yaml_text) or {}
    except Exception as exc:
        raise ValueError(f"failed to parse YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("workflow must be a YAML object")
    norm = normalize_workflow(raw, resources_dir=resources_dir)

    base = str(name or norm.get("id") or "workflow").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-")
    if not safe or safe in (".", ".."):
        safe = "workflow"

    dest_dir = workflows_dir / _UI_SUBDIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{safe}.yaml"
    path.write_text(yaml_text if yaml_text.endswith("\n") else yaml_text + "\n")
    return {"ok": True, "path": str(path), "name": f"{_UI_SUBDIR}/{safe}.yaml"}


def list_adversary_scenarios(resources_dir: Path) -> list[dict[str, Any]]:
    """Return the adversary scenarios available to the UI.

    Scenarios live in the top-level ``adversaries/{service}/{scenario}/
    scenario.yaml`` tree (a sibling of *resources_dir*); the legacy
    ``resources/{service}/adversarial/{scenario}/`` location is still scanned
    as a fallback. Each entry carries the owning service, scenario name, file
    path, a cheap ``has_lift`` flag, declared prompt hints, and the param
    declarations (name/default/description) so the adversary panel and the
    workflow injection editor can describe them without re-parsing.
    """
    import yaml

    # (service_root, is_legacy) pairs: the top-level adversaries/ tree first,
    # then the legacy in-resources layout. Each yields service subdirectories.
    roots: list[tuple[Path, bool]] = [(resources_dir.parent / "adversaries", False)]
    if resources_dir.exists():
        roots.append((resources_dir, True))

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for root, legacy in roots:
        if not root.is_dir():
            continue
        for svc_dir in sorted(root.iterdir()):
            if not svc_dir.is_dir():
                continue
            # New layout: adversaries/<svc>/<scen>/; legacy: <svc>/adversarial/<scen>/.
            scen_parent = (svc_dir / "adversarial") if legacy else svc_dir
            if not scen_parent.is_dir():
                continue
            for scen_dir in sorted(scen_parent.iterdir()):
                scenario_file = scen_dir / "scenario.yaml"
                if not scenario_file.is_file():
                    continue
                key = (svc_dir.name, scen_dir.name)
                if key in seen:
                    continue
                seen.add(key)
                entry: dict[str, Any] = {
                    "service": svc_dir.name,
                    "scenario": scen_dir.name,
                    "path": str(scenario_file),
                    "has_lift": False,
                    "prompt_hints": {},
                }
                try:
                    data = yaml.safe_load(scenario_file.read_text()) or {}
                    if isinstance(data, dict):
                        entry["has_lift"] = data.get("lift") is not None
                        entry["prompt_hints"] = data.get("prompt_hints") or {}
                        entry["params"] = data.get("params") or {}
                except Exception:
                    entry["ok"] = False
                result.append(entry)
    return sorted(result, key=lambda e: (e["service"], e["scenario"]))


def cluster_status() -> dict[str, Any]:
    """Return a best-effort Kubernetes reachability status for the UI banner.

    Shells out to ``kubectl cluster-info`` with a short timeout, cached for
    a few seconds. Never raises: a missing binary, an unreachable cluster,
    or a timeout all map to a structured status string rather than an
    exception.
    """
    now = time.monotonic()
    if _cluster_cache["value"] is not None and now - _cluster_cache["ts"] < _CLUSTER_TTL_SEC:
        return _cluster_cache["value"]
    result = _probe_cluster()
    _cluster_cache["value"] = result
    _cluster_cache["ts"] = now
    return result


def _probe_cluster() -> dict[str, Any]:
    """Run the actual ``kubectl cluster-info`` probe (uncached)."""
    try:
        proc = subprocess.run(
            ["kubectl", "cluster-info"],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except FileNotFoundError:
        return {"status": "kubectl-missing"}
    except subprocess.TimeoutExpired:
        return {"status": "unreachable", "detail": "kubectl cluster-info timed out"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"status": "error", "detail": str(exc)}

    if proc.returncode == 0:
        return {"status": "ok"}
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return {"status": "unreachable", "detail": detail[0] if detail else ""}
