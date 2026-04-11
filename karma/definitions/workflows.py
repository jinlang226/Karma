"""
Workflow loading, stage resolution, namespace alias handling, and the
single-case-to-workflow conversion.

A *workflow* is an ordered sequence of stages defined in a YAML file.
This module loads that file, validates its structure, resolves cross-stage
parameter references, and builds the row list consumed by ``runtime.workflow``.

A *workflow row* is the unit of work for one stage::

    {
        "stage_id":          str,
        "service":           str,
        "case_name":         str,
        "case":              dict,        # normalized case descriptor
        "namespace_roles":   list[str],
        "adversary_deploy":  list[dict],
        "adversary_lift":    list[dict],
        "adversary_hint":    str | None,
        "prompt_mode":       str,
        "agent_timeout_sec": int,
        "retries":           int,
    }

Single-case UI runs are normalized into a 1-stage workflow by
:func:`single_case_to_workflow` so that both the CLI and HTTP paths
execute through an identical runtime stack.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import re

import yaml

_VALID_PROMPT_MODES = ("progressive", "concat_stateful", "concat_blind")
_DEFAULT_PROMPT_MODE = "progressive"
_DEFAULT_AGENT_TIMEOUT_SEC = 900
_DEFAULT_NAMESPACE_ALIAS = "default"
_STAGE_PARAM_REF_RE = re.compile(
    r"^\s*\$\{stages\.([a-zA-Z0-9_.-]+)\.params\.([a-zA-Z0-9_.-]+)\}\s*$"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_valid_name(name: str) -> bool:
    """Return ``True`` when *name* contains only alphanumerics, hyphens, and underscores."""
    ...


def _parse_stage_param_ref(value: str) -> dict[str, str] | None:
    """Return ``{"stage_id": str, "param": str}`` for a ``${stages.<id>.params.<n>}``
    expression, or ``None`` when *value* does not match the pattern.
    """
    match = _STAGE_PARAM_REF_RE.match(value)
    if not match:
        return None
    return {"stage_id": match.group(1), "param": match.group(2)}


def _resolve_stage_param_overrides(
    stage: dict[str, Any],
    stage_index: int,
    all_stages: list[dict[str, Any]],
    prior_stage_params: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Resolve ``${stages.<id>.params.<n>}`` references in a stage's ``param_overrides``.

    References to the current or a future stage raise :class:`ValueError`.
    References to a stage whose params may have been invalidated by an
    intermediate stage with overlapping namespace aliases produce a warning.

    Parameters
    ----------
    stage:
        The stage dict being resolved.
    stage_index:
        Zero-based index of *stage* within *all_stages*.
    all_stages:
        Ordered list of all stage dicts in the workflow.
    prior_stage_params:
        Map of stage ID to resolved params for all preceding stages.

    Returns
    -------
    tuple[dict, list[str]]
        ``(resolved_overrides, warnings)``.
    """
    ...


def _namespace_aliases_for_stage(stage: dict[str, Any]) -> list[str]:
    """Return the namespace alias list for *stage*, defaulting to ``["default"]``."""
    aliases = [
        str(a).strip()
        for a in (stage.get("namespaces") or [])
        if str(a).strip()
    ]
    return aliases if aliases else [_DEFAULT_NAMESPACE_ALIAS]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_workflow_file(path: Path) -> dict[str, Any]:
    """Load and parse a workflow YAML file from disk.

    Raises
    ------
    RuntimeError
        When *path* does not exist or cannot be parsed as a YAML object.
    """
    if not path.exists():
        raise RuntimeError(f"workflow file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        raise RuntimeError(f"failed to parse workflow file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"workflow file {path} must be a YAML object")
    return data


def normalize_workflow(
    raw: dict[str, Any],
    *,
    resources_dir: Path,
) -> dict[str, Any]:
    """Validate and normalize a raw workflow dict.

    Validates the metadata and spec blocks, validates adversary entries
    via ``definitions.adversary``, and resolves cross-stage param
    references. Does not load case files or adversary scenario files;
    those are deferred to :func:`resolve_workflow_rows`.

    Parameters
    ----------
    raw:
        Parsed workflow YAML dict.
    resources_dir:
        Root resources directory (used for future schema checks).

    Raises
    ------
    ValueError
        When the workflow is structurally invalid.

    Returns
    -------
    dict
        Keys: ``id`` (str), ``label`` (str or ``None``),
        ``prompt_mode`` (str), ``stages`` (list[dict]),
        ``adversary`` (list[dict]).
    """
    ...


def resolve_workflow_rows(
    workflow: dict[str, Any],
    *,
    resources_dir: Path,
) -> list[dict[str, Any]]:
    """Resolve a normalized workflow into an ordered list of workflow rows.

    Loads case files, normalizes cases, resolves adversary scenarios from
    disk, and assembles the full row dict for each stage. This is the
    single point of filesystem I/O for case and adversary resolution.

    Parameters
    ----------
    workflow:
        Normalized workflow dict from :func:`normalize_workflow`.
    resources_dir:
        Root resources directory.

    Raises
    ------
    RuntimeError
        When any case or adversary scenario cannot be loaded.

    Returns
    -------
    list[dict]
        Ordered list of workflow row dicts. See module docstring for the
        row shape.
    """
    ...


def single_case_to_workflow(
    service: str,
    case_name: str,
    param_overrides: dict[str, Any] | None = None,
    *,
    prompt_mode: str = _DEFAULT_PROMPT_MODE,
    agent_timeout_sec: int = _DEFAULT_AGENT_TIMEOUT_SEC,
    namespace_roles: list[str] | None = None,
) -> dict[str, Any]:
    """Return a 1-stage workflow dict for a single-case run.

    Called by ``interfaces.http.jobs`` to convert a UI form submission
    into the same workflow representation used by the CLI path, ensuring
    both paths execute through an identical runtime stack.

    The generated stage ID is ``"stage_1"``. The workflow ID is derived
    from *service* and *case_name*.

    Parameters
    ----------
    service:
        Service name, e.g. ``"rabbitmq-experiments"``.
    case_name:
        Case name, e.g. ``"failover"``.
    param_overrides:
        Optional parameter overrides applied to the case.
    prompt_mode:
        One of ``"progressive"``, ``"concat_stateful"``,
        ``"concat_blind"``.
    agent_timeout_sec:
        Agent timeout in seconds for the single stage.
    namespace_roles:
        Explicit namespace roles; when ``None`` the case contract is used.
    """
    ...


def get_all_stage_ids(workflow: dict[str, Any]) -> list[str]:
    """Return the ordered list of stage IDs from a normalized workflow dict."""
    return [str(s.get("id") or "") for s in (workflow.get("stages") or [])]
