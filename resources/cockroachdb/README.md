# CockroachDB Operator Benchmark Suite

This directory contains benchmarks that simulate the reconcile loop operations from the CockroachDB Operator. Each benchmark represents a specific operator action that agents can perform **instead of** the operator.

## Overview

The CockroachDB Operator uses a controller-runtime reconcile loop to manage CockroachDB clusters. The main reconcile logic is in `pkg/controller/cluster_controller.go`, which delegates to various "actors" via the Director pattern (`pkg/actor/director.go`).

Each benchmark in this suite corresponds to one operator actor/action, allowing AI agents to learn and perform operator-like reconciliation tasks.

## ⚠️ Important: Testing Agent Capabilities, Not Operator

**Key Principle:** These tests evaluate **agent capabilities**, not operator functionality.

### What This Means:

✅ **Agents should work with native Kubernetes resources:**
- Direct manipulation of `StatefulSet`, `Service`, `PVC`, `Pod`, `Secret`, `ConfigMap`
- Use `kubectl` commands and Kubernetes API directly
- Understand and manage Kubernetes primitives

❌ **Agents should NOT rely on operator abstractions:**
- No `CrdbCluster` CRD or other custom resources
- No operator reconciliation loop
- No operator-specific annotations or labels (unless for compatibility)

### Why This Matters:

1. **Tests Real Agent Understanding:**
   - Agent must understand how StatefulSets work, not just how to modify a CRD
   - Agent must know about pod ordering, persistent volumes, headless services
   - Agent must understand the actual Kubernetes resources, not operator abstractions

2. **Operator-Independent:**
   - No need to install CockroachDB operator CRDs
   - No dependency on operator behavior or bugs
   - Tests are portable across different CockroachDB deployment methods

3. **More Realistic for Agent Use Cases:**
   - In reality, agents often work with existing deployments (no operator)
   - Agents need to handle raw Kubernetes resources
   - Better tests of troubleshooting and operational skills

### Test Setup Approach:

```bash
# ❌ OLD: Using CRD
kubectl apply -f crds.yaml
kubectl apply -f crdb-cluster.yaml  # CrdbCluster CR
# Wait for operator to create resources...

# ✅ NEW: Direct resource creation
kubectl apply -f statefulset.yaml   # Direct StatefulSet
kubectl apply -f service.yaml       # Direct Service  
kubectl apply -f pvc.yaml           # Direct PVC (if needed)
```

This way, we test if the agent truly understands CockroachDB operations at the Kubernetes primitive level.

## Benchmark Structure

Each benchmark is organized in its own directory with:
- `test.yaml` - Benchmark configuration with:
  - `preOperationCommands` - Setup steps
  - `detailedInstructions` - Task description for agents (concise, action-oriented)
  - `operatorContext` - Technical details about the operator action (for human reference)
  - `verification` - How to verify success
- `resource/` - Kubernetes manifests needed for the benchmark

## Benchmark Summary

Complete suite of 19 CockroachDB operator benchmarks:


| # | Benchmark |Type | Purpose |
|---|-----------|------------|---------|
| 1 | monitoring-integration | Observability |  Prometheus/Grafana setup |
| 2 | version-check | Pre-deploy |  Validate container image version |
| 3 | generate-cert | TLS setup |  Generate TLS certificates |
| 4 | deploy | Core |  Deploy Services, StatefulSet, PDB |
| 5 | initialize | Post-deploy |  Initialize CockroachDB cluster |
| 6 | partitioned-update | Maintenance |  Rolling version upgrade |
| 7 | resize-pvc | Maintenance |  Expand storage capacity |
| 8 | decommission | Scale-down |  Safely remove nodes |
| 9 | cluster-restart | On-demand |  Rolling pod restart |
| 10 | expose-ingress | External access |  Create Ingress resources |
| 11 | cluster-settings | SQL Operations |  Modify cluster settings via SQL |
| 12 | zone-config | SQL Operations |  Configure replication zones |
| 13 | major-upgrade-finalize | Upgrade |  Major version upgrade + finalization |
| 14 | certificate-rotation | Security |  Zero-downtime cert rotation |
| 15 | quorum-loss-recovery | Disaster Recovery |  Recover from catastrophic failure |
| 16 | multi-region-setup | Topology |  Multi-zone/region deployment |
| 17 | node-drain-maintenance | Maintenance |  Drain node without deletion |
| 18 | health-check-recovery | Monitoring |  Automated pod recovery |
| 19 | backup-restore | Data Protection |  Backup and restore procedures |

## References

- [CockroachDB Operator Source](https://github.com/cockroachdb/cockroach-operator)
- [Operator Pattern](https://kubernetes.io/docs/concepts/extend-kubernetes/operator/)
- [StatefulSet](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)
- [CockroachDB Documentation](https://www.cockroachlabs.com/docs/)
