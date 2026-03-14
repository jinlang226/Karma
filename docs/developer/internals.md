# Internals

This document is a maintainer-oriented map of how the framework is wired today.
It focuses on runtime behavior, module boundaries, and extension points.

## 1) Repository Topology

Core runtime code lives under `app/`:

- `app/runner.py`: stateful backend object (`BenchmarkApp`) used by UI server and orchestrator flows.
- `app/runner_core/*`: single-stage run lifecycle, command execution, cleanup/metrics, UI workflow/judge job adapters.
- `app/orchestrator_core/*`: headless orchestrator runtime, workflow loop, bundle/proxy handling, orchestration CLI.
- `app/workflow.py`: workflow YAML normalization + prompt rendering + namespace aliasing.
- `app/preconditions.py`, `app/oracle.py`, `app/case_params.py`, `app/test_schema.py`: schema normalization and test-yaml contracts.
- `app/judge/*`: trajectory judge input build, rubric merge, model call, scoring, evidence validation.
- `app/metrics/*`: external metric calculators selected by `externalMetrics`.

Other important roots:

- `main.py`: web backend entrypoint.
- `orchestrator.py`: headless orchestrator entrypoint (thin shim into `app/orchestrator_core/runtime_glue.py`).
- `resources/`: benchmark corpus (`resources/<service>/<case>/test.yaml` + `resource/`, `oracle/`, `solver/`).
- `workflows/`: workflow specs for `workflow-run`.
- `tests/unit` and `tests/integration`: custom test harnesses (`tests/run_unit.py`, `tests/run_integration.py`).
- `static/`: debugger UI assets.

## 2) Two Runtime Planes

### A. Web/UI plane

Execution path:

1. `main.py` creates `BenchmarkApp`.
2. `app/server.py` exposes HTTP + SSE APIs.
3. Handler methods call `BenchmarkApp` methods, which delegate to `runner_core` helpers.

Primary use:

- Debugger UI single-case manual runner.
- Debugger UI workflow runner (starts orchestrator subprocess jobs).
- Judge job launch/streaming.

### B. Headless orchestrator plane

Execution path:

1. `orchestrator.py` calls `app/orchestrator_core/runtime_glue.main()`.
2. `app/orchestrator_core/cli.py` parses CLI (`run`, `batch`, `workflow-run`).
3. `runtime_glue.py` composes helper modules and dispatches run/batch/workflow execution.

Primary use:

- CI/local automated runs.
- Docker/local sandbox orchestration.
- Workflow chain execution with agent submit loop.

## 3) Core Stateful Object: `BenchmarkApp`

`app/runner.py` owns mutable state and synchronization primitives:

- `run_state`: current single-stage run status and metadata.
- `run_lock`, `judge_lock`, `workflow_lock`: synchronized updates.
- job stores for judge/workflow UI streams.
- manual workflow bridge session (optional path for manual runner semantics).

`BenchmarkApp` itself is intentionally thin on heavy logic:

- setup/submit/verification and cleanup delegate to `runner_core/run_flow.py` and `runner_core/post_run.py`.
- command execution delegates to `runner_core/command_runtime.py`.
- workflow/judge UI job orchestration delegates to `runner_core/workflow_jobs.py` and `runner_core/judge_jobs.py`.

This is a core modularity decision: keep a single state owner, but push behavior to stateless helper modules.

## 4) Single-Stage Run Lifecycle

Main flow is implemented in `app/runner_core/run_flow.py` and triggered by `BenchmarkApp.start_run()` / `submit_run()`.

Setup phase:

1. Validate and load case YAML.
2. Resolve/validate `preconditionUnits`.
3. Ensure namespace targets exist when namespace context is provided.
4. Execute preconditions:
   - per unit: `probe` -> `apply` (only if probe fails) -> `verify` retries.
5. Run setup self-check loop (`setup_self_check.precondition_check`) when configured.
6. Apply decoys if required by external metrics.
7. Transition state to `ready`.

Submit/verify phase:

1. On submit, oracle config is read from `oracle.verify`.
2. Verification thread runs:
   - optional before hooks
   - verify commands
   - optional after hooks (warn/fail mode)
3. Result transitions to `passed` or `failed`.
4. Cleanup is triggered unless deferred.
5. External metrics are computed and written.

State statuses used by runtime:

- `idle`, `setup_running`, `ready`, `verifying`, `passed`, `failed`, `auto_failed`, `setup_failed`.

## 5) Workflow Runtime Lifecycle

Canonical loop is in `app/orchestrator_core/workflow_engine.py`.
Supporting normalization/rendering is split into `app/workflow.py`, `app/orchestrator_core/glue_workflow.py`, and `app/orchestrator_core/workflow_run.py`.

Workflow execution sequence:

1. Load and normalize workflow YAML (`load_workflow_spec`).
2. Resolve stage rows (`_resolve_workflow_rows`):
   - load each case `test.yaml`
   - resolve case params + stage param references
   - render case with resolved params
   - run namespace hygiene validation
3. Attach namespace contexts (alias -> concrete namespace mapping).
4. Ensure all required namespaces exist.
5. Setup stage 1 via standard run setup path.
6. Prepare agent bundle and launch agent process.
7. Enter submit loop:
   - wait for `submit.signal`
   - write `submit.ack` after submit receipt and stale-result invalidation
   - run stage verification
   - branch: retry same stage / advance / terminal failure
   - non-retryable stage-failure branch is controlled by `stage_failure_mode` (`continue|terminate`)
8. On advance, next-stage setup runs before confirming advance to agent.
9. At terminal state, run final sweep (or skip when `spec.final_sweep_mode=off`) and workflow cleanup.

Important current behavior:

- Precondition skipping is state-driven (`probe` success), not a hardcoded cross-stage skip list.
- Retryable stage failures do not republish `PROMPT.md`; only `submit_result.json` updates.
- `start.signal` is only used when `--manual-start` is enabled.
- Stage run artifacts are nested inside workflow run dir under `stage_runs/<nn>_<stage_id>/...`.
- In `concat_blind`, agent-bundle `submit_result.json` redacts stage-identifying fields and `verification_log`.
- `workflow_final_sweep.json` is always written; when sweep is disabled it is emitted with `status=skipped`.

## 6) Workflow Prompt + Progress Contract

Prompt generation is centralized in `app/workflow.py::render_workflow_prompt`.
Runtime publication is in `app/orchestrator_core/workflow_run.py::workflow_publish_prompt_and_state`.

Prompt modes:

- `progressive`: active stage only.
- `concat_stateful`: all stages with active marker and bundled `WORKFLOW_STATE.json`.
- `concat_blind`: all stages without active marker or previous outcomes; progress must be read from submit/state artifacts.

Main files inside workflow run bundle:

- `agent_bundle/PROMPT.md`
- `agent_bundle/submit.signal`
- `agent_bundle/submit.ack`
- `agent_bundle/submit_result.json`
- optional `agent_bundle/WORKFLOW_STATE.json` (`concat_stateful` only)
- run-dir `workflow_state.json` (always)

Contract details are documented in `docs/design/workflow-agent-progress-contract.md`.

## 7) Namespace Virtualization and Command Rendering

Namespace logic is modularized in `app/orchestrator_core/namespace_runtime.py` and mirrored in `app/runner_core/command_runtime.py`.

What it does:

- resolves role->namespace env vars (`BENCH_NAMESPACE`, `BENCH_NS_*`).
- resolves param env vars (`BENCH_PARAM_*`).
- injects `-n <namespace>` into kubectl commands when not explicitly provided.
- renders placeholder tokens in command args/manifests.
- materializes rendered manifest files to run-scoped output paths.

Namespace lifecycle ownership:

- namespace precreate/cleanup is orchestrator-owned in supported run flows.
- runner command runtime only renders/executes stage commands; it does not perform namespace bootstrap fallback.

Workflow hygiene guardrails (`app/orchestrator_core/workflow_validation.py`) reject:

- `kubectl -A` / `--all-namespaces`.
- hardcoded static namespaces.
- namespace create/delete operations.
- manifests defining `kind: Namespace` or fixed `metadata.namespace`.

## 8) Schema and Parameter Layers

Test case schema normalization is intentionally split by responsibility:

- `app/preconditions.py`: normalize and validate `preconditionUnits`.
- `app/oracle.py`: normalize `oracle.verify` commands + hook semantics.
- `app/case_params.py`: resolve typed params and render `{{params.*}}` templates.
- `app/test_schema.py`: reject legacy top-level keys.

Workflow-level param flow:

- workflow stage `param_overrides` may reference earlier stage params (`${stages.<id>.params.<name>}`).
- references are resolved in `app/workflow.py::resolve_stage_param_overrides`.
- resolved params are injected into per-stage case rendering and namespace env tokens.

## 9) Orchestrator Modularity Pattern

`app/orchestrator_core/runtime_glue.py` acts as composition root.
It wires together pure helper modules rather than concentrating business logic.

Split by concern:

- `cli.py`: argument parsing + top-level dispatch.
- `agent_runtime.py`: agent launch/submit-wait/start-signal termination logic.
- `bundle.py`: agent workspace files, wrapper kubectl, proxy kubeconfig/env script.
- `proxy.py`: local proxy lifecycle helpers.
- `exec_runtime.py`: polling and command-list execution utilities.
- `artifacts.py`: stage/submit/usage artifact writes.
- `workflow_engine.py`: main workflow state machine.
- `workflow_run.py`: reusable workflow helper operations.
- `glue_workflow.py`: workflow row loading and validation glue.
- `judge_flow.py`: post-run/post-batch judge routing.

This keeps each unit testable with dependency injection, and reduces coupling to global runtime state.

Docker-specific note:

- Docker agent launch tracks container id in a run-scoped cid file (`agent_container.cid`).
- Termination uses that id to force-remove the container (`docker rm -f`) during normal orchestrator shutdown.

## 10) Judge Pipeline

Primary modules:

- `app/judge/engine.py` (`TrajectoryJudge`): orchestrates end-to-end evaluation.
- `app/judge/input_builder.py`: builds judge input packet from run artifacts.
- `app/judge/rubric.py`: global/service/case rubric merge.
- `app/judge/classification.py`: rubric-driven post-score classifier evaluation.
- `app/judge/client.py`: OpenAI-compatible client abstraction.
- `app/judge/scoring.py`: weighted score computation.
- `app/judge/evidence.py`: evidence-id validation.
- `app/judge/cli.py`: `scripts/judge.py` backend.

Artifacts produced per run:

- `judge/input_v1.json`
- `judge/raw_response_v1.json`
- `judge/result_v1.json`
- `judge/summary.md`

`judge/result_v1.json` includes:

- `scores`: weighted score outputs
- `model_output`: raw normalized LLM dimension outputs
- `classifications`: optional rubric-driven labels (`label`, `rule_id`, `confidence`, `evidence_ids`, `status`)

Batch outputs:

- `batch_*/judge_index.json`
- `batch_*/judge_summary.json`

## 11) External Metrics Pipeline

Metrics are plugin-like functions registered in `app/metrics/__init__.py::METRIC_TOOLS`.
The runtime selects metrics declared in case `externalMetrics`.

Execution points:

- main compute in `app/runner_core/post_run.py::maybe_compute_metrics`.
- residual drift after cleanup in `post_cleanup_metrics_from_state`.

Common metric inputs:

- `action_trace.jsonl`
- snapshots (`snapshot_pre.json`, `snapshot_post.json`, `snapshot_post_cleanup.json`)
- cluster/API reads via kubectl.

Metric defaults/config are loaded from `resources/metrics.yaml`.

## 12) UI Job Model (Workflow + Judge)

UI runs orchestrator/judge as subprocess jobs via:

- `app/runner_core/workflow_jobs.py`
- `app/runner_core/judge_jobs.py`

Both provide:

- job registries
- SSE event buffering and replay cursors
- job snapshots for `server.py` endpoints

Workflow runner supports source-aware execution profiles:

- default CLI profile
- UI debug-local profile (`BENCHMARK_UI_WORKFLOW_DEBUG_LOCAL`)
- UI docker profile

## 13) Artifacts and Debug Targets

Single-stage run directory key files:

- `meta.json`
- `preoperation.log`
- `setup_checks.json`
- `verification_<n>.log`
- `cleanup.log`
- `external_metrics.json`
- snapshots (`snapshot_*.json`)
- `action_trace.jsonl`
- `agent.log`

Workflow run directory key files:

- `orchestrator_stage.json`
- `workflow_state.json`
- `workflow_stage_results.jsonl`
- `workflow_transition.log`
- `workflow_final_sweep.json`
- `workflow_cleanup.log`
- `submit_results.log`
- `agent_bundle/*`
- `stage_runs/<nn>_<stage_id>/*` (per-stage `meta.json`, `preoperation.log`, `verification_*.log`, `cleanup.log`, etc.)

When debugging stage transitions, first inspect:

1. `orchestrator_stage.json`
2. `agent_bundle/submit_result.json`
3. `workflow_state.json`
4. `workflow_transition.log`

## 14) Modularity Rules in Practice

Current framework modularity is encoded as:

- Thin entrypoints (`main.py`, `orchestrator.py`, `app/server.py`).
- Single state owner (`BenchmarkApp`) with behavior extracted to helper modules.
- Namespace/parameter rendering logic centralized instead of duplicated in case-specific code.
- Workflow loop mechanics separated from prompt rendering and stage-row resolution.
- Judge stack isolated from runner/orchestrator execution stack.

When adding functionality, preserve this split:

- Put transport/parsing in edge modules.
- Put reusable behavior in domain helpers.
- Keep case/workflow schema logic centralized (`app/preconditions.py`, `app/oracle.py`, `app/case_params.py`, `app/workflow.py`).

## 15) Change Map (Where to Edit)

If you need to change:

- workflow prompt text/layout: edit `app/workflow.py` and update contract docs/tests.
- workflow submit/advance semantics: edit `app/orchestrator_core/workflow_engine.py`.
- stage setup/probe/apply/verify behavior: edit `app/runner_core/run_flow.py`.
- namespace placeholder behavior: edit `app/orchestrator_core/namespace_runtime.py` and `app/runner_core/command_runtime.py`.
- judge scoring/rubric behavior: edit `app/judge/scoring.py` and/or `app/judge/rubric.py`.
- UI workflow/judge stream behavior: edit `app/runner_core/workflow_jobs.py` / `app/runner_core/judge_jobs.py`.

Then update the relevant design/developer docs in `docs/` and keep tests aligned.
