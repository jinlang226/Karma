# Apache Spark Benchmark Cases

This service now exposes only the Spark base-functionality cases.
Adversarial scenarios should be added later as reusable plugins layered onto
these base cases instead of being embedded directly in `test.yaml`.

## Active Base Cases

- `spark_pi_job_execution`
  - Run a healthy SparkPi job in one namespace.
  - Base capability: single-job execution with correct service account and image.

- `spark_multi_tenant_job_execution`
  - Run one healthy SparkPi job in each active tenant namespace.
  - Base capability: namespace-isolated multi-tenant execution across a bounded 2-4 tenant set.

- `spark_history_server_ready`
  - Stand up a healthy Spark History Server with PVC-backed log storage.
  - Base capability: history server deployment/service/storage readiness.

- `spark_runtime_bundle_ready`
  - Keep the runtime support bundle healthy and run a batch processor job.
  - Base capability: config, credentials, monitor deployment, and batch job execution.

- `spark_worker_scaling`
  - Scale a healthy Spark standalone worker deployment from an exact baseline.
  - Base capability: in-place Spark worker scaling.

- `spark_sql_job_execution`
  - Run a healthy Spark SQL job and produce the expected summary output.
  - Base capability: SQL query execution.

- `spark_etl_pipeline_completion`
  - Run a healthy ETL-style Spark job backed by PVC storage and produce the expected outputs.
  - Base capability: storage-backed ETL pipeline completion.

## Plugin Direction

The old Spark troubleshooting folders mixed base functionality with drift injection.
Those scenarios should come back as plugins, not as standalone active cases.

Recommended plugin families:

- `spec_drift`
  - bad image
  - wrong memory value
  - suspended job
  - wrong mount path

- `identity_drift`
  - missing RBAC
  - wrong RoleBinding subject namespace
  - invalid service account

- `runtime_pressure`
  - quota pressure
  - low worker count
  - low shuffle partitions
  - reduced memory overhead

- `workload_stress`
  - skewed input data
  - phased traffic spikes
  - large input volume

## Mapping From The Previous Spark Folders

- `deploy_spark_pi` -> `spark_pi_job_execution` + future spec/identity plugins
- `spark_multi_tenant` -> `spark_multi_tenant_job_execution` + `spark_history_server_ready` + future quota/RBAC/storage plugins
- `spark_runtime_ops` -> `spark_runtime_bundle_ready` + future config/secret/suspend/image plugins
- `spark_data_skew` -> `spark_sql_job_execution` + future skew/AQE/partition plugins
- `spark_streaming_autoscale` -> `spark_worker_scaling` + future traffic/autoscale plugins
- `spark_etl_skew_oom` -> `spark_etl_pipeline_completion` + future skew/memory/partition plugins
