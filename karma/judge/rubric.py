"""
Rubric loading, normalization, and override merging.

A rubric defines the scoring criteria used by the judge LLM. It contains
a list of weighted items with descriptions and guidance strings, plus a
passing threshold.

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
        ],
        "passing_threshold": float      # minimum score for a "pass" verdict
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


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
    ...


def normalize_rubric(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a raw rubric dict.

    Verifies that all items contain required fields, that weights are
    positive and sum to 1.0 within floating-point tolerance, and that
    ``passing_threshold`` is in ``[0.0, 1.0]``.

    Raises
    ------
    ValueError
        When the rubric is structurally invalid.
    """
    ...


def merge_rubric_overrides(
    base: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Return *base* rubric with *overrides* merged in.

    Override items are matched by ID. Matching base items are replaced
    by the override item. Non-matching override items are appended, and
    all weights are renormalized to sum to 1.0.
    """
    ...
