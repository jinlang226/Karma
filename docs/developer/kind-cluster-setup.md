# Kind Cluster Setup

Use this to create the local Kind cluster for KARMA:

```bash
./scripts/setup-cluster.sh --provider kind
```

Direct Kind entrypoint:

```bash
./scripts/setup-kind-cluster.sh
```

The script creates a 4-node Kind cluster, waits for core system workloads, and runs a DNS smoke.

## Common options

Recreate an existing cluster:

```bash
./scripts/setup-kind-cluster.sh --recreate
```

## Future extension

`scripts/setup-cluster.sh` is provider-shaped on purpose.
Today it only supports `--provider kind`, but that gives us a stable entrypoint if we add real-cluster setup flows later.
