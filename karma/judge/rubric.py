"""
Rubric loading, normalization, and override merging.

A rubric defines the scoring criteria used by the judge LLM. It contains
a list of weighted items with descriptions and guidance strings.

Rubric schema::

    {
        "items": [
            {
                "id":          str,
                "description": str,
                "weight":      float,   # weights must sum to 1.0
                "rubric":      str,     # judge scoring guidance
            },
            ...
        ]
    }

The rubric produces a 0-1 quality score for each oracle-passing stage; there is
no passing_threshold -- pass/fail is the oracle's job.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def rubric_hash(rubric: dict[str, Any] | None) -> str | None:
    """Return a stable content hash of *rubric*, or None when there is no rubric.

    Used to detect that a rubric changed since a run was last scored (so the
    rubric result is stale and should be re-judged), independent of any file
    path -- covers both the bundled default and a custom CLI rubric.
    """
    if not rubric:
        return None
    return hashlib.sha256(
        json.dumps(rubric, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def load_rubric(
    run_dir: Path,
    stage_id: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the rubric for one stage from the stage run directory.

    Reads the stage metadata to locate the rubric source, normalizes it,
    then applies *overrides* when provided.

    Parameters
    ----------
    run_dir:
        Root directory of the run.
    stage_id:
        ID of the stage whose rubric to load.
    overrides:
        Optional rubric overrides. Override items are matched by ID;
        matching base items are replaced, unrecognized IDs are appended
        and weights are renormalized.

    Raises
    ------
    RuntimeError
        When no rubric can be located for *stage_id*.
    """
    from .. import protocol
    import json

    stage_meta_path = protocol.stage_meta_path(run_dir, stage_id)
    if stage_meta_path.exists():
        try:
            meta = json.loads(stage_meta_path.read_text())
        except Exception:
            meta = {}
    else:
        meta = {}

    raw_rubric = meta.get("rubric")
    if raw_rubric is None:
        # Fall back to a default single-item rubric.
        raw_rubric = {
            "items": [
                {
                    "id": "task_completion",
                    "description": "Did the agent complete the task correctly?",
                    "weight": 1.0,
                    "rubric": "Score 1.0 if the task objective was fully achieved, 0.0 otherwise.",
                }
            ],
        }

    rubric = normalize_rubric(raw_rubric)
    if overrides:
        rubric = merge_rubric_overrides(rubric, overrides)
    return rubric


def normalize_rubric(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a raw rubric dict.

    Verifies that all items contain required fields and that weights are
    positive and sum to 1.0 within floating-point tolerance. Returns
    ``{"items": [...]}``; any legacy ``passing_threshold`` in *raw* is dropped.

    Raises
    ------
    ValueError
        When the rubric is structurally invalid.
    """
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("rubric must have a non-empty 'items' list")

    normalized_items: list[dict[str, Any]] = []
    total_weight = 0.0
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"rubric items[{i}] must be a dict")
        for field in ("id", "description", "weight", "rubric"):
            if field not in item:
                raise ValueError(f"rubric items[{i}] missing required field '{field}'")
        weight = float(item["weight"])
        if weight <= 0:
            raise ValueError(f"rubric items[{i}].weight must be positive")
        total_weight += weight
        normalized_items.append({
            "id": str(item["id"]),
            "description": str(item["description"]),
            "weight": weight,
            "rubric": str(item["rubric"]),
        })

    if abs(total_weight - 1.0) > 0.01:
        raise ValueError(f"rubric item weights must sum to 1.0, got {total_weight:.4f}")

    # No passing_threshold: the oracle is the pass/fail gate (a stage that failed
    # its oracle never reaches the rubric LLM), so the rubric produces only a
    # 0-1 quality score. A legacy passing_threshold in *raw* is ignored.
    return {"items": normalized_items}


def load_rubric_file(path: str | Path) -> dict[str, Any]:
    """Load and normalize a rubric from a YAML or JSON file.

    The file must match the rubric schema (a non-empty ``items`` list whose
    weights sum to 1.0). Used by the judge's
    ``--rubric`` option so oracle-passing stages are scored against custom
    weighted criteria (0.0-1.0) instead of a flat full-marks pass.
    """
    import yaml

    p = Path(path)
    try:
        text = p.read_text()
    except FileNotFoundError:
        raise ValueError(f"rubric file not found: {p}")
    return load_rubric_text(text)


def load_rubric_text(text: str) -> dict[str, Any]:
    """Load and normalize a rubric from a YAML/JSON string.

    Used by the HTTP judge routes, which receive the rubric file's *content*
    from the browser (so YAML and JSON both work) rather than a server path.
    """
    import yaml

    raw = yaml.safe_load(text)  # YAML is a JSON superset
    if not isinstance(raw, dict):
        raise ValueError("rubric must be a mapping with an items[] list")
    return normalize_rubric(raw)


def merge_rubric_overrides(
    base: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Return *base* rubric with *overrides* merged in.

    Override items are matched by ID. Matching base items are replaced
    by the override item. Non-matching override items are appended, and
    all weights are renormalized to sum to 1.0.
    """
    from copy import deepcopy
    result = deepcopy(base)
    base_by_id = {item["id"]: i for i, item in enumerate(result["items"])}

    for override_item in (overrides.get("items") or []):
        oid = override_item.get("id")
        if oid in base_by_id:
            result["items"][base_by_id[oid]] = dict(override_item)
        else:
            result["items"].append(dict(override_item))

    # Renormalize weights
    total = sum(item.get("weight", 0.0) for item in result["items"])
    if total > 0:
        for item in result["items"]:
            item["weight"] = round(item.get("weight", 0.0) / total, 6)

    return result
