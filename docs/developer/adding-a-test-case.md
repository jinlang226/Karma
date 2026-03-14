# Adding A Test Case

This guide focuses on authoring `resources/**/test.yaml` cases that work in both:

- single-run mode
- workflow chain mode

Use the modern schema only:

- `preconditionUnits`
- `oracle.verify.commands`
- `oracle.verify.hooks` (optional)
- `cleanUpCommands`

## Core Principle: Composition-Safe Preconditions

A case should work standalone **and** compose into a workflow stage without destroying useful carry-over state from earlier stages.

That means preconditions should prefer:

- `probe` for the exact state you need
- `apply` only when the probe fails
- `verify` to confirm the state after apply

## Chainability Rule: Avoid Unnecessary Core Workload Reapply

If a case can run after another stage that already created the core service/workload (for example a RabbitMQ cluster), do not blindly re-apply a baseline StatefulSet/Deployment in `apply`.

Bad pattern (can overwrite previous stage state):

- `apply`: always `kubectl apply -f statefulset.yaml` (fixed image/version)

Better pattern (workflow-safe):

- `probe`: check workload exists + healthy
- `apply`: create workload only if it is absent
- `verify`: wait/check healthy

This preserves upstream state such as:

- upgraded image versions
- already-rotated secrets
- tuned config that should carry into the next stage

## Example: Version-Agnostic Follow-Up Stage

For follow-up stages like TLS rotation, the case should usually be **version-agnostic**:

- verify the cluster is present and healthy
- verify the TLS baseline/fault state is present
- do **not** require a specific RabbitMQ version in precondition checks

Why:

- an earlier workflow stage may upgrade the cluster
- the TLS stage should operate on the carried-over version, not reset it

## Practical Authoring Checklist

Before shipping a new case:

1. Standalone behavior:
   - `preconditionUnits` builds the intended problem state
   - `oracle.verify` is deterministic and observational
2. Workflow behavior:
   - preconditions do not unnecessarily recreate the core workload
   - preconditions are tolerant of valid carried-over state
3. Identity / namespace:
   - use namespace placeholders (`${BENCH_NAMESPACE}`, `${BENCH_NS_*}`) instead of hardcoded namespaces
   - parameterize workload identity (for example `cluster_prefix`) if the case may need to target a non-default instance
4. Cleanup:
   - clean resources inside the assigned namespace(s)
   - do not manage namespace lifecycle in the test case unless the challenge itself is about namespaces

## Designing Parameterization

Parameterization should make a case reusable across workflows without making the case hard to read.

### 1) Decide what should be a parameter

Parameterize values that vary by environment, stage, or target outcome:

- workload identity (`cluster_prefix`)
- version transitions (`from_version`, `to_version`)
- target object names (`tls_secret_name`, `report_configmap_name`)
- fault-shape controls (`fault_http_addr`, `fault_mode`)
- scenario thresholds (`seed_row_count_min`, `min_rotated_validity_days`)

Keep static values hardcoded when they are true implementation constants for the case.

### 2) Use clear parameter names

Prefer explicit names tied to meaning and scope:

- good: `report_key`, `update_partition`, `setting_name`, `setting_value`
- bad: `value1`, `target`, `flag`, `config`

If a parameter is node-specific or role-specific, encode that in the name.

### 3) Set defaults to the canonical standalone baseline

Default values should represent the normal standalone run for that case.
Workflow overrides can then change only what they need.

### 4) Wire each parameter end-to-end

The same parameter should be used consistently in:

- `preconditionUnits.probe`
- `preconditionUnits.apply`
- `preconditionUnits.verify`
- `detailedInstructions`/prompt text
- oracle checks (directly or via resolved runtime state)

Do not leave hidden hardcoded duplicates once a value is parameterized.

### 5) Keep parameters aligned with independent precondition units

Each precondition unit should own only the parameters relevant to that unit's concern.
Do not couple unrelated concerns by sharing one broad "catch-all" parameter set.

### 6) Quick validation for parameterization

Before handoff:

1. Run with defaults and ensure setup reaches `ready`.
2. Run one targeted override (for example `cluster_prefix` or `to_version`) and ensure probes still converge.
3. Search for leftover hardcoded values that should now be parameterized.
4. Confirm resolved prompt text and effective params match runtime behavior.

### Example: Why independent parameterization helps chaining

Consider a blue/green migration workflow with two clusters.
If one precondition unit probes both clusters together and both share one coupled parameter block, a problem on green can trigger re-apply on both clusters.
That can mutate already-correct blue state and hurt chaining flexibility.

If source and target are modeled as independent units with independent parameters, only the side with a problem re-applies, and carried-over state remains stable.

## Oracle Independence For Full Sweep

Oracle design directly affects regression-analysis quality.
For full-sweep analysis, use this oracle contract:

1. Read-only checks only
   - oracle verifies state; it does not mutate cluster state.
   - do not run `kubectl delete/apply/patch/set image` or mutating SQL in oracle.
2. Same parameter contract as setup
   - resolve namespace and identities via runtime values (`BENCH_NAMESPACE`, `BENCH_PARAM_*`).
   - avoid hardcoded namespace, cluster names, versions, schema names, and endpoints.
3. Small independent assertions
   - split checks into narrow signals instead of one bundled gate.

### Why split assertions

Bundled checks reduce signal quality.
If one combined check fails, you cannot tell which property regressed.

Example:

- Better split:
  - `cluster_pods_exist`
  - `cluster_pod_count_expected`
- Worse bundle:
  - `cluster_pods_exist_and_count_expected`

If a trajectory only breaks scaling, split checks show:

- existence still passes
- count fails

That makes full-sweep output more accurate and easier to debug.

### Case Study: CockroachDB Oracle Regression Noise

Observed anti-patterns:

- Oracle mutates state during verification (for example deleting a pod to test persistence).
- Oracle uses hardcoded values (namespace, image tag, endpoint), ignoring runtime params.

Why this hurts full sweep:

- mutation can create new side effects and hide true regressions.
- hardcoded expectations can fail valid runs where params were intentionally overridden.

Correct pattern:

- keep oracle purely observational.
- parameterize expected values through `BENCH_PARAM_*`.
- verify stage outcome with independent assertions so partial pass/fail is visible.

## Case Studies: Why Independent Preconditions Matter

These examples show why each precondition unit should check one thing only.

### Case Study 1: RabbitMQ Blue/Green Migration

Imagine two clusters:

- blue (old cluster)
- green (new cluster)

Goal:

- move messages to green
- do not break blue if blue is already correct

Bad setup (hard to chain):

- one precondition checks blue and green together
- if green is missing something, the check fails
- then apply runs for both blue and green
- blue gets changed again even though blue was already fine

What can go wrong:

- extra changes on blue
- old good state can be overwritten
- later stages become less stable

Good setup (independent):

- one unit checks only blue
- one unit checks only green
- one unit checks only seed/migration data

Result:

- if only green has a problem, only green is changed
- blue stays untouched

### Case Study 2: CockroachDB Monitoring Integration

Goal:

- Prometheus can scrape CockroachDB metrics

Bad setup:

- probe says “CRD must exist”
- even if monitoring already works another way, probe fails
- apply installs CRD/operator anyway

What can go wrong:

- unnecessary changes
- stage forces one tool choice even when result is already correct

Good setup:

- probe checks the real result:
- Prometheus endpoint is reachable
- targets API responds
- CockroachDB metrics are available
- apply can still use CRD/operator as fallback when result is not ready

Result:

- if monitoring already works, no extra install
- if monitoring is missing, fallback apply can fix it

### Case Study 3: MongoDB Stage1 -> Stage2 RBAC Drift Setup

Scenario:

- Stage 1 already created a healthy MongoDB replica set and some baseline users.
- Stage 2 needs a specific reporting-RBAC drift state.

Bad setup:

- Stage 2 has one large precondition unit.
- If one RBAC check fails, apply does full cluster rebuild (delete/recreate core workload resources).

What goes wrong:

- carry-over state from Stage 1 is destroyed.
- workflows become order-fragile and harder to chain.
- setup mutates far more than the stage requires.

Good setup (copy this structure):

- Unit A: `mongodb_cluster_runtime_ready`
  - probe: "is core runtime healthy and are required auth/secret inputs present?"
  - apply: idempotent bootstrap only (apply missing resources, wait, init-if-needed, ensure admin auth path).
  - verify: replica set health and required secrets.
- Unit B: `custom_roles_setup_baseline_ready`
  - probe: "is the exact RBAC drift shape for this stage already present?"
  - apply: in-place role/user reconciliation only (no infra reset).
  - verify: exact RBAC drift shape.

Result:

- Stage 2 can run standalone.
- Stage 2 can run immediately after Stage 1 in the same namespace.
- only drifted concern is changed; core runtime carry-over remains stable.

### Reference Pattern: Chain-Safe Two-Unit Preconditions

Use this template whenever a stage needs both core runtime and challenge-specific drift:

1. Runtime unit (`*_runtime_ready`)
   - probe/verify check only core health + required identity material.
   - apply is bootstrap-only and idempotent.
   - never do destructive rebuild in this unit.
2. Drift unit (`*_drift_ready` / `*_baseline_ready`)
   - probe/verify check only challenge-specific fault/baseline shape.
   - apply mutates only the exact target roles/policies/data for that stage.
   - keep reconciliation in-place.

### Simple Rule

For every precondition unit:

- probe: “Is this one thing already working?”
- apply: “Do the smallest change to fix this one thing.”
- verify: “Did this one thing become correct?”

Do not check many unrelated things in one unit.
Do not force one implementation style in probe when the goal is an outcome.

## When To Split Preconditions

If a case becomes hard to compose, split one large precondition into smaller units:

- core workload exists/healthy
- helper tools ready
- challenge-specific fault/baseline state injected

This makes workflow reuse easier because each unit can independently `probe -> apply -> verify`.

## Example: Two-Cluster Case (Blue/Green-Style)

For any testcase that uses two clusters/namespaces (source/target, blue/green, primary/standby), keep units independent per side.

Bad (coupled):

- One precondition unit probes both source and target readiness together.
- Previous stage already satisfied source, but target is missing one requirement.
- Combined probe fails, then apply reruns both source and target.
- Result: already-correct source state may be unnecessarily mutated, which reduces chaining flexibility.

Good (independent):

- Separate units such as `source_cluster_ready` and `target_cluster_ready`.
- If only target drifts, only target unit reruns.
- Carried-over source state is preserved, and workflow stage chaining remains stable.
