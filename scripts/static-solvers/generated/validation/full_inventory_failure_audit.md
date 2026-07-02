# Full Inventory Failure Audit

This file is a human-readable audit of the non-pass buckets in
`scripts/static-solvers/generated/validation/full_inventory_reconciled_results.jsonl`.

## Scope and method

- Scope: all **912** reconciled workflows in the final full-inventory result set.
- Audited buckets:
  - `env_chain_conflict`
  - `env_runtime_issue`
  - `resource_issue`
  - `workflow_definition_issue`
- Method:
  - started from `full_inventory_reconciled_results.jsonl`
  - mapped each non-pass workflow back to its source batch record
  - when available, inspected stage artifacts such as:
    - `precondition.log`
    - `agent.log`
    - `oracle.json`
    - `evidence.json`
  - categorized each workflow by both:
    1. **failure phase** — preflight, precondition/setup, agent/runtime, oracle verification, or workflow-definition/orchestration
    2. **dominant failure signature** — e.g. TLS handshake timeout, Ray job timeout, queue redeclaration mismatch, invalid workflow override

## Overall reconciled status

| status | count |
| --- | ---: |
| pass | 393 |
| env_chain_conflict | 380 |
| env_runtime_issue | 129 |
| resource_issue | 5 |
| workflow_definition_issue | 5 |

Non-pass total: **519 / 912**

## Executive summary

1. **`env_chain_conflict` is mostly workflow composition, not missing solver support.**
   The biggest buckets are inherited-state conflicts where a stage passes standalone but fails after earlier stages reshape the namespace.
2. **`env_runtime_issue` is dominated by runtime stability, not unsupported workflows.**
   The biggest signatures are Kubernetes API / proxy collapse, Ray job timeouts, and generic readiness stalls.
3. **`resource_issue` is a pure capacity bucket.**
   All 5 are Elasticsearch scale-up workflows skipped by the Docker memory preflight.
4. **`workflow_definition_issue` is very small and is genuinely semantic.**
   These are impossible or invalid workflow definitions/overrides, not solver gaps.

## `env_chain_conflict` audit

Total: **380**

### Phase breakdown

| phase | count | interpretation |
| --- | ---: | --- |
| workflow topology mismatch before agent execution | 155 | inherited namespace/topology already violates what the next case expects |
| agent runtime timeout/no submit on inherited state | 135 | the stage works standalone, but the inherited state causes the agent stage to stall or never submit |
| oracle verification failure on inherited state | 83 | the stage submits or runs, but the inherited state makes the oracle fail |
| other chained-stage failure on inherited state | 7 | chained-only failures that did not cleanly fall into the above buckets |

### Dominant reason categories

| category | count | interpretation |
| --- | ---: | --- |
| stage passes standalone but inherited workflow state breaks chained execution | 225 | solver is viable for the case, but prior stages leave the namespace in a state the later stage does not tolerate |
| rabbitmq queue declaration conflict after `classic_queue` stage | 72 | `manual_user_permission` inherits `app-queue` with `x-queue-mode=lazy`, then redeclares it without that argument |
| elasticsearch snapshot stage inherits namespace without required `Service/minio` | 67 | additive snapshot fixture creates MinIO objects but not the service that inherited-chain execution needs |
| rabbitmq TLS rotation inherits plain-AMQP topology | 15 | `manual_tls_rotation` expects TLS resources, but the earlier chained state leaves a non-TLS RabbitMQ deployment |
| elasticsearch seed-hosts-repair inherits incompatible service topology | 1 | the inherited `internal-http-service-drift` topology has no `es-config`-backed `es-http` target for the repair stage |

### Where the volume sits

| workflow family | count |
| --- | ---: |
| rabbitmq | 95 |
| elasticsearch | 94 |
| mongodb | 90 |
| ray | 53 |
| platform/mixed | 16 |
| cockroachdb | 15 |
| nginx | 14 |
| spark | 3 |

### Representative examples

| workflow | category | note |
| --- | --- | --- |
| `error-prone/rabbitmq-day2-lifecycle-midaudit-rollback.yaml` | RabbitMQ queue declaration conflict | inherited queue arguments differ from what `manual_user_permission` redeclares |
| `error-prone/elasticsearch-harden-snapshot-change-plan.yaml` | Elasticsearch snapshot inherited MinIO-service gap | workflow needs a chain-aware `Service/minio` helper |
| `error-prone/mongodb-rotate-users-snappy-roles-audit.yaml` | Standalone stage passes, chained state breaks it | chained run fails, but standalone probe passes |
| `error-prone/nginx-ingress-class-canary-rollback.yaml` | Standalone stage passes, chained state breaks it | oracle fails only in inherited workflow state |

## `env_runtime_issue` audit

Total: **129**

### Phase breakdown

| phase | count | interpretation |
| --- | ---: | --- |
| precondition/setup | 62 | namespace cleanup races, API/proxy collapse during setup, or setup-time readiness failure |
| agent/runtime job execution | 42 | workload-specific jobs never completed (`ray-job-runner`, `log-writer`, etc.) |
| agent/runtime execution | 13 | generic readiness / wait timeouts while the stage was running |
| oracle verification | 10 | stage ran far enough for oracle verification, but the oracle then failed on connectivity/readiness |
| workflow/runtime orchestration | 1 | workflow aborted without a structured failed stage |
| agent/runtime polling | 1 | runtime collapse while the solver was polling the cluster |

### Dominant reason categories

| category | count | interpretation |
| --- | ---: | --- |
| Kubernetes API / proxy connectivity collapse | 52 | repeated `TLS handshake timeout`, `client connection lost`, proxy `502`, OpenAPI download failure, or similar control-plane collapse |
| Ray job-runner never completed | 32 | `jobs/ray-job-runner` timed out; this is the single biggest concrete runtime bucket |
| workload readiness / wait timeout | 20 | generic `timed out waiting for the condition`, plus pod/statefulset readiness stalls |
| namespace cleanup / termination race | 9 | usually `namespaces \"cockroachdb\" already exists` while deletion is still in progress |
| Elasticsearch log-writer job never completed | 6 | `jobs/log-writer` stalled in Elasticsearch networking/ingress paths |
| service-level connectivity failure during oracle verification | 5 | the cluster existed, but the oracle could not reach the expected service endpoint (for example CockroachDB ingress SQL) |
| RabbitMQ setup job never completed | 2 | `jobs/rabbitmq-setup` timed out |
| Spark Pi job never completed | 1 | `jobs/spark-pi` timed out |
| Spark skew baseline job never completed | 1 | `jobs/spark-skew-baseline` timed out |
| workflow aborted without structured failed stage | 1 | the run died before a clean failed-stage record was produced |

### Where the volume sits

| workflow family | count |
| --- | ---: |
| spark | 39 |
| ray | 36 |
| cockroachdb | 22 |
| platform/mixed | 12 |
| rabbitmq | 9 |
| elasticsearch | 9 |
| nginx | 2 |

### Representative examples

| workflow | category | note |
| --- | --- | --- |
| `long/spark-long-observability-rollout.yaml` | Kubernetes API / proxy connectivity collapse | setup/oracle path hit repeated `TLS handshake timeout` |
| `short/platform-scaling-day-ext.yaml` | Kubernetes API / proxy connectivity collapse | originally surfaced as a false `solver_issue`; probe artifacts showed precondition-time TLS handshake collapse and proxy `502`s |
| `error-prone/ray-scale-upgrade-job-compliance-audit.yaml` | Ray job-runner never completed | `jobs/ray-job-runner` timed out |
| `short/elasticsearch-ingress-with-service-drift.yaml` | Elasticsearch log-writer job never completed | `jobs/log-writer` timed out |
| `short/cockroachdb-ingress-exposure.yaml` | service-level connectivity failure during oracle verification | ingress/UI existed, but SQL oracle could not connect to the service endpoint |
| `short/cockroachdb-cert-rotation-campaign-adversary.yaml` | namespace cleanup / termination race | setup hit `object is being deleted` during namespace recreation |

## `resource_issue` audit

Total: **5**

### Phase breakdown

| phase | count | interpretation |
| --- | ---: | --- |
| preflight resource gate | 5 | workflow skipped before execution because the Docker memory cap was too small for the expected Elasticsearch scale-up |

### Category breakdown

| category | count | interpretation |
| --- | ---: | --- |
| docker-memory preflight skip for Elasticsearch scale-up | 5 | the workflow asked for more Elasticsearch nodes than the available Docker memory budget could realistically support |

### Exact workflows

| workflow | preflight reason |
| --- | --- |
| `short/elasticsearch-cluster-stress-test.yaml` | 7 nodes, estimated ~8.0 GiB required, Docker total ~7.7 GiB |
| `short/elasticsearch-formation-discovery-scale.yaml` | 7 nodes, estimated ~8.0 GiB required, Docker total ~7.7 GiB |
| `short/elasticsearch-large-cluster-scale-sweep.yaml` | 11 nodes, estimated ~12.0 GiB required, Docker total ~7.7 GiB |
| `short/elasticsearch-scale-shard-marathon.yaml` | 7 nodes, estimated ~8.0 GiB required, Docker total ~7.7 GiB |
| `short/elasticsearch-transform-scaling.yaml` | 7 nodes, estimated ~8.0 GiB required, Docker total ~7.7 GiB |

## `workflow_definition_issue` audit

Total: **5**

### Phase breakdown

| phase | count | interpretation |
| --- | ---: | --- |
| workflow definition validation | 2 | the workflow definition was invalid before meaningful stage execution |
| workflow semantics invalid at oracle verification | 2 | the workflow overrides create impossible semantics that the oracle can never satisfy |
| oracle verification against impossible workflow semantics | 1 | a workflow/case combination asks for a setting the target system does not support |

### Exact issues

| workflow | category | note |
| --- | --- | --- |
| `adversary-capstone.yaml` | adversary `inject_at_stage` targets missing stage | references `stage_3`, but that stage ID is not present in the stage map |
| `demo/workflow-demo-adversary.yaml` | adversary `inject_at_stage` targets missing stage | references `stage_2`, but that stage ID is not present in the stage map |
| `error-prone/cockroachdb-rebalance-recovery-raftlog-rollback.yaml` | workflow/case references unsupported CockroachDB setting | oracle checks `kv.raft_log.synchronize`, which the target cluster reports as unknown |
| `short/cockroachdb-storage-management.yaml` | workflow zone-config overrides are internally invalid | `range_min_bytes >= range_max_bytes` after workflow overrides |
| `short/mongodb-password-rotation-sweep.yaml` | workflow credential overrides create impossible oracle expectations | overrides collapse app and reporting credentials into the same user, so success/failure oracle expectations conflict |

## Bottom line

1. The remaining **non-pass volume is dominated by composition and runtime stability**, not missing static-solver coverage.
2. **`env_chain_conflict`** is mostly about inherited namespace topology and chained-state drift.
3. **`env_runtime_issue`** is mostly about:
   - Kubernetes API / proxy collapse
   - Ray job completion timeouts
   - readiness/setup stalls
4. **`resource_issue`** is a narrow, well-understood Elasticsearch memory-capacity gate.
5. **`workflow_definition_issue`** is tiny and consists of truly invalid workflow semantics.
