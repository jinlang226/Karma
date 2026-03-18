# Apache Spark on Kubernetes General Handbook

This handbook covers the Spark patterns used by the active benchmark cases in
`resources/spark/`.

## Runtime Shapes In This Suite

The active Spark base cases use two simple runtime shapes:

- local-mode Spark jobs inside Kubernetes Jobs
  - good for deterministic single-job execution checks
  - examples: `spark_pi_job_execution`, `spark_sql_job_execution`, `spark_etl_pipeline_completion`

- Spark standalone master/worker deployments
  - good for readiness and scaling checks
  - example: `spark_worker_scaling`

The suite also includes support-plane components such as:

- Spark History Server
- PVC-backed data or log storage
- ServiceAccounts, Roles, and RoleBindings
- ConfigMaps and Secrets used by Spark jobs

## Common Debug Commands

```bash
# Jobs
kubectl -n <ns> get job,pod
kubectl -n <ns> logs job/<job-name> --tail=100
kubectl -n <ns> describe job/<job-name>

# Standalone cluster
kubectl -n <ns> get deploy,svc,pod
kubectl -n <ns> logs deploy/<deployment-name> --tail=100
kubectl -n <ns> rollout status deploy/<deployment-name> --timeout=180s

# History server
kubectl -n <ns> get deploy,svc,pvc,pod
kubectl -n <ns> describe deployment/<history-deployment>
```

## Common Spark Paths

- Spark binaries: `/opt/spark/bin`
- Spark examples jar: `/opt/spark/examples/jars/spark-examples_2.12-3.5.3.jar`
- Standalone start scripts: `/opt/spark/sbin/start-master.sh`, `/opt/spark/sbin/start-worker.sh`

## Common Failure Modes

- `ImagePullBackOff`
  - verify the image exists and is pullable from the cluster

- `CreateContainerConfigError`
  - check Secret, ConfigMap, PVC, or ServiceAccount references

- Job never reaches `Complete`
  - inspect pod logs and describe output
  - confirm the command exits successfully

- Deployment never becomes Available
  - inspect rollout status, events, and logs
  - confirm the container command keeps the process alive

- PVC stays Pending
  - confirm the cluster has a default storage class

## Design Notes For Future Plugins

When you add adversarial plugins later:

- keep the base runtime/unit healthy and reusable
- inject drift in a separate precondition or plugin layer
- keep oracles read-only and outcome-focused
- prefer exact baselines over broad “good enough” probes
