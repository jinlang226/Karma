# Workflow Model

## Overview

The KARMA workflow model enables independent test cases to be composed
into longer, realistic multi-stage operational scenarios.

A workflow is a linear sequence of stages. Each stage represents a
parameterized test case with:

-   Preconditions
-   Agent execution
-   Oracle verification

The workflow model ensures:

-   Deterministic stage boundaries
-   Safe stage composition
-   Automatic precondition skipping
-   Cross-stage regression observation via final sweep

For exact agent-facing runtime behavior (`PROMPT.md` regeneration, submit/advance
signals, and `concat_blind` visibility), see:
`docs/design/workflow-agent-progress-contract.md`.

------------------------------------------------------------------------

## Stage Structure

Each stage consists of:

1.  Preconditions (resource groups)
2.  Agent execution (runtime only)
3.  Oracle verification

------------------------------------------------------------------------

## Preconditions as Resource Groups

Preconditions are not defined as a flat list of commands.\
Instead, they are organized into **resource groups**.

Each resource group represents one logical resource mutation and
contains three phases:

-   `probe` --- Check whether the desired resource state already exists
-   `apply` --- Create or modify the resource if needed
-   `verify` --- Confirm that the resource now satisfies the intended
    condition

Conceptually:

    if probe succeeds:
        skip apply
    else:
        apply
        verify

This structure makes preconditions idempotent and safe to reuse across
stages.

------------------------------------------------------------------------

## Why Probe / Apply / Verify Is Necessary

Without probe logic, preconditions would always execute, which can
cause:

-   Duplicate resource creation
-   Invalid upgrades
-   State instability
-   Cross-stage interference

By introducing `probe`, KARMA can detect whether a prior stage has
already satisfied part of the next stage's requirements.

This is essential for workflow composition.

------------------------------------------------------------------------

## Runtime Stage Setup

Before each stage executes, KARMA performs live stage setup using the
precondition resource groups:

1.  Run `probe`
2.  If probe fails, run `apply`
3.  Run `verify`

This keeps stage boundaries runtime-grounded and allows carryover state
from prior stages to satisfy later preconditions without a separate
pre-run witness step.

------------------------------------------------------------------------

## Precondition Skipping Across Stages

The key question:

> How does KARMA know when to skip a precondition in stage N if it was
> satisfied by stage N-1?

The answer lies in the probe mechanism at runtime.

During workflow execution:

-   Stage N-1 leaves the cluster in some observed state (agent-produced or
    precondition-applied).
-   When Stage N runs, its resource group `probe` checks the cluster.
-   If probe succeeds, `apply` is skipped automatically.

No manual dependency tracking is required.

Skipping works because:

-   Preconditions are defined as state checks, not execution steps.
-   Stage boundaries are defined by invariants.
-   Probe checks are evaluated against the live cluster state.

Thus, probe correctness determines skip correctness.

------------------------------------------------------------------------

## Why Runtime Probe / Apply / Verify Works

Runtime precondition groups treat setup as a state reconciliation step,
not a one-time script.

This ensures:

-   Redundant setup is automatically eliminated when `probe` succeeds.
-   Carryover state from earlier stages can satisfy later prerequisites.
-   Setup behavior is based on the actual cluster state produced during the run.

This model depends on strong probe and verify logic, not on a separate
compile artifact.

------------------------------------------------------------------------

## Runtime Execution

During runtime:

1.  Preconditions execute using probe/apply/verify logic.
2.  Agent attempts to solve the stage.
3.  `oracle.verify()` determines success or failure.

Stage failure policy is controlled by `spec.stage_failure_mode` (`continue|terminate`, default `continue`):

- `continue`: non-retryable stage failures are recorded and workflow can advance.
- `terminate`: first non-retryable stage failure ends the workflow immediately.

If the agent corrupts state:

-   Verification fails.
-   Failure is attributed to agent behavior.
-   The framework does not silently repair state.

------------------------------------------------------------------------

## Final Sweep

After all stages complete, KARMA can run a final sweep (`spec.final_sweep_mode: full|off`, default `full`).

When `final_sweep_mode=full`:

-   `oracle.verify()` is re-run for earlier stages.
-   Cross-stage regressions are observed and reported.
-   Raw final-sweep outcomes are recorded for deterministic reporting.

When `final_sweep_mode=off`:

-   Final sweep execution is skipped.
-   Per-stage runtime verification during submit still applies.
-   A `workflow_final_sweep.json` artifact is still written with `status: skipped` for stable downstream consumption.

This prevents later stages from silently breaking earlier guarantees.

------------------------------------------------------------------------

## Design Properties

The workflow model guarantees:

-   Deterministic invariant boundaries
-   Idempotent preconditions
-   Automatic skip logic
-   Explicit state modeling
-   Separation between stage correctness and trajectory evaluation

It does not guarantee:

-   Automatic reasoning over cluster-scoped resources
-   Formal state transition proofs
-   Automatic detection of semantically incompatible parameter
    combinations

------------------------------------------------------------------------

## Summary

The KARMA workflow model composes test cases through:

-   Resource-group-based preconditions (probe/apply/verify)
-   Stateless invariant validation via `oracle.verify()`
-   Final sweep regression observation

Precondition skipping works because resource groups encode desired
state, not execution history.\
Subsequent stages detect satisfied conditions through probe checks on the
live cluster state.

This model enables flexible, deterministic, and scalable composition of
Kubernetes microservice lifecycle tasks.
