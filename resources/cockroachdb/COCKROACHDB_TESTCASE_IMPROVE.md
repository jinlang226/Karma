# CockroachDB Test Case Improvement Plan

Date: 2026-02-28

## Objective

Migrate CockroachDB cases from old `test.yaml` shape to the current schema and simplify each case to a single base task.

Target outcome:

- one testcase = one core operator action
- no embedded adversarial complexity in base form
- workflow chaining remains horizontal
- future adversarial behavior can be attached vertically as plugins

This document covers base-case and schema migration only (no plugin design yet).

## Dedicated Section: Oracle Independence Audit and Refactor Plan

### Why this section exists

For workflow full-sweep regression analysis, each oracle must be as independent and observational as possible.
If an oracle mutates cluster state or encodes unrelated assumptions, sweep output becomes noisy:

- false positive example: a stage oracle fails because another stage changed an unrelated implementation detail.
- false negative example: a stage oracle passes because it mutates state during verification and hides real regressions.

RabbitMQ oracles are the reference pattern for this:

- read-only checks
- environment/param-aware (for example `BENCH_NAMESPACE`, `BENCH_PARAM_*`)
- outcome-focused (verify goal state, not one specific implementation path unless strictly required)

### Audit scope and outcome

Audited files: all 13 CockroachDB oracle scripts under `resources/cockroachdb/*/oracle/oracle.py`.

High-level result:

1. one critical mutation bug exists (pod deletion in oracle).
2. most oracles are hardcoded to `cockroachdb` namespace and fixed identity names.
3. several oracles are over-coupled to implementation details (fixed image/version/endpoint/host assumptions) beyond stage intent.

### Severity-ranked findings

#### P0: Oracle mutation (must fix first)

- File: `resources/cockroachdb/cluster-settings/oracle/oracle.py`
- Current behavior: deletes `crdb-cluster-0` pod during verification to test persistence.
- Why this is harmful:
  - verification is no longer read-only.
  - can perturb later stages and full-sweep outcomes.
  - introduces non-deterministic timing failures unrelated to stage objective.
- Required fix:
  - remove all mutation commands (`kubectl delete pod`, `kubectl wait` restart path) from oracle.
  - move persistence-style restart checks into a dedicated precondition/fault unit or into stage task intent if needed.
  - oracle should only read current setting and validate expected threshold/value.

#### P1: Hardcoded namespace and cluster identity across oracles

Most CockroachDB oracles directly embed:

- namespace: `"cockroachdb"`
- pod identity: `"crdb-cluster-0"`
- service/statefulset names: `"crdb-cluster"`, `"crdb-cluster-public"`

Why this is harmful:

- prevents reuse in workflow stages with different namespace contracts.
- creates brittle coupling to one naming convention.
- increases sweep failures caused by naming differences instead of real behavior regressions.

Required fix for every oracle:

- read namespace from `BENCH_NAMESPACE` (fallback allowed for standalone runs).
- read cluster identity from params:
  - `BENCH_PARAM_CLUSTER_PREFIX`
  - other case params as needed (`from_version`, `to_version`, schema names, endpoints).
- build pod/service/statefulset names from params instead of hardcoded literals.

### Per-oracle independence gap matrix

| Case | Current independence issue | Full-sweep risk | Required oracle change |
|---|---|---|---|
| `deploy` | Hardcoded ns/prefix and exact implementation checks (`v24.1.0`, fixed SA/serviceName/join text) | Fails on valid equivalent deployments | Parameterize namespace/prefix/version; keep essential safety checks but avoid unnecessary fixed-string coupling |
| `initialize` | Hardcoded ns/prefix; otherwise mostly observational | Moderate portability issues | Add namespace/prefix params; keep read-only initialization/connectivity checks |
| `generate-cert` | Hardcoded ns/prefix; fixed cert path assumptions | Moderate portability issues | Parameterize namespace/prefix/certs dir secret names where needed |
| `certificate-rotation` | Hardcoded ns/secret/configmap names (`crdb-cluster-certs`, `crdb-old-cert`) | Fails if names are parameterized in workflow | Parameterize cert secret and old-cert configmap names; keep CA-unchanged + leaf-rotated assertions |
| `cluster-settings` | Oracle mutates cluster + hardcoded names | High false positives/negatives | Remove mutation; parameterize setting key/value/threshold and identity |
| `decommission` | Hardcoded ns/pod/sql host/target pod list | Incorrect checks when replica topology differs | Parameterize from/to replica counts, target node identities, seed table name; keep read-only status checks |
| `expose-ingress` | Hardcoded ingress host/controller service/SQL host-port | Stage passes only for one ingress wiring | Parameterize `ui_host`, ingress URL, SQL host/port; verify outcome not controller brand |
| `health-check-recovery` | Hardcoded ns/prefix and expected node count | Portability and chain-fragility | Parameterize replica count and identity; keep recovery/health assertions only |
| `major-upgrade-finalize` | Hardcoded target image/version (`24.1`) | Fails when test params differ | Parameterize `from_version`/`to_version`; verify finalized state from params |
| `monitoring-integration` | Hardcoded Prometheus endpoint and scrape assumptions | False fail if monitoring stack is equivalent but wired differently | Parameterize Prometheus endpoint/job/metrics path/port; assert successful scrape outcome |
| `partitioned-update` | Hardcoded target image/version (`v24.1.1`) | False fail when versions are overridden | Parameterize `to_version`; keep partition/update completion checks |
| `version-check` | Hardcoded report CM and version literals (`23.2`, `v24.1.0`) | Can fail even when stage objective is met with different target values | Parameterize report configmap key/name and version expectations; compare against params |
| `zone-config` | Hardcoded schemas (`tenant_a`, `tenant_b`) and expected zone values | Fails for valid alternate schema names/values | Parameterize protected/target schema and zone config targets/values |

### Refactor contract for all CockroachDB oracles

Each oracle should satisfy all of these:

1. **Read-only verification only**
   - no `kubectl delete/apply/patch/set image` or any mutating SQL in oracle.
2. **Parameter-driven identity**
   - namespace from `BENCH_NAMESPACE`.
   - names and expected values from `BENCH_PARAM_*`.
3. **Outcome over implementation detail**
   - verify stage objective and safety invariants.
   - avoid failing solely due to equivalent but different resource wiring.
4. **Deterministic and fast**
   - bounded calls, explicit failure messages, no long mutation-retry loops.
5. **Stage-local scope**
   - avoid asserting unrelated subsystems unless they are part of this stage goal.

### Parameterization checklist to apply while refactoring

For each oracle, map hardcoded values to params/environment:

- Namespace:
  - `cockroachdb` -> `BENCH_NAMESPACE`
- Cluster identity:
  - `crdb-cluster` -> `BENCH_PARAM_CLUSTER_PREFIX`
  - `crdb-cluster-0` -> `${cluster_prefix}-0`
- Version expectations:
  - fixed literals -> `BENCH_PARAM_FROM_VERSION`, `BENCH_PARAM_TO_VERSION`
- Case-specific identifiers:
  - configmap/secret names -> `BENCH_PARAM_*_NAME`
  - schema names -> `BENCH_PARAM_TARGET_SCHEMA`, `BENCH_PARAM_PROTECTED_SCHEMA`
  - endpoint/host/port -> `BENCH_PARAM_*` fields in case params

### Verification discipline for future edits

When modifying or adding CockroachDB oracles:

1. run a grep safety check to confirm no mutation commands in oracle files.
2. verify every oracle reads `BENCH_NAMESPACE` and required `BENCH_PARAM_*`.
3. run standalone case verification.
4. run workflow full-sweep and confirm failures, if any, are stage-meaningful (not naming or implementation-noise failures).

### Concrete execution order

1. Fix `cluster-settings` oracle mutation bug first.
2. Parameterize namespace and cluster identity in all 13 oracles.
3. Parameterize version/schema/endpoint assumptions per case.
4. Reduce over-coupled implementation checks where not strictly required by stage goal.
5. Re-run unit tests + smoke + workflow full-sweep to validate improved regression signal quality.

## Implementation Status (Completed)

All 13 CockroachDB `test.yaml` files under `resources/cockroachdb/*/test.yaml` were migrated to the modern schema.

Applied across every case:

- added `maxAttempts`
- replaced legacy `verificationCommands` with `oracle.verify.commands`
- removed legacy free-text `verification` blocks from runtime schema
- replaced monolithic `preOperationCommands` with independent `preconditionUnits` (`probe -> apply -> verify`)
- retained existing oracle scripts and resource manifests so test intent stays unchanged

Case files updated:

1. `certificate-rotation`
2. `cluster-settings`
3. `decommission`
4. `deploy`
5. `expose-ingress`
6. `generate-cert`
7. `health-check-recovery`
8. `initialize`
9. `major-upgrade-finalize`
10. `monitoring-integration`
11. `partitioned-update`
12. `version-check`
13. `zone-config`

## Validation Results

### Static validation

- `python3 tests/run_unit.py`: passed (`[unit] ok (273 tests)`)
- integration corpus structure checks (targeted):
  - `test_all_real_cases_load_without_errors`: passed
  - `test_all_real_case_command_timeouts_are_valid_when_present`: passed
  - `test_all_real_case_corpus_has_no_legacy_module_config`: passed

### Kind smoke validation (all 13 cases)

Executed with `orchestrator.py run` in local sandbox and immediate submit agent for **every** CockroachDB case.
Validation date: 2026-02-28 (latest rerun includes fix for `health-check-recovery` fault probe matcher).

Results:

| Case | Smoke status |
|---|---|
| `certificate-rotation` | `auto_failed` |
| `cluster-settings` | `auto_failed` |
| `decommission` | `auto_failed` |
| `deploy` | `auto_failed` |
| `expose-ingress` | `auto_failed` |
| `generate-cert` | `auto_failed` |
| `health-check-recovery` | `auto_failed` |
| `initialize` | `auto_failed` |
| `major-upgrade-finalize` | `auto_failed` |
| `monitoring-integration` | `auto_failed` |
| `partitioned-update` | `auto_failed` |
| `version-check` | `auto_failed` |
| `zone-config` | `auto_failed` |

Interpretation:

- `auto_failed` is expected for this smoke mode because the auto agent submits without solving; it confirms setup + oracle path wiring.
- For all 13 latest runs, `setup_phase=ready` and `last_verification_kind=oracle_fail`, which means precondition setup converged and oracle execution was reached.
- `health-check-recovery` required one fix: parameter `fault_http_addr` now defaults to `127.0.0.1:8080` (instead of matching the full assignment text), so precondition `probe/verify` correctly matches the faulted StatefulSet command text.

Smoke conclusions:

- Modern schema compatibility is confirmed across all 13 migrated `test.yaml` files.
- Precondition unit execution path is working across all 13 cases.
- Oracle invocation path is working across all 13 cases in smoke mode.

## Remaining Follow-Ups (Not In This Patch)

1. Namespace parameterization is still limited.
Current CockroachDB resources/oracles are tied to `cockroachdb` (and `monitoring` where applicable), so full `${BENCH_NAMESPACE}`/`namespace_contract` migration should be done together with manifest/oracle refactors.

2. Base-task splitting is still pending for multi-action cases.
Examples: `major-upgrade-finalize`, `decommission`, `version-check`, and `expose-ingress` still represent compound tasks and can be split into smaller base cases in a follow-up pass.

## Source Of Truth

- Current schema contract: `docs/developer/schema.md`
- Current authoring guidance: `docs/developer/adding-a-test-case.md`
- Up-to-date example style: `resources/rabbitmq-experiments/*/test.yaml`

## Previous State (CockroachDB folder)

Scope analyzed: `resources/cockroachdb/*/test.yaml` (13 cases)

All 13 are still in old format style:

- use `preOperationCommands` as primary setup flow
- use legacy `verificationCommands`
- many contain legacy/free-text `verification` block
- no `preconditionUnits`
- no `oracle.verify.commands`
- mostly hardcoded namespace names (`cockroachdb`, `monitoring`)

## Required Schema Migration (applies to every CockroachDB case)

### A) Top-level fields

Add or normalize:

- `maxAttempts`
- `namespace_contract` (even single-namespace cases should declare default role)
- `params` (for stable identities like cluster name/image/version/namespace-sensitive values)
- `preconditionUnits`
- `oracle.verify.commands` (and hooks if needed)

### B) Replace legacy verification fields

Replace:

- `verificationCommands` -> `oracle.verify.commands`
- `verification` (string) -> remove or move relevant details into docs/comments (not runtime schema)

### C) Convert setup flow to resource-group preconditions

Do not keep setup as one linear `preOperationCommands` script.

Instead, split into precondition resource groups with `probe/apply/verify`:

1. identity/rbac ready
2. config ready
3. services ready
4. workload object present
5. workload health ready
6. case-specific baseline/fault ready

### D) Parameterize namespaces and identities

Replace hardcoded namespaces and fixed names in commands with runtime placeholders/params:

- `${BENCH_NAMESPACE}` for single-role cases
- `${BENCH_NS_<ROLE>}` for multi-role cases
- `{{params.<name>}}` for names and versions (for example `cluster_prefix`, `target_version`)

### E) Keep oracle observational

`oracle.verify.commands` should validate state only.
Baseline creation/fault injection belongs in `preconditionUnits.apply`.

## Field-by-Field Conversion Map

| Old Pattern | New Pattern |
|---|---|
| `preOperationCommands` as main setup | `preconditionUnits` with explicit probe/apply/verify resource groups |
| `verificationCommands` | `oracle.verify.commands` |
| `verification` free text | remove from schema; keep rationale in this doc or README |
| hardcoded namespace (`cockroachdb`) in case logic | `${BENCH_NAMESPACE}` or `${BENCH_NS_*}` |
| combined setup gate | split into independent units |
| multi-concern testcase goal | split into separate base cases |

## Base Task Extraction From Current 13 Cases

| Current Case | Current Intent | Extracted Base Task(s) | Split Needed |
|---|---|---|---|
| `deploy` | Create core cluster workload resources | `deploy_cluster_3node` | No |
| `initialize` | Fix join config and initialize cluster | `repair_join_config`, `initialize_cluster` | Yes |
| `generate-cert` | Turn on TLS for running cluster | `enable_tls` | No |
| `certificate-rotation` | Rotate TLS certs using existing CA | `rotate_tls_certificates` | No |
| `partitioned-update` | Upgrade to newer minor version with controlled rollout | `upgrade_minor_version` | No |
| `major-upgrade-finalize` | Upgrade binaries and finalize major upgrade | `upgrade_major_version`, `finalize_major_upgrade` | Yes |
| `cluster-settings` | Change rebalancing rate setting | `update_cluster_setting` | No |
| `zone-config` | Apply zone config only to tenant_b tables | `apply_zone_config_scoped` | No |
| `decommission` | Decommission nodes and scale down to 3 | `decommission_nodes`, `scale_down_cluster` | Yes |
| `health-check-recovery` | Restore cluster readiness from degraded state | `recover_cluster_health` | No |
| `expose-ingress` | Expose UI HTTPS and SQL TCP via ingress-nginx | `expose_ui_https`, `expose_sql_tcp` | Yes |
| `monitoring-integration` | Enable Prometheus scraping for CRDB metrics | `enable_metrics_scrape` | No |
| `version-check` | Detect feature/version state and write ConfigMap report | `read_version_state`, `write_version_report` | Yes |

## Canonical Base Task Catalog

1. `deploy_cluster_3node`
2. `repair_join_config`
3. `initialize_cluster`
4. `enable_tls`
5. `rotate_tls_certificates`
6. `upgrade_minor_version`
7. `upgrade_major_version`
8. `finalize_major_upgrade`
9. `update_cluster_setting`
10. `apply_zone_config_scoped`
11. `decommission_nodes`
12. `scale_down_cluster`
13. `recover_cluster_health`
14. `expose_ui_https`
15. `expose_sql_tcp`
16. `enable_metrics_scrape`
17. `read_version_state`
18. `write_version_report`

## Concrete Per-Case Change List

### 1) `deploy`

- Convert setup into independent preconditions (`rbac_ready`, `services_ready`, `statefulset_present`, `cluster_ready`).
- Move oracle to `oracle.verify.commands`.
- Add `maxAttempts`, `params`, `namespace_contract`.

### 2) `initialize`

- Split into two base cases:
  - `repair_join_config`
  - `initialize_cluster`
- Each new case uses narrow preconditions and independent oracle.

### 3) `generate-cert`

- Keep as one base case (`enable_tls`).
- Precondition should create only TLS baseline prerequisites.
- Oracle checks secure mode enabled and insecure mode rejected.

### 4) `certificate-rotation`

- Keep as one base case (`rotate_tls_certificates`).
- Precondition ensures near-expiry baseline.
- Oracle verifies rotated leaf + CA invariants.

### 5) `partitioned-update`

- Keep as one base case (`upgrade_minor_version`).
- Precondition only ensures starting version baseline.
- Oracle verifies final version and health.

### 6) `major-upgrade-finalize`

- Split into:
  - `upgrade_major_version` (binary/version bump path)
  - `finalize_major_upgrade` (finalization action)

### 7) `cluster-settings`

- Keep as one base case (`update_cluster_setting`).
- Parameterize setting key/value in `params`.

### 8) `zone-config`

- Keep as one base case (`apply_zone_config_scoped`).
- Parameterize target schema/pattern and values.

### 9) `decommission`

- Split into:
  - `decommission_nodes`
  - `scale_down_cluster`
- Avoid combining node lifecycle and workload size mutation in one base case.

### 10) `health-check-recovery`

- Keep as one base case (`recover_cluster_health`).
- Precondition should set a deterministic degraded state.

### 11) `expose-ingress`

- Split into:
  - `expose_ui_https`
  - `expose_sql_tcp`
- Keep HTTP ingress and TCP ingress as independent concerns.

### 12) `monitoring-integration`

- Keep as one base case (`enable_metrics_scrape`).
- Precondition should establish Prometheus + unsatisfied scrape wiring baseline.

### 13) `version-check`

- Split into:
  - `read_version_state`
  - `write_version_report`
- Reporting write path should be independent from feature-state detection logic.

## Target Testcase Skeleton (What To Implement)

Use this shape for migrated CockroachDB cases:

```yaml
type: cockroachdb-<base-task>
targetApp: CockroachDB
numAppInstance: 3
maxAttempts: 3

params:
  definitions:
    cluster_prefix:
      type: string
      default: crdb-cluster

namespace_contract:
  default_role: default
  required_roles:
  - default

preconditionUnits:
- id: <unit_id>
  probe:
    commands:
    - command: ["kubectl", "-n", "${BENCH_NAMESPACE}", "get", ...]
      timeout_sec: 20
  apply:
    commands:
    - command: ["kubectl", "-n", "${BENCH_NAMESPACE}", "apply", "-f", "resources/cockroachdb/<case>/resource/<file>.yaml"]
      timeout_sec: 60
      sleep: 1
  verify:
    commands:
    - command: ["kubectl", "-n", "${BENCH_NAMESPACE}", "get", ...]
      timeout_sec: 20
    retries: 24
    interval_sec: 5

detailedInstructions: |
  <single-task problem statement only>

operatorContext: |
  <minimal debugging context commands>

oracle:
  verify:
    commands:
    - command: ["python3", "resources/cockroachdb/<case>/oracle/oracle.py"]
      timeout_sec: 600

cleanUpCommands:
- command: ["kubectl", "delete", "namespace", "${BENCH_NAMESPACE}", "--ignore-not-found=true"]
  timeout_sec: 120
```

## Definition Of Done (Per Migrated Case)

1. No legacy fields:
   - no `verificationCommands`
   - no `verification`
2. Uses `preconditionUnits` + `oracle.verify.commands`.
3. Uses placeholders/params instead of hardcoded namespace-specific assumptions.
4. Represents exactly one base task.
5. Precondition units are independent resource groups.
6. Oracle logic is observational and deterministic.
7. Case passes standalone smoke and is workflow-chain safe.

## Execution Order Recommendation

1. Migrate schema for all existing 13 files first (mechanical conversion).
2. Split multi-task cases into separate base cases.
3. Re-run smoke/workflow checks and adjust precondition independence.
4. Add adversarial plugin integration after base-case set is stable.

## Parameterization Plan (Clear and Straightforward Names)

This section defines a simple naming standard and a per-case parameter matrix.

### Naming Rules

Use these rules for all CockroachDB case parameters:

1. Use `snake_case`.
2. Use full words (avoid unclear abbreviations).
3. Prefer `from_*` and `to_*` for transitions.
4. Prefer `*_count`, `*_version`, `*_name`, `*_port`, `*_seconds`, `*_bytes` suffixes.
5. Keep one concept per parameter.
6. Keep derived names derived (for example `cluster_prefix` -> `<prefix>-public`) instead of over-parameterizing.

### Universal Parameter (All 13 Cases)

All CockroachDB cases should include:

- `cluster_prefix`
  - Why: unique identifier for each test/workflow stage.
  - Example: `crdb-cluster-a`, `crdb-cluster-b`, `crdb-upgrade-main`.

### Upgrade Parameters (Upgrade-Style Cases)

Cases that change versions should include:

- `from_version`
- `to_version`

Applies to:

- `major-upgrade-finalize`
- `partitioned-update`
- `version-check` (mixed binary/feature baseline still maps cleanly to from/to semantics)

### Per-Case Applicability and Parameter List

Parameterization applicability result: **Yes for all 13 CockroachDB cases**.

| Case | Parameterization Applies? | Recommended Parameters (clear names) |
|---|---|---|
| `deploy` | Yes | `cluster_prefix`, `replica_count`, `to_version`, `storage_size_gi` |
| `initialize` | Yes | `cluster_prefix`, `replica_count` |
| `generate-cert` | Yes | `cluster_prefix`, `replica_count`, `cert_secret_name`, `cert_validity_days` |
| `certificate-rotation` | Yes | `cluster_prefix`, `replica_count`, `cert_secret_name`, `old_cert_configmap_name`, `min_rotated_validity_days` |
| `cluster-settings` | Yes | `cluster_prefix`, `setting_name`, `setting_value` |
| `decommission` | Yes | `cluster_prefix`, `from_replica_count`, `to_replica_count`, `seed_table_name`, `seed_row_count_min` |
| `expose-ingress` | Yes | `cluster_prefix`, `ui_host`, `sql_port`, `ingress_class_name`, `tls_secret_name` |
| `health-check-recovery` | Yes | `cluster_prefix`, `replica_count`, `fault_http_addr` |
| `major-upgrade-finalize` | Yes | `cluster_prefix`, `from_version`, `to_version` |
| `monitoring-integration` | Yes | `cluster_prefix`, `metrics_path`, `metrics_port`, `service_monitor_name` |
| `partitioned-update` | Yes | `cluster_prefix`, `from_version`, `to_version`, `update_partition` |
| `version-check` | Yes | `cluster_prefix`, `from_version`, `to_version`, `report_configmap_name`, `report_key` |
| `zone-config` | Yes | `cluster_prefix`, `target_schema`, `protected_schema`, `num_replicas`, `gc_ttl_seconds`, `range_min_bytes`, `range_max_bytes` |

### Parameters To Avoid (Too Hard To Understand)

Do not introduce opaque parameter names such as:

- `p1`, `p2`, `mode_x`
- `cfg_a`, `cfg_b`
- `vnext` (use `to_version` instead)

Do not parameterize implementation-only internals unless needed by real workflow composition.

### Recommended Defaults Pattern

Keep defaults easy to read:

- `cluster_prefix: crdb-cluster`
- `replica_count: 3`
- `from_version: "23.2.0"` (only where relevant)
- `to_version: "24.1.0"` (only where relevant)
- `metrics_path: "/_status/vars"` (monitoring case)

This keeps prompts understandable for both humans and agents while still supporting workflow-level overrides.
