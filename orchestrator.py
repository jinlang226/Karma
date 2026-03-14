#!/usr/bin/env python3
"""Thin entrypoint + compatibility shim for orchestrator runtime."""

from app.orchestrator_core import runtime_glue as _runtime_glue

# Stable public contract:
# - `python3 orchestrator.py ...` CLI entrypoint behavior
# - `orchestrator.main`
#
# Compatibility guardrail (current state):
# - Compatibility exports are retired (`_COMPAT_EXPORTS == ()`).
# - Keep the explicit tuple so accidental re-introduction remains auditable.
_COMPAT_EXPORTS = ()

# Keep compatibility exports explicit (no wildcard re-export) so retirement is
# auditable and testable.
for _name in _COMPAT_EXPORTS:
    globals()[_name] = getattr(_runtime_glue, _name)

__all__ = ["main", *_COMPAT_EXPORTS]


def main():
    return _runtime_glue.main()


if __name__ == "__main__":
    raise SystemExit(main())
