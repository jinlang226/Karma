# Elasticsearch Test Case Improvement Plan

Date: 2026-02-28

## Objective

Remove the deprecated `full-restart-upgrade-ha-hard` testcase folder, then migrate the remaining 16 Elasticsearch test cases from legacy `test.yaml` shape to the current workflow schema with chain-safe preconditions and clear parameterization.

Scope in this document:

- decommission of `full-restart-upgrade-ha-hard`
- schema migration requirements
- base task extraction
- independent precondition resource-group design
- parameterization design for workflow flexibility

Out of scope:

- implementation of adversarial plugin system
- writing code in this document

## Current State (Audit)

Current folder set includes 17 case variants under `resources/elasticsearch/*/test.yaml`, all in legacy shape.
This plan first removes the duplicate hard variant and then migrates the remaining 16 canonical cases.

Common legacy pattern:

- `preOperationCommands`
- `verificationCommands`
- optional free-text `verification`
- no `preconditionUnits`
- no `oracle.verify.commands`
- no `params`
- no `namespace_contract`
- no `maxAttempts`

Implication:

- setup logic is monolithic and not chain-safe
- implementation details and desired outcomes are often coupled
- hardcoded values reduce workflow reuse
- duplicate hard variant increases maintenance surface without adding base coverage

## Case Set Decision

Canonical migration set after cleanup: 16 cases.

Removed folder:

- `full-restart-upgrade-ha-hard`

Reason:

- it is a scenario variant of `full-restart-upgrade-ha`, not a distinct base task.
- hard-mode behavior should be represented by workflow/scenario layering, not a duplicate base case folder.

## Required Schema Migration (All 16 Base Cases)

Each case should be converted to:

1. Top-level structure

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

2. Replace legacy verification

- remove `verificationCommands`
- remove free-text `verification` from runtime contract
- move checks into `oracle.verify.commands`

3. Replace monolithic setup

- remove `preOperationCommands`
- split setup into independent `preconditionUnits` with:
  - `probe`
  - `apply`
  - `verify`

4. Avoid destructive baseline resets in setup

- do not use namespace delete/recreate in preconditions
- preconditions should reconcile only missing/drifted concern

## Base Task Extraction

Design model:

- base task: core operator objective
- adversarial scenario: perturbation plugin layered onto base task

For now, only base task design is defined.

### Base Task Catalog From Current Cases

1. `deploy_core_cluster`
2. `bootstrap_initial_master_nodes`
3. `repair_seed_hosts`
4. `reconcile_internal_http_service`
5. `expose_secure_http_ingress`
6. `rotate_http_certs`
7. `add_transport_additional_ca_trust`
8. `configure_snapshot_repo_and_snapshot`
9. `merge_file_realm_users_roles`
10. `rotate_elastic_password`
11. `scale_up_new_nodeset`
12. `safe_downscale_with_shard_migration`
13. `master_downscale_with_voting_exclusions`
14. `full_restart_upgrade_ha`
15. `enable_stack_monitoring_sidecars`
16. `recover_transform_job`

## Case-by-Case Design Plan (16 Base Cases)

### 1) `deploy-core-cluster`

- Base task: deploy core 3-node ES cluster with HTTP access and auth baseline.
- Independent precondition groups:
  - `es_namespace_ready`
  - `tls_secret_ready`
  - `elastic_password_secret_ready`
  - `core_services_ready`
  - `core_statefulset_ready`
  - `curl_test_ready`
  - `seed_index_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`, `http_service_name`, `replica_count`
  - `es_image`, `tls_secret_name`, `elastic_password_secret_name`
  - `index_name`, `seed_doc_count`

### 2) `bootstrap-initial-master-nodes`

- Base task: restore bootstrap/discovery config so cluster forms.
- Independent precondition groups:
  - `es_namespace_ready`
  - `discovery_config_ready`
  - `statefulset_ready`
  - `cluster_formation_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`, `configmap_name`
  - `initial_master_nodes`, `seed_hosts`, `replica_count`

### 3) `seed-hosts-repair`

- Base task: repair seed hosts and recover full membership.
- Independent precondition groups:
  - `cluster_workload_ready`
  - `discovery_seed_hosts_ready`
  - `membership_ready`
  - `data_read_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`, `configmap_name`
  - `seed_hosts`, `expected_node_count`, `index_name`

### 4) `internal-http-service-drift`

- Base task: correct internal HTTP service routing for reader/writer behavior.
- Independent precondition groups:
  - `es_namespace_ready`
  - `source_cluster_ready`
  - `target_cluster_ready`
  - `internal_service_route_ready`
  - `log_seed_ready`
  - `curl_test_ready`
- Parameterization:
  - `es_namespace`, `source_cluster_prefix`, `target_cluster_prefix`
  - `service_name`, `service_selector_label`, `index_name`

### 5) `secure-http-ingress`

- Base task: expose HTTPS ingress endpoint to ES API.
- Independent precondition groups:
  - `ingress_controller_ready`
  - `es_namespace_ready`
  - `es_cluster_ready`
  - `ingress_tls_secret_ready`
  - `ingress_resource_ready`
  - `curl_test_ready`
- Parameterization:
  - `es_namespace`, `ingress_namespace`, `cluster_prefix`
  - `ingress_class_name`, `ingress_host`, `ingress_tls_secret_name`
  - `backend_service_name`, `backend_port`

### 6) `rotate-http-certs`

- Base task: rotate HTTP cert leaf and keep endpoint healthy.
- Independent precondition groups:
  - `es_namespace_ready`
  - `es_cluster_tls_ready`
  - `openssl_toolbox_ready`
  - `http_tls_secret_ready`
  - `old_cert_record_ready`
  - `curl_test_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`
  - `tls_secret_name`, `http_ca_configmap_name`, `old_cert_configmap_name`
  - `min_rotated_validity_days`

### 7) `transport-additional-ca-trust`

- Base task: add transport CA trust and restore healthy 3-node cluster.
- Independent precondition groups:
  - `es_namespace_ready`
  - `es_cluster_baseline_ready`
  - `openssl_toolbox_ready`
  - `transport_additional_ca_secret_ready`
  - `transport_trust_config_ready`
  - `cluster_membership_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`
  - `transport_tls_secret_name`, `additional_ca_secret_name`
  - `target_node_count`

### 8) `snapshot-repo-setup`

- Base task: configure snapshot repository and complete a snapshot.
- Independent precondition groups:
  - `es_namespace_ready`
  - `es_cluster_ready`
  - `minio_ready`
  - `minio_bucket_ready`
  - `snapshot_credentials_ready`
  - `snapshot_repo_ready`
  - `snapshot_created`
  - `curl_test_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`
  - `minio_service_name`, `minio_endpoint`, `minio_bucket`
  - `snapshot_repo_name`, `snapshot_name`
  - `s3_access_key_secret_name`, `s3_secret_key_secret_name`

### 9) `file-realm-user-roles-merge`

- Base task: merge file-realm users/roles safely.
- Independent precondition groups:
  - `es_namespace_ready`
  - `es_tls_cluster_ready`
  - `file_realm_generator_ready`
  - `users_secret_ready`
  - `users_roles_secret_ready`
  - `curl_test_ready`
  - `auth_smoke_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`
  - `users_secret_name`, `users_roles_secret_name`
  - `existing_username`, `existing_password_secret_name`
  - `new_username`, `new_password_secret_name`
  - `index_name`

### 10) `rotate-elastic-password`

- Base task: rotate `elastic` password and align dependent auth.
- Independent precondition groups:
  - `es_namespace_ready`
  - `es_cluster_ready`
  - `elastic_password_current_ready`
  - `elastic_password_next_ready`
  - `auth_checker_ready`
  - `curl_test_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`, `elastic_username`
  - `current_password_secret_name`, `next_password_secret_name`
  - `active_password_secret_name`, `auth_checker_deployment_name`

### 11) `scale-up-new-nodeset`

- Base task: add new nodeset and move shards onto it.
- Independent precondition groups:
  - `es_namespace_ready`
  - `base_cluster_ready`
  - `curl_test_ready`
  - `index_seed_ready`
  - `new_nodeset_spec_ready`
  - `shard_relocation_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`
  - `base_replicas`, `new_nodeset_name`, `new_nodeset_replicas`
  - `index_name`, `allocation_attr_key`, `allocation_attr_value`

### 12) `safe-downscale-with-shard-migration`

- Base task: safely downscale and clean orphaned PVCs.
- Independent precondition groups:
  - `es_namespace_ready`
  - `base_cluster_ready`
  - `index_allocation_ready`
  - `target_replicas_ready`
  - `shard_migration_complete`
  - `orphan_pvc_cleanup_ready`
  - `data_read_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`
  - `from_replicas`, `to_replicas`
  - `index_name`, `pvc_name_prefix`, `cleanup_label_key`

### 13) `master-downscale-voting-exclusions`

- Base task: perform master downscale with proper voting exclusion flow.
- Independent precondition groups:
  - `es_namespace_ready`
  - `master_cluster_ready`
  - `curl_test_ready`
  - `voting_exclusions_state_ready`
  - `master_target_replicas_ready`
  - `cluster_health_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`
  - `from_master_replicas`, `to_master_replicas`
  - `voting_exclusion_timeout_sec`

### 14) `full-restart-upgrade-ha`

- Base task: full-restart upgrade to target version with data intact.
- Independent precondition groups:
  - `es_namespace_ready`
  - `cluster_ready_at_from_version`
  - `seed_data_ready`
  - `upgrade_target_spec_ready`
  - `restart_complete_ready`
  - `version_verified`
  - `data_integrity_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`
  - `from_version`, `to_version`
  - `index_name`, `expected_doc_count`

### 15) `stack-monitoring-sidecars`

- Base task: restore sidecar-based monitoring flow to monitoring cluster.
- Independent precondition groups:
  - `monitoring_namespace_ready`
  - `monitoring_cluster_ready`
  - `monitoring_client_ready`
  - `es_namespace_ready`
  - `es_cluster_ready`
  - `sidecar_config_ready`
  - `sidecar_runtime_ready`
  - `monitoring_data_ready`
  - `es_curl_test_ready`
- Parameterization:
  - `es_namespace`, `monitoring_namespace`
  - `cluster_prefix`, `monitoring_cluster_prefix`
  - `monitoring_endpoint`, `metricbeat_image`, `filebeat_image`
  - `monitoring_index_prefix`

### 16) `transform-job-recovery`

- Base task: restore transform capacity and resume transform progress.
- Independent precondition groups:
  - `es_namespace_ready`
  - `es_cluster_ready`
  - `transform_nodeset_ready`
  - `source_data_ready`
  - `transform_definition_ready`
  - `transform_runtime_ready`
  - `dest_progress_ready`
  - `curl_test_ready`
- Parameterization:
  - `es_namespace`, `cluster_prefix`
  - `transform_nodeset_name`, `transform_job_id`
  - `source_index`, `dest_index`
  - `checkpoint_configmap_name`, `min_dest_docs`

## Independent Resource Group Rules (Elasticsearch-Specific)

Apply these rules uniformly when rewriting each case:

1. One unit, one concern

- Examples: namespace, RBAC, service objects, workload readiness, data seed, fault injection.

2. Probe outcome, not implementation

- Ask "is required state already true?"
- Do not require specific implementation details unless they are part of the explicit case objective.

3. Apply smallest reconciliation

- apply only for the concern of that unit.
- avoid broad apply bundles that mutate unrelated state.

4. Verify same concern only

- verify should not become a multi-concern gate.

5. Split cross-namespace concerns

- for Elasticsearch + monitoring/ingress/minio cases, keep units separated by namespace and responsibility.

6. Preserve carried-over workflow state

- no namespace reset in preconditions.
- avoid reapplying core cluster manifests when a probe can prove existing state is already valid.

## Parameterization Standard

### Core identity pack (most cases)

- `es_namespace`
- `cluster_prefix`
- `http_service_name`

### Transition pack

- `from_version`, `to_version`
- `from_replicas`, `to_replicas`

### Security pack

- `tls_secret_name`
- `elastic_password_*_secret_name`
- `*_ca_*` names
- `ingress_tls_secret_name`

### Data/assertion pack

- `index_name`
- `seed_doc_count`
- `expected_doc_count`
- `expected_node_count`

### Integration pack

- monitoring: `monitoring_namespace`, `monitoring_endpoint`
- snapshot: `minio_endpoint`, `snapshot_repo_name`, `snapshot_name`
- transform: `transform_job_id`, `source_index`, `dest_index`

### Naming rules

- use explicit names only: `*_secret_name`, `*_configmap_name`, `*_replicas`, `*_version`
- avoid ambiguous names: `target`, `value1`, `flag`, `config`
- defaults should represent canonical standalone baseline

### Wiring rules

Each parameterized value must be used consistently across:

- `probe`
- `apply`
- `verify`
- `detailedInstructions`
- oracle checks or oracle expectations

## Recommended Execution Order (Migration Work)

1. Cleanup first:

- remove `resources/elasticsearch/full-restart-upgrade-ha-hard/`
- remove references to that folder from Elasticsearch docs/index lists

2. Low-coupling base cases:

- `deploy-core-cluster`
- `bootstrap-initial-master-nodes`
- `scale-up-new-nodeset`
- `full-restart-upgrade-ha`

3. Security and config cases:

- `rotate-http-certs`
- `rotate-elastic-password`
- `file-realm-user-roles-merge`
- `transport-additional-ca-trust`
- `secure-http-ingress`

4. Multi-system and recovery-heavy cases:

- `snapshot-repo-setup`
- `stack-monitoring-sidecars`
- `transform-job-recovery`
- `internal-http-service-drift`
- `seed-hosts-repair`
- `master-downscale-voting-exclusions`
- `safe-downscale-with-shard-migration`

## Acceptance Criteria For The Migration

For each migrated Elasticsearch case:

1. `test.yaml` loads with current schema expectations.
2. setup reaches `ready` in kind smoke (with no namespace reset logic).
3. oracle command executes via `oracle.verify.commands`.
4. precondition units are independent and concern-scoped.
5. parameter names are clear and reusable in workflow chaining.

Repository-level criteria:

1. `resources/elasticsearch/full-restart-upgrade-ha-hard` no longer exists.
2. Elasticsearch case listings/docs do not reference `full-restart-upgrade-ha-hard`.
3. Elasticsearch migration backlog tracks exactly 16 cases.
