"""
karma.adversary -- Adversary injection subpackage.

Two submodules with one owner each:

``definitions``
    Scenario loading, param resolution, YAML validation, and per-stage
    operation collection. No runtime imports.

``runtime``
    Deploy, lift, and report lifecycle API called by ``runtime.case``
    at the appropriate stage execution points. No ``runtime.*`` imports.

All public symbols are re-exported here so callers import from
``karma.adversary`` only, never from the submodules directly.
"""

from .definitions import (
    validate_adversary_workflow_block,
    resolve_adversary_scenario,
    collect_stage_operations,
    collect_pending_lift_units,
    collect_stage_hint,
)
from .runtime import deploy, lift, report

__all__ = [
    "validate_adversary_workflow_block",
    "resolve_adversary_scenario",
    "collect_stage_operations",
    "collect_pending_lift_units",
    "collect_stage_hint",
    "deploy",
    "lift",
    "report",
]
