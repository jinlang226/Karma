# KARMA Architecture

## Architectural Overview

KARMA is a layered evaluation framework for composable Kubernetes agent benchmarking.  
It separates workflow definition, runtime stage setup, agent execution, invariant verification, and behavioral evaluation into clearly defined components.

The system is organized around three primary execution phases:

1. Agent Runtime Execution  
2. Final Regression Sweep  
3. Trajectory-Based Evaluation  

The Oracle Engine is a shared invariant system invoked across multiple phases.

---

# High-Level Architecture

                     ┌──────────────────────────────┐
                     │     Workflow Definition      │
                     └──────────────┬───────────────┘
                                    │
                                    ▼
          ┌────────────────────────────────────────────────┐
          │                CONTROL PLANE                   │
          │                                                │
          │  ┌──────────────────────────────────────────┐  │
          │  │      Workflow Runtime Setup              │  │
          │  │  - Resolve namespace roles               │  │
          │  │  - precondition probe/apply/verify       │  │
          │  └──────────────────────────────────────────┘  │
          │                                                │
          │  ┌──────────────────────────────────────────┐  │
          │  │          Final Sweep                     │  │
          │  │  - oracle.verify()                       │  │
          │  │  - Observed regression reporting         │  │
          │  └──────────────────────────────────────────┘  │
          └────────────────────────────────────────────────┘
                                    │
                                    ▼
          ┌────────────────────────────────────────────────┐
          │               EXECUTION PLANE                  │
          │                                                │
          │  ┌──────────────────────────────────────────┐  │
          │  │        Agent Runtime Executor            │  │
          │  │  - Run agent against cluster             │  │
          │  │  - oracle.verify()                       │  │
          │  └──────────────────────────────────────────┘  │
          └────────────────────────────────────────────────┘
                                    │
                                    ▼
          ┌────────────────────────────────────────────────┐
          │               EVALUATION PLANE                 │
          │                                                │
          │  ┌──────────────────────────────────────────┐  │
          │  │            LLM-as-Judge                  │  │
          │  │  - Trajectory analysis                   │  │
          │  │  - Rubric scoring                        │  │
          │  └──────────────────────────────────────────┘  │
          └────────────────────────────────────────────────┘


Oracle Engine (shared subsystem):
  • verify() — stateless invariant validation

The Oracle Engine is invoked by:
  - Agent Runtime Executor
  - Final Sweep (`final_sweep_mode=full`; optional)


**Note:**  
The Oracle Engine is invoked during runtime and (when enabled) final sweep.  
It is not a downstream component, but a shared invariant subsystem.

---

## Component Interaction Model

| Component | Calls / Produces | Uses | Outputs |
|---|---|---|---|
| Workflow Runtime Setup | precondition `probe/apply/verify` | Workflow definition, params, namespace roles | Stage-ready environment (live cluster state) |
| Agent Runtime Executor | Runs agent + `oracle.verify()` | Namespace bindings, live stage state | Stage results, run artifacts (logs, metrics, snapshots) |
| Final Sweep | `oracle.verify()` (re-check) | Final cluster state | Observed regression report (which stage invariants broke) |
| LLM-as-Judge | Rubric scoring + citation checks | Run artifacts + rubric overlays | Trajectory scores (process quality, efficiency, anti-patterns) |
| Oracle Engine (shared) | `verify()` | Stage definition + namespace context | Invariant verdict |

## Core Architectural Layers

### 1. Workflow Definition Layer

Defines ordered stages composed of:

- Task template
- Parameter bindings
- Namespace roles
- Preconditions
- Oracle definitions

Workflows are declarative and environment-agnostic.

---

### 2. Workflow Runtime Setup

Workflow runtime setup prepares each stage for agent execution by:

- Resolving namespace roles into concrete namespaces
- Running precondition resource groups (`probe -> apply -> verify`)
- Establishing stage-ready live cluster state

This phase guarantees deterministic setup behavior without requiring a
separate pre-run witness step.

---

### 3. Agent Runtime Executor

The runtime executor:

- Initializes stage execution context
- Runs the agent against the live Kubernetes cluster
- Captures logs, traces, and metrics
- Invokes `oracle.verify()` after agent execution

Each stage is evaluated independently.

---

### 4. Final Sweep

After all stages complete:

- Re-run `oracle.verify()` for each stage
- Detect cross-stage regressions
- Record observed invariant outcomes after workflow completion

This ensures later stages do not silently corrupt earlier guarantees.

---

### 5. LLM-as-Judge Layer

The LLM-as-Judge layer evaluates agent behavior beyond binary correctness.

Inputs include:

- `agent.log`
- Command traces
- Resource snapshots
- External metrics
- Token usage
- Rubric definitions

Outputs include:

- Structured scoring JSON
- Citation integrity validation
- Weighted objective scores

This layer evaluates *how* the agent solved the task, not just whether it succeeded.

---

## Architectural Properties

KARMA enforces:

- Deterministic stage setup and invariant verification
- Stateless invariant verification
- Cross-stage regression awareness
- Namespace role isolation via explicit stage namespace bindings
- Separation between correctness verification and behavioral scoring
