# Apache Spark on Kubernetes Runbook (Benchmark)

This runbook covers all Spark benchmark cases in `resources/spark/`.
For general Kubernetes knowledge, see `resources/KUBERNETES.md`. For Spark on Kubernetes
background, see `resources/spark/GENERAL.md`.

## Common Environment

- **Spark Operator**: Installed in `spark-operator` namespace
- **Applications**: Run in `default` namespace
- **ServiceAccount**: `spark` (with required RBAC permissions)
- **Spark Version**: 3.5.0
- **Base Image**: `spark:3.5.0` (official Apache Spark image)

Common debug commands:
```bash
# Check operator status
kubectl -n spark-operator get deploy,pod

# List all Spark applications
kubectl get sparkapplications

# Check application status
kubectl get sparkapplication <name> -o yaml

# View driver and executor pods
kubectl get pods -l spark-role=driver
kubectl get pods -l spark-role=executor

# Check logs
kubectl logs <pod-name>
```

## Case Index

- `deploy_spark_pi`: Troubleshoot a broken SparkPi application (beginner)
- `spark_multi_tenant`: Troubleshoot multi-tenant Spark environment (advanced)
- `spark_runtime_ops`: Fix runtime issues using kubectl commands only (advanced)
- `spark_data_skew`: Optimize multi-table join with data skew (advanced)
- `spark_streaming_autoscale`: Auto-scale streaming pipeline under traffic spikes (expert)

---

## Case: deploy_spark_pi

Folder: `resources/spark/deploy_spark_pi`

### Scenario

The Spark Operator is installed and ready to manage Spark applications.
You need to deploy a SparkPi application, verify it runs successfully,
and perform basic management operations.

### Key Resources

- **Namespace**: `default`
- **SparkApplication**: `spark-pi`
- **ServiceAccount**: `spark`
- **Application Type**: Scala
- **Main Class**: `org.apache.spark.examples.SparkPi`
- **Initial Executors**: 2

### Operations to Perform

1. **Deploy SparkApplication**: The SparkPi application will be deployed automatically
2. **Monitor Status**: Check that the application reaches "COMPLETED" state
3. **View Logs**: Examine driver logs to see the Pi calculation result
4. **List Executors**: Find all executor pods created for this application
5. **Scale**: Modify the number of executors (demonstration purposes)
6. **Delete**: Clean up the application

### Validation Commands

Check application status:
```bash
kubectl get sparkapplication spark-pi -o jsonpath='{.status.applicationState.state}'
```

View driver pod logs:
```bash
DRIVER_POD=$(kubectl get pods -l spark-role=driver,sparkoperator.k8s.io/app-name=spark-pi -o jsonpath='{.items[0].metadata.name}')
kubectl logs $DRIVER_POD | grep "Pi is roughly"
```

List all executor pods:
```bash
kubectl get pods -l spark-role=executor,sparkoperator.k8s.io/app-name=spark-pi
```

Scale executors (modify the SparkApplication):
```bash
kubectl patch sparkapplication spark-pi --type=merge -p '{"spec":{"executor":{"instances":3}}}'
```

Delete the application:
```bash
kubectl delete sparkapplication spark-pi
```

### Success Criteria

1. The SparkApplication reaches "COMPLETED" state
2. Driver logs contain a line with "Pi is roughly" followed by a number close to 3.14
3. Executor pods can be listed successfully
4. Application can be deleted cleanly

### Detailed Instructions

The SparkPi application has been deployed. It calculates the value of Pi using a Monte Carlo method.

Your tasks:
1. Verify the application completed successfully
2. Check the driver logs to see the calculated Pi value
3. List all executor pods that were created
4. (Optional) If you want to test scaling, you can modify the executor count, though this won't affect a completed job
5. Verify you can cleanly delete the SparkApplication

Use the validation commands above to complete each task.

### Operator Context

Useful commands for troubleshooting:

```bash
# Check Spark Operator logs
kubectl -n spark-operator logs deploy/spark-operator

# Describe the SparkApplication for detailed status
kubectl describe sparkapplication spark-pi

# View all events related to this application
kubectl get events --field-selector involvedObject.name=spark-pi

# Check driver pod details if it fails
kubectl describe pod -l spark-role=driver,sparkoperator.k8s.io/app-name=spark-pi

# View executor pod logs
kubectl logs -l spark-role=executor,sparkoperator.k8s.io/app-name=spark-pi
```

### Common Issues

- **Pending Pods**: Check if the cluster has enough resources
- **ImagePullBackOff**: Ensure the Spark image is accessible
- **Permission Errors**: Verify the `spark` ServiceAccount has proper RBAC
- **Application Stuck**: Check operator logs for reconciliation errors

---

## Case: spark_multi_tenant

Folder: `resources/spark/spark_multi_tenant`

### Scenario

A multi-tenant Spark environment has been set up with multiple configuration issues.
Two teams share the cluster, each with their own namespace and resource quotas.
A Spark History Server has been deployed but is not functioning correctly.

### Key Resources

- **Namespaces**: `spark-team-a`, `spark-team-b`, `spark-history`
- **SparkApplications**: `spark-pi-team-a`, `spark-pi-team-b`
- **ServiceAccounts**: `spark-team-a`, `spark-team-b`
- **ResourceQuotas**: Configured per namespace
- **History Server**: Deployment with PVC for event logs

### Hidden Issues (5 bugs)

This is a troubleshooting task. The following issues are intentionally broken:

1. **Team A RoleBinding**: Subject references wrong namespace (`default` instead of `spark-team-a`)
2. **Team A Resources**: Executor cores (2) × instances (2) = 4 CPU exceeds quota (2 CPU)
3. **History Server PVC**: References non-existent PVC (`spark-history-pvc-wrong` instead of `spark-history-pvc`)
4. **History Server Log Directory**: SPARK_HISTORY_OPTS points to `/wrong/path/spark-logs` but volume is mounted at `/mnt/spark-logs`
5. **LimitRange**: Team B has a LimitRange that may require explicit resource requests

### Success Criteria

1. Both SparkApplications reach "COMPLETED" state
2. Spark History Server pod is Running
3. All RBAC permissions are correct
4. Resource configurations fit within quotas

### Troubleshooting Commands

```bash
# Check applications in all namespaces
kubectl get sparkapplications -A

# Check Team A
kubectl describe sparkapplication spark-pi-team-a -n spark-team-a
kubectl get role spark-role -n spark-team-a -o yaml
kubectl describe resourcequota spark-quota -n spark-team-a

# Check Team B
kubectl describe sparkapplication spark-pi-team-b -n spark-team-b
kubectl get sparkapplication spark-pi-team-b -n spark-team-b -o yaml

# Check History Server
kubectl get pods -n spark-history
kubectl describe deployment spark-history-server -n spark-history
kubectl get pvc -n spark-history

# Check Spark Operator
kubectl -n spark-operator logs deploy/spark-operator --tail=100
```

---

## Case: spark_runtime_ops

Folder: `resources/spark/spark_runtime_ops`

### Scenario

A Spark data processing environment has been deployed with several runtime issues.
Unlike other tasks, you must fix these issues using **kubectl commands only** -
do NOT modify any YAML files directly.

### Key Resources

- **Namespace**: `spark-runtime`
- **SparkApplication**: `spark-data-processor`
- **Deployment**: `spark-monitor`
- **Job**: `spark-batch-processor`
- **ConfigMap**: `spark-config`
- **Secret**: `spark-credentials`

### Hidden Issues (5 bugs)

This task requires using kubectl commands to fix runtime issues:

1. **ConfigMap**: `spark.executor.memory` is set to `100m` (too low, causes OOM)
   - Fix: `kubectl patch configmap spark-config -n spark-runtime --type=merge -p '{"data":{"spark.executor.memory":"512m"}}'`

2. **Secret**: `api-key` contains "EXPIRED" (invalid credential)
   - Fix: `kubectl patch secret spark-credentials -n spark-runtime --type=merge -p '{"stringData":{"api-key":"sk-valid-key-12345"}}'`

3. **SparkApplication**: Application is suspended (`spec.suspend: true`)
   - Fix: `kubectl patch sparkapplication spark-data-processor -n spark-runtime --type=merge -p '{"spec":{"suspend":false}}'`

4. **Deployment**: Using non-existent image tag `busybox:broken-tag-v999`
   - Fix: `kubectl rollout undo deployment/spark-monitor -n spark-runtime`

5. **Job**: Job is suspended (`spec.suspend: true`)
   - Fix: `kubectl patch job spark-batch-processor -n spark-runtime --type=strategic -p '{"spec":{"suspend":false}}'`

### Success Criteria

1. SparkApplication `spark-data-processor` reaches COMPLETED state
2. Deployment `spark-monitor` has all pods Running
3. Job `spark-batch-processor` completes successfully
4. ConfigMap `spark-config` has `spark.executor.memory` >= 512m
5. Secret `spark-credentials` has valid api-key (not containing "EXPIRED")

### Required kubectl Commands

```bash
# Check current state
kubectl get sparkapplication,deployment,job,configmap,secret -n spark-runtime

# Patch ConfigMap
kubectl patch configmap spark-config -n spark-runtime --type=merge \
  -p '{"data":{"spark.executor.memory":"512m"}}'

# Patch Secret
kubectl patch secret spark-credentials -n spark-runtime --type=merge \
  -p '{"stringData":{"api-key":"sk-valid-production-key"}}'

# Resume SparkApplication
kubectl patch sparkapplication spark-data-processor -n spark-runtime --type=merge \
  -p '{"spec":{"suspend":false}}'

# Rollback Deployment
kubectl rollout undo deployment/spark-monitor -n spark-runtime

# Resume Job
kubectl patch job spark-batch-processor -n spark-runtime --type=strategic \
  -p '{"spec":{"suspend":false}}'

# Verify fixes
kubectl get sparkapplication,deployment,job -n spark-runtime
```

---

## Case: spark_data_skew

Folder: `resources/spark/spark_data_skew`

### Scenario

A multi-table join query is experiencing severe performance issues due to data skew.
The task requires detecting the skew, selecting optimization strategies, and achieving
significant performance improvement.

### Dataset

| Table | Size (Full Scale) | Description |
|-------|-------------------|-------------|
| orders | ~50GB | Large fact table with skewed product_id |
| products | ~5GB | Dimension table (5 hot products get 50% of orders) |
| customers | ~30GB | Medium dimension table |

### Data Skew Characteristics

- **Hot Keys**: product_id 1-5 receive ~50% of all orders
- **Skew Ratio**: ~10x (hot products have 10x more orders than average)
- **Impact**: Join shuffle concentrates data on few partitions

### Task Requirements

1. **Detect Data Skew**
   - Analyze order distribution per product_id
   - Calculate skew ratio (max/average orders)
   - Identify hot keys (>5x average threshold)

2. **Test Optimization Strategies**
   - Broadcast Join
   - Salting (key splitting)
   - Adaptive Query Execution (AQE)
   - Repartitioning
   - Two-phase Aggregation

3. **Measure Performance**
   - Run baseline query
   - Time each optimization strategy
   - Compare speedup percentages

4. **Achieve Target**
   - Best strategy must achieve >= 40% improvement

### Success Criteria

1. Data skew detected with ratio >= 10x
2. At least 3 optimization strategies tested
3. Best strategy achieves >= 40% performance improvement
4. Results are properly documented

### Optimization Code Examples

```python
# 1. Broadcast Join
result = orders.join(F.broadcast(products), "product_id")

# 2. Salting
orders_salted = orders.withColumn("salt", F.floor(F.rand() * 10))
orders_salted = orders_salted.withColumn(
    "product_id_salted",
    F.concat(F.col("product_id"), F.lit("_"), F.col("salt"))
)

# 3. AQE
spark.conf.set("spark.sql.adaptive.enabled", "true")
spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "true")

# 4. Two-phase Aggregation
orders_preagg = orders.groupBy("product_id", "customer_id").agg(
    F.sum(F.col("quantity") * F.col("price")).alias("revenue")
)
```

### Troubleshooting Commands

```bash
# Check job status
kubectl get sparkapplication -n spark-skew

# View driver logs
kubectl logs -n spark-skew -l spark-role=driver --tail=100

# Check executor distribution
kubectl logs -n spark-skew -l spark-role=executor --tail=50
```

---

## Case: spark_streaming_autoscale

Folder: `resources/spark/spark_streaming_autoscale`

### Scenario

A Kafka -> Spark Streaming -> Redis pipeline experiences traffic spikes.
The system must automatically scale to handle load while maintaining SLA and cost constraints.

### Traffic Pattern

| Phase | Rate | Duration | Description |
|-------|------|----------|-------------|
| Baseline | 1M events/sec | 60s | Normal load |
| Spike 2x | 2M events/sec | 90s | First spike |
| Spike 5x | 5M events/sec | 120s | Peak load |
| Cooldown | 1M events/sec | 60s | Return to normal |

### Architecture

```
[Kafka] --> [Spark Streaming] --> [Redis]
   |              |                   |
Events       Processing           Results
(topic)      (aggregation)        (cache)
```

### Task Requirements

1. **Detect Backpressure**
   - Monitor Kafka consumer lag in real-time
   - Track processing latency per micro-batch
   - Alert when lag exceeds 10,000 messages

2. **Auto-scale Cluster**
   - Initial: 5 executors
   - Scale to 10 when lag > 10K or latency > 3s
   - Scale to 20 when lag > 50K or latency > 4s
   - Scale down when lag < 1K and latency < 2s

3. **Adjust Batch Interval**
   - Default: 2000ms
   - Reduce to 500ms for higher throughput
   - Increase to 5000ms for stability

4. **Maintain SLA**
   - End-to-end latency: < 5 seconds
   - Target: 95% of events within SLA

5. **Control Costs**
   - Baseline: 5 executors (5 units/min)
   - Max allowed increase: 40% (7 executors)
   - SLA override: Up to 20 executors

### Success Criteria

| Metric | Target |
|--------|--------|
| SLA Satisfaction | >= 95% |
| Cost Increase | <= 40% |
| Scale Response Time | < 60 seconds |
| Backpressure Detection | Real-time |

### Evaluation Dimensions

1. **SLA Satisfaction Rate**: % of events processed within 5s
2. **Cost Efficiency**: (actual_cost / baseline_cost - 1) * 100%
3. **Response Time**: Time from spike detection to scaling action

### Key Commands

```bash
# Monitor Kafka lag
kubectl exec -n spark-streaming kafka-0 -- \
  kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --describe --group spark-streaming-group

# Check streaming metrics
kubectl logs -n spark-streaming -l spark-role=driver --tail=100

# Scale executors
kubectl patch sparkapplication streaming-processor -n spark-streaming \
  --type=merge -p '{"spec":{"executor":{"instances":10}}}'

# Enable dynamic allocation
kubectl patch sparkapplication streaming-processor -n spark-streaming \
  --type=merge -p '{"spec":{"dynamicAllocation":{"enabled":true,"minExecutors":5,"maxExecutors":20}}}'
```

---

## Future Cases (Planned)

- `spark_with_dependencies`: Deploy Spark job with custom JAR dependencies
- `spark_s3_integration`: Read/write data from S3-compatible storage
