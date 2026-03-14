# LLM-as-Judge and Trajectory Evaluation

## Overview

KARMA extends invariant-based correctness verification with a structured LLM-based trajectory evaluation system.

While stage oracles determine whether a task invariant holds, they do not assess:

- Quality of reasoning
- Hypothesis formation
- Debugging methodology
- Efficiency
- Safety of actions

The LLM-as-Judge subsystem evaluates *how* the agent solved a task, using captured execution artifacts and a layered rubric system.

The judge operates strictly on recorded artifacts and does not influence runtime execution.

---

## Design Goals

The LLM-as-Judge subsystem is designed to:

- Evaluate process quality beyond binary correctness
- Ground evaluation in observable artifacts
- Enforce structured output
- Remain deterministic under fixed settings
- Support extensible, layered rubric definitions

It is not intended to provide formal verification or semantic proof, but rather structured behavioral assessment.

---

## System Architecture

The LLM-as-Judge subsystem consists of:

- Trajectory Capture Layer
- Judge Input Builder
- Rubric Resolution Engine
- LLM Client
- Evidence Validation Layer
- Scoring Engine

These components run from recorded run artifacts in either:

- `post-run` mode (judge as each run completes), or
- `post-batch` mode (judge after batch completion).

---

## 1. Trajectory Capture

During runtime, KARMA captures structured artifacts for each stage:

### Captured Artifacts

- `agent.log` — complete agent command and output log
- `action_trace.jsonl` — structured kubectl execution traces (if enabled)
- `external_metrics.json` — performance metrics (e.g., time-to-success, mutation counts)
- `agent_usage.json` — token usage and API statistics
- `snapshot_*.json` — Kubernetes resource state snapshots
- `meta.json` — workflow metadata and configuration

Artifacts are stored per-run and serve as the sole input source for the judge.

The judge does not access live cluster state.

---

## 2. Rubric System

Evaluation is driven by structured rubrics.

Rubrics define:

- Objective weights (e.g., process quality vs efficiency)
- Question prompts
- Track assignments
- Milestones
- Anti-patterns

### Layered Rubric Resolution

Rubrics are constructed by merging multiple layers in deterministic order:

1. Default baseline rubric
2. Global defaults
3. Service-level defaults
4. Profile-specific configuration
5. Case-level overrides

Later layers override earlier definitions by question ID.

This design enables domain-specific evaluation without modifying core logic.

---

## 3. Judge Input Construction

The Judge Input Builder constructs a structured prompt from:

- Problem statement
- Agent trajectory artifacts
- Extracted efficiency facts
- Rubric configuration

The builder performs:

- Artifact loading
- Structured summarization
- Fact extraction (e.g., command count, time-to-first-mutation)
- Context truncation if needed

The resulting input is formatted into a constrained system prompt.

---

## 4. Dynamic Prompt Generation

Judge prompts are dynamically generated per run.

The generated prompt includes:

1. Task description  
2. Stage outcome (optional; enabled with `--judge-include-outcome`)  
3. Relevant artifact excerpts  
4. Efficiency metrics  
5. Rubric questions  
6. Instructions for structured JSON output  

### Example Prompt Structure

```text
You are evaluating an AI agent's performance.

Use only the provided artifacts.

For each question:
- Assign a score
- Provide reasoning
- Cite evidence using evidence_ids

Return output as structured JSON.
```
---

## 5. Evidence and Citation Integrity

The judge must cite evidence for each score.

Evidence references may include:

- `agent.log:L120-L140`
- `external_metrics:time_to_success_seconds`
- `agent_usage:totals.input_tokens`

Current validation guarantees:

- Referenced artifact exists
- Line ranges are in bounds
- Metric paths exist in data structure

This enforces citation integrity (existence validation).

It does not yet guarantee semantic correctness of the cited content.

---

## 6. Scoring Model

The judge produces structured JSON containing:

- Per-question scores
- Per-question rationale
- Evidence references
- Track-level aggregation
- Final weighted score

Scores are aggregated using objective weights defined in the rubric.

Example objective weighting:

- Process Quality: 0.7
- Efficiency: 0.3

The scoring engine computes final scores deterministically.

---

## 7. Rubric-Driven Classifiers

Beyond scalar scores, rubrics may define optional classifier blocks that map judged evidence to case-specific labels (for example awareness classes such as `explicit`, `implicit`, `none`).

Classifier behavior is intentionally case-configurable:

- classifier definitions are loaded from the same layered rubric merge path
- no label taxonomy is hard-coded in framework logic
- rules can reference:
  - question outputs (`q.<question_id>.score|confidence|has_evidence|evidence_count`)
  - aggregate scores (`s.final_score`, `s.process_quality_score`, `s.efficiency_score`)
  - workflow facts (`w.workflow_enabled`, `w.active_stage_id`, regression counts)
  - run meta fields (`m.*`)

Rule evaluation:

- first matching rule wins
- if no rule matches, classifier falls back to `default_label`
- optional `unknown_policy` can force an `unknown` label when evidence is missing or confidence is too low
- optional scope gates (`workflow_only`, `stage_ids`) allow stage-specific labeling in workflow mode

Classifier outputs are written into `judge/result_v1.json` under:

- `classifications.<classifier_id>.label`
- `classifications.<classifier_id>.rule_id`
- `classifications.<classifier_id>.confidence`
- `classifications.<classifier_id>.evidence_ids`
- `classifications.<classifier_id>.status`

This enables behavior taxonomy to evolve per testcase/profile without framework code edits.

---

## 8. Determinism and Stability

To reduce evaluation variance:

- Sampling temperature is fixed to 0
- Structured JSON output is required
- Rubric merging is deterministic
- Artifact inputs are fixed

Cross-run stability is expected under identical artifacts and model version.

Future improvements may introduce calibration protocols and variance measurement.

---

## 9. Interaction with Oracle

The LLM-as-Judge is independent of invariant verification.

Oracle determines:

- Whether the task invariant holds.

LLM-as-Judge determines:

- How well the agent reached that outcome.

This separation ensures correctness validation is not dependent on LLM evaluation.

---

## 10. Design Boundaries

The current LLM-as-Judge system:

- Enforces citation existence validation
- Uses structured prompts and deterministic settings
- Supports layered domain-specific rubrics

It does not yet:

- Verify semantic grounding of cited evidence
- Guarantee calibration against human raters
- Provide formal correctness guarantees

These boundaries are explicit and may be strengthened in future iterations.

---

## 11. Extensibility

The LLM-as-Judge system supports:

- Custom service-level rubrics
- Profile-based overlays
- Case-level overrides
- New evaluation tracks
- Alternative scoring strategies

Core orchestration logic does not need modification to extend rubric logic.

---

## Summary

The LLM-as-Judge subsystem provides structured, rubric-driven evaluation of agent trajectories grounded in captured execution artifacts.

By separating invariant verification (Oracle) from behavioral evaluation (Judge), KARMA enables both deterministic correctness validation and qualitative process assessment within a unified benchmarking framework.
