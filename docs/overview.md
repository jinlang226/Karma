# KARMA: Kubernetes Agent Runtime Measurement Architecture

## Overview

KARMA (Kubernetes Agent Runtime Measurement Architecture) is a framework for composable, large-scale evaluation of AI agents operating over Kubernetes microservice lifecycles.

AI agents are increasingly applied to operational domains such as deployment, configuration, debugging, recovery, upgrade, and migration of distributed systems. However, existing benchmarks are often:

- Static and monolithic  
- Difficult to compose across tasks  
- Hard to reproduce across environments  
- Limited to binary success/failure metrics  

KARMA addresses these limitations by introducing a structured, invariant-driven workflow model for agent benchmarking in real Kubernetes environments.

---

## Core Idea

KARMA models each task as a **stage invariant** within a composable workflow.

A stage defines:

- Preconditions  
- A stateless verifier (`verify`)  

Rather than treating benchmarks as standalone scripts, KARMA composes stages into multi-step workflows (e.g., deploy → upgrade → migrate → recover). During runtime, stage boundaries are resolved live using precondition `probe -> apply -> verify`, the agent interacts with Kubernetes resources, and stage invariants are verified deterministically.

This design enables:

- Safe task composition  
- Automatic precondition resolution  
- Regression detection across stages  
- Reproducible state transitions  

---

## System Workflow

KARMA operates in three runtime phases (plus optional authoring/self-check tooling):

### 1. Agent Runtime Execution

- Agent executes actions against the cluster
- Stage setup uses precondition `probe -> apply -> verify`
- Stage invariants are verified using `verify`
- Artifacts and metrics are recorded

### 2. Final Regression Sweep

- Re-verify stage invariants (`final_sweep_mode: full`, default)
- Detect observed cross-stage regressions
- Record raw sweep outcomes for deterministic reporting and optional higher-level analysis

For workflows that intentionally reset state between stage segments, `final_sweep_mode: off` can be used to skip the terminal sweep while keeping per-stage verification behavior unchanged.

### 3. Trajectory-Based Evaluation

- Capture agent logs, command traces, and metrics
- Evaluate process quality and efficiency
- Use LLM-as-Judge with structured rubrics and citation validation

---

## Key Features

### Composable Workflow Model

Stages can be chained into longer operational scenarios without duplicating setup logic.

### Namespace Role Isolation

Stages operate within isolated or shared namespace roles, enabling deterministic boundaries and parallel execution.

### Parameterized Task Templates

Tasks are defined as reusable templates with validated parameters, enabling systematic evaluation across configuration spaces.

### Invariant-Based Oracles

Each stage defines executable invariants through structured verification scripts, enabling deterministic correctness checks.

### Trajectory Capture and LLM-Based Evaluation

Agent trajectories are recorded in detail and evaluated using structured rubrics, allowing assessment beyond binary success metrics.

---

## Design Principles

KARMA is built around the following principles:

- **Determinism first** — Stage invariants and stateless verification define correctness.  
- **Separation of concerns** — Environment setup, verification, and behavioral scoring are independent layers.  
- **Composability** — Tasks scale from single-step checks to multi-stage workflows.  
- **Isolation by explicit role design** — Namespace virtualization prevents unintended coupling when stages use distinct namespace aliases/roles.  
- **Extensibility** — New services and evaluation strategies can be added without modifying core logic.  

---

## Scope

KARMA currently focuses on Kubernetes microservice lifecycle evaluation. It is designed as a structured evaluation substrate rather than a simulator or formal verification system.

Ongoing efforts include:

- Formal schema validation and conformance enforcement  
- Strengthening grounding and stability of LLM-based judging  
- Expanding microservice coverage  
- Exploring extensions beyond Kubernetes environments  

---

## Getting Started

Run a sample workflow:

```bash
python3 orchestrator.py workflow-run \
  --workflow workflows/workflow-demo.yaml
```

Terminate workflow immediately on first non-retryable stage failure:

```bash
python3 orchestrator.py workflow-run \
  --workflow workflows/workflow-demo.yaml \
  --stage-failure-mode terminate
```

Notes:
- Default behavior is `continue` (advance after non-retryable stage failures).
- CLI flag accepts `inherit|continue|terminate` and overrides workflow YAML when set.

## Further Reading

- Architecture details: `docs/architecture.md`
- Workflow model details: `docs/design/workflow-model.md`
- Agent progress contract: `docs/design/workflow-agent-progress-contract.md`
- Maintainer code map: `docs/developer/internals.md`
