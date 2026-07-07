# Resource Solver Archive

This folder archives the temporary solver scripts that were created during the
resources-folder Kind workflow audit. They are useful as repeatable, known-good
operator actions when validating workflow combinations, self-chain behavior, and
oracle behavior.

These scripts are reference validation helpers, not benchmark source of truth.
They assume the benchmark runner or an equivalent harness has already exported
the expected environment variables, such as `BENCH_NAMESPACE`, `BENCH_NS_*`, and
`BENCH_PARAM_*`. Most scripts write a short `submit.txt` message because the
workflow harness expects submit behavior.

The scripts under `solvers/` are exact copies of the `/private/tmp/solve_*.sh`
artifacts captured during the audit. Keeping them exact makes it easier to
reproduce the original workflow observations. If a script needs to become a
maintained reusable tool later, promote it deliberately instead of silently
editing the archived copy.

## Audit Notes

- Archived scripts: 80.
- Resource test cases mapped: 80.
- `Provenance` shows whether the mapping appeared in a real queue file or was
  inferred from the resource name plus script body.
- `resources-kind-queue.tsv` was the broad observation queue.
- `resources-remediation-reruns.tsv` was the confirmed-defect remediation queue.
- `resources-harness-reruns.tsv`, `elasticsearch-name-reruns.tsv`,
  `mongodb-name-reruns.tsv`, and the `solver-inconclusive*.tsv` files captured
  focused reruns.
- The `elasticsearch/rotate-http-certs` and
  `cockroachdb/monitoring-integration` solvers are archived as reference only
  because those test cases still have open design questions.
- The `mongodb/arbiters`, `elasticsearch/scale-up-new-nodeset`, and
  `elasticsearch/master-downscale-voting-exclusions` solvers include corrected
  validation behavior from the remediation audit.

## Mapping

| Resource case | Solver script | Provenance | Notes |
| --- | --- | --- | --- |
| `cockroachdb/certificate-rotation` | [`solve_crdb_certificate.sh`](solvers/solve_crdb_certificate.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `cockroachdb/cluster-settings` | [`solve_crdb_settings.sh`](solvers/solve_crdb_settings.sh) | resources-kind-queue.tsv |  |
| `cockroachdb/decommission` | [`solve_crdb_decommission.sh`](solvers/solve_crdb_decommission.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `cockroachdb/deploy` | [`solve_crdb_deploy.sh`](solvers/solve_crdb_deploy.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv, solver-inconclusive-cockroach-reruns.tsv, solver-inconclusive-reruns.tsv |  |
| `cockroachdb/expose-ingress` | [`solve_crdb_ingress.sh`](solvers/solve_crdb_ingress.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `cockroachdb/generate-cert` | [`solve_cockroachdb_generate_cert.sh`](solvers/solve_cockroachdb_generate_cert.sh) | inferred from resource/script name and script body |  |
| `cockroachdb/health-check-recovery` | [`solve_crdb_health.sh`](solvers/solve_crdb_health.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `cockroachdb/initialize` | [`solve_crdb_initialize.sh`](solvers/solve_crdb_initialize.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv, solver-inconclusive-cockroach-reruns.tsv, solver-inconclusive-reruns.tsv |  |
| `cockroachdb/major-upgrade-finalize` | [`solve_crdb_major.sh`](solvers/solve_crdb_major.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv, solver-inconclusive-cockroach-reruns.tsv, solver-inconclusive-reruns.tsv |  |
| `cockroachdb/monitoring-integration` | [`solve_crdb_monitoring.sh`](solvers/solve_crdb_monitoring.sh) | resources-kind-queue.tsv | Reference only for current design-discussion case; reset boundary still needs decision. |
| `cockroachdb/partitioned-update` | [`solve_crdb_partition.sh`](solvers/solve_crdb_partition.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `cockroachdb/version-check` | [`solve_crdb_version.sh`](solvers/solve_crdb_version.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `cockroachdb/zone-config` | [`solve_crdb_zone.sh`](solvers/solve_crdb_zone.sh) | resources-kind-queue.tsv |  |
| `demo/configmap-update` | [`solve_demo_configmap_update.sh`](solvers/solve_demo_configmap_update.sh) | inferred from resource/script name and script body |  |
| `demo/configmap-update-two-ns` | [`solve_demo_configmap_update_two_ns.sh`](solvers/solve_demo_configmap_update_two_ns.sh) | inferred from resource/script name and script body |  |
| `elasticsearch/bootstrap-initial-master-nodes` | [`solve_es_bootstrap.sh`](solvers/solve_es_bootstrap.sh) | resources-kind-queue.tsv |  |
| `elasticsearch/deploy-core-cluster` | [`solve_es_deploy.sh`](solvers/solve_es_deploy.sh) | resources-kind-queue.tsv |  |
| `elasticsearch/file-realm-user-roles-merge` | [`solve_es_file_realm.sh`](solvers/solve_es_file_realm.sh) | resources-kind-queue.tsv |  |
| `elasticsearch/full-restart-upgrade-ha` | [`solve_es_upgrade.sh`](solvers/solve_es_upgrade.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv, solver-inconclusive-reruns.tsv |  |
| `elasticsearch/internal-http-service-drift` | [`solve_elasticsearch_service_drift.sh`](solvers/solve_elasticsearch_service_drift.sh) | inferred from resource/script name and script body |  |
| `elasticsearch/master-downscale-voting-exclusions` | [`solve_es_master_downscale.sh`](solvers/solve_es_master_downscale.sh) | elasticsearch-name-reruns.tsv, resources-kind-queue.tsv, resources-remediation-reruns.tsv | Corrected validation solver: relocates primaries before downscale. |
| `elasticsearch/rotate-elastic-password` | [`solve_es_password.sh`](solvers/solve_es_password.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `elasticsearch/rotate-http-certs` | [`solve_es_http_certs.sh`](solvers/solve_es_http_certs.sh) | resources-kind-queue.tsv | Reference only for current design-discussion case; identity/regression semantics still need decision. |
| `elasticsearch/safe-downscale-with-shard-migration` | [`solve_es_safe_downscale.sh`](solvers/solve_es_safe_downscale.sh) | elasticsearch-name-reruns.tsv, resources-kind-queue.tsv |  |
| `elasticsearch/scale-up-new-nodeset` | [`solve_es_scale_up.sh`](solvers/solve_es_scale_up.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv | Corrected validation solver: reuses live Elasticsearch image for new nodeset. |
| `elasticsearch/secure-http-ingress` | [`solve_es_secure_ingress.sh`](solvers/solve_es_secure_ingress.sh) | elasticsearch-name-reruns.tsv, resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `elasticsearch/seed-hosts-repair` | [`solve_es_seed_hosts.sh`](solvers/solve_es_seed_hosts.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv, solver-inconclusive-reruns.tsv |  |
| `elasticsearch/snapshot-repo-setup` | [`solve_elasticsearch_snapshot_repo.sh`](solvers/solve_elasticsearch_snapshot_repo.sh) | inferred from resource/script name and script body |  |
| `elasticsearch/stack-monitoring-sidecars` | [`solve_elasticsearch_stack_monitoring.sh`](solvers/solve_elasticsearch_stack_monitoring.sh) | inferred from resource/script name and script body |  |
| `elasticsearch/transform-job-recovery` | [`solve_es_transform.sh`](solvers/solve_es_transform.sh) | resources-kind-queue.tsv |  |
| `elasticsearch/transport-additional-ca-trust` | [`solve_es_transport_ca.sh`](solvers/solve_es_transport_ca.sh) | resources-kind-queue.tsv |  |
| `mongodb/arbiters` | [`solve_mongo_arbiters.sh`](solvers/solve_mongo_arbiters.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv | Corrected validation solver: sets default read/write concern before adding arbiter. |
| `mongodb/certificate-rotation` | [`solve_mongodb_certificate_rotation.sh`](solvers/solve_mongodb_certificate_rotation.sh) | inferred from resource/script name and script body |  |
| `mongodb/custom-roles` | [`solve_mongodb_custom_roles.sh`](solvers/solve_mongodb_custom_roles.sh) | inferred from resource/script name and script body |  |
| `mongodb/decommission` | [`solve_mongodb_decommission.sh`](solvers/solve_mongodb_decommission.sh) | inferred from resource/script name and script body |  |
| `mongodb/deploy` | [`solve_mongo_deploy.sh`](solvers/solve_mongo_deploy.sh) | resources-kind-queue.tsv |  |
| `mongodb/external-access-horizons` | [`solve_mongo_horizons.sh`](solvers/solve_mongo_horizons.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `mongodb/health-check-recovery` | [`solve_mongodb_health_check.sh`](solvers/solve_mongodb_health_check.sh) | inferred from resource/script name and script body |  |
| `mongodb/initialize` | [`solve_mongodb_initialize.sh`](solvers/solve_mongodb_initialize.sh) | inferred from resource/script name and script body |  |
| `mongodb/manual-rbac-reset` | [`solve_mongo_rbac_reset.sh`](solvers/solve_mongo_rbac_reset.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `mongodb/mongod-config-update` | [`solve_mongodb_mongod_config.sh`](solvers/solve_mongodb_mongod_config.sh) | inferred from resource/script name and script body |  |
| `mongodb/monitoring-integration` | [`solve_mongo_monitoring.sh`](solvers/solve_mongo_monitoring.sh) | mongodb-name-reruns.tsv, resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `mongodb/password-rotation` | [`solve_mongo_password.sh`](solvers/solve_mongo_password.sh) | resources-kind-queue.tsv |  |
| `mongodb/readiness-probe-tuning` | [`solve_mongodb_probe_tuning.sh`](solvers/solve_mongodb_probe_tuning.sh) | inferred from resource/script name and script body |  |
| `mongodb/replica-scaling` | [`solve_mongodb_replica_scaling.sh`](solvers/solve_mongodb_replica_scaling.sh) | inferred from resource/script name and script body |  |
| `mongodb/setup-rbac-drift-app` | [`solve_mongo_drift_app.sh`](solvers/solve_mongo_drift_app.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `mongodb/setup-rbac-drift-reporting` | [`solve_mongo_drift_reporting.sh`](solvers/solve_mongo_drift_reporting.sh) | mongodb-name-reruns.tsv, resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `mongodb/statefulset-customization` | [`solve_statefulset_customization.sh`](solvers/solve_statefulset_customization.sh) | inferred from resource/script name and script body |  |
| `mongodb/tls-setup` | [`solve_mongo_tls.sh`](solvers/solve_mongo_tls.sh) | resources-kind-queue.tsv |  |
| `mongodb/user-management` | [`solve_mongodb_user_management.sh`](solvers/solve_mongodb_user_management.sh) | inferred from resource/script name and script body |  |
| `mongodb/version-upgrade` | [`solve_mongodb_version_upgrade.sh`](solvers/solve_mongodb_version_upgrade.sh) | inferred from resource/script name and script body |  |
| `nginx-ingress/header_canary_routing` | [`solve_nginx_header_canary.sh`](solvers/solve_nginx_header_canary.sh) | inferred from resource/script name and script body |  |
| `nginx-ingress/https_ingress_ready` | [`solve_nginx_https_ingress.sh`](solvers/solve_nginx_https_ingress.sh) | inferred from resource/script name and script body |  |
| `nginx-ingress/ingress_class_routing` | [`solve_nginx_ingress_class.sh`](solvers/solve_nginx_ingress_class.sh) | inferred from resource/script name and script body |  |
| `nginx-ingress/ingress_route_ready` | [`solve_nginx_ingress_route.sh`](solvers/solve_nginx_ingress_route.sh) | inferred from resource/script name and script body |  |
| `nginx-ingress/otel_ingress_logging_ready` | [`solve_nginx_otel.sh`](solvers/solve_nginx_otel.sh) | inferred from resource/script name and script body |  |
| `nginx-ingress/rate_limit_ingress` | [`solve_nginx_rate_limit.sh`](solvers/solve_nginx_rate_limit.sh) | resources-kind-queue.tsv | Includes sticky service behavior so multiple ingress-nginx pods share the client path for rate limiting. |
| `rabbitmq-experiments/blue_green_migration` | [`solve_rabbit_blue_green.sh`](solvers/solve_rabbit_blue_green.sh) | resources-kind-queue.tsv | Wrapper around in-tree Python solver. |
| `rabbitmq-experiments/classic_queue` | [`solve_rabbit_classic.sh`](solvers/solve_rabbit_classic.sh) | resources-kind-queue.tsv | Wrapper around in-tree Python solver. |
| `rabbitmq-experiments/failover` | [`solve_rabbit_failover.sh`](solvers/solve_rabbit_failover.sh) | resources-kind-queue.tsv | Wrapper around in-tree Python solver. |
| `rabbitmq-experiments/manual_backup_restore` | [`solve_rabbit_backup.sh`](solvers/solve_rabbit_backup.sh) | resources-kind-queue.tsv | Wrapper around in-tree Python solver. |
| `rabbitmq-experiments/manual_monitoring` | [`solve_rabbit_monitoring.sh`](solvers/solve_rabbit_monitoring.sh) | resources-harness-reruns.tsv, resources-kind-queue.tsv, resources-remediation-reruns.tsv | Wrapper around in-tree Python solver. |
| `rabbitmq-experiments/manual_policy_sync` | [`solve_rabbit_policy.sh`](solvers/solve_rabbit_policy.sh) | resources-kind-queue.tsv | Wrapper around in-tree Python solver. |
| `rabbitmq-experiments/manual_runtime_reset` | [`solve_rabbit_runtime_reset.sh`](solvers/solve_rabbit_runtime_reset.sh) | resources-kind-queue.tsv |  |
| `rabbitmq-experiments/manual_skip_upgrade` | [`solve_rabbit_skip_upgrade.sh`](solvers/solve_rabbit_skip_upgrade.sh) | resources-kind-queue.tsv | Wrapper around in-tree Python solver. |
| `rabbitmq-experiments/manual_tls_rotation` | [`solve_rabbitmq_tls_rotation.sh`](solvers/solve_rabbitmq_tls_rotation.sh) | inferred from resource/script name and script body |  |
| `rabbitmq-experiments/manual_user_permission` | [`solve_rabbit_user_permission.sh`](solvers/solve_rabbit_user_permission.sh) | resources-harness-reruns.tsv, resources-kind-queue.tsv, resources-remediation-reruns.tsv | Wrapper around in-tree Python solver. |
| `ray/cluster_ready` | [`solve_ray_cluster_ready.sh`](solvers/solve_ray_cluster_ready.sh) | resources-harness-reruns.tsv, resources-kind-queue.tsv |  |
| `ray/cluster_teardown` | [`solve_ray_cluster_teardown.sh`](solvers/solve_ray_cluster_teardown.sh) | resources-kind-queue.tsv |  |
| `ray/dashboard_reachable` | [`solve_ray_dashboard.sh`](solvers/solve_ray_dashboard.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `ray/job_execution` | [`solve_ray_job_execution.sh`](solvers/solve_ray_job_execution.sh) | resources-harness-reruns.tsv, resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `ray/version_upgrade` | [`solve_ray_version_upgrade.sh`](solvers/solve_ray_version_upgrade.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `ray/worker_scaling` | [`solve_ray_worker_scaling.sh`](solvers/solve_ray_worker_scaling.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `spark/spark_etl_pipeline_completion` | [`solve_spark_etl.sh`](solvers/solve_spark_etl.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `spark/spark_history_server_ready` | [`solve_spark_history.sh`](solvers/solve_spark_history.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `spark/spark_multi_tenant_job_execution` | [`solve_spark_multi_tenant.sh`](solvers/solve_spark_multi_tenant.sh) | resources-harness-reruns.tsv, resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `spark/spark_pi_job_execution` | [`solve_spark_pi.sh`](solvers/solve_spark_pi.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `spark/spark_runtime_bundle_ready` | [`solve_spark_runtime.sh`](solvers/solve_spark_runtime.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `spark/spark_sql_job_execution` | [`solve_spark_sql.sh`](solvers/solve_spark_sql.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
| `spark/spark_worker_scaling` | [`solve_spark_scaling.sh`](solvers/solve_spark_scaling.sh) | resources-kind-queue.tsv, resources-remediation-reruns.tsv |  |
