# Ray on Kubernetes Runbook (Benchmark)

This runbook covers Ray benchmark cases in `resources/ray/`.
For general Kubernetes knowledge, see `resources/KUBERNETES.md`.

## Common environment

- Namespace: `ray`.
- Ray head Deployment: `ray-head`.
- Ray worker Deployment: `ray-worker`.
- Head Service: `ray-head.ray.svc`.
- Ray client pod (when present): `ray-client`.
- Curl test pod (dashboard case): `curl-test`.

Common inspect commands:
```bash
kubectl -n ray get deploy,svc,pod
kubectl -n ray logs deploy/ray-head --tail=200
kubectl -n ray logs deploy/ray-worker --tail=200
```

## Case index

- `deploy_cluster`: install Ray head + worker from manifests
- `scale_workers`: scale worker deployment to 3 replicas
- `upgrade_version`: upgrade Ray image version
- `worker_recovery`: recover workers from a bad startup command
- `job_submission`: run a Ray job from a client pod
- `dashboard_exposure`: expose the Ray dashboard port via Service
- `teardown_cluster`: remove Ray cluster resources

---

## Case: deploy_cluster

Scenario
- Namespace `ray` exists with a `ray-client` pod.
- No Ray head/worker deployments or Service exist yet.

Key resources
- Manifests: `resource/ray-head.yaml`, `resource/ray-head-service.yaml`, `resource/ray-worker.yaml`.

Validation
```bash
kubectl -n ray exec ray-client -- python -c \
  "import ray; ray.init(address='ray-head:6379'); print('ok'); ray.shutdown()"
```

Success criteria
- `ray-head` is ready.
- `ray-worker` has 2 ready replicas.
- The validation command prints `ok`.

---

## Case: scale_workers

Scenario
- Ray head and workers are running.
- Worker deployment is only 1 replica.

Validation
```bash
kubectl -n ray get deploy ray-worker
kubectl -n ray exec ray-client -- python -c \
  "import ray; ray.init(address='ray-head:6379'); print(len([n for n in ray.nodes() if n.get('Alive')])); ray.shutdown()"
```

Success criteria
- `ray-worker` has 3 ready replicas.
- Ray reports at least 4 live nodes.

---

## Case: upgrade_version

Scenario
- Ray head/worker are running with image `rayproject/ray:2.8.0`.

Validation
```bash
kubectl -n ray get deploy ray-head -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl -n ray get deploy ray-worker -o jsonpath='{.spec.template.spec.containers[0].image}'
```

Success criteria
- Both deployments use `rayproject/ray:2.9.0` and are ready.

---

## Case: worker_recovery

Scenario
- Head is running.
- Worker pods crash loop because of a bad flag in the startup command.

Validation
```bash
kubectl -n ray get pod -l app=ray-worker
kubectl -n ray exec ray-client -- python -c \
  "import ray; ray.init(address='ray-head:6379'); print(len([n for n in ray.nodes() if n.get('Alive')])); ray.shutdown()"
```

Success criteria
- `ray-worker` has 2 ready replicas.
- Ray reports at least 3 live nodes.

---

## Case: job_submission

Scenario
- Ray cluster is running.
- `ray-client` has `/opt/job.py`, but it points to the wrong head address.

Validation
```bash
kubectl -n ray exec ray-client -- python /opt/job.py
```

Success criteria
- The command exits 0 and prints `pong`.

---

## Case: dashboard_exposure

Scenario
- Ray cluster is running.
- The head Service exposes only the GCS port.

Validation
```bash
kubectl -n ray exec curl-test -- curl -sS -o /dev/null -w "%{http_code}" \
  http://ray-head:8265/api/cluster_status
```

Success criteria
- The validation command returns HTTP 200.

---

## Case: teardown_cluster

Scenario
- Ray cluster resources exist in the `ray` namespace.

Validation
```bash
kubectl -n ray get deploy ray-head
kubectl -n ray get deploy ray-worker
kubectl -n ray get svc ray-head
```

Success criteria
- The above resources are deleted (namespace remains).
