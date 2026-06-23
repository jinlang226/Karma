# Case Design Guideline

How to design KARMA test cases — **preconditions** and **oracles** — that pass
with a competent agent, fail only for real reasons, and survive composition into
multi-stage workflows on reused clusters.

This is the consolidated, practical rulebook. It folds together three sources:

1. The canonical authoring guide — [docs/developer/adding-a-test-case.md](docs/developer/adding-a-test-case.md)
   (composition-safe preconditions: *reuse infrastructure, replant the problem*).
2. The composition failure-pattern catalog —
   [docs/developer/composition-failure-patterns.md](docs/developer/composition-failure-patterns.md).
3. **The verified faults from the re-run campaigns** (the `-rerun` and `-rererun`
   sweeps), each confirmed to be a genuine test/workflow fault — not an agent
   miss and not an infra flake.

Every rule below is stated as: **the rule**, *why*, and a one-line **evidence**
pointer to the run that motivated it.

---

## 0. Triage first — is it actually a test fault?

Before "fixing" any failure, classify it. Most re-run failures are **not** test
bugs. In the latest `-rererun`, ~20 of 32 failures were an LLM-provider outage,
not the test.

- **INFRA-FLAKE (do not touch the case).** The agent harness errored: `submit.txt`
  is literally `API Error: 529 Overloaded` / `500` / `Unable to connect ...
  ConnectionRefused`, and `evidence.json` shows `trace_facts.total_calls == 0`
  (or the agent died mid-run). The precondition planted the problem and the
  oracle correctly graded the *unsolved* baseline — there was simply no agent
  work to grade. **Action:** re-run the stage; never edit the case.
  *Recommended harness fix: detect a terminal `is_error` result whose submit
  matches `API Error:`/`ConnectionRefused` with ~0 calls and auto-retry the
  stage instead of scoring it as an oracle fail.*
- **AGENT_FAULT (do not mask).** The agent ran fully and genuinely got it wrong.
  Leave the case; this is a valid measurement. *Evidence: mongo
  snappy→user-management — the agent ran `db.reports.deleteMany({})` and wiped
  the precondition's seed docs.*
- **TEST_BUG / WORKFLOW_BUG (fix the case/workflow).** A **perfect** agent could
  not have passed: the precondition failed before the agent ran, an input the
  prompt promised was absent, the oracle checked something not derivable from the
  prompt, or an earlier stage left state the failed stage cannot reconcile.

> **The test-fault test:** *Could a flawless operator, given only this stage's
> prompt and the live cluster, pass this oracle?* If no, it's a test/workflow
> bug. If yes but the agent failed, it's an agent fault. If the agent never ran,
> it's infra.

---

## 1. Precondition design rules

### 1.1 Reuse infrastructure, replant the problem (composition-safe units)
Split env setup into a **runtime unit** (`*_runtime_ready`/`*_env_ready`: core
health + identity material, idempotent, skip-safe) and a **problem unit**
(`*_drift_ready`/`*_baseline_ready`: probes the exact unsolved problem and
replants it). Never bundle the planted fault into the skip-gated deploy.
*Why:* in a workflow the cluster is already up, so a bundled deploy is skipped
and the problem is never planted. *Evidence: the whole `existence-gated
preconditions` class (mongo mongod-config, statefulset-customization; es
voting-drift).*

### 1.2 Probe by intent, not existence
A skip-gate probe must test the **exact state the oracle checks**, not a proxy
marker. Probing "ConfigMap exists" / "pods Running" / "user exists" lets a
stale-but-present artifact skip the planting. *Evidence: es
transform-scale-upgrade — `seed_index_baseline` probes the `es-seed` ConfigMap's
existence and blind-POSTs 3 docs onto an already-seeded index → 6 docs, oracle
wants 3.*

### 1.3 Be robust to a reused / dirty cluster (namespace reset)
Any precondition that establishes a **fixed (case-owned) namespace** must delete
**and wait for deletion** before creating, so it tolerates a leftover-Active or
Terminating namespace from a prior run:
```
kubectl delete namespace X --ignore-not-found=true --wait=false
kubectl wait --for=delete namespace/X --timeout=300s 2>/dev/null || true
kubectl create namespace X        # or: kubectl apply -f <namespace.yaml>
```
Keep it inside the skip-gate so workflow persistence is preserved. Also gate the
probe on the namespace being **Active**, so a leftover pod can't read "ready"
while the namespace is dying. *Why:* a reused cluster's orphaned namespace causes
`is being deleted`/`is being terminated` and StatefulSet pods that never appear.
*Evidence: the entire first `-rererun` wave (ray/nginx/cockroachdb) — ray had no
delete, nginx's probe passed on a leftover `curl-test` while `demo` terminated.*
The framework retries `object is being deleted`, `being terminated`, and
`no matching resources found` as transient, but the case must still initiate the
clean delete.

### 1.4 Wait at the controller level, never `wait -l` right after apply
Use `kubectl rollout status statefulset/<n>` / `deploy/<n>`, not
`kubectl wait --for=condition=ready pod -l <label>` immediately after applying
the workload. *Why:* with zero pods matching yet, `wait -l` returns instantly
`no matching resources found`. *Evidence: the ES wait-race class.*

### 1.5 Flap-retry every reachability/health check; gate post-mutation verifies
Wrap each precondition `verify` reachability/health command in a bounded
re-evaluate loop (~120s), and gate any verify that follows a disruptive op
(`scale`, restart) on `rollout status` first. Make the client `--max-time`
exceed the server-side `timeout=`. *Why:* a single curl in the re-stabilization
dip fails a correct setup. *Evidence: es master-downscale-voting-exclusions — a
single `curl --max-time 5` right after `kubectl scale --replicas=1` hit exit 28
and exhausted the budget; agent never ran.*

### 1.6 Seed idempotently against live state
Seed/replant against the **live quantity the oracle checks** (e.g.
`/index/_count`, `countDocuments({}) >= N`), not a marker. Skip or
delete-and-recreate so the result is exact under composition. *Evidence: es
transform-scale (1.2 above); reinforced anywhere a problem unit re-applies onto
inherited data.*

### 1.7 Validate every seeded literal against the real system
Roles, versions, image tags, object names baked into a precondition must be real.
*Evidence: es file-realm-user-roles-merge — seeds the reporting user with
`elasticsearch-users useradd ... -r read`, but `read` is not a built-in ES role
(`viewer` is); precondition.log warns `roles [read] are not in the file`, the
user is powerless, and no agent can grant an authorization the seed never made.*
Likewise the framework transient-matcher must cover the tool's **actual** strings
(mongosh emits `ECONNREFUSED`, not kubectl's `connection refused`).

### 1.8 Budget timeouts for the worst (loaded, inherited) case
Rollout waits ≥600s; drains use a short `--grace-period`; and **a seed/setup
script must finish within its `timeout_sec`**. *Evidence: cockroachdb/decommission
— `seed_data.py`'s internal upreplication/relocation retry budget exceeds the
300s command timeout even though the cluster is already healthy (data was
seeded); the unit times out and the agent never runs.*

### 1.9 Never bake a mutable secret into a readiness probe
A pod readiness/health probe that hardcodes `${ELASTIC_PASSWORD}` (or any secret
a later stage rotates) goes NotReady after rotation, dropping the Service's
endpoints. Read the secret live from the mounted file. *Evidence: es
scale-certs-...-snapshot stage_05 — stage_04 rotated the password, the readiness
probe's baked password broke, `es-http` had zero endpoints, the snapshot oracle
got curl exit 7 even though the agent's snapshot work was correct.*

### 1.10 Don't issue the first DB call the instant the pod is Ready
Pod-`condition=ready` ≠ the service is accepting connections. Gate the first
`mongosh`/`cockroach`/client call with a `ping`-until-ok poll. *Evidence: mongo
statefulset-customization — `rs.initiate(...)` runs right after
`wait --for=condition=ready pod` and fails `MongoNetworkError: connect
ECONNREFUSED 127.0.0.1:27017`; the bare `rs.initiate` has no retry and
`_is_transient_apply_error` didn't match `econnrefused`.*

---

## 2. Oracle design rules

### 2.1 Observational and deterministic by default
Read cluster state, return a verdict; do not repair the workload or complete the
task. Active verification (e.g. restart-to-prove-persistence) is allowed only
when the property *is* the task, and must not set the value itself or leave the
cluster unstable.

### 2.2 Grade only what the prompt promised
The pass condition must be fully derivable from the stage prompt. Any exact
filename, count, label, version, or object name the oracle hard-checks must
appear in the prompt or be planted by the precondition — never left for the agent
to guess. *Evidence (prior): es safe-downscale marker filename; reinforced
broadly.*

### 2.3 Resolve expectations from live state; scope to the target object
Never sum a global topology in a namespace that legitimately accumulates, and
never hardcode a standalone count/name. *Evidence: es incident-response-chain
stage_04 — `_resolve_expected_nodes()` sums `spec.replicas` over **all** ES
StatefulSets and demands 2 nodes after a prior stage legitimately downscaled to
1; es service-and-network-repair — oracle hardcodes `SERVICE=es-http` against a
topology (`search-*`/`es-alpha`) that has no such Service →
`curl: (6) Could not resolve host`.* Count only StatefulSets backing live
`_cat/nodes`; resolve the service/expected from the live target.

### 2.4 Present the right client identity; fall back to pod-local
An oracle that connects over TLS must present a **client cert** when the cluster
may be in mutual `requireTLS` — `--tlsAllowInvalidCertificates` is not enough; a
mutual-TLS server drops a certless monitor connection. And when a check goes
through a Service that may have no endpoints, fall back to
`kubectl exec <pod> -- curl localhost:<port>`. *Evidence: mongo
external-access-horizons — the oracle's own `mongo-client` pod is a bare
`mongo:6.0` with no cert mounted; after a prior TLS stage it fails
`connection <monitor> ... closed` though the agent's split-horizon work was
correct.*

### 2.5 Split assertions and flap-retry transient-prone checks
Small independent checks (pods exist / count ready / endpoint responds) localize
failures and survive the regression sweep. Wrap any reachability/HTTP check in
the standard ~120s re-evaluate loop. *Evidence: es secure-http-ingress had no
retry and failed on a single transient curl.*

### 2.6 Accept equivalent valid outcomes
Don't over-specify a single mechanism when the platform legitimately produces
another. *Evidence: nginx rate-limit oracles — ingress-nginx returns **503** on a
rate-limited burst, not always **429**; accept either.*

### 2.7 Don't depend on a seed count an agent can zero, and lint oracles
If the oracle requires N seed docs/objects, either warn in the prompt that the
data is load-bearing or have the problem unit re-seed idempotently — so a
(mis)behaving agent's cleanup can't permanently strand it. Static-lint every
`oracle.py` (compile + name resolution) before a sweep. *Evidence: mongo
user-management (agent fault, but a self-healing seed would have made it
unwinnable-proof); es rotate-http-certs earlier `import os` NameError.*

---

## 3. Composition / workflow design rules

### 3.1 Identity contract across stages
If stage A's deliverable is **agent-built**, A's prompt and oracle must *mandate*
every label/name/selector that later stages depend on. *Evidence: cockroachdb
deploy→initialize — `deploy` lets the agent build the StatefulSet and grades it
by the STS's own `spec.selector`, but `initialize`'s oracle hardcodes
`-l app.kubernetes.io/name=cockroachdb`; a healthy agent-built cluster is
invisible to it → "Expected 3 pods, found 0" (hit 2 long workflows).* Fix: make
`deploy` require the canonical labels, or have downstream oracles select by the
StatefulSet's own selector / name prefix.

### 3.2 Don't chain contradictory stages
A successor stage's oracle preconditions must be satisfiable by the predecessor's
end-state. *Evidence: es incident-response-chain (a snapshot stage expecting ≥2
nodes placed after a downscale-to-1 stage); es service-and-network-repair (a
seed-hosts-repair stage with `es-http` hardcoded placed after a stage that builds
a `search-*` topology with no `es-http`).* A workflow linter should reject a
successor whose oracle the predecessor cannot satisfy.

### 3.3 Adversary scope must match the stage's real target
An injected fault must target the resource the active stage actually uses, and
should lift before/at the stage whose oracle would otherwise grade through it.
*Evidence: nginx-long-06 — `scale_down_backend` targets `demo-app` while the
stage routes to `rate-echo`, and the fault stays active across grading.*

### 3.4 Skip-gates must be authoritative for the case's own shape
A probe that's too generic ("6 pods Running") skips the build for an
**incompatible** inherited topology, leaving the case's required objects absent.
Probe for the case's own specific objects. *Evidence: es service-and-network-repair
(3.2) — `es_env_ready` skipped on a bare `grep -c Running` against an unrelated
topology.*

---

## 4. Pre-ship checklist

- [ ] **Standalone:** preconditions build the infra AND plant the unsolved
      problem; a real solve satisfies the oracle; generated namespaces are
      removed.
- [ ] **Reused cluster:** fixed-namespace setup delete+wait-for-delete+create;
      probe gated on namespace Active (§1.3).
- [ ] **Composition:** runtime vs problem units split (§1.1); problem probe tests
      the oracle's exact state (§1.2); seeds idempotent vs live (§1.6).
- [ ] **Waits/retries:** controller-level waits (§1.4); flap-retry on every
      reachability check, precondition and oracle (§1.5, §2.5); first DB call
      polls until listening (§1.10).
- [ ] **Literals:** roles/versions/names validated as real (§1.7); no mutable
      secret baked into a readiness probe (§1.9); timeouts budget the worst case
      (§1.8).
- [ ] **Oracle:** grades only the prompt's promise (§2.2); resolves from live
      state, scoped to the target (§2.3); presents proper client identity / pod-
      local fallback (§2.4); accepts equivalent valid outcomes (§2.6); compiles +
      name-resolves (§2.7).
- [ ] **Workflow:** identity contract honored across stages (§3.1); no
      contradictory chaining (§3.2); adversary scoped to the real target (§3.3).
- [ ] **Triage discipline:** before filing a "test bug," confirm it isn't an
      INFRA-FLAKE (API-error submit, 0 calls) or an AGENT_FAULT (§0).

---

## 5. Verified faults from the `-rererun` campaign (worklist)

Genuine test/workflow faults found and confirmed (a perfect agent could not have
passed). The infra-flakes (~20 runs, API outage) and the 1 agent fault are
**excluded** from this list.

| Case / stage | Class | Rule | Fix |
| --- | --- | --- | --- |
| es/file-realm-user-roles-merge | TEST | §1.7 | seed `-r viewer` (or ship a `roles.yml` defining `read`) |
| es/master-downscale-voting-exclusions | TEST | §1.5 | flap-retry the verify curl; `rollout status` after scale |
| es/transform-job-recovery seed (transform-scale-upgrade) | TEST | §1.2,§1.6 | seed idempotently vs live `_count`, not the ConfigMap marker |
| es/snapshot-repo-setup readiness (scale-certs…) | TEST/WF | §1.9,§2.4 | read password live in the probe; oracle pod-local fallback |
| es/full-restart-upgrade-ha + snapshot oracles | WF | §2.3 | count only live `_cat/nodes`-backed StatefulSets |
| es/seed-hosts-repair (service-and-network-repair) | WF | §3.2,§3.4 | authoritative probe; don't chain incompatible topology |
| mongodb/statefulset-customization | TEST | §1.10 | ping-before-`rs.initiate`; add `econnrefused` to transient matcher |
| mongodb/external-access-horizons | TEST | §2.4 | mount a client keypair into the `mongo-client` oracle pod |
| cockroachdb/decommission seed | TEST | §1.8 | raise unit `timeout_sec` ≥600s / trim the script's retry budget |
| cockroachdb/initialize oracle (deploy→initialize) | WF | §3.1 | mandate canonical labels in `deploy`, or select by STS selector |
| nginx/long-06 adversary scope | WF | §3.3 | target the adversary at the stage's real backend |

**Highest-leverage harness change:** auto-retry a stage whose agent result is a
terminal API/transport error (§0) — it would have removed ~20 phantom failures
from this campaign and stopped the outage from masking the ~11 real faults.
