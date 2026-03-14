# Workflow Agent Progress Contract

## Purpose

This document defines the **current runtime contract** between the orchestrator and an agent in workflow mode.

It answers:

- What happens after a submit
- When stage advance occurs
- When prompt/state files are regenerated
- What is different for `progressive`, `concat_stateful`, and `concat_blind`

This is implementation-facing and should match runtime behavior.

## Files and Signals

During a workflow run, the agent should treat these as canonical:

- `PROMPT.md` (in `agent_bundle/`): current human-readable workflow prompt
- `submit.signal` (in `agent_bundle/`): agent submit trigger file
- `submit.ack` (in `agent_bundle/`): orchestrator receipt marker for the current submit
- `submit_result.json` (in `agent_bundle/`): orchestrator response for the latest submit
- `WORKFLOW_STATE.json` (in `agent_bundle/`): state mirror, only written in `concat_stateful`
- `workflow_state.json` (in workflow run dir): full machine state, always written
- `workflow_transition.log` (in workflow run dir): stage transition log
- `stage_runs/` (in workflow run dir): per-stage run artifact directories
  (for example `stage_runs/01_stage_x/preoperation.log`, `verification_1.log`)

## Stage Lifecycle

For each stage, runtime flow is:

1. Stage setup runs (`probe -> apply(if needed) -> verify`) and waits for `ready`.
2. Agent receives/reads `PROMPT.md`.
3. Agent writes `submit.signal`.
4. Orchestrator consumes `submit.signal`, clears stale `submit_result.json`, and writes `submit.ack`.
5. Orchestrator runs verification for that stage and writes a fresh `submit_result.json`.
6. Orchestrator decides one branch:
   - Retry same stage (`can_retry=true`)
   - Advance to next stage (`continue=true`)
   - Terminal/fatal end (`final=true`)

Non-retryable stage failures follow workflow policy:
- `stage_failure_mode=continue` (default): may advance to next stage with failed stage outcome recorded.
- `stage_failure_mode=terminate`: ends workflow immediately on first non-retryable stage failure.

Important: stage setup for the next stage runs **before** the advance is confirmed to the agent. If next-stage setup fails, workflow ends with `next_stage_setup_failed`.

## Precondition Skip Behavior

There is no explicit cross-stage skip list injected during normal stage advance.

Skipping is state-driven:

- If a precondition unit `probe` succeeds, its `apply` is skipped.
- If `probe` fails, `apply` then `verify` run.

So "skip" is achieved by probe correctness against live cluster state, not by hardcoded stage-to-stage skip rules.

## Prompt Regeneration Semantics

`PROMPT.md` is republished on:

- Initial stage readiness
- Successful stage advance to a new active stage
- Terminal/fatal state publication

`PROMPT.md` is **not** republished on retryable failure (`can_retry=true`): in that case, orchestrator only writes `submit_result.json` and waits for another submit on the same stage.

In `concat_blind`, prompt content may look unchanged across advances because active-stage markers are intentionally hidden.

## Prompt Layout Order

The runtime prompt layout is:

1. `# workflow/<name>`
2. `Execution Protocol`
3. `Feedback Files`
4. `Post-Run Validation`
5. `Workflow Summary`
6. `Prompt Mode`
7. Stage content (`progressive` or `All Stages`)
8. `Submission`
9. `Tools`

`Post-Run Validation` is mode-aware:
- `final_sweep_mode=full` (default): final full-sweep notice across all workflow stages + drift preference guidance.
- `final_sweep_mode=off`: explicit notice that terminal sweep is disabled for this workflow run.

## Prompt Modes (Agent-Visible)

### `progressive`

- Shows only current stage block
- Shows `Active Stage: i/N (...)`
- No bundled `WORKFLOW_STATE.json`

### `concat_stateful`

- Shows all stages
- Marks active stage with `(ACTIVE)`
- Shows previous stage outcomes
- Writes bundled `WORKFLOW_STATE.json`

### `concat_blind`

- Shows all stages
- No active-stage marker
- No `Active Stage` header (shows `Total Stages: N`)
- No previous stage outcomes section
- No bundled `WORKFLOW_STATE.json`
- Agent-bundle `submit_result.json` redacts stage-identifying fields and verification-log path

For `concat_blind`, the agent must track progress from `submit_result.json` and/or run-dir `workflow_state.json`.

## Prompt Placeholder Rendering

Workflow stage instruction/context text in `PROMPT.md` resolves runtime placeholders when values are available:

- `${BENCH_NAMESPACE}`
- `${BENCH_NS_<ROLE>}`
- `${NS_<role>}`
- `${BENCH_PARAM_<PARAM>}`

If a placeholder token has no runtime value, it remains unchanged.

## Submit Contract

`submit.signal` payloads:

- Empty file: normal submit
- `{"action":"cleanup"}`: request cleanup/stop path

Receipt and freshness:

- Wait for submit receipt (`submit.ack`) before trusting `submit_result.json`.
- Before `submit.ack`, `submit_result.json` can be stale from a previous submit/stage.
- If feedback files are not ready yet, continue waiting and avoid re-submitting.

`submit_result.json` carries workflow branch control:

- Always available in agent bundle:
  - `can_retry`: retry same stage
  - `workflow.continue`: stage advance happened
  - `workflow.final`: workflow ended
  - `workflow.reason`: branch reason (`advance_next_stage`, `oracle_failed_retryable`, etc.)
- Not guaranteed in `concat_blind` agent bundle:
  - `workflow.stage_id`, `workflow.stage_index`, `workflow.stage_total`, `workflow.stage_attempt`, `workflow.stage_status`, `workflow.next_stage_id`
  - top-level `verification_log`

The orchestrator run-level `submit_results.log` keeps the full unredacted payload for operators/debugging.

Agent logic should primarily branch on `continue`, `can_retry`, and `final` rather than assuming stage visibility from prompt text in blind mode.

## Recommended Agent Loop

1. Read `PROMPT.md`.
2. Execute actions for current stage.
3. Write `submit.signal`.
4. Wait until `submit.signal` is consumed, then wait for `submit.ack`.
5. After `submit.ack`, wait for fresh `submit_result.json`.
6. Parse `workflow` payload:
   - If `final=true`: stop.
   - If `continue=true`: reload `PROMPT.md` and continue on next stage.
   - If `can_retry=true`: stay on same stage, gather new evidence, retry.
   - Else: treat as terminal/error and stop.
