# MongoDB Test Case Improvement Plan

Date: 2026-02-28

## Objective

Design a full migration plan for all 17 MongoDB base testcases so they:

1. match the current workflow framework schema,
2. are expressed as base tasks (adversarial plugin split model),
3. use independent precondition resource groups for workflow chaining,
4. use clear parameterization for reuse in workflow mode.

This document is design-only (no code changes here).

## Current State (Audit)

Current repo state:

- 18 MongoDB case directories exist under `resources/mongodb/*/test.yaml`.
- 17 are base cases.
- 1 is a hard/adversarial variant: `version-upgrade-hard` (to be retired in this plan).

All current MongoDB case files are legacy format:

- `preOperationCommands`
- `verificationCommands`
- no `preconditionUnits`
- no `oracle.verify.commands`
- no `params`
- no `namespace_contract`
- no `maxAttempts`

Most cases also hard-reset namespaces in setup (`kubectl delete namespace ...`), which is not chain-safe.

## Required Schema Migration (17 Base Cases)

Every case should be migrated to modern schema:

1. Required top-level keys

- `type`
- `targetApp`
- `numAppInstance`
- `maxAttempts`
- `externalMetrics`
- `params.definitions`
- `namespace_contract`
- `preconditionUnits`
- `detailedInstructions`
- `operatorContext` (optional)
- `oracle.verify.commands`
- `cleanUpCommands`

2. Remove legacy keys

- remove `preOperationCommands`
- remove `verificationCommands`
- remove free-text `verification` runtime usage

3. Setup design change

- split monolithic setup into concern-scoped `preconditionUnits`
- each unit must be `probe -> apply -> verify`

4. Chain-safety rule

- no namespace delete/recreate in precondition setup
- preconditions should reconcile only the missing/drifted concern

## Base Case Extraction (Adversarial Split Model)

Model:

- base task = core operator action
- adversarial scenario = plugin layered on base

Scope for this plan:

- 18 directories exist today.
- `version-upgrade-hard` is explicitly out of scope and will be removed.
- Migration target is 17 base cases only.

## Base Task Catalog

1. `deploy_replica_set`
2. `initialize_replica_set`
3. `scale_replica_set_members`
4. `decommission_replica_member`
5. `add_arbiter_member`
6. `upgrade_mongodb_version`
7. `enable_tls`
8. `rotate_server_certificates`
9. `manage_database_users`
10. `rotate_user_password`
11. `manage_custom_roles`
12. `configure_monitoring_scrape`
13. `configure_external_access_horizons`
14. `tune_readiness_probe`
15. `update_mongod_runtime_config`
16. `customize_statefulset_template`
17. `recover_cluster_health_checks`

Retirement note:

- `version-upgrade-hard` is treated as a hard/adversarial variant and will be retired from this base-case migration.

## Independent Precondition Design Rules (MongoDB)

Apply these to every migrated case:

1. One unit = one concern
- namespace, secrets, services, statefulset readiness, rs topology, monitoring wiring, etc.

2. Probe outcome, not implementation detail
- ask "is required state true now?"
- do not require one implementation path unless explicitly part of task objective

3. Apply minimal reconcile
- apply only what is needed for that unit concern

4. Verify same concern only
- avoid multi-concern verify gates

5. Split multi-namespace concerns
- MongoDB namespace and monitoring namespace units must be separate

6. Preserve carry-over workflow state
- no global reset patterns in preconditions
- avoid unnecessary re-apply of core workload when already healthy

## Parameterization Standard

### Global identity pack

- `mongodb_namespace`
- `cluster_prefix`
- `headless_service_name`
- `client_service_name`

### Topology pack

- `from_member_count`
- `to_member_count`
- `arbiter_count`
- `decommission_member_index`

### Version/compatibility pack

- `from_version`
- `to_version`
- `from_fcv`
- `to_fcv`
- `intermediate_versions` (for multi-hop upgrades)

### Auth/user pack

- `admin_username`
- `admin_password_secret_name`
- `app_username`
- `app_password_secret_name`
- `next_password_secret_name`
- `readonly_username`
- `reporting_username`

### TLS pack

- `tls_ca_secret_name`
- `tls_server_secret_name`
- `tls_old_cert_configmap_name`
- `min_rotated_validity_days`

### Monitoring pack

- `monitoring_namespace`
- `prometheus_service_name`
- `prometheus_service_port`
- `metrics_endpoint_path`
- `exporter_deployment_name`

### Networking pack

- `nodeport_domain_1`
- `nodeport_domain_2`
- `nodeport_domain_3`
- `nodeport_port_1`
- `nodeport_port_2`
- `nodeport_port_3`

### Config/probe pack

- `readiness_probe_initial_delay_sec`
- `readiness_probe_period_sec`
- `readiness_probe_timeout_sec`
- `verbosity_delta`
- `slowms_multiplier`
- `journal_compressor_target`

### Naming rules

- use explicit names only (`*_secret_name`, `*_configmap_name`, `*_count`, `*_version`)
- avoid ambiguous names (`target`, `value1`, `flag`)
- default values should represent canonical standalone baseline

### Wiring rules

Each parameterized value should be used consistently across:

- `probe`
- `apply`
- `verify`
- `detailedInstructions`
- oracle expectations

## Case-by-Case Design Plan (17 Base Cases + 1 Retirement)

### 0) `version-upgrade-hard` (retire)

- Remove directory `resources/mongodb/version-upgrade-hard`.
- Remove references from MongoDB docs/plans and workflow examples.
- Do not replace with a new hard variant in this phase.

### 1) `deploy`

- Base task: deploy a healthy 3-member replica set with auth and app access.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `core_auth_secrets_ready`
  - `core_services_ready`
  - `core_statefulset_ready`
  - `replica_set_healthy_ready`
  - `app_rw_smoke_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`, `member_count`
  - `admin_username`, `admin_password_secret_name`
  - `app_username`, `app_password_secret_name`
  - `app_database_name`

### 2) `initialize`

- Base task: restore/initialize replica set topology safely.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `services_ready`
  - `statefulset_process_ready`
  - `initial_rs_config_ready`
  - `replica_set_healthy_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`, `member_count`
  - `rs_name`, `member_hosts`

### 3) `replica-scaling`

- Base task: scale replica set from baseline to target members.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_cluster_ready`
  - `target_statefulset_replicas_ready`
  - `replica_set_membership_ready`
  - `data_integrity_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `from_member_count`, `to_member_count`
  - `admin_username`, `admin_password_secret_name`

### 4) `decommission`

- Base task: remove one member with no data/availability loss.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_cluster_ready`
  - `decommission_target_ready`
  - `membership_after_decommission_ready`
  - `primary_secondary_health_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `from_member_count`, `to_member_count`
  - `decommission_member_index`

### 5) `arbiters`

- Base task: add arbiter voting member while preserving data-bearing layout.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `data_members_statefulset_ready`
  - `arbiter_statefulset_ready`
  - `replica_set_with_arbiter_ready`
  - `election_stability_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `data_member_count`, `arbiter_count`
  - `arbiter_prefix`

### 6) `version-upgrade`

- Base task: supported upgrade from 5.0.x to 6.0.5 and FCV finalize.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_version_ready`
  - `baseline_fcv_ready`
  - `upgrade_target_spec_ready`
  - `rolling_upgrade_complete_ready`
  - `fcv_finalized_ready`
  - `data_integrity_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `from_version`, `to_version`
  - `from_fcv`, `to_fcv`
  - `admin_username`, `admin_password_secret_name`

### 7) `tls-setup`

- Base task: enable TLS for running replica set.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_cluster_ready`
  - `openssl_toolbox_ready`
  - `tls_material_ready`
  - `tls_statefulset_runtime_ready`
  - `tls_connectivity_ready`
  - `plain_connectivity_rejected_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `tls_ca_secret_name`, `tls_server_secret_name`
  - `cert_validity_days`

### 8) `certificate-rotation`

- Base task: rotate server certs while keeping CA trust unchanged.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `tls_enabled_cluster_ready`
  - `openssl_toolbox_ready`
  - `old_cert_record_ready`
  - `rotated_server_cert_secret_ready`
  - `tls_cluster_health_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `tls_ca_secret_name`, `tls_server_secret_name`
  - `tls_old_cert_configmap_name`
  - `min_rotated_validity_days`

### 9) `user-management`

- Base task: reconcile user roles/privileges for app and readonly users.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_auth_cluster_ready`
  - `user_password_secrets_ready`
  - `baseline_users_ready`
  - `target_user_roles_ready`
  - `authorization_smoke_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `admin_username`, `admin_password_secret_name`
  - `app_username`, `app_password_secret_name`
  - `readonly_username`, `readonly_password_secret_name`
  - `reporting_role_name`, `app_database_name`

### 10) `password-rotation`

- Base task: rotate app user password to next secret value.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_auth_cluster_ready`
  - `password_secrets_ready`
  - `target_password_applied_ready`
  - `legacy_password_revoked_ready`
  - `other_users_unchanged_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `target_username`
  - `current_password_secret_name`
  - `next_password_secret_name`
  - `active_password_secret_name`

### 11) `custom-roles`

- Base task: enforce custom role model for reporting access boundaries.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_auth_cluster_ready`
  - `custom_role_definition_ready`
  - `user_role_binding_ready`
  - `allowed_query_path_ready`
  - `disallowed_query_path_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `admin_username`, `admin_password_secret_name`
  - `reporting_username`, `reporting_password_secret_name`
  - `custom_role_name`, `reports_collection`, `raw_collection`

### 12) `monitoring-integration`

- Base task: enable end-to-end Prometheus scrape for MongoDB metrics.
- Independent precondition units:
  - `monitoring_namespace_ready`
  - `mongodb_namespace_ready`
  - `mongodb_cluster_ready`
  - `prometheus_stack_ready` (outcome-based probe)
  - `mongodb_exporter_ready`
  - `scrape_configuration_ready`
  - `metrics_visibility_ready`
  - `curl_test_ready`
- Parameterization:
  - `mongodb_namespace`, `monitoring_namespace`
  - `cluster_prefix`
  - `prometheus_service_name`, `prometheus_service_port`
  - `exporter_deployment_name`
  - `metrics_endpoint_path`

### 13) `external-access-horizons`

- Base task: configure split-horizon external host advertisement.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `nodeport_services_ready`
  - `baseline_cluster_ready`
  - `horizon_mapping_ready`
  - `external_client_connectivity_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `nodeport_domain_1`, `nodeport_domain_2`, `nodeport_domain_3`
  - `nodeport_port_1`, `nodeport_port_2`, `nodeport_port_3`

### 14) `readiness-probe-tuning`

- Base task: tune probe config so healthy cluster becomes stably Ready.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_auth_cluster_ready`
  - `probe_script_ready`
  - `faulty_probe_config_ready`
  - `probe_tuning_target_ready`
  - `pod_readiness_stable_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `readiness_probe_initial_delay_sec`
  - `readiness_probe_period_sec`
  - `readiness_probe_timeout_sec`
  - `failure_threshold`

### 15) `mongod-config-update`

- Base task: apply approved runtime config policy across members.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_auth_cluster_ready`
  - `mongod_configmap_ready`
  - `config_applied_to_runtime_ready`
  - `replica_set_health_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `verbosity_delta`
  - `slowms_multiplier`
  - `journal_compressor_target`

### 16) `statefulset-customization`

- Base task: reconcile pod template customization without breaking readiness.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_auth_cluster_ready`
  - `guard_script_ready`
  - `faulty_template_state_ready`
  - `template_customization_target_ready`
  - `cluster_ready_and_observable_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `template_label_key`, `template_label_value`
  - `resource_requests_cpu`, `resource_requests_memory`
  - `resource_limits_cpu`, `resource_limits_memory`

### 17) `health-check-recovery`

- Base task: repair health-check behavior and restore stable 3-member topology.
- Independent precondition units:
  - `mongodb_namespace_ready`
  - `baseline_auth_cluster_ready`
  - `health_fault_state_ready`
  - `health_check_fix_target_ready`
  - `replica_set_healthy_ready`
  - `pod_ready_stable_ready`
- Parameterization:
  - `mongodb_namespace`, `cluster_prefix`
  - `healthcheck_configmap_name`
  - `readiness_probe_script_path`
  - `target_member_count`

## Recommended Migration Order

0. Scope correction first:

- retire `version-upgrade-hard`
- scrub references to the retired case from MongoDB docs/plans

1. Core topology first:

- `deploy`
- `initialize`
- `replica-scaling`
- `decommission`
- `arbiters`

2. Security/auth next:

- `tls-setup`
- `certificate-rotation`
- `user-management`
- `password-rotation`
- `custom-roles`

3. Operational configuration:

- `readiness-probe-tuning`
- `mongod-config-update`
- `statefulset-customization`
- `health-check-recovery`

4. Integration and networking:

- `monitoring-integration`
- `external-access-horizons`

5. Version flow:

- `version-upgrade`

## Acceptance Criteria For Migration

For each migrated case:

1. `test.yaml` matches modern schema (`preconditionUnits`, `oracle.verify.commands`, `params`, `namespace_contract`, `maxAttempts`).
2. Setup reaches `ready` in kind smoke without namespace reset logic.
3. Oracle runs from `oracle.verify.commands`.
4. Precondition groups are independent and concern-scoped.
5. Parameter names are explicit and reusable in workflow chaining.

## Validation Coverage Requirement

- This migration touches 17 base-case `test.yaml` files.
- Per AGENT.md runtime coverage rule, run full e2e for all 17 touched cases:
  - setup/precondition ready
  - real solve action
  - oracle verify
  - cleanup with `cleanup_status=done`
