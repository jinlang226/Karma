# Namespace Virtualization (Namespace Roles)

## Overview

KARMA uses **namespace virtualization** to provide test-case isolation, deterministic workflow composition, and scalable parallel execution on shared Kubernetes clusters.

Most Kubernetes-based test frameworks implicitly assume a fixed namespace (or a small set of static namespaces). This becomes problematic when composing multiple test cases into longer workflows: resources from one stage may collide with, leak into, or interfere with subsequent stages. Namespace virtualization addresses this by decoupling **logical namespaces** used in a test case from **physical namespaces** created in the cluster.

In KARMA, test cases operate on **namespace roles** (logical placeholders such as `cluster_a`, `cluster_b`, or `control`) rather than hard-coded namespaces. At runtime, KARMA resolves each role to a unique physical namespace, and ensures role-to-namespace bindings remain consistent for the duration of a workflow run.

---

## Problem: Resource Collision in Composed Workflows

Consider two independent stages executed sequentially:

- Stage 1: Create a RabbitMQ cluster A (v3.6)
- Stage 2: Create a RabbitMQ cluster B (v3.8)

If both stages use the same static namespace (e.g., `rabbitmq`), Stage 2 may observe resources left by Stage 1 and either:

- fail due to naming collisions (Deployments/Services already exist),
- produce undefined behavior (modifies the wrong resources),
- or accidentally "succeed" due to leftover state.

This breaks benchmark isolation and makes composed workflows unstable and non-reproducible.

---

## Design Goals

Namespace virtualization in KARMA is designed to:

1. **Make stage isolation explicit and reliable** via namespace roles.
2. **Allow controlled sharing** when workflows intentionally require state continuity.
3. **Enable parallel execution** of many workflows on the same cluster without global namespace locking.
4. **Remain deterministic** across workflow parse/runtime phases without relying on fixed namespace names.

---

## Core Abstraction: Namespace Roles

A namespace role is a logical identifier referenced by a stage. Examples:

- `cluster_a` — the namespace where service instance A lives
- `cluster_b` — the namespace where service instance B lives
- `control` — a control-plane namespace for tooling/oracle clients

Roles are stable within a workflow definition, but are **not** real namespaces.

### Why Roles?

Roles allow workflows to describe dependencies and shared state semantically (e.g., "migrate from A to B") without hardcoding cluster-specific namespace names or requiring manual cleanup.

---

## Role Resolution and Binding

At run time, KARMA resolves each role to a unique physical namespace.

Example:

- `cluster_a` → `karma-demo-9f3c-cluster-a`
- `cluster_b` → `karma-demo-9f3c-cluster-b`
- `control`   → `karma-demo-9f3c-control`

The generated names do not need to be consistent across different runs. The only requirement is:

- Role bindings are deterministic **within the run** and persist across workflow stages.

### Binding Lifetime

A role-to-namespace binding persists for the duration of a workflow run (runtime execution and final sweep). This ensures:

- preconditions and `oracle.verify()` reference the same namespace context
- stages that require shared state can reliably observe each other’s resources

---

## Default Isolation vs Explicit Sharing

### Recommended: Stage Isolation

Stage isolation should be configured explicitly by using distinct namespace aliases/roles per stage. Under role-based virtualization, isolation typically means:

- Stage uses its own roles, and those roles resolve to unique namespaces for that run.

If stages omit explicit namespace aliases, workflow normalization falls back to a shared `default` alias, so isolation is not automatic.

### Explicit Sharing

Some workflows require continuity across stages. Example:

- Stage 1: Install RabbitMQ cluster A (v3.7)
- Stage 2: Upgrade cluster A to v4.0

This only works if both stages reference the same logical role (e.g., `cluster_a`), so that both stages operate on the same physical namespace binding.

In KARMA, **sharing is opt-in** and expressed by selecting the same namespace roles across stages.

---

## Multi-Namespace Stages and Namespace Binding

Some stages operate over multiple namespaces simultaneously. Example: a migration stage that reads from `cluster_a` and writes to `cluster_b`.

In these cases, KARMA supports explicit role binding semantics so that the test case can refer to roles consistently.

Example workflow sketch:

```yaml
workflow_id: demo
namespaces: [cluster_a, cluster_b, control]
stages:
  - id: s1
    service: rabbitmq-experiments
    case: create_cluster
    namespaces: [cluster_a]

  - id: s2
    service: rabbitmq-experiments
    case: migrate
    namespaces: [cluster_a, cluster_b]
    namespace_binding:
      source: cluster_a
      target: cluster_b
```

## Why Binding Matters

When a stage uses multiple namespaces:

- The ordering and interpretation of namespaces must be deterministic.
- Role names must map to stable semantics inside the test logic (e.g., `source` vs `target`).

Role binding makes cross-namespace tasks explicit and avoids ambiguity.

---

## Namespace Role Ownership (Framework-Managed vs Case-Managed)

Namespace virtualization solves *naming* and *isolation*, but some tasks require
the agent to manage namespace lifecycle itself (for example, "create namespace
X and deploy a workload into it").

If the framework pre-creates every bound namespace, it can accidentally solve
part of the task. To support this class of tests without giving up isolation,
KARMA should distinguish **namespace binding** from **namespace ownership**.

### Ownership Modes

- `framework` (default): KARMA pre-creates the namespace and manages lifecycle.
- `case`: KARMA binds and exposes the namespace name (e.g. via `BENCH_NS_*`) but
  does not pre-create it; the task/agent is expected to create it.

### Why This Is Needed

- Preserves namespace isolation and unique naming.
- Enables namespace-creation / namespace-recovery challenges.
- Prevents framework setup from pre-solving task objectives.

### Runtime Semantics (Proposed)

- Role resolution remains unchanged: every role gets a deterministic physical
  namespace within the run.
- Environment injection remains unchanged: cases/oracles still receive
  `BENCH_NAMESPACE` / `BENCH_NS_<ROLE>` names for all roles.
- Namespace precreation changes: only `framework`-owned roles are pre-created.
- Final cleanup should delete both `framework`-owned and `case`-owned roles
  (ignore-not-found) to avoid leaks.

This extension is orthogonal to namespace binding and can be introduced without
changing workflow stage binding semantics.

---

## Interaction with Workflow Runtime

Workflow runtime executes stage preconditions live (`probe -> apply -> verify`).
Namespace virtualization integrates cleanly with this model because:

- Runtime does not require fixed namespace names across runs.
- It only requires that stage-to-stage role equality is preserved (shared vs isolated roles).
- Role bindings are resolved consistently within the run.

In other words, runtime stage setup cares about whether stage N and stage N+1
share roles, not whether the physical namespace strings are identical across
different runs.

---

## Parallel Execution

Namespace virtualization enables high-throughput benchmarking:

- Multiple workflows can run concurrently on the same cluster.
- Stages from different workflows do not collide because their roles resolve to distinct namespaces.
- No global namespace locking is required by default.

This addresses a common limitation of SRE test frameworks that rely on shared static namespaces and therefore require serialized execution.

---

## Scope and Limitations

Namespace virtualization provides isolation for namespaced resources, including:

- Deployments
- Services
- Pods
- ConfigMaps
- Secrets
- Namespaced Custom Resources (CRs)

However, it does not fully isolate:

- Cluster-scoped resources (CRDs, ClusterRoles, StorageClasses)
- Shared infrastructure (nodes, ingress controllers, external DNS, load balancers)
- Persistent volumes (depending on provisioning model)
- External dependencies (cloud services, SaaS APIs)

KARMA treats namespace virtualization as the default isolation boundary and requires explicit handling when tasks depend on cluster-scoped resources. Potential approaches include:

- Explicit declaration of cluster-scoped dependencies
- Serialization or locking for global resources
- Dedicated clusters per run (heavyweight)
- Resource prefixing and ownership tracking

---

## Summary

Namespace virtualization in KARMA replaces static namespaces with role-based bindings:

- Stages operate on logical namespace roles (`cluster_a`, `cluster_b`, etc.).
- KARMA resolves roles into unique physical namespaces per run.
- Isolation and sharing are both explicit via role/alias design.
- Multi-namespace stages use explicit binding (e.g., `source` / `target`).

This enables deterministic workflow composition and scalable parallel execution.

Namespace virtualization is a foundational building block for composing independent test cases into realistic multi-stage microservice lifecycle workflows.
