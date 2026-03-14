# Debugger UI Design

## Purpose

The Debugger UI exists to make benchmark cases and workflows easier to iterate on, validate, and diagnose without
manually digging through run artifacts. It provides a single place to:

- Build and run workflows with clear stage boundaries.
- Run a single case with manual submit/cleanup loops.
- Inspect logs, prompts, and run artifacts in real time.
- Trigger judge evaluations and inspect scoring outputs.

The intent is to reduce friction when authoring cases, verifying invariants, and debugging failures across the
workflow lifecycle.

---

## Modes

The UI is organized around three primary modes, each optimized for a specific development and debugging task.

### 1. Manual Runner (Base Case)

**Goal:** Rapid iteration on a single case.

Capabilities:
- Select a service + case and start a run.
- Inspect live logs and prompt output.
- Manually submit or cleanup a run via the UI.
- Validate preconditions, oracles, and cleanup behavior in isolation.

Why it helps:
- Shortest loop for validating a new or edited test case.
- Eliminates the need to craft CLI commands during iteration.

### 2. Workflow

**Goal:** Debug multi-stage workflows and namespace isolation.

Capabilities:
- Workflow Builder: construct workflow YAML via a visual form.
- Workflow Runner: execute an existing workflow spec and watch stage transitions.
- Stage-level logs, prompt blocks, and namespace bindings.
- Manual stage submit/cleanup in debug mode (local submit.signal path).

Why it helps:
- Makes cross-stage regressions visible.
- Highlights namespace role bindings and stage invariants.
- Allows step-by-step verification of a workflow chain without CLI complexity.

### 3. Judge

**Goal:** Evaluate run trajectories and judge outputs.

Capabilities:
- Submit runs or batches for judging.
- View judge status, scores, and artifacts.
- Inspect structured output and trace summary.

Why it helps:
- Confirms that judging configurations are correct.
- Makes it easier to compare runs and validate scoring stability.

---

## Debugging Principles

The UI is designed around these debugging principles:

- **Visibility first:** show prompts, logs, and artifacts inline to reduce context switching.
- **Manual control:** allow explicit submit/cleanup triggers so operators can test edge cases.
- **Minimal CLI dependence:** the UI should mirror CLI capabilities but reduce friction for rapid iteration.
- **Workflow parity:** workflow-stage debug should match the single-case debug experience.

---

## Expected User Outcomes

After using the Debugger UI, a developer should be able to:

- Validate that a case’s preconditions and oracle behave as intended.
- Debug workflow transitions and isolate which stage fails.
- Confirm namespace bindings and parameter substitution.
- Inspect judge results without manual file inspection.

---

## Out of Scope (for now)

- Authoring new case YAMLs directly in the UI.
- Managing cluster setup or dependencies.
- Full workflow persistence from the builder (preview-only by design).

