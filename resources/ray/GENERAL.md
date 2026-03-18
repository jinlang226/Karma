# Ray Base Functionality Handbook

This handbook describes the modern Ray base-capability suite in `resources/ray/`.

These cases intentionally cover only the base capability being benchmarked:

- cluster bootstrap
- dashboard exposure
- job execution
- worker scaling
- version upgrade
- cluster teardown

Adversarial setup such as bad worker flags, wrong job addresses, or missing
dashboard ports should be modeled as separate overlays/plugins in the future.

## Common environment

- Namespace: assigned at runtime via `${BENCH_NAMESPACE}`
- Cluster prefix: assigned via `cluster_prefix` (default: `ray`)
- Head deployment/service: `<cluster_prefix>-head`
- Worker deployment: `<cluster_prefix>-worker`
- Client pod: `<cluster_prefix>-client`
- Curl pod: `<cluster_prefix>-curl-test`

## Common inspect commands

```bash
kubectl -n ${BENCH_NAMESPACE} get deploy,svc,pod,job
kubectl -n ${BENCH_NAMESPACE} logs deploy/${BENCH_PARAM_CLUSTER_PREFIX:-ray}-head --tail=200
kubectl -n ${BENCH_NAMESPACE} logs deploy/${BENCH_PARAM_CLUSTER_PREFIX:-ray}-worker --tail=200
```
