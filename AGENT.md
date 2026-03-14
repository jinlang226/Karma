# AGENT.md

## Purpose
This repo expects **self-checks** after changes that affect runtime behavior (orchestration, runner/workflow logic, test YAMLs, judge pipeline). The goal is to catch regressions early and ensure the repo state is actually runnable.

## Assumptions / Preconditions
- `kubectl` is available in PATH.
- Cluster access is available.

If either precondition fails, **stop and ask** before proceeding.

## Preflight (Always)
Run quick checks before any self‑check:
- `kubectl version --client` (or `kubectl` help) to confirm binary exists.
- `kubectl get ns` with a short timeout to confirm cluster access.

If preflight fails, report it and ask how to proceed.

## Self‑Check Tiers
### Tier 0 (Fast validation)
Use when changes are minimal or non‑runtime (docs, comments, formatting).
- `kubectl apply --dry-run=client` on any touched Kubernetes manifests.

### Tier 1 (Smoke)
Use when changes affect runtime behavior in framework code (runner/orchestrator/workflow/server), without changing testcase runtime contracts.
- Run a short base-chain smoke (no perturbation scenarios):
  - Use `rabbitmq-experiments` cases unless the user asks otherwise.
  - Confirm run reaches terminal state and `cleanup_status=done`.
  - Run namespace leak check (`kubectl get ns`) and verify no new workflow/test namespaces remain.

### Tier 2 (Full e2e)
Use when explicitly requested, or when testcase runtime definitions are changed.
- Run full unit/integration and required e2e coverage (see Runtime Case Coverage).

## Runtime Case Coverage (Required)
When changing testcase runtime behavior (`resources/**/test.yaml`, `resources/**/oracle/**`, or stage resource manifests consumed by those cases), representative-only validation is not enough.

Hard rule for `test.yaml` edits:
- If a testcase `test.yaml` is changed, run that testcase full end-to-end on cluster:
  - precondition/setup -> manual solve -> oracle verify -> cleanup.
- If 13 testcase `test.yaml` files are changed, run all 13 testcase e2e flows.

Required coverage:
- If exactly 1 case is touched: run full e2e for that case.
- If 2+ cases are touched: run full e2e for **every** touched case.
- If the user asks to validate all affected cases, do not substitute with a sample/representative run.

Per-case e2e requirements:
- Precondition/setup reaches ready state.
- Perform an actual solve action (do not use submit-only auto-fail shortcut as proof).
- Oracle verify executes and returns terminal result.
- Cleanup finishes with `cleanup_status=done`.

Allowed exception:
- Only skip per-case e2e when the user explicitly opts out. If skipped, state this clearly in handoff.

Handoff gate:
- Do not hand off testcase runtime changes until the required per-case e2e coverage above is complete (or user explicitly skips).

## Workflow Runner Mode Defaults (Important)
- CLI `workflow-run` remains docker-default unless flags override.
- Web UI Workflow Runner `Run (Debug)` is source-tagged and backend-resolved to local interactive flow.
- Web UI Workflow Runner `Run (Docker)` keeps standard workflow-run defaults.
- Emergency fallback switch:
  - `BENCHMARK_UI_WORKFLOW_DEBUG_LOCAL=0` disables UI debug-local override globally.

## Architecture Guardrail (Required for Planned Changes)
When implementing a planned feature or refactor, do not add one-off logic directly into large orchestration/UI entrypoints if a reusable module/helper already exists or should exist.

For this repo specifically:
- Prefer extending existing domain modules (for example `app/orchestrator_core/*`, `app/runner_core/*`) over adding more branching in top-level entrypoints.
- Keep HTTP handlers / CLI dispatch thin: parse input, call a helper/service, return structured output.
- Reuse existing schema validation / normalization paths instead of creating parallel parsing logic (especially for workflow/test YAML).
- If a change requires new logic that could be reused by another feature, extract it into a named helper/module now rather than embedding it inline.

PR/patch quality bar:
- Behavior change is clear and localized.
- New logic has a single obvious home.
- Tests cover the new path and protect against regressions.
- No duplicate “temporary” code paths unless explicitly documented with a removal plan.

## Documentation Sync Guardrail (Required for Functional Changes)
For any functional/runtime behavior change (runner flow, workflow semantics, prompt contract, schema behavior, judge behavior, or test case execution rules), you must review and update relevant docs under `docs/` before handoff.

Minimum expectation:
- Update impacted design/developer docs in the same change.
- If no doc update is needed, explicitly state why in the handoff.
- Keep `docs/developer/internals.md` aligned with current module responsibilities and runtime flow.

## Fast Refactor Validation Playbook
Use this for refactors to keep validation time predictable.

### 1) Start with one baseline snapshot
- Run preflight once:
  - `kubectl version --client --request-timeout=5s`
  - `kubectl get ns --request-timeout=5s`
- Save namespace baseline:
  - `kubectl get ns --request-timeout=5s -o name > /tmp/benchmark_ns_before.txt`

### 2) Use a strict validation ladder (stop on first failure)
1. `python tests/run_unit.py`
2. `python tests/run_integration.py`
3. One base smoke only (no perturbation scenario), using `rabbitmq-experiments`:
   - `python3 orchestrator.py run --service rabbitmq-experiments --case manual_monitoring --setup-timeout 180 --setup-timeout-mode auto --submit-timeout 120 --verify-timeout 180 --cleanup-timeout 180 --max-attempts 1 --proxy-server 127.0.0.1:65535 --agent-cmd "bash -c \"touch submit.signal; while [ ! -f submit_result.json ]; do sleep 0.2; done\""`

### 3) Run expensive checks only once
- Do not run full integration/e2e after every small patch.
- Use targeted local checks while coding; run full unit + integration + one smoke once before handoff.
- This shortcut does **not** apply to testcase runtime edits (`resources/**/test.yaml`, `resources/**/oracle/**`, `resources/**/resource/**`).
- For testcase runtime edits, follow Runtime Case Coverage requirements instead.

### 4) Always close with namespace leak check
- Capture post-run namespaces:
  - `kubectl get ns --request-timeout=5s -o name > /tmp/benchmark_ns_after.txt`
- Compare:
  - `diff -u /tmp/benchmark_ns_before.txt /tmp/benchmark_ns_after.txt`
- Clean leftover workflow/test namespaces (`wf-*`, `cmp-*`) if any remain.

### 5) Sandbox guidance (important for speed)
- If running from a restricted sandbox, integration/smoke may fail with local socket or cluster-connect permissions (`operation not permitted`).
- Some unit tests also start local HTTP servers / SSE streams and can fail in restricted sandboxes with the same error (`operation not permitted`) when socket bind/listen is blocked.
- In that environment, run `python tests/run_integration.py` and `orchestrator.py run` with elevated permissions from the start to avoid duplicate runs.
- If `python tests/run_unit.py` fails on server/API/SSE contract tests with `operation not permitted`, rerun the unit suite with elevated permissions as well (this is an environment restriction, not necessarily a code failure).

## Case Authoring Guardrails (Do Not Violate)
Use these rules whenever creating or editing `resources/**/test.yaml`.

### 1) Keep case logic stage-local
- A case is a single stage unit, not a workflow topology container.
- Do not encode workflow A/B sequencing strategy into the case itself.
- Do not propose or create sibling cases unless the user explicitly asks for that architecture.

### 2) Resource-first setup
- Baseline resources must live under `resource/` (for example `resource/config.yaml`).
- `preconditionUnits[].apply.commands` should apply baseline resources from files.
- Do not implement baseline creation inside oracle verification logic.

### 3) Mutation scope
- For configmap update cases, the mutation updates value fields; it must not create baseline resources.

### 4) Oracle contract
- Keep Oracle verification deterministic and observational (`oracle.verify.commands`).
- Do not use legacy top-level verification keys (`verificationCommands`, `verificationHooks`).

### 5) Resource-group independence (required, service-agnostic)
- Treat each `preconditionUnit` as one independent concern, not a full environment gate.
- `probe` must be read-only and check only one concern.
- `apply` must reconcile only that same concern (create/update/delete are all valid).
- `verify` must confirm only that same concern.
- Do not combine source+target checks or workload+data+monitoring checks in one unit.

Why this matters:
- Any single-concern drift should only trigger one single-concern reconcile.
- If a unit bundles multiple concerns, one local drift can force unnecessary re-apply of unrelated state and cause cross-stage interference.
- Multi-cluster workflows (for example blue/green migration) are one example: if source and target are coupled in one unit and only one side drifts, setup can rerun both sides and mutate already-correct carried-over state.

### 6) Delivery checklist before handing off a new case
- Confirm target behavior matches user scope exactly (for example "one configmap per stage").
- Confirm case includes real resource files under `resource/` (not placeholder-only setup).
- Confirm a fast case smoke reaches terminal state and cleanup succeeds (use demo/local agent if needed).

### 6.1) Quiesce-before-empty guardrail (required)
When a case transitions through "stop writers" -> "assert empty queue/table/topic", do not treat desired spec as enough.

Required checks:
- Quiesce probe/verify must check observed runtime state, not only desired state:
  - example: `spec.replicas == 0` **and** `readyReplicas == 0` **and** no live writer pods.
- Empty-state precondition must run only after quiesce is truly complete.
- e2e validation must include at least one chained run where this stage follows earlier stages that can leave live writers.

Common failure mode this prevents:
- purge/reset succeeds once, then writer pods still alive repopulate data, causing repeated empty-check verify failures.

### 7) Parameterization guardrail (required)
- Parameterize reusable identity and transition values:
  - identity: `cluster_prefix` (and role-specific prefixes for multi-cluster cases)
  - transitions: `from_version`, `to_version`
  - target objects: `*_secret_name`, `*_configmap_name`, `report_*`
  - fault-shape values: explicit markers such as `fault_http_addr`
- Keep parameter names explicit and domain-specific. Avoid ambiguous names (`value1`, `target`, `flag`, `config`).
- Default values must represent the canonical standalone baseline for the case.
- Reuse the same parameter consistently across `probe`, `apply`, `verify`, prompt text, and oracle expectations.
- Do not parameterize orchestration internals (for example generic command timeouts) unless the case semantics require it.
- If a value is parameterized, do not leave hidden hardcoded copies of that value in other commands.

### 8) Case-study guardrail: independent preconditions and outcome probes
- Write precondition units so each one checks one concern only.
- Prefer probing the required outcome, not one specific implementation detail.

RabbitMQ blue/green example:
- Keep source and target checks in separate units.
- If only target is missing something, only target should re-apply.
- Do not re-apply source when source is already correct.

CockroachDB monitoring example:
- The goal is "metrics are scrapeable," not "CRDs exist."
- Probe should check Prometheus readiness/targets outcome.
- Apply may still use CRD/operator as fallback if outcome is not satisfied.

MongoDB Stage1 -> Stage2 chaining example:
- Stage1 leaves a valid running replica set and some carry-over users/roles.
- Stage2 must set reporting RBAC drift, but must not tear down core cluster state from Stage1.
- Bad pattern: one monolithic Stage2 unit that treats any mismatch as "rebuild everything" (delete/recreate `sts`, services, secrets, PVCs).
- Good pattern: split Stage2 into two units:
  - `mongodb_cluster_runtime_ready`
    - `probe`: only core runtime readiness and required identity material (for example reporting secret exists, replica set healthy).
    - `apply`: idempotent bootstrap only (apply missing manifests, wait, init-if-needed, ensure admin auth path).
    - `verify`: runtime still healthy.
    - hard rule: no destructive infra reset.
  - `custom_roles_setup_baseline_ready`
    - `probe`: only the RBAC/data drift shape required by this stage.
    - `apply`: in-place role/user reconciliation only.
    - `verify`: exactly that drift shape.
- Validation rule for this pattern:
  - run standalone case e2e; and
  - run a chained workflow where Stage2 follows Stage1; both must pass with full sweep and `cleanup_status=done`.

### 9) Oracle independence for full-sweep accuracy (required)
- Oracle checks must follow the same runtime parameter contract as setup:
  - namespace via `BENCH_NAMESPACE` (or role-specific namespace envs)
  - expected values/identities via `BENCH_PARAM_*`
- Oracle verification must be read-only:
  - no `kubectl delete/apply/patch/set image`, no mutating SQL writes in oracle checks
- Oracle checks should be split into small independent assertions instead of bundled gates.

Why this matters:
- Full-sweep regression analysis is more accurate when each failed check maps to one specific broken property.
- Bundled checks hide signal. If one combined check fails, you lose information about what still works.
- Parameterized oracle checks reduce false failures caused by hardcoded names/versions/endpoints.

Example: split checks instead of bundling
- Better:
  - `pods_exist`
  - `pod_count_expected`
- Worse:
  - one check that combines "pods exist and count is 3"

If a trajectory breaks only scaling, full sweep should show:
- existence check passes
- count check fails

That partial signal is more actionable than one opaque failure.

CockroachDB example:
- A cluster-settings oracle that deletes a pod during verification is not independent and can perturb later stage checks.
- Correct pattern: oracle only reads setting state and validates expected outcome, using runtime params for namespace/cluster identity.

## When to Run Which Tier
- Changes to any of the following → **Tier 1**:
  - `app/runner.py`
  - `app/runner_core/**`
  - `app/orchestrator_core/**`
  - `app/workflow.py`
  - `app/server.py`
  - `orchestrator.py`
- Changes to any of the following require **Tier 2**:
  - `resources/**/test.yaml`
  - `resources/**/oracle/**`
  - `resources/**/resource/**` when those resources are consumed by touched testcases
- Doc‑only changes → **Tier 0** or skip.
- If user explicitly asks for full e2e → **Tier 2**.

## Reporting Requirements
After running checks, always report:
- Which tier was used.
- Commands (high‑level, not verbatim logs unless requested).
- Pass/fail summary.
- Any non‑fatal noise or warnings.
- For testcase/runtime edits: include a touched-case validation matrix with one row per case (`setup`, `solve`, `oracle`, `cleanup`, `final status`).

If checks are skipped, **state why**.

## Verification Isolation
- Verification helpers (for example oracle client pods) must not mutate workload state unexpectedly.
- Keep verification dependencies explicit in `verification_hooks` and always clean them up.

## Avoid Long Blind Waits
- Do **not** use long fixed waits like `--timeout=600s` without progress checks.
- Prefer short polling loops (e.g., 5–10s) that detect early failures and surface errors quickly.
- If pods fail or crash early, **stop and adjust** preconditions rather than waiting out long timeouts.

## Opt‑Out / Short‑Circuit
If the user explicitly says to skip checks, do so and note it in the response.
