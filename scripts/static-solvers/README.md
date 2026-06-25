# Static Solvers

This directory holds the static workflow solver corpus described in
[docs/developer/static-solver-plan.md](/Users/jimmyouyang/code/Karma/docs/developer/static-solver-plan.md).

## Rules

- Do not edit `cases/**`.
- Do not edit `workflows/**`.
- Vendored solver sources copied from `import-improve-resources` stay unchanged
  under `vendor/` for provenance.
- Do not automatically rewrite imported solver sources. Active solver scripts
  may copy them one-by-one and be edited deliberately.
- Generated workflow plan files live under `plans/workflows/**` and should keep
  the same relative names as `workflows/**`.
- Only workflows that actually work should be promoted beyond `candidate`
  support status.

## Main Areas

- `bin/`
  - runner entrypoint for the current workflow contract
- `lib/`
  - shared runtime helpers
- `solvers/`
  - active bash case solvers
- `registry/`
  - case and workflow support metadata
- `tools/`
  - inventory, import, registry, and generation scripts
- `vendor/`
  - copied sources from `import-improve-resources`
- `generated/manifests/`
  - derived inventory and support reports

## Current Implementation Target

The first implementation target is the current branch runtime:

- `--sandbox local`
- `--agent-cmd`
- stage-reentrant solver execution

The workflow plan contract is kept runner-agnostic so the same plans can later
be interpreted by a restored long-lived workflow runner if needed.

## Tooling Note

The scripts under `tools/` are build-time scaffolding. They can be removed once
the generated static solver corpus is stable and fully reviewed.
