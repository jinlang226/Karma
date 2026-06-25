# Static Solver Plan

## Goal

Add a static solver corpus for workflow execution without modifying:

- `cases/**`
- `workflows/**`

This work should only add solver-side assets, generation tools, registries, and
runner glue. The benchmark framework remains the source of truth for setup,
verification, and cleanup.

## Scope

- One workflow file maps to one workflow solver plan file with the same relative
  name for easy tracking.
- Reusable stage solver functions are shared across workflows and derived from
  the current workflow corpus's unique `service/case` pairs.
- Workflows with broken chaining or unstable precondition transitions are
  allowed to remain unsupported. We only keep solver plans for workflows that
  actually work.

## Non-Goals

- Do not edit testcase definitions under `cases/**`.
- Do not edit workflow definitions under `workflows/**`.
- Do not change the benchmark's runtime semantics as part of the initial static
  solver rollout.

## Current Runtime Constraints

At the current branch `HEAD`, workflow execution is stage-reentrant:

- the workflow loop calls `run_stage(...)` once per stage
- each stage launches a fresh agent process
- the agent submits by writing `submit.txt` in the stage directory

This differs from older long-lived workflow-agent behavior, but the static
solver corpus can still be implemented on top of the current branch by using a
reentrant runner. The design below keeps workflow plan files runner-agnostic so
they can later be interpreted by either:

- a current-branch reentrant runner, or
- a restored long-lived legacy-style runner

## High-Level Architecture

The static solver area lives under `scripts/static-solvers/`.

There are four layers:

1. Plan files
   - One file per workflow, same relative name as `workflows/**`.
   - Declares stage order and which active case solver script each stage uses.
2. Active case solver scripts
   - One bash script per reusable `service/case`.
   - Imported from or manually adapted from the historical solver corpus where possible.
3. Runtime adapters
   - Small framework-facing glue for the current reentrant contract and the
     optional legacy long-lived contract.
4. Registries and tooling
   - Inventory the workflow corpus.
   - Map current cases to imported solver sources.
   - Generate supported workflow plans.

## File Layout

```text
scripts/static-solvers/
  README.md

  bin/
    run_current_workflow.sh

  lib/
    common.sh
    runtime/
      current_reentrant.sh

  solvers/
    demo/*.sh
    cockroachdb/*.sh
    elasticsearch/*.sh
    mongodb/*.sh
    nginx-ingress/*.sh
    rabbitmq/*.sh
    ray/*.sh
    spark/*.sh

  plans/
    workflows/
      demo/*.sh
      short/*.sh
      long/*.sh
      error-prone/*.sh
      *.sh

  registry/
    imported_resource_case_map.yaml
    current_case_map.yaml
    workflow_support.yaml

  vendor/
    import-improve-resources/
      scripts/resource-solvers/solvers/*.sh
      resources/rabbitmq-experiments/common/solver_utils.py
      resources/rabbitmq-experiments/*/solver/solve.py

  generated/
    manifests/
      workflow_inventory.json
      case_usage.json
      candidate_workflows.json
      supported_workflows.txt
      skipped_workflows.txt

  tools/
    _shared.py
    inventory_workflows.py
    import_from_branch.py
    build_case_registry.py
    generate_workflow_plans.py
    validate_workflow_support.py
```

## Workflow Plan Contract

Workflow plan files are runner-agnostic. They should not contain framework
submission semantics directly. They only declare ordered stage intent.

Example:

```bash
#!/usr/bin/env bash

plan_stage stage_01 rabbitmq/manual_policy_sync.sh
plan_stage stage_02 rabbitmq/manual_user_permission.sh
plan_stage stage_03 rabbitmq/manual_monitoring.sh
```

The runtime adapter interprets the same plan file differently depending on the
execution contract:

- `current_reentrant.sh`
  - only executes the entry matching the current stage directory
  - writes `submit.txt`
- `legacy_longlived.sh`
  - loops through stages in order
  - submits and waits between stages

## Runner Strategy

### Current Branch

Initial implementation uses the existing runtime without framework changes:

```bash
python3 orchestrator.py run-workflow <workflow.yaml> \
  --sandbox local \
  --agent-cmd "bash /abs/path/scripts/static-solvers/bin/run_current_workflow.sh /abs/path/<workflow.yaml>"
```

This works because local `--agent-cmd` already runs with the stage directory as
the working directory and submission is just `./submit.txt`.

### Legacy Compatibility

If old long-lived workflow-agent behavior is restored later, the same workflow
plan files can be interpreted by a future legacy runner without regenerating
the corpus.

## Import Strategy

Source branch: `import-improve-resources`

Safety rule:

- never modify imported solver sources in place under `vendor/`
- copy raw sources into `vendor/` unchanged
- active solver scripts under `solvers/` may be copied from archived scripts
  and then edited deliberately, one by one
- do not bulk-rewrite imported solver sources with a generic normalizer
- only direct-copy a solver into active use when its behavior is understood and
  its assumptions still match the current case contract

### Shell Solvers

Use the archived solver corpus under:

- `scripts/resource-solvers/solvers/*.sh`

These are copied into `vendor/import-improve-resources/...` unchanged for
provenance.

Active solver scripts are generated or hand-copied into:

- `solvers/<service>/<case>.sh`

Wrapper rules:

- keep the kubectl and reconciliation logic intact when using direct-copy shell
  execution
- preserve vendored files unchanged
- keep environment-variable usage such as `BENCH_NAMESPACE`, `BENCH_NS_*`, and
  `BENCH_PARAM_*`
- if a vendored solver makes assumptions that no longer hold, do not rewrite it
  automatically; instead copy it into a reviewed hand-edited active solver
  script or mark the workflow unsupported when the issue is the workflow's
  environment chain rather than the solver logic

### RabbitMQ Python Helpers

RabbitMQ historical solvers rely on Python helper code. Copy these into the
static solver tree instead of rewriting them immediately:

- `resources/rabbitmq-experiments/common/solver_utils.py`
- `resources/rabbitmq-experiments/*/solver/solve.py`

The active RabbitMQ bash solver scripts may call these helpers directly by
invoking the archived `solve.py` entrypoints with `python3`.

## Registry Strategy

### `imported_resource_case_map.yaml`

Maps old resource cases to their imported solver sources using the historical
mapping from the other branch.

### `current_case_map.yaml`

Maps current branch `service/case` pairs to one of:

- `direct_shell`
- `shell_wrapper_variant`
- `python_wrapper`
- `new_manual`
- `unsupported`

### `workflow_support.yaml`

Tracks workflow support decisions such as:

- `candidate`
- `review_required`
- `unsupported`

Only workflows marked `candidate` should keep generated workflow plan files in
the initial committed corpus. `review_required` workflows remain listed but do
not get committed active plans until validated.

## Generation Pipeline

1. Inventory workflows
   - scan `workflows/**/*.yaml`
   - write `generated/manifests/workflow_inventory.json`
   - write `generated/manifests/case_usage.json`
2. Import old-branch assets
   - copy archived shell solvers into `vendor/`
   - copy RabbitMQ Python solver assets into `vendor/`
3. Build case registries
   - import historical mapping
   - map current cases to imported solver sources or unsupported reasons
4. Generate active shell solvers
   - create one active solver script under `solvers/<service>/<case>.sh`
   - allow per-script manual edits when parameter handling differs
5. Generate workflow plans
   - one `plans/workflows/**/<same-name>.sh` file per fully mapped workflow
6. Validate support
   - run selected workflows
   - keep only working workflows as `candidate`
   - record unsupported ones explicitly

## Progress Log

### 2026-06-23 Live Sweep Notes

- Validation is running through `scripts/static-solvers/tools/run_candidate_workflows.py`
  against the generated candidate list.
- Classification rule in force:
  - `regression_sweep` failure is not a workflow/env problem
  - workflow/env problems mean only true precondition-chain conflicts
  - solver defects must be fixed in solver scripts rather than classified away
  - `resource_issue` is a first-pass skip category for workflows that are
    statically likely to reproduce known cluster-capacity failures and therefore
    should be revisited later on a larger Docker/kind memory budget

Elasticsearch fixes landed during the live sweep:

- `solvers/elasticsearch/deploy-core-cluster.sh`
  - native deploy kept
  - rollout/status handling hardened for transient watch races
- `solvers/elasticsearch/rotate-http-certs.sh`
  - helper-pod delete/recreate path hardened
- `solvers/elasticsearch/file-realm-user-roles-merge.sh`
  - native additive merge kept; validated in chained security workflows
- `solvers/elasticsearch/rotate-elastic-password.sh`
  - replaced vendored pass-through with native scheme-aware solver
  - now works after TLS/cert rotation and rewrites `auth-checker` correctly
- `solvers/elasticsearch/readonly-audit.sh`
  - auth detection now also tries the live `elastic-password` secret
- `solvers/elasticsearch/change-plan-only.sh`
  - replaced submit-only placeholder with a real read-only planner that writes
    `ConfigMap/change-plan`

Known stale validations to rerun after the current broad sweep reaches a safe
pause point:

- workflow 49: `error-prone/elasticsearch-certs-password-rollback.yaml`
  - originally failed at `rotate-elastic-password`
  - should be rerun after the native password solver landed
- workflow 50: `error-prone/elasticsearch-certs-password-users-audit-change-plan.yaml`
  - originally failed at `change-plan-only`
  - should be rerun after the native Elasticsearch change-plan solver landed

Latest validation signals before context compaction / handoff:

- workflow 50 already revalidated the new password solver in a real chained run:
  `rotate-elastic-password` passed inside the full workflow
- workflow 50 later failed only because `change-plan-only` was still submit-only
  at that moment; that solver is now fixed
- workflow 51 (`error-prone/elasticsearch-certs-password-users-rollback.yaml`)
  is/was used as the next live chain to confirm the Elasticsearch security
  sequence still works after these fixes

### 2026-06-24 Live Sweep Notes

- workflow 50 (`error-prone/elasticsearch-certs-password-users-audit-change-plan.yaml`)
  has now completed cleanly end-to-end in the live sweep
  - stages 01-06 all passed
  - this revalidated `rotate-elastic-password`, `file-realm-user-roles-merge`,
    `readonly-audit`, and the new `change-plan-only` solver in one chained run
- workflow 49 (`error-prone/elasticsearch-certs-password-rollback.yaml`)
  reached stage 04 and exposed a real bug in the first native
  `rollback-rehearsal.sh` implementation
  - failure cause: Python planner assumed dict payloads and crashed on scalar
    values during live cluster snapshot parsing
  - fix landed: guard non-dict index/node payloads and sanitize temp filenames
  - workflow 49 still needs a clean rerun because the stale failed record was
    written before the parser fix
- `solvers/elasticsearch/scale-up-new-nodeset.sh`
  - replaced the vendored insecure pass-through with a native secure solver
  - root cause for the old failure: the imported solver created the warm
    nodeset with `xpack.security.enabled: false`, which does not match the
    current TLS-enabled cluster used by the active branch
  - new solver now reuses the live cluster image, reuses the live HTTP cert
    secret, mounts the secure config, waits for pod readiness robustly, then
    moves shards and verifies node-count convergence
  - follow-up hardening:
    - removed `kubectl rollout status` from this solver after a live run showed
      the stage-local kubectl proxy/watch path flaking with repeated
      `Connection refused`
    - added explicit HTTPS/auth-aware index seeding inside the solver because
      the inherited case precondition still seeds `app-data` over plain HTTP
      after cert rotation and can silently no-op in chained workflows
- workflow 51 (`error-prone/elasticsearch-certs-password-users-rollback.yaml`)
  is being used as the next live validation chain for the new
  `rollback-rehearsal.sh`
- runtime finding during rerun of workflow 52:
  - the recreated kind cluster got through Elasticsearch stages 01 and 02 again
  - stage 03 then stalled in direct readiness polling with no solver-side crash
  - at the same time, host-side `kubectl get ns` began returning
    `TLS handshake timeout`, and `docker exec` / `docker logs` against
    `kind-control-plane` also hung while `docker ps` still showed the container
    as `Up`
  - this points to kind/Docker control-plane instability rather than stale
    namespace state; the updated scale-up solver still needs a stable cluster to
    finish validation
- `solvers/elasticsearch/snapshot-repo-setup.sh`
  - replaced the vendored plain-HTTP/no-auth pass-through with a native solver
  - native solver now:
    - recreates `Secret/es-secure-settings` when absent
    - detects live HTTP scheme (`http` vs `https`)
    - authenticates with the live `elastic-password` secret when present
    - reloads secure settings correctly
    - deletes the fixed smoke snapshot name before recreating it so repeated
      snapshot stages are idempotent
    - retries repository registration on transient MinIO verification failures
- workflow 54 (`error-prone/elasticsearch-certs-users-snapshot-rollback.yaml`)
  - original live result labeled this as a solver failure, but deeper audit
    showed the first real blocker was a precondition-chain bug:
    `snapshot-repo-setup` inherits a live Elasticsearch namespace, its additive
    `s3_keystore_fixture` creates MinIO + `minio-init`, but omits
    `Service/minio`
  - consequence: `minio-init` loops on DNS failures until a workflow-side
    helper creates the missing service
  - after adding that one runtime helper (`precreate_minio_service`) and using
    the new native snapshot solver, the full workflow completed end-to-end:
    stages 01-05 pass, regression sweep pass
  - support interpretation for the first-pass corpus:
    - stage solver is good
    - workflow needs a workflow-side helper before the inherited snapshot stage
    - therefore classify as `env_chain_conflict` for the current stage-reentrant
      batch runner, with a note that it is solvable by a future per-workflow
      wrapper

### First-Pass Snapshot-Chain Skip Heuristic

- The global validation pass now also performs a static preflight for
  Elasticsearch snapshot workflows.
- Current heuristic:
  - find the first `elasticsearch/snapshot-repo-setup` stage in a workflow
  - if that first snapshot stage is preceded by any earlier Elasticsearch stage
    in the same workflow
  - and there is no earlier snapshot stage that would have already created the
    MinIO service path
  - then classify the workflow as `env_chain_conflict` and skip execution in
    the stage-reentrant batch runner
- Rationale:
  - the current `snapshot-repo-setup` case composes safely only when it owns
    namespace bootstrap or when `Service/minio` already exists
  - inherited Elasticsearch chains hit the additive precondition bug before the
    stage solver runs
  - these workflows are still potentially solvable later by one-workflow
    wrapper scripts that precreate `Service/minio` before stage execution

### First-Pass Resource Skip Heuristic

- The global validation pass now performs a static preflight before each
  workflow run.
- Current heuristic:
  - if a workflow contains `elasticsearch/scale-up-new-nodeset`
  - and the maximum `expected_nodes` across those stages is at least 4
  - and `max_expected_nodes * 1Gi + 1Gi` exceeds the live Docker engine total
    memory
  - then classify the workflow as `resource_issue` and skip execution
- Rationale:
  - kind reports allocatable memory per node, but all kind nodes often share a
    much smaller single Docker VM memory pool
  - secure Elasticsearch scale-up workflows can therefore look schedulable to
    Kubernetes while still destabilizing the kind control plane under real host
    memory pressure
- This is intentionally a first-pass efficiency rule, not a permanent support
  verdict. These workflows should be revisited later on a larger Docker memory
  budget.

## Validation and Cleanup Rules

Every workflow execution used for validation must be treated as potentially
stateful:

- prefer framework-managed cleanup
- after each validation run, explicitly check for stale namespaces
- if cleanup did not complete, delete leftover namespaces before proceeding
- do not let repeated failed validation runs accumulate namespace drift

Cleanup checks should be part of `validate_workflow_support.py` or the wrapper
scripts that invoke workflow validation.

## Commit Discipline

This commit series should only add:

- plan docs
- solver-side scripts
- vendored solver copies
- generation tools
- registries
- generated workflow plan files

It should not edit benchmark source inputs such as cases or workflows.

## Tooling Lifecycle

The tooling under `scripts/static-solvers/tools/` is build-time scaffolding.
It is acceptable to remove it after:

- the static solver corpus is generated
- the supported workflow set is validated
- the committed wrappers and workflow plans are considered stable

Until then, keep the tooling because it preserves reproducibility while the
corpus is still being built and reviewed.
