# Elasticsearch Operator Benchmark Suite (No-Operator)

This directory defines lifecycle benchmarks derived from the ECK Elasticsearch controller.
Each benchmark simulates a reconcile-loop action that an agent must perform directly
using Kubernetes primitives and the Elasticsearch API (no CRDs/operator usage by the agent).

Key rules:
- Agent must not rely on ECK CRDs or the operator.
- Agent must work with native resources: StatefulSet, Service, ConfigMap, Secret, PDB, PVC, Pod.
- Ingress-nginx is the only allowed external operator dependency.
- All test cases are compatible with the free (Basic) Elasticsearch distribution.

Structure:
```
resources/elasticsearch/<case>/
  test.yaml
  resource/
  oracle/
```

Each test.yaml includes:
- preOperationCommands: precondition setup
- detailedInstructions: prompt for the agent
- verification: oracle checks

Later, resource manifests and automated oracles can be added under each case.
