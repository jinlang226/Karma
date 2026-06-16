"""Minimal stderr warning helper for best-effort operations.

Best-effort steps (namespace teardown, metadata/state writes, the regression
sweep) must not crash a run, but a silent failure hides real problems from the
operator (leaked namespaces, stale UI state, a dropped sweep). These paths call
``warn`` so the failure is at least visible, without introducing a logging
framework or changing control flow.
"""
from __future__ import annotations

import sys


def warn(message: str) -> None:
    """Print a one-line warning to stderr (never raises)."""
    try:
        print(f"warning: {message}", file=sys.stderr, flush=True)
    except Exception:
        pass
