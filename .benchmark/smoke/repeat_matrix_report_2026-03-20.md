# Self-Loop Carry-Over Report (2026-03-20)

This report summarizes the current carry-over chainability status of every active
`resources/*/*/test.yaml` case.

Method used:
- Full self-loop matrix pass over active cases using
  [run_repeat_matrix.py](/Users/junhan.ouyang/personal-code/Karma/.benchmark/smoke/run_repeat_matrix.py)
- Additional prior targeted repeat-workflow runs for cases that were already
  validated earlier in the session
- Explicit separation between case outcome and harness limitations

Classification meanings:
- `ideal_noop`: stage 2 preconditions stayed satisfied; no stage 2 `apply`
- `unexpected_mutation`: stage 2 passed, but preconditions still re-applied
- `carryover_failed`: stage 2 preconditions tried to re-establish the baseline
  and the repeat workflow failed
- `failed`: repeat workflow failed, but not specifically from stage 2
  carry-over precondition behavior
- `environment_blocked`: case is known not to be reliable on this Kind setup
- `harness_blocked`: repeat harness could not produce a trustworthy verdict
- `no_smoke_solver`: no auto-solver exists in the current smoke harness

## Counts

- `ideal_noop`: 19
- `unexpected_mutation`: 2
- `carryover_failed`: 2
- `failed`: 2
- `environment_blocked`: 1
- `harness_blocked`: 5
- `no_smoke_solver`: 49

## ideal_noop

- `demo/configmap-update`
- `demo/configmap-update-two-ns`
- `nginx-ingress/header_canary_routing`
- `nginx-ingress/https_ingress_ready`
- `nginx-ingress/ingress_class_routing`
- `nginx-ingress/ingress_route_ready`
- `nginx-ingress/rate_limit_ingress`
- `rabbitmq-experiments/manual_monitoring`
- `ray/cluster_teardown`
- `ray/dashboard_reachable`
- `ray/version_upgrade`
- `ray/worker_scaling`
- `spark/spark_etl_pipeline_completion`
- `spark/spark_history_server_ready`
- `spark/spark_multi_tenant_job_execution`
- `spark/spark_pi_job_execution`
- `spark/spark_runtime_bundle_ready`
- `spark/spark_sql_job_execution`
- `spark/spark_worker_scaling`

## unexpected_mutation

- `nginx-ingress/otel_ingress_logging_ready`
- `rabbitmq-experiments/blue_green_migration`

## carryover_failed

- `mongodb/deploy`
- `rabbitmq-experiments/classic_queue`

## failed

- `rabbitmq-experiments/failover`
- `rabbitmq-experiments/manual_backup_restore`

## environment_blocked

- `ray/job_execution`

## harness_blocked

- `rabbitmq-experiments/manual_policy_sync`
- `rabbitmq-experiments/manual_skip_upgrade`
- `rabbitmq-experiments/manual_tls_rotation`
- `rabbitmq-experiments/manual_user_permission`
- `ray/cluster_ready`

## no_smoke_solver

- `cockroachdb/certificate-rotation`
- `cockroachdb/cluster-settings`
- `cockroachdb/decommission`
- `cockroachdb/deploy`
- `cockroachdb/expose-ingress`
- `cockroachdb/generate-cert`
- `cockroachdb/health-check-recovery`
- `cockroachdb/initialize`
- `cockroachdb/major-upgrade-finalize`
- `cockroachdb/monitoring-integration`
- `cockroachdb/partitioned-update`
- `cockroachdb/version-check`
- `cockroachdb/zone-config`
- `elasticsearch/bootstrap-initial-master-nodes`
- `elasticsearch/deploy-core-cluster`
- `elasticsearch/file-realm-user-roles-merge`
- `elasticsearch/full-restart-upgrade-ha`
- `elasticsearch/internal-http-service-drift`
- `elasticsearch/master-downscale-voting-exclusions`
- `elasticsearch/rotate-elastic-password`
- `elasticsearch/rotate-http-certs`
- `elasticsearch/safe-downscale-with-shard-migration`
- `elasticsearch/scale-up-new-nodeset`
- `elasticsearch/secure-http-ingress`
- `elasticsearch/seed-hosts-repair`
- `elasticsearch/snapshot-repo-setup`
- `elasticsearch/stack-monitoring-sidecars`
- `elasticsearch/transform-job-recovery`
- `elasticsearch/transport-additional-ca-trust`
- `mongodb/arbiters`
- `mongodb/certificate-rotation`
- `mongodb/custom-roles`
- `mongodb/decommission`
- `mongodb/external-access-horizons`
- `mongodb/health-check-recovery`
- `mongodb/initialize`
- `mongodb/manual-rbac-reset`
- `mongodb/mongod-config-update`
- `mongodb/monitoring-integration`
- `mongodb/password-rotation`
- `mongodb/readiness-probe-tuning`
- `mongodb/replica-scaling`
- `mongodb/setup-rbac-drift-app`
- `mongodb/setup-rbac-drift-reporting`
- `mongodb/statefulset-customization`
- `mongodb/tls-setup`
- `mongodb/user-management`
- `mongodb/version-upgrade`
- `rabbitmq-experiments/manual_runtime_reset`

## Notes

- The first broad matrix pass produced trustworthy results through
  `rabbitmq-experiments/manual_backup_restore`.
- A later partial rerun exposed two harness-level issues:
  - local proxy startup can fail under sandboxed execution
  - the loop agent can miss or stall on submit/result handoff for some cases
- The `harness_blocked` cases above should be rerun after the self-loop driver
  is converted to a fully external stage controller instead of an in-bundle
  looping agent.
