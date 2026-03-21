# Kind Cluster Setup

KARMA now ships a repo-owned one-click cluster bootstrap for local Kind development.

Primary entrypoint:

```bash
./scripts/setup-cluster.sh --provider kind
```

Direct Kind entrypoint:

```bash
./scripts/setup-kind-cluster.sh
```

## What it does

The script is designed for benchmark use, not just "nodes are Ready".

It will:

1. Build a repo-owned Kind node image `karma/kind-node:v1.32.1` from the official `kindest/node:v1.32.1` base unless you opt into the official image directly.
2. Create a 4-node Kind cluster using `scripts/kind/cluster-4node.yaml`.
3. Wait for core system workloads (`coredns`, `kindnet`, `kube-proxy`, `local-path-provisioner`).
4. Run an in-cluster DNS smoke.

## Why the repo-owned image exists

The previous local setup depended on `jacksonarthurclark/aiopslab-kind-arm:latest`.
That image is effectively the official Kind node image plus a small package delta (`socat`, `udev`).

The repo-owned image keeps that delta while removing the dependency on a personal registry tag.

If you want to compare against the official Kind node image directly:

```bash
./scripts/setup-kind-cluster.sh --use-official-node-image
```

## Common options

Recreate an existing cluster:

```bash
./scripts/setup-kind-cluster.sh --recreate
```

Keep temporary smoke namespaces for debugging:

```bash
./scripts/setup-kind-cluster.sh --keep-smoke-namespaces
```

## Future extension

`scripts/setup-cluster.sh` is provider-shaped on purpose.
Today it only supports `--provider kind`, but that gives us a stable entrypoint if we add real-cluster setup flows later.
