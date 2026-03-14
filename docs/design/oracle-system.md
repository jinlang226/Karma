# Oracle System

## Overview

The Oracle System is the invariant validation backbone of KARMA.

Its primary purpose is stateless invariant validation (`verify()`).

In workflow mode, the Oracle is invoked during:

- Runtime stage execution
- Final regression sweep (observed-only)

The Oracle is not a downstream component. It is a shared invariant subsystem that defines what correctness means for each test case.

---

## Design Philosophy

KARMA treats each test case as a transition toward a **state invariant**.

A stage is correct if and only if its invariant holds.

Rather than relying on implicit success signals (e.g., exit codes or log strings), KARMA requires explicit invariant definition via the Oracle.

This makes correctness:

- Deterministic
- Observable
- Composable across workflow stages

---

## Core Interfaces

Each test case may define:

```yaml
oracle:
  verify:
    commands:
      - command: ["python3", "oracle.py"]
    hooks:
      before_commands:
      - command: ["kubectl", "wait", "--for=condition=ready", "pod/test"]
      after_commands:
      - command: ["kubectl", "logs", "test"]
      after_failure_mode: warn
```

This maps to one primary operation:

- `oracle.verify()` — validate invariant against current state

---

## Why `verify()` Exists

### Stateless Invariant Validation

`verify()` is the canonical correctness check.

It answers:

> Does the current cluster state satisfy the invariant?

It must be:

- **Stateless** (does not depend on prior execution history)
- **Deterministic** (given identical cluster state)
- **Observational** (should not repair state)

`verify()` ensures that:

- Agent success is measured against actual system state.
- Stage correctness can be independently re-evaluated.
- Workflow composition has stable invariant boundaries.

---

### Structure of `verify()`

`verify()` may include:

- `hooks.before_commands` (optional) — readiness or stabilization checks
- `commands` (required) — core invariant validation
- `hooks.after_commands` (optional) — logging or diagnostics
- `hooks.after_failure_mode` (optional) — `warn` or `fail`

The main `commands` block must determine pass/fail.

The Oracle does not attempt to repair failed runtime states.

---

## Optional Deterministic Setup (Outside Oracle)

Some cases still benefit from deterministic setup helpers for authoring
or fixtures.

Those commands should live outside the Oracle contract (for example in
preconditions) so Oracle verification remains purely observational.

---

## Straightforward Verification vs Extra Setup

There are two practical validation patterns.

### 1. Straightforward Verification

For simple tasks:

- The invariant is directly checkable.
- No canonicalization required.
- `verify()` alone may be sufficient.

Examples:

- "Service returns HTTP 200"
- "Three replicas are ready"
- "Queue contains N messages"

These are purely observational invariants.

### 2. Complex Tasks with Extra Setup

For complex transitions:

- Version upgrades
- Migration scenarios
- Multi-cluster state reconciliation

State may need setup or normalization before verification is meaningful.

In these cases:

- preconditions or setup jobs establish required baseline state.
- `verify()` confirms success.

---

## Oracle in Workflow Mode

### Runtime

During execution:

- The agent attempts to satisfy the invariant.
- `verify()` determines stage success.
- Failure is attributed to agent behavior.
- The Oracle does not silently repair runtime failures.

---

### Final Sweep

After all stages complete (`final_sweep_mode=full`, default):

- `verify()` is re-invoked.
- Cross-stage regressions are observed.
- Raw final-sweep outcomes are reported for deterministic inspection.

This ensures later stages do not silently break earlier invariants.

When `final_sweep_mode=off`, final-sweep oracle execution is skipped and only per-stage runtime verification is used.

---

## Design Guarantees

The Oracle System provides:

- Explicit invariant specification
- Stateless correctness validation
- Workflow-compatible stage composition

It does not guarantee:

- Semantic minimality of canonical states
- Formal proof of transition correctness
- Isolation of cluster-scoped resources

These are treated as modeling boundaries.

---

## Separation from LLM-as-Judge

The Oracle answers:

> Is the task correct?

The LLM-as-Judge answers:

> Was the reasoning process good?

This strict separation ensures:

- Correctness is deterministic and cluster-grounded.
- Behavioral scoring does not affect invariant validation.

---

## Summary

The Oracle System formalizes correctness in KARMA through:

- `verify()` — stateless invariant validation

Together, these mechanisms enable:

- Deterministic stage composition
- Cross-stage regression detection
- Clear separation between correctness and trajectory evaluation

The Oracle is a foundational subsystem that makes composable agent benchmarking feasible in complex Kubernetes microservice environments.
