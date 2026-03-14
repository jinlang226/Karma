# CockroachDB Operator General Handbook

This handbook provides general knowledge about CockroachDB and Kubernetes operators. It is not tied to any specific benchmark case.

## Core Components

- **Namespace**: typically `cockroachdb`
- **CRD**: `CrdbCluster` (from cockroach-operator)
- **StatefulSet**: `crdb-cluster` (manages pods)
- **Discovery Service**: `crdb-cluster` (headless, for DNS)
- **Public Service**: `crdb-cluster-public` (client access)
- **PodDisruptionBudget**: `crdb-cluster-budget` (HA protection)

Common inspect commands:
```bash
kubectl -n cockroachdb get crdbcluster
kubectl -n cockroachdb get statefulset,service,pvc,pdb
kubectl -n cockroachdb get pods -o wide
kubectl -n cockroachdb logs crdb-cluster-0
```

## CockroachDB Architecture

**Distributed SQL Database**:
- Multi-node cluster with automatic replication
- Raft consensus for consistency
- Survivable across node failures
- No single point of failure

**Key Concepts**:
- **Node**: Each pod is a CockroachDB node
- **Replica**: Data is replicated across nodes (default: 3 copies)
- **Range**: Unit of data distribution
- **Quorum**: Majority of nodes needed for operations (2 out of 3)

## CockroachDB CLI Commands

All commands run inside a pod:

```bash
# Initialize cluster (run once after deploy)
./cockroach init --insecure

# Check node status
./cockroach node status --insecure

# SQL shell
./cockroach sql --insecure

# Decommission a node
./cockroach node decommission <node-id> --insecure

# Check cluster health
./cockroach node status --insecure --format=table
```

## Operator Actions

The CockroachDB operator performs these reconciliation actions:

1. **Deploy**: Create StatefulSet, Services, PDB
2. **Initialize**: Run `cockroach init` command
3. **Scale**: Add or remove nodes
4. **Upgrade**: Rolling update to new version
5. **Resize PVC**: Expand storage volumes
6. **Certificate Rotation**: Update TLS certificates
7. **Backup/Restore**: Manage backups
8. **Decommission**: Safely remove nodes

## StatefulSet Characteristics

- **Stable identities**: pods named `crdb-cluster-0`, `crdb-cluster-1`, etc.
- **Stable DNS**: `crdb-cluster-0.crdb-cluster.cockroachdb.svc.cluster.local`
- **Ordered operations**: pods created/deleted in order
- **Persistent storage**: each pod has a PVC

## Services Explained

**Discovery Service (headless)**:
- `clusterIP: None`
- Creates DNS entries for each pod
- Used for internal cluster communication
- Pods use this for `--join` flag

**Public Service**:
- Normal ClusterIP (or LoadBalancer)
- Load balances across all pods
- Used by SQL clients

## TLS Configuration

**Insecure mode** (testing):
- `--insecure` flag on cockroach commands
- No certificate required
- Easy for development

**Secure mode** (production):
- Requires CA, node, and client certificates
- Certificates stored in Kubernetes Secrets
- Mounted at `/cockroach/cockroach-certs`
- More complex but secure

## Storage (PVC)

Each pod gets a PVC for data persistence:
- Default name pattern: `datadir-<statefulset-name>-<ordinal>`
- Example: `datadir-crdb-cluster-0`
- Mount path: `/cockroach/cockroach-data`

**PVC Expansion**:
- Requires StorageClass with `allowVolumeExpansion: true`
- Some storage drivers expand filesystem automatically
- Others require pod restart

## High Availability

**PodDisruptionBudget**:
- Protects cluster during voluntary disruptions
- Example: `minAvailable: 1` for 3-node cluster
- Allows eviction of at most 2 pods at once

**Quorum**:
- 3-node cluster: can lose 1 node
- 5-node cluster: can lose 2 nodes
- Formula: `floor(N/2)`

## Common Operations

**Check cluster health**:
```bash
kubectl -n cockroachdb exec crdb-cluster-0 -- \
  ./cockroach node status --insecure
```

**SQL query**:
```bash
kubectl -n cockroachdb exec crdb-cluster-0 -- \
  ./cockroach sql --insecure -e "SELECT 1;"
```

**View logs**:
```bash
kubectl -n cockroachdb logs crdb-cluster-0 --tail=100
```

**Check storage**:
```bash
kubectl -n cockroachdb get pvc
kubectl -n cockroachdb exec crdb-cluster-0 -- df -h /cockroach/cockroach-data
```

## Troubleshooting

**Cluster won't initialize**:
- Check all pods are Running
- Verify `--join` flag includes all node addresses
- Run `cockroach init` from any pod

**Node not joining cluster**:
- Check DNS resolution between pods
- Verify discovery service exists
- Check pod logs for connection errors

**Storage issues**:
- Verify PVCs are Bound
- Check StorageClass configuration
- Confirm PV capacity matches PVC request

**Performance problems**:
- Check resource requests/limits
- Monitor CPU and memory usage
- Review CockroachDB metrics (http port 8080)

## CrdbCluster CR Spec

Key fields:
```yaml
spec:
  nodes: 3                        # Number of replicas
  image:
    name: cockroachdb/cockroach:v24.1.0
  dataStore:
    pvc:
      spec:
        resources:
          requests:
            storage: 10Gi
  resources:
    requests:
      cpu: "1"
      memory: "2Gi"
  tlsEnabled: false              # Insecure for testing
  grpcPort: 26257                # Inter-node + SQL
  httpPort: 8080                 # Admin UI + metrics
```

## Best Practices

1. **Always initialize**: Cluster won't work without `cockroach init`
2. **Maintain quorum**: Keep majority of nodes healthy
3. **Rolling operations**: Update one node at a time
4. **Monitor metrics**: Use http port for Prometheus scraping
5. **Backup regularly**: Use `BACKUP` SQL command
6. **Plan capacity**: Consider growth when sizing storage

## Additional Resources

- CockroachDB docs: https://www.cockroachlabs.com/docs/
- Operator source: https://github.com/cockroachdb/cockroach-operator
- CRD reference: https://github.com/cockroachdb/cockroach-operator/blob/master/install/crds.yaml
