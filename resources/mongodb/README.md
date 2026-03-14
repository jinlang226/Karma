# MongoDB Community Operator: What This Benchmark Should Simulate

This folder contains scenarios that “simulate the operator’s reconcile logic.” The MongoDB Community Operator automates a specific set of lifecycle actions; several scenarios here are outside its scope and should be modeled as DBA/Kubernetes procedures instead of operator automation.

Use this matrix to align each scenario with what the Community Operator actually does.
## Support Matrix

All benchmarks have been validated against MongoDB Community Operator reconcile logic from source code analysis:

| Benchmark | Status | Evidence | Reconcile Logic Reference |
|-----------|--------|----------|--------------------------|
| deploy | ✅ Supported | Section 3.1: CR validation<br>Section 4.1: Service creation<br>Section 7.1: StatefulSet deployment | Creates Services, StatefulSet with AutomationConfig sidecar |
| initialize | ✅ Supported | Section 6.2: AutomationConfig generation<br>Section 8.1: ConfigMap creation | Generates AutomationConfig with replica set topology, Agent performs rs.initiate() |
| replica-scaling | ✅ Supported | Section 5.4: Scaling up members<br>Section 5.6: Scaling down members<br>Section 8.2: Immediate reconcile on scale | Updates spec.members → StatefulSet replicas → AutomationConfig → rs.add()/rs.remove() |
| version-upgrade | ✅ Supported | Section 5.7: Version upgrade decision logic<br>Section 6.2: AutomationConfig with target version | spec.version → AutomationConfig → Agent rolling upgrade → FCV update |
| user-management | ✅ Supported | Section 3.4: Users watch<br>Section 6.2: AutomationConfig with users[] | spec.users → Watch Secrets → AutomationConfig → Agent creates/updates users |
| password-rotation | ✅ Supported | Section 3.5: User password Secret watch<br>Scenario 5: Password rotation flow | User Secret watch → AutomationConfig update → Agent rotates passwords |
| tls-setup | ✅ Supported | Section 2.3-2.5: TLS field validation<br>Section 3.2: Certificate Secret watch<br>Section 5.3: TLS enablement | spec.security.tls → Watch cert Secrets → AutomationConfig → Agent configures TLS |
| certificate-rotation | ✅ Supported | Section 3.5: Certificate Secret watch<br>Section 13.3: TLS Secret watch trigger | TLS Secret watch → AutomationConfig update → Agent rotates certificates |
| monitoring-integration | ✅ Supported | Section 3.3: Prometheus metrics config<br>Section 7.1: StatefulSet with metrics sidecar | spec.prometheus → StatefulSet with mongodb-agent metrics endpoint |
| monitoring-tls | ✅ Supported | Section 3.3: Prometheus with TLS<br>Section 5.3: TLS enablement | spec.prometheus + spec.security.tls → TLS-secured metrics endpoint |
| arbiters | ✅ Supported | Section 7.1: StatefulSet with arbiter pods | spec.arbiters → AutomationConfig → Agent adds arbiters to replica set |
| custom-roles | ✅ Supported | users.md: spec.security.roles reconcile | spec.security.roles → AutomationConfig → Agent creates custom roles |
| external-access-horizons | ✅ Supported | external_access.md: spec.replicaSetHorizons reconcile | spec.replicaSetHorizons → AutomationConfig → Agent configures split horizon DNS |
| readiness-probe-tuning | ✅ Supported | deploy-configure.md: spec.statefulSet.spec.template.spec.containers[mongodb-agent].readinessProbe | spec.statefulSet customization → StatefulSet template merge |
| mongod-config-update | ✅ Supported | Section 6.2: additionalMongodConfig in AutomationConfig | spec.additionalMongodConfig → AutomationConfig → Agent applies mongod config |
| statefulset-customization | ✅ Supported | Section 7.1-7.2: StatefulSet template customization | spec.statefulSet.spec.template → StatefulSet merge → Pod resources/labels/annotations |

References (raw docs in the operator repo):
- deploy-configure.md: https://raw.githubusercontent.com/mongodb/mongodb-kubernetes-operator/master/docs/deploy-configure.md
- users.md: https://raw.githubusercontent.com/mongodb/mongodb-kubernetes-operator/master/docs/users.md
- secure.md (TLS): https://raw.githubusercontent.com/mongodb/mongodb-kubernetes-operator/master/docs/secure.md
- prometheus/README.md: https://raw.githubusercontent.com/mongodb/mongodb-kubernetes-operator/master/docs/prometheus/README.md
- external_access.md: https://raw.githubusercontent.com/mongodb/mongodb-kubernetes-operator/master/docs/external_access.md
- resize-pvc.md: https://raw.githubusercontent.com/mongodb/mongodb-kubernetes-operator/master/docs/resize-pvc.md

## How to model reconcile correctly

Prefer spec-driven, idempotent updates over ad-hoc annotations:
- Scale: set `spec.members` (operator reconciles StatefulSet and replica set membership)
- Upgrade: set `spec.version`, then set `spec.featureCompatibilityVersion`
- TLS: set `spec.security.tls` to reference Secrets created by cert-manager
- Monitoring: set `spec.prometheus` (exporter sidecar); apply your own ServiceMonitor
- StatefulSet/pod knobs the operator exposes via `spec.statefulSet.spec.template` (e.g., readinessProbe overrides) should be used instead of deleting pods

Avoid modeling these as “operator” actions:
- Backups/Restores, Force Reconfig for quorum loss, Generic cluster settings via setParameter/rs.reconfig, Maintenance drain/stepDown, Auto PVC resize, Auto external Service/Ingress creation

Operator architecture notes you may want to simulate:
- Reconcile loop is spec → AutomationConfig → Agent applies rs.init/add/remove → Status updated
- Changes are observed via Kubernetes resource watches (CRs/Secrets/Pods)
- Reconcile is idempotent; removing “trigger annotations” is not how the Community Operator works

## Suggested benchmark adjustments

- Replace custom annotations like `mongodb.com/restart`, `.../cluster-settings`, `.../drain-node` with spec changes the operator actually watches (examples above).
- For TLS rotation tests, rely on cert-manager Certificate with `renewBefore` and model a rolling restart by changing a harmless pod template annotation or toggling a spec field to trigger rollout.
- For monitoring, keep `spec.prometheus` in the CR and create a ServiceMonitor YAML in the test to reflect the documented flow.
- Mark backup/quorum-loss/cluster-settings/drain/resize-pvc as DBA/Kubernetes procedures, not operator reconcile, and keep them under a different category if you want to benchmark “agent” vs. “operator”.
# MongoDB Community Operator Benchmark Suite

This directory contains benchmarks that simulate the reconcile loop operations from the MongoDB Community Kubernetes Operator. Each benchmark represents a specific operator action that agents can perform instead of the operator.

## Overview

The MongoDB Community Kubernetes Operator manages MongoDB replica sets in Kubernetes. The operator uses controller-runtime to reconcile MongoDBCommunity custom resources, delegating to various internal packages for specific tasks like user management, TLS configuration, and monitoring.

Each benchmark in this suite corresponds to one operator capability, allowing AI agents to learn and perform operator-like reconciliation tasks.

## Benchmark Structure

Each benchmark is organized in its own directory with:
- `test.yaml` - Benchmark configuration with:
  - `preOperationCommands` - Setup steps
  - `detailedInstructions` - Task description for agents (concise, action-oriented)
  - `operatorContext` - Technical details about the operator action (for human reference)
  - `verification` - How to verify success
- `resource/` - Kubernetes manifests needed for the benchmark

## Benchmark Summary

Complete suite of 16 MongoDB Community Operator benchmarks that represent **actual operator reconcile logic**, plus three workflow utility cases:

| # | Benchmark | Type | Purpose | Reconcile Logic Reference |
|---|-----------|------|---------|--------------------------|
| 1 | deploy | Core | Deploy replica set with Services, StatefulSet | Section 3.1, 4.1, 7.1 |
| 2 | initialize | Core | Initialize replica set via AutomationConfig | Section 6.2 |
| 3 | replica-scaling | Scale | Scale replica set up/down via spec.members | Section 5.4, 5.6, 8.2 |
| 4 | version-upgrade | Upgrade | Upgrade MongoDB version and FCV | Section 5.7 |
| 5 | user-management | Security | Create/update MongoDB users via spec.users | Section 3.4 |
| 6 | password-rotation | Security | Rotate user passwords via Secret watch | Section 3.5, Scenario 5 |
| 7 | tls-setup | Security | Enable TLS on running cluster | Section 2.3-2.5, 3.2, 5.3 |
| 8 | certificate-rotation | Security | Rotate TLS certificates via Secret watch | Section 3.5, 13.3 |
| 9 | monitoring-integration | Observability | Prometheus metrics via spec.prometheus | Section 3.3 |
| 10 | monitoring-tls | Observability | Prometheus metrics with TLS | Section 3.3 |
| 11 | arbiters | Topology | Add arbiters via spec.arbiters | Section 7.1 |
| 12 | custom-roles | Security | Define custom roles via spec.security.roles | users.md |
| 13 | external-access-horizons | Networking | External access via spec.replicaSetHorizons | external_access.md |
| 14 | readiness-probe-tuning | Configuration | Tune readiness probes via spec.statefulSet | deploy-configure.md |
| 15 | mongod-config-update | Configuration | Update MongoDB config via spec.additionalMongodConfig | Section 6.2 |
| 16 | statefulset-customization | Configuration | Customize pod template via spec.statefulSet.spec.template | Section 7.1-7.2 |
| 17 | manual-rbac-reset | Utility | Reconcile RBAC drift via reusable reset script artifact | Workflow utility case (non-operator) |
| 18 | setup-rbac-drift-app | Utility | Seed app/read-only user drift baseline for workflow regression studies | Workflow utility case (non-operator) |
| 19 | setup-rbac-drift-reporting | Utility | Seed reporting-role/rawRead drift baseline for workflow regression studies | Workflow utility case (non-operator) |

### Tests Removed (No Operator Reconcile Logic)

The following tests were removed as they do **not** represent operator reconcile logic:

- ❌ **backup-restore** - No automated backup reconcile logic (use mongodump/external tools)
- ❌ **cluster-restart** - No explicit restart reconcile actor (only side effect of other changes)
- ❌ **cluster-settings** - No generic setParameter reconcile logic (only operator-owned fields)
- ❌ **resize-pvc** - No automated PVC resize reconcile logic (manual procedure)
- ❌ **node-drain-maintenance** - No drain/stepdown reconcile logic (manual DBA task)
- ❌ **quorum-loss-recovery** - No force reconfig reconcile logic (emergency manual task)
- ❌ **expose-ingress** - No automated Ingress creation reconcile logic (manual pattern)
- ❌ **decommission** - Redundant with replica-scaling (uses same reconcile path)
- ❌ **health-check-recovery** - Kubernetes handles pod recovery, not operator reconcile

## MongoDB Community Operator Architecture

### Key Components

1. **ReplicaSetReconciler**: Main controller that reconciles MongoDBCommunity resources
2. **Automation Agent**: Sidecar container that manages MongoDB configuration
3. **Automation Config**: JSON configuration describing desired MongoDB state
4. **StatefulSet**: Manages MongoDB pods with stable identities
5. **Services**: Headless service for pod discovery, regular service for client access

### How It Works

The MongoDB Community Operator uses a unique architecture:

1. **Automation Config Pattern**: Instead of directly managing MongoDB, the operator generates an "automation config" (JSON document) describing the desired state
2. **Automation Agent**: Each pod runs a mongodb-agent sidecar that reads the automation config and applies it to the local MongoDB process
3. **Declarative Management**: Users declare desired state in MongoDBCommunity CR, operator translates to automation config, agent implements

This architecture is inspired by MongoDB Ops Manager and provides:
- Consistent configuration management
- Automatic recovery from failures
- Support for complex MongoDB configurations
- Separation of concerns (operator vs agent)

### Reconcile Flow

1. User creates/updates MongoDBCommunity CR
2. Operator validates the CR spec
3. Operator creates/updates Service (headless for StatefulSet)
4. Operator builds automation config based on CR spec
5. Operator creates/updates ConfigMap with automation config
6. Operator creates/updates StatefulSet with:
   - mongod container (MongoDB server)
   - mongodb-agent container (automation agent)
   - mongodb-agent-readiness-probe (health checks)
7. Pods start and agents read automation config
8. Agents configure and start MongoDB processes
9. Operator creates SCRAM credentials for users
10. Operator updates CR status with phase and conditions

## Key Differences from CockroachDB Operator

| Aspect | MongoDB Community Operator | CockroachDB Operator |
|--------|---------------------------|---------------------|
| Architecture | Automation agent pattern | Direct pod management |
| Configuration | Automation config (JSON) | Direct spec translation |
| Initialization | Agent-driven | Init containers + SQL |
| User Management | Declarative via CR | Manual or operator-driven |
| Backup | External (mongodump) | Built-in BACKUP command |
| Monitoring | Prometheus via mongodb-exporter | Built-in metrics endpoint |
| TLS | Requires cert-manager | Can self-generate or use cert-manager |
| Scaling | Update CR, agent handles | Update CR, operator handles |

## Operator Context

### Important Files in Operator

- `controllers/replica_set_controller.go`: Main reconcile loop
- `controllers/mongodb_users.go`: User management
- `controllers/mongodb_tls.go`: TLS configuration
- `controllers/prometheus.go`: Prometheus integration
- `pkg/automationconfig/`: Automation config builders
- `pkg/kube/statefulset/`: StatefulSet construction

### Custom Resource Definition

The MongoDBCommunity CRD includes:

```yaml
spec:
  members: 3                    # Replica set size
  type: ReplicaSet              # Deployment type
  version: "6.0.5"              # MongoDB version
  featureCompatibilityVersion: "6.0"  # FCV
  security:
    authentication:
      modes: ["SCRAM"]          # Auth methods
    tls:
      enabled: true             # TLS config
      certificateKeySecretRef: ...
      caCertificateSecretRef: ...
  users:                        # User definitions
  - name: user1
    db: admin
    roles: [...]
    passwordSecretRef: ...
  prometheus:                   # Monitoring config
    username: prometheus-user
    passwordSecretRef: ...
  additionalMongodConfig:       # MongoDB settings
    storage.wiredTiger.engineConfig.journalCompressor: zlib
  statefulSet:
    spec:
      volumeClaimTemplates: ... # Storage config
```

## Prerequisites

To run these benchmarks, you need:

1. **Kubernetes cluster** (kind, minikube, or real cluster)
2. **kubectl** configured to access the cluster
3. **cert-manager** (for TLS-related tests)
4. **Prometheus Operator** (optional, for monitoring test)
5. **Storage class** with volume expansion support (for PVC resize test)

## Getting Started

Each test can be run independently. The general flow:

1. Review the `test.yaml` for the benchmark
2. Run the `preOperationCommands` to set up initial state
3. Follow the `detailedInstructions` to perform the operator task
4. Use the `verification` steps to confirm success

Example:
```bash
# Run pre-operation commands from deploy test
kubectl create namespace mongodb
kubectl apply -f https://raw.githubusercontent.com/mongodb/mongodb-kubernetes-operator/master/config/crd/bases/mongodbcommunity.mongodb.com_mongodbcommunity.yaml
kubectl -n mongodb apply -f mongodb/replacing-operator/deploy/resource/

# Perform the deploy task (create Services, StatefulSet, etc.)
# ... agent performs the reconciliation ...

# Verify deployment
kubectl -n mongodb get mongodbcommunity mongodb-replica
kubectl -n mongodb get pods -l app=mongodb-replica
```

## Community vs Enterprise Operator

**MongoDB Community Operator** (this benchmark suite):
- Open source, free to use
- Manages MongoDB Community Edition
- Basic replica set management
- SCRAM authentication
- TLS via cert-manager
- External backup (mongodump)
- Prometheus monitoring via exporter

**MongoDB Enterprise Operator** (not covered here):
- Requires MongoDB Enterprise license
- Advanced features:
  - Ops Manager integration
  - Automated backups
  - Sharded clusters
  - LDAP/Kerberos authentication
  - Advanced monitoring

## References

- [MongoDB Community Operator GitHub](https://github.com/mongodb/mongodb-kubernetes-operator)
- [MongoDB Documentation](https://www.mongodb.com/docs/manual/)
- [Operator Pattern](https://kubernetes.io/docs/concepts/extend-kubernetes/operator/)
- [StatefulSet](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)
- [MongoDB Replica Sets](https://www.mongodb.com/docs/manual/replication/)
- [cert-manager](https://cert-manager.io/)
- [Prometheus MongoDB Exporter](https://github.com/percona/mongodb_exporter)

## Notes

- These benchmarks focus on the **MongoDB Community Operator**, not the Enterprise Operator
- The operator repository has been deprecated in favor of [mongodb/mongodb-kubernetes](https://github.com/mongodb/mongodb-kubernetes), but the concepts remain the same
- Some operations (like backup) require external tools since the Community Operator doesn't have built-in backup features
- Tests assume a development/testing environment; production deployments should follow MongoDB's security and operational best practices
