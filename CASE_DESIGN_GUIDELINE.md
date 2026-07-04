# KARMA Case Design Encyclopedia

A complete reference for what can go wrong when a KARMA test case is composed into
a multi-stage workflow — and the design rule that prevents each fault. Written for
**both a human author and an AI agent** editing cases, preconditions, oracles,
prompts, adversaries, and the runtime.

Every entry is a **case-agnostic rule** — universal, or at most
application/service-specific (Elasticsearch, MongoDB, CockroachDB, RabbitMQ,
nginx, Ray, Spark), **never tied to a single case**. Use it both ways: as the
**guideline** a new case / workflow / adversary must follow, and as the
**criteria** to evaluate the existing suite against. Entries are tagged by
**category** — PRECONDITION · ORACLE · COMPOSITION · PROMPT · ADVERSARY ·
FRAMEWORK · TRIAGE — with a stable id (e.g. `P3`, `O13`, `C2`) for
cross-reference, and each carries a short **Example** at the application level to
make the pattern recognizable. **Ids are permanent: assigned in discovery order
and grouped by theme, so within a section they read out of numeric sequence by
design (e.g. `C6` is followed by `C13`). Never renumber — use the per-section
Quick reference to navigate.**

**How to use it.** Start at the **Governing Laws** (§I) — every specific rule is
an instance of one. Before "fixing" a failure, run the **Triage** (§II). When
authoring or auditing a case, walk the **Pre-ship checklist** (§IX) and the
relevant rule sections. When a failure looks weird, check the **Framework
reference** (§VIII) — it may be a framework bug, not your case. The
**service appendix** (§X) holds the case-specific patterns (ES is the fault
epicenter).

> **The test-fault test.** *Could a flawless operator, given only this stage's
> prompt and the live cluster, pass this oracle?* If **no**, it's a test/workflow
> bug (fix it). If **yes but the agent failed**, it's an agent fault (leave it —
> a valid measurement). If **the agent never ran**, it's infra (re-run it).

---

## I. The Governing Laws

Eight cross-cutting universals. Everything below is a corollary of one of these.

1. **The Persistence Invariant.** Cluster state *and* the agent's environment
   persist across workflow stages. A precondition is an **idempotency-guarded
   re-seed, never a reset** — it (re)plants only the specific problem the stage
   tests and must never destroy inherited valid state (no deleting an inherited
   namespace, no restarting an `emptyDir` pod, no dropping a populated
   collection). Destructive operations live *inside* a skip-gate so they run only
   when the state is genuinely absent.

2. **Pod-Ready ≠ Ready-to-Use.** Applied / scheduled / `condition=ready` does not
   mean the service accepts connections. Every cross-component interaction (apply
   into a fresh namespace, first `mongosh`/`cockroach` call, `wait -l`, an oracle
   query) must tolerate the async-convergence window with **signature-scoped
   retries that never loosen a pass criterion**. *(§VIII transient allowlist;
   verify/error-gate default-retry; flap-retry oracles)*

3. **Explicit empty ≠ missing.** `required_roles: []` ("I manage my own literal
   namespaces") must be distinguished *everywhere* from `None`/absent ("give me a
   default"). Never `x or [default]`; always `if x is None`.

4. **Producer and consumer must agree** — on artifact paths (one canonical
   `protocol.py` helper), on log schema (raw-HTTP vs kubectl-verb), on env-var
   names (`BENCH_*`), and on inline formats (adversary `scenario`/`inject_at_stage`).
   A mismatch fails **silently** (empty logs, zero metrics, dead features) — far
   more dangerous than a crash.

5. **A silent no-op is a false-pass trap.** An atomic JSON-patch with a bad
   pointer, an unscoped `kubectl` command, an unresolved `{{param}}` token, a
   force-skipped destructive apply on contaminated state, or an adversary that
   reports `ok=True` without running its apply — each lets a *broken* case pass.
   Always verify that setup/injection actually changed state.

6. **The oracle is authoritative.** The LLM judge can never override an oracle
   verdict; the regression sweep re-runs the oracle against still-live state with
   each stage's real `BENCH_*` bindings; a crashed agent defers to the oracle, not
   a "timeout" label. Oracles must verify the *prompt's* contract, accept all
   valid solution forms, poll volatile state to convergence, and never contradict
   their own precondition.

7. **Compose additively or don't compose.** A case is chainable mid-workflow only
   if its precondition can be satisfied *additively* on the inherited cluster and
   every oracle dependency is brought by an additive, skip-gated fixture.
   Destructive / order-sensitive / recovery-from-degraded / version-downgrade
   premises belong at `stage_01` or in short dedicated workflows — **curate them
   out** of marathons.

8. **Fix the pattern, sweep the suite.** Every distinct bug below was fixed across
   *all* affected cases in one sweep, not just the one that failed. A fault in a
   cloned case family (trap cases, helper pods, node-count oracles) exists in all
   of them.

---

## II. Triage — classify before you touch a case

Most re-run failures are **not** test bugs. In one `-rererun`, ~20 of 32 failures
were an LLM-provider outage. Classify first:

- **INFRA-FLAKE — re-run, never edit.** `submit.txt` is literally `API Error: 529
  Overloaded` / `500` / `ConnectionRefused`, and `evidence.json`
  `trace_facts.total_calls == 0` (the agent never acted). The precondition planted
  the problem and the oracle correctly graded the *unsolved* baseline.
  *(Recommended harness fix: auto-retry a stage whose terminal result is an
  API/transport error — it would have removed ~20 phantom failures and stopped the
  outage from masking the real ones.)*
- **AGENT_FAULT — do not mask.** The agent ran fully and got it wrong (e.g. ran
  `db.reports.deleteMany({})` and wiped the seed). A valid measurement; leave it.
- **TEST_BUG / WORKFLOW_BUG — fix it.** A perfect agent could not have passed.

**Framework-failure signatures** (these mean *framework/test bug, not agent*):

| You see… | It's almost certainly… | Rule |
|---|---|---|
| "agent submitted (0s)" + empty agent.log on a **retried** stage | stale `submit.txt` not cleared before relaunch | F-E4 |
| oracle "found 0 pods" / 401 right after a verified-green cluster | transient connectivity / readiness flap | O2, §VIII |
| precondition "error" ~8s into a fresh/busy cluster | SA-not-ready / connection-refused apply race | §VIII |
| "namespace is being terminated/deleted", repeated give-ups | namespace uniqueness / teardown race | F-E8 |
| oracle fails but the prompt never asked for the missing thing | oracle-contract drift / non-additive dep | O3, C3 |
| run aborts with **no `run.json`** | adversary stage-ref mismatch (pre-run) | ADV4 |
| a heavy case "trivially passes" / finishes in ~6s | silent no-op precondition (Law 5) | P28 |
| zero kubectl activity in evidence | proxy log double-nesting / empty KUBECONFIG / schema mismatch | F-E13, F-E15, F-B7 |
| a check fails on **every** attempt (and on an idle node) | **deterministic** root cause — find it from agent ground truth; do **not** paper over with retries/timeout bumps | O18 |

**Re-validate after every fix** — surface fixes routinely unmask the next layer
(http→https reaches ES → exposes 401; pinning a blocker surfaces a downstream
version mismatch).

---

## III. Precondition rules

> **Quick reference.** *Probe semantics:* P1–P5, P28, P32, P33. *Verify post-state:* P6,
> P7, P8, P27. *Cluster reuse & timing:* P9–P14, P29, P30, P31. *Manifests, literals, identity:*
> P15, P16, P17, P18–P26, P34, P35, P36, P37. *Primary-aware & parallel-safe:* P38, P39.

### Probe semantics & specificity
- **P1 — Skip-probe success is exit-code based; never `|| true`.** The port that
  replaced readiness probes with `kubectl get pods | grep -c Running || true`
  made every probe exit 0, so the harness treated the scenario as set up and
  **skipped the apply** — 57 heavy cases "passed" in ~6s against an empty
  namespace. A fresh (0-pod) namespace must yield non-zero (apply runs); pods
  present → zero (apply skipped). Guarded by `tests/unit/test_case_probes.py`.
  [PRECONDITION]
- **P2 — Probe for *this case's own named resource*, not "any pod."** `get pods |
  grep -c Running` matches a foreign/leftover cluster in a shared namespace → the
  case runs against the wrong cluster and its oracle can't find its pods. Probe
  `get pod {{params.cluster_prefix}}-0` / `get statefulset <name>`. [PRECONDITION]
- **P3 — Probe by intent (your own deliverable), not a proxy marker.** A skip-probe
  must test the **exact artifact the oracle checks**, not a *nearby* marker a prior
  stage may have left present. An additive re-plant fixture especially must gate on
  the thing *it* produces (the baseline ConfigMap / secret / seed it writes), never
  on a sibling resource — else on an inherited cluster the proxy is present, the
  fixture skips, and the oracle's dependency is never created. *Example: an ES
  transform-recovery case whose re-plant fixture skips on "transform `_stats`==200"
  while its real job is to write a `transform-checkpoint` ConfigMap; composed
  after another ES stage the transform is live but the ConfigMap is absent →
  oracle "Unable to read checkpoint_before". Fix: probe `get configmap
  transform-checkpoint`.* [PRECONDITION]
- **P4 — Force-fail the probe (`exit 1`) when the case must always re-provision**
  a clean namespace and has no idempotent target to detect. [PRECONDITION]
- **P5 — `error`-gate = assertion only; `skip`-gate = repair.** A unit that must
  *mutate* state to restore a baseline must be `on_probe_fail: skip` with a real
  `apply` (the runner only runs `apply` for a skip-gate; an error-gate's apply is
  dead code). Sweep by *behavior* (asserts-broken-baseline), not by grepping
  strings. [PRECONDITION]
- **P28 — Verify the fault actually planted.** An atomic JSON-patch with one
  bad pointer (`/.../cases/requests/memory` where `cases` should be `resources`)
  silently no-ops the *whole* patch → the StatefulSet stays healthy → trivial
  pass. **Use `op:add`, not `op:replace`, for any field the inherited workload may
  not already have.** A JSON-patch `op:replace` on a path that doesn't exist
  *fails*, aborting the whole atomic patch — so a fault-replant that `replace`s an
  *optional* field (a `livenessProbe.timeoutSeconds` a foreign inherited STS never
  set → `None`) plants nothing, and the oracle then grades an unfaulted workload
  (`None` vs the expected faulty value). `op:add` creates-or-replaces, so it works
  whether or not the field pre-exists. *Example: readiness-probe-tuning's
  probe_fault_replant `op:replace`d liveness probe fields absent on the composed
  cluster → the fault never planted → "timeoutSeconds (None) must exceed the faulty
  baseline".* [PRECONDITION]

### Verify must assert the right post-state
- **P6 — Verify what is true *after setup, before the agent acts*.** A case that
  deliberately breaks a StatefulSet must not verify `grep -c Running` (the pods are
  *meant* broken); a case where the agent does the deploy must verify the
  namespace/baseline, not Running pods (else instant precondition error). [PRECONDITION]
- **P27 — Never confirm an injected fault *through the capability the fault
  disables*.** When the precondition degrades the service itself — quorum loss
  (master gone), a blocked port, a stepped-down primary, disabled shard allocation,
  a stopped process — its probe/verify (and the oracle's pre-recovery checks) must
  **not** call through the very path the fault breaks. That check can *never* pass:
  the data-plane query errors forever, the verify loops to the setup cap, and the
  agent never even starts. Instead **(a)** capture any in-service proof (a setting
  value, a row/doc count) *before* injecting the fault, and **(b)** confirm the
  fault from the **control plane** — `kubectl get sts -o …replicas`, pod count/phase,
  a `kubectl exec` that doesn't need the broken service — or any path the fault
  leaves intact. *Example: an ES master-downscale that sets
  `auto_shrink_voting_configuration=false` while healthy, scales the masters below
  quorum (no elected master), then a verify that does
  `GET /_cluster/settings` — which returns `503 master_not_discovered` on a
  master-less cluster → the verify never matches → 600s setup timeout, agent never
  runs. (A shorter loop just fails faster — the check is unsatisfiable, not slow.)
  Fix: verify the StatefulSet is at the target replica count (control-plane) and
  trust the pre-scale-down `acknowledged:true`, instead of re-reading cluster
  settings on the broken cluster.* [PRECONDITION] (the deeper root cause behind some P14 "timeouts").
- **P7 — Tolerate (`|| true`) flaky steps the oracle doesn't depend on.** A
  race-prone, non-essential setup step (`ray start` GCS registration) must not
  abort the whole precondition. [PRECONDITION]
- **P8 — Additive composition fixtures are strictly best-effort.** A fixture that
  re-establishes an oracle artifact must verify a no-op `true`, `|| true` every
  apply, no `exit 1` — so a slow/missing artifact degrades to a clean oracle FAIL,
  never a framework precondition ERROR (which is worse and regresses a passing
  stage). [PRECONDITION]

### Cluster reuse, namespaces, timing
- **P9 — Be robust to a reused/dirty cluster.** A fixed (case-owned) namespace
  left Active (orphaned by a crashed run) or Terminating makes a bare `create` —
  or an apply racing an async delete — fail. Inside the skip-gate:
  `kubectl delete namespace X --ignore-not-found --wait=false` →
  `kubectl wait --for=delete namespace/X --timeout=300s 2>/dev/null || true` →
  `create`. Also gate the env probe on the namespace being **Active**, so a
  leftover ready helper pod in a dying namespace can't false-skip the rebuild.
  [PRECONDITION]
- **P10 — Namespace teardown must be non-blocking with a real budget.** A bare
  `kubectl delete namespace` blocks until PVC finalizers release (minutes under
  load); the harness delete default (180s, or 300s inside a `/bin/sh -c` wrapper)
  can still be exceeded under load and die mid-wait. Use `--wait=false` +
  a tolerant `wait --for=delete … --timeout=400s || true` + `timeout_sec` ≥460s.
  [PRECONDITION]
- **P11 — Don't issue the first DB call the instant the pod is Ready.** Gate the
  first `mongosh`/`rs.initiate`/`cockroach` call with a `ping`-until-ok poll;
  mongosh emits `ECONNREFUSED` (not kubectl's "connection refused"). [PRECONDITION] (Law 2.)
- **P12 — Wait at the controller level, not `wait -l` right after apply.** With
  zero pods matching yet, `kubectl wait --for=condition=ready pod -l <label>`
  returns instantly rc=1 ("no matching resources found"). Use
  `kubectl rollout status statefulset/<n>`; before waiting on a *named* pod,
  first poll for it to exist. [PRECONDITION]
- **P13 — Pair every `kubectl wait --timeout=N` with `timeout_sec ≥ N`** and a
  matching inner loop bound; a `--timeout` flag is **not honored** unless the
  unit's budget exceeds it. Size budgets for the *cold + loaded* worst case
  (rollout ≥600s, named-pod ready ≥300s), not the warm-local one — but with a
  ceiling: if it still times out at the final bump, the op is **failing, not
  slow** — read the logs, stop raising. [PRECONDITION/FRAMEWORK]
- **P14 — A seed/setup script must finish within its `timeout_sec`.** Keep the
  script's internal retry budget *under* the unit budget. A verify/health inner
  loop that runs *longer* than its unit's `timeout_sec` is killed mid-loop, and the
  harness then **re-runs the whole verify** — so an N-iteration loop that overruns
  multiplies into the precondition cap (`setup timeout: preconditions exceeded
  600s`) and the agent never launches. Size the inner loop strictly below one unit
  budget. To set per-unit `retries`/`interval_sec` at all, author the structured
  block form `{commands: […], retries: N, interval_sec: M}` for probe/apply/verify
  (a bare string or list carries no per-unit retry budget; a malformed wrapper
  surfaces at load as the misleading *"verify command(s) are required"*).
  *Example: an ES master-downscale whose `seq 1 30`×`sleep 3` (~240s) verify in a
  120s unit was retried ~13× → blew the 600s cap.* [PRECONDITION]
- **P29 — Gate a clustered workload's setup on the engine's own *live-member*
  view, and give liveness probes load headroom.** A setup step that needs quorum or
  replica placement (upreplication, shard allocation, primary election, rebalancing)
  only works once ≥quorum members are actually LIVE in the cluster's membership —
  and pod-Ready / `rollout status` can pass *before* membership forms. Worse, a
  too-aggressive liveness probe (short `periodSeconds`, ~1s `timeoutSeconds`, low
  `failureThreshold`) restarts healthy members under concurrent load, dropping the
  live count below quorum and cascading — so the operation silently never completes
  and the unit times out (looks like slowness, is lost quorum). Gate on the engine's
  own liveness (`node status … is_live`, `_cat/nodes`, `rs.status()` members) rather
  than pod readiness, and size liveness probes (longer `timeoutSeconds`/`periodSeconds`,
  higher `failureThreshold`) so a transient load pause can't evict a healthy member.
  The precondition analog of O15. *Example: gate a CockroachDB/ES/Mongo setup
  on `node status … is_live` / `_cat/nodes` / `rs.status()` and **log the count**
  before any quorum/placement op — so a member that is pod-Ready but not yet admitted
  is caught, and (critically) a later stall is *attributed from data* — membership vs.
  a hung exec (cf. O17) — instead of guessed; the live-member emit is what
  disproves a wrong hypothesis.* [PRECONDITION]
- **P30 — Seed only what the oracle grades; never wait-and-hard-fail on the
  engine's asynchronous data placement.** A setup step must not force, then block on,
  the database's own best-effort replica/shard placement finishing on a fixed
  schedule — CockroachDB replica up-replication + `RELOCATE` onto chosen nodes,
  Elasticsearch shard `reroute` / `routing.allocation` convergence, Mongo chunk
  balancing. That placement is **unbounded** (no timing guarantee), its resource
  identity is **unstable** (a range/shard splits or renumbers mid-wait, so a poll on a
  captured id never resolves), and it usually needs **spare capacity** that may not
  exist yet — so any lag makes the unit `return 1` and the agent never runs. Seed
  **only the state the oracle actually checks** (typically: the data exists and reads
  back), and let the engine place copies itself (default replication factor
  distributes them across the members). If placement genuinely must be shaped, use the
  engine's **declarative** setting (an allocation/zone attribute) and move on — do not
  add an imperative relocate-and-wait-until-converged loop that hard-fails. Corollary
  of P3/§minimal-setup: hand-built state the oracle never verifies is pure fragility.
  *Example: a CockroachDB decommission seed forced 3-way up-replication then
  hand-`RELOCATE`d copies onto the to-be-removed nodes — placement the oracle never
  inspects — and hard-failed when up-replication lagged and the range renumbered; the
  fix seeds the rows, sets the replication factor, and lets default RF=3 distribute
  them.* [PRECONDITION]
- **P31 — A precondition that MUTATES live engine state to set a baseline must
  poll to convergence, not single-shot apply + immediate verify.** Clearing a policy,
  resetting a config parameter, deleting a queue/role, revoking a permission — these
  land in the engine **asynchronously** (they propagate across cluster members), so a
  lone mutate followed by an immediate verify races the propagation and false-fails
  even though the mutation was accepted. The race is worst under **composition**: when
  a workflow schedules the case more than once, the reconcile runs against the prior
  run's inherited state, exactly where propagation lag bites. Wrap the mutate in a
  bounded loop that re-issues it until the engine reports the target state (or give the
  paired verify `retries`/`interval_sec`), so the baseline is reliably established. The
  precondition analog of O13. *Example: a RabbitMQ policy-sync case (scheduled twice)
  whose `clear_policy ha-all` + one-shot verify intermittently saw `ha-all` still
  listed; the fix re-issues the clear until `list_policies` no longer reports it.*
  [PRECONDITION]

### Manifests, literals, identity
- **P15 — Get-or-apply for helper Pods.** `kubectl apply` of a bare helper Pod
  (openssl-toolbox, curl-test, mongo-client, ray-client, file-realm-gen) is
  `Forbidden` when an inherited same-named Pod has a different (immutable) spec.
  Reuse if present, create only if absent. Only `kind: Pod` needs this
  (StatefulSet/Deployment/Service/Secret/ConfigMap are patchable). [PRECONDITION/COMPOSITION]
- **P16 — Don't re-apply a whole manifest whose immutable fields a prior stage may
  have changed.** A `kubectl apply -f <full-statefulset>.yaml` onto an inherited
  StatefulSet whose immutable fields differ fails `updates to statefulset spec …
  are forbidden` and aborts the precondition (the agent never runs). Either
  **orphan-delete first** — `kubectl delete sts <x> --cascade=orphan
  --ignore-not-found` (preserves running pods + data PVCs, critical for `emptyDir`
  clusters) — **or patch only the field you need** (`kubectl patch` the readiness
  probe / image) instead of re-applying the whole manifest. *Example: a MongoDB
  case that re-applies the full manifest after an earlier stage
  customized the STS → immutable-field Forbidden.* **A create-once resource
  (a `Job`, a completed `Pod`) is *fully* immutable — its pod template cannot be
  patched at all, so a case re-scheduled with a different value (a per-stage image
  override) MUST `delete --ignore-not-found` the old object before re-applying, or
  the second instance silently inherits the first's spec and the oracle false-fails
  against the new expectation.** *Example: spark/deploy_spark_pi scheduled twice
  (`spark_image: 3.5.1` then `3.5.3`); the re-apply fixture never deleted the Job, so
  stage 2 kept stage 1's 3.5.1 image and the oracle "expected 3.5.3, got 3.5.1".*
  [COMPOSITION]
- **P32 — Gate a destructive fresh-build (namespace/StatefulSet delete) on
  the workload's EXISTENCE, never on the case's fault signature.** With
  `on_probe_fail: skip`, a probe-fail *runs* the apply. If the skip-probe tests the
  fault shape (`spec.replicas==1`) instead of workload presence, an inherited *healthy*
  cluster fails the probe → the apply `delete namespace`s it, destroying every prior
  stage's state; the regression sweep then fails work the agent did correctly. Gate on
  "workload present in any state → skip", and re-plant the fault in a separate additive
  unit. *Example: elasticsearch/master-downscale-voting-exclusions probes
  `spec.replicas==1` and its apply `delete namespace`s a healthy 3-replica inherited
  cluster.* [PRECONDITION]
- **P33 — An additive fixture may re-establish only scenario
  scaffolding/INPUT the oracle reads, never the agent's graded DELIVERABLE.** When a
  fixture's skip-probe is the case's own missing-artifact signature and its apply
  *produces the graded artifact*, it pre-solves the task on every run whose probe fires
  — even standalone. Bring the scaffolding (deploy the object store, seed the index,
  re-plant the *broken* baseline); leave the deliverable (keystore creds, the fix) to
  the agent. *Example: elasticsearch/snapshot-repo-setup's `s3_keystore_fixture` adds
  the `s3.client.default.*` keystore creds that ARE task #1.* [PRECONDITION]
- **P26 — Make shared cluster-scoped applies tolerant; never let them abort a
  precondition.** Cluster-scoped objects (`IngressClass`, `CRD`, `ClusterRole(Binding)`,
  `PersistentVolume`, `StorageClass`, `PriorityClass`) are **not namespaced**, so a
  prior or sibling case on a reused cluster already owns them — and many of their
  fields are immutable. A bare `kubectl apply` then fails (`IngressClass "nginx" …
  spec.controller: field is immutable`) and aborts the whole unit. Wrap such applies
  best-effort (`… || true`) or get-or-skip (`kubectl get ingressclass nginx ||
  apply`); the unit's `verify` (e.g. the controller Deployment is Ready) is the real
  gate. *Example: an ES secure-ingress case and a CockroachDB expose-ingress case
  both abort on a pre-existing `IngressClass nginx` left by an nginx
  case.* [PRECONDITION] (cluster-scoped sibling of P15.)
- **P34 — Decoy planting aborts the stage on a missing/ill-scoped manifest;
  treat it like a precondition.** `plant_decoys` *raises* (killing the stage before
  the agent runs) when a `decoys:` path is absent or its apply fails, and a decoy
  with no explicit `namespace:` lands in the proxy default, not the role-bound one.
  Validate every decoy path exists, render-resolves, and carries the intended
  namespace — a broken decoy is a silent "stage error," not a graded fail. [PRECONDITION]
- **P17 — Use the correct literals.** Correct role names (`viewer`, not the
  non-existent `read`), image tags, memory units (`512Mi`, not bare `512`),
  service-account names, settings names — a wrong value fails (often silently).
  [PRECONDITION]
- **P18 — Parameterize versions/images; don't hardcode.** A skip-gated destructive
  apply pinned to a fixed image clobbers a workflow's version baseline. Use a
  `*_version` param. **Sub-trap:** whitelist the variable *name* in single quotes
  (`envsubst '${BENCH_PARAM_CRDB_VERSION}'`) — an unquoted name is expanded by the
  inner `/bin/sh -c` to `envsubst "23.2.0"` *before* envsubst runs, leaving the
  literal `${…}`. Preserve downward-API refs (`$(POD_NAME)`). **Thread the param
  through *every* consumer, not just the oracle:** if the manifest is applied
  **raw** (no envsubst) with a hardcoded tag while the oracle reads the param, a
  workflow override is unsolvable — the agent can only produce the hardcoded value.
  Render the manifest through envsubst *and* derive any version-coupled sibling (a
  jar/artifact path, a checksum) from the same param. *Example: a Spark deploy case
  whose oracle reads `spark_image` but whose raw-applied Job manifest hardcodes both
  the image and the `spark-examples_…-3.5.3.jar` path — a `3.5.1` override can never
  pass.* [PRECONDITION/COMPOSITION]
- **P19 — Never bake a mutable secret into a readiness probe** (e.g.
  `${ELASTIC_PASSWORD}`); a later rotate stage breaks it, the pod goes NotReady,
  and the Service loses endpoints. Read the secret live from the mounted file.
  [PRECONDITION]
- **P20 — Seed idempotently against live state.** Seed against the *quantity the
  oracle checks* (deterministic `_id` + `refresh`; `countDocuments({}) >= N` and
  top-up only the difference) — never blind-POST onto an inherited index (doubles
  3→6) or drop+recreate a collection. [PRECONDITION]
- **P21 — Don't double-wrap shell commands.** The harness already runs commands in
  a shell; an extra `/bin/sh -c '… mongosh --eval '…''` closes the outer quote →
  `syntax error near unexpected token '('`. Guarded by
  `tests/unit/test_case_command_syntax.py`. [PRECONDITION]
- **P22 — Quote URLs with `&`/`?`.** `curl …/health?wait_for_status=yellow&timeout=5s`
  backgrounds curl (the `&`) → grep gets no input → burns the budget. [PRECONDITION]
- **P23 — Size *requests* to fit the node, *limits* to the engine's real floor.**
  The request governs scheduling and the DB scales its cache to the *limit*, so a
  small request is safe — 5×2Gi *requests* don't fit a 7.6Gi node → pods Pending. But
  the *limit* must meet the engine's documented floor and stay consistent across every
  case of a service: because the DB scales to the limit, a too-small limit OOM-kills
  (**exit 137**) under a memory-heavy op (upreplication, rebalancing, compaction, an
  in-pod client), the workload stalls, and the symptom surfaces *downstream* as a
  precondition/oracle **timeout**, not an obvious OOM — so triage a precondition
  timeout by checking the workload for OOMKilled/CrashLoop before assuming "needs more
  time." The trap is drift: one case left below the service standard fails
  reproducibly only under concurrent load and reads as a flake. *Example: a
  CockroachDB case whose 5 nodes were capped at 1Gi (vs 2Gi in every sibling case)
  OOM'd during range relocation, stalling its seed until it overran the unit timeout.*
  [PRECONDITION]
- **P24 — TLS re-key without a mixed-CA window.** A CA swap done with rolling
  `kubectl delete pod` leaves old- and new-cert pods that can't handshake. Scale to
  0 first then up, or use `podManagementPolicy: Parallel` (PVCs untouched → data /
  node IDs persist); make `rollout status` best-effort and let a `SELECT 1`/health
  loop be the real gate. [PRECONDITION]
- **P25 — Use portable tool flags.** `openssl x509 -not_before/-not_after` needs
  3.2+; use `openssl ca -startdate/-enddate -days N`. Verify fixture-gen inside the
  *runner* image, not the dev host. [PRECONDITION]
- **P35 — A secure-TLS cert must carry the exact SANs/identity the handshake
  validates, or the node never comes up.** A ported/inlined cert-gen step that drops
  its `subjectAltName` (pod DNS, service name, `localhost`, advertised host) yields a
  cert that *exists* but fails mutual node-to-node TLS — pods stay NotReady and a
  downstream `exec` reports a phantom *"container not found"* (the container never
  started), masquerading as a timing bug. Generate certs from a static reviewed
  gen-script (heredoc SAN config) and verify a real handshake (`SELECT 1` /
  cluster-Ready), not just that the Secret exists. *Example: a CockroachDB
  node-cert gen step that drops its SANs → secure inter-node TLS fails → pods
  NotReady → a downstream `cockroach init` exec reports
  "container not found db".* [PRECONDITION]
- **P36 — Pin helper/tool images to a fixed tag that ships the binary;
  never `:latest`.** A helper pod (openssl-toolbox, curl-test, mongo-client) on a
  `:latest` tag **drifts** — a later image build can move or drop the very binary
  your precondition execs, so `kubectl exec toolbox -- sh -c 'openssl …'` dies
  `command not found` (exit 127) the next time the image is pulled. Pin to a
  specific version known to ship the binary on `PATH`, and prefer a purpose-built
  image (`alpine/openssl:3.1.4`) over a bare base that `apk add`s at runtime (that
  races the exec). This is the *helper-pod* counterpart of P18 (parameterize the
  **workload** image so workflows can override it, but **pin** the **tool** image)
  and the precondition-side of O12 (exec a binary only into a pod that ships
  it). Especially insidious under composition: a get-or-apply toolbox + a skip-gated
  cert path means the drift only bites when the cert artifact *isn't* inherited and
  the exec actually runs. *Example: a CockroachDB cert-rotation case whose
  openssl-toolbox on `alpine/openssl:latest` → cert-gen script "openssl: command
  not found"; a whole family of cert cases shared the same `:latest` toolbox.* [PRECONDITION]
- **P37 — Order precondition units by dependency; make the dependency explicit.**
  Units run in declared order, so a fixture that authenticates as admin (seed data,
  create a downstream user, plant an auth-gated fault) must be declared *after* the
  unit that establishes that credential — otherwise it runs against a not-yet-existing
  principal and its `apply` silently no-ops on an inherited cluster. State the order
  in a comment so a later edit can't reorder it. *Example: a MongoDB precondition where
  the admin-user fixture is declared before the seed fixture so seeding can
  authenticate, and a get-or-apply openssl-toolbox precedes any unit that execs
  openssl.* [PRECONDITION]
- **P38 — A precondition write that needs the primary must target the *detected*
  primary, not a hardcoded pod ordinal — and its verify must confirm the write
  landed.** A fixture that execs a primary-only mutation (`createUser`,
  `rs.reconfig`, a config write, an auth-gated seed) into a hardcoded `…-0`
  silently no-ops when composition has moved the primary elsewhere: the exec fails
  `NotWritablePrimary`, a `|| true` swallows it, and a verify that only checks a
  *companion* artifact (the secret it also created) still passes — so the graded
  dependency (the user / row) never exists and the oracle false-fails a flawless
  agent. Detect the live primary (`db.hello().isWritablePrimary` across members,
  cached) and run the write there, and make the unit's `verify` *authenticate or
  read back the thing it wrote* (polling to tolerate election lag), never merely
  assert a sibling secret exists. The precondition mirror of O8 (+O32's
  prove-don't-assume). *Example: a MongoDB `health_user_fixture` that `createUser`s
  into `mongodb-replica-0` — a secondary after an earlier scaling stage — so the
  health user is never created and every `readiness-probe-tuning` health-auth check
  fails on a correctly-tuned cluster.* [PRECONDITION]
- **P39 — Stage per-run artifacts on a run-scoped path, never a fixed host
  scratch dir.** A precondition (or oracle) that records a baseline or stages
  certs/keys on a hardcoded orchestrator-host path (`/tmp/crdb-old-certs`,
  `/tmp/ingress_env`) is parallel-unsafe: when the suite runs several instances of
  the case concurrently on one dispatcher host (the normal multi-cluster mode),
  their `rm -rf`+`cp` races clobber each other, so the recorded baseline diverges
  from the live per-cluster artifact and the oracle false-fails (e.g. "CA
  fingerprint changed" though the agent kept the CA). Prefer recording baselines
  **in-cluster** (read the live secret, or a pod-local `kubectl exec` read) over any
  host path; if a host path is unavoidable, scope it by run-id/namespace
  (`mktemp -d`, or `/tmp/${BENCH_NAMESPACE}/…`) and keep producer and consumer on
  the *same* scoped path (Law 4). *Example: a CockroachDB cert-rotation family that
  records the "old CA" baseline from a shared `/tmp/crdb-old-certs`; three
  concurrent cert runs corrupt it — sweep the whole family (Law 8).*
  [PRECONDITION/FRAMEWORK]

---

## IV. Oracle rules

> **Quick reference.** *Grade the contract:* O1, O2, O3,
> O4, O5. *Connection & identity:* O6, O7, O8,
> O9, O10, O11, O12. *Robustness & timing:* O13,
> O14, O15, O16, O17, O18, O19, O20,
> O21, O22, O23. *Scripting hygiene:* O24, O25, O26,
> O27. *Assertion completeness & structure:* O28, O29,
> O30, O31, O32, O33, O34, O35, O36,
> O37, O38, O39, O40, O41, O42, O43, O44, O45, O46.

### Grade the contract, from live state, scoped to the target
- **O1 — Grade only what the prompt promised.** Any exact filename, count, label,
  version, role/object name, magic probe value, or `replSetName` the oracle checks
  must appear in the prompt or be planted by the precondition — never left for the
  agent to guess. Prefer grading the **effective outcome** (can read reports;
  denied writes) over an undisclosed identifier. **An identifier disclosed only by
  a skip-gated artifact is unobservable under composition — grade the effective
  outcome.** A key/client name/path the case "discloses" by planting a
  secret/ConfigMap becomes invisible when that artifact is skip-gated away on an
  inherited cluster (or deliberately omitted per P33); a valid solve that picks an
  equally-correct name then false-fails. *Example: an ES snapshot oracle hardcoding
  `s3.client.default.*` keystore keys while the agent used a working
  `s3.client.minio.*` and the snapshot completed SUCCESS — grade the successful
  snapshot (client-agnostic), not the key name.* [ORACLE/PROMPT]
- **O2 — Resolve expectations from live state, scoped to the target object.**
  Never sum a global topology a namespace legitimately accumulates; never hardcode
  a standalone count/name/scheme. Count only StatefulSets backing live
  `_cat/nodes` members (gate on **desired `spec.replicas` of not-being-deleted**
  STSs, *not* transient `status.readyReplicas`); resolve service/version/setting
  from the live cluster; read `BENCH_PARAM_*` with the old value as default.
  **Exception:** where the count/mode *is* the graded outcome (downscale,
  decommission, generate-cert), stay param-first — deriving from live would mask a
  failed operation. **A cross-topology tally sums over the live members, never
  matches a scalar against one object:** an "original replicas" / "nodes before
  scale-up" assertion on a base that is *several* StatefulSets must sum
  `spec.replicas` over the non-new STSs, not assert a single STS equals a scalar
  param (a two-STS 3+2 base has no STS at 5). *Example: an ES scale-up-new-nodeset
  oracle asserting `es-transport.replicas == 5` when the composed base is
  es-transport(3)+es-data(2).* [ORACLE]
- **O3 — Don't contradict the prompt or your own precondition.** Wrong
  scheme (`http://` vs required HTTPS), wrong hardcoded path, demanding both
  sidecars when the prompt named one, accepting only `--advertise-host` not
  `--advertise-addr=$(hostname -f)`, or asserting backend-TLS the precondition
  deliberately deployed as plain-HTTP — all fail an honest agent. [ORACLE]
- **O4 — Inspect *every* entry of a multi-valued artifact; accept a valid
  superset.** When the oracle reads something that can legitimately hold more than
  one value — a PEM file (CA **bundle**), a multi-doc YAML, a list, a label set —
  parse **all** entries, don't assume the first/only one. A tool that reads just
  the head (`openssl x509` on a bundle reads only the leading cert) silently grades
  the wrong element, and the agent's correct answer is often a *bundle/superset*
  (old+new for a zero-gap rollover). Assert "the required value is **present among**
  the entries," not "the single value equals X." *Example: an ES http-cert rotation
  where the agent set `ca.crt` to `old-ca + new-ca` (prompt-required trust bundle); the oracle
  fingerprinted only the first cert → false "CA fingerprint did not change".*
  [ORACLE]
- **O5 — Validate against an *absolute* target, not an inherited
  artifact.** A "rotate to ~1y" check that required the new cert to *outlive* the
  inherited old one breaks when chained after a multi-year cert. Derive the target
  from the prompt ("≥10 months from now"), or from the *recorded* baseline for
  relative asks ("+1", "2x") — never the raw inherited value. A relative-change case's
  oracle whose expected value is a **fixed** derivation of the seeded baseline
  (`baseline+1`) is only correct on the case's **first** application: if a workflow
  schedules the same relative-change case **twice**, the second run legitimately
  advances the value again (`baseline+2`) and the fixed oracle false-fails the *correct*
  agent. Either read the live pre-change value in the oracle, or don't schedule a
  relative-change case more than once per workflow. *Example: a MongoDB
  `mongod-config-update` ("+1 verbosity") run at two stages; the agent correctly moves
  2→3 but the oracle expects the baseline-derived 2.* **If you take the
  reset-the-baseline-before-each-instance escape hatch, the reset fixture's *probe
  and write* must operate on the *same source* the agent mutates and the oracle
  grades.** A reset that writes the runtime value (`setParameter`/`setProfilingLevel`)
  but *probes* the start-up config (`getCmdLineOpts`) to decide whether to run is
  broken: the agent's runtime change never touches start-up config, so the probe
  always sees "already at baseline" and skips the reset — the relative change then
  compounds across sweep instances (`baseline+2`, `+3`) and the oracle false-fails.
  Probe the live *runtime* value (`getParameter`/`getProfilingStatus`). Corollary of
  P3 (probe by intent) and P31 (reset to convergence). *Example: mongod-config-update's
  `mongo_config_baseline_ready` reset probed `getCmdLineOpts.verbosity` (start-up) →
  skipped on every repeat → stage 2 read verbosity 3 vs an expected 2.* [ORACLE]

### Connection & client identity
- **O6 — Connect exactly as the agent's proven command does** (ground truth
  from `agent.log`/`kubectl_log`). Under mutual `requireTLS` a certless or
  wrong-cert connection is dropped (`connection <monitor> … closed`). Pass
  `--tls/--tlsCAFile/--tlsCertificateKeyFile` as **CLI flags** (mongosh *ignores*
  file-path TLS options in a URI; a `mongodb://` URI defaults `tls=false` and
  overrides `--tls`); present the client cert the cluster expects; cache cert paths
  **per target pod** (different pods mount different certs); `test -f` each path so
  standalone stays plain. [ORACLE]
- **O7 — Don't impose a connection mode the agent never uses.** Reading
  `rs.conf()`/`rs.status()` against default localhost starts replica-set SDAM
  monitoring, which drops under `requireTLS`; a short `serverSelectionTimeoutMS`
  then drops under load. Read with **no URI / no directConnection / default
  timeouts** (as the agent does), from the **first member that answers**.
  [ORACLE]
- **O8 — Detect the live primary; don't assume pod-0.** After an election
  (arbiters/scaling stage) the primary moves; primary-only ops execed into a fixed
  `…-replica-0` fail `not primary and secondaryOk=false`. Detect via
  `db.hello().isWritablePrimary` across members (cached); standalone resolves to
  pod-0 unchanged. [ORACLE]
- **O9 — Consumer oracles that only need to *connect* should relax cert
  checks** (`--tlsAllowInvalidCertificates/Hostnames`, or `sslmode=require` +
  client cert through a proxy whose hostname a backend cert can't match). Only the
  TLS-*defining* cases keep strict validation. [ORACLE]
- **O10 — Fall back to pod-local when a Service has no endpoints.** When a
  check goes through a Service that a prior stage may have drained, fall back to
  `kubectl exec <pod> -- curl localhost:<port>`. [ORACLE]
- **O11 — Fetch admin/console endpoints scheme-adaptively** (`https -k -L`
  then `http`) and SQL/HTTP mode-adaptively (`ls ca.crt` → `--certs-dir` vs
  `--insecure`). A secured endpoint 307-redirects plain HTTP to HTML. [ORACLE]
- **O12 — Exec a binary only into a pod whose image ships it** (run
  `openssl s_client` from the broker pod, not a curl-only helper). [ORACLE]

### Robustness & timing
- **O13 — Poll volatile state to convergence.** Multi-node clusters flap at the
  readiness edge (GC, shard recovery, master election, rolling restart) though
  stably green. Refactor volatile checks into `evaluate()` and re-run for a bounded
  deadline (~75–150s), passing on the first clean snapshot; keep config/cert/count
  checks single-pass. Not a loosening — a genuinely degraded cluster fails every
  attempt. [ORACLE]
- **O14 — A count/topology tally read *after* a solution that touches the
  pod template is volatile — flap-retry it.** The "keep count checks single-pass"
  carve-out in O13 only holds when nothing restarts the pods. If the agent's task
  is a label/probe/resource/config edit to a StatefulSet, it forces a **rolling
  restart**, and the last-restarted member spends seconds in a rejoin window
  (mongod `STARTUP2`/`RECOVERING`, an ES node re-electing, a crdb node re-Ready)
  during which a member/replica/node tally reads short. So a PRIMARY/SECONDARY count,
  a "`N` nodes" check, or a "`N` ready" check that follows a template mutation MUST
  use the O13 convergence wrapper (or wait on `rollout status` first), not a
  single snapshot. **When you add a convergence wrapper, you MUST also raise the
  oracle command's `timeout_sec` above the loop deadline (O21) — a 120s loop
  under a 60s budget just *relocates* the timeout to "[timed out after 60s]". And
  loops added to *separate* check functions run **sequentially**, so the budget must
  exceed their SUM (two 120s loops ⇒ `timeout_sec ≥ ~300`), not a single loop.**
  *Example: a MongoDB case whose solution sets a StatefulSet `monitoring=enabled`
  label → rolling restart → an oracle that reads `rs.status()` once with the
  last-restarted pod at age 7s → "expected 2 SECONDARY, got 1" on a stably-healthy
  set — fixed with a poll loop, but only if the oracle's `timeout_sec` was raised to
  fit it.* [ORACLE]
- **O15 — Grade *functional* readiness (the service serves), not just the
  k8s pod-`Ready` bit.** "Pod-Ready ≠ ready-to-use" cuts **both** ways: an oracle
  that asserts `pod.status.conditions[Ready]==True` can *false-fail a healthy*
  workload whose own readiness probe **lags** functional readiness. CockroachDB's
  `/health?ready=1` keeps returning not-ready while ranges replicate after a fresh
  init even though the node already serves SQL; a yellow ES cluster serves; a mongo
  member answers before it flips Ready. Grade what the deliverable actually *is* —
  the service **serves** (`SELECT 1` / a successful query / `node status … is_live`)
  — rather than (or in addition to) the k8s Ready condition, which is a stricter,
  laggier proxy under load. Not a loosening: a genuinely uninitialized/dead cluster
  fails `SELECT 1` and has non-live nodes. *Example: a CockroachDB initialize case where the agent
  proved `SELECT 1+1=2` + all nodes `is_live=true`, but the oracle's pod-Ready poll
  failed under heavy multi-cluster load → "Pod crdb-cluster-N is not Ready".* [ORACLE]
- **O16 — Client `--max-time` must exceed any server-side `wait_for_*`** it
  triggers (else curl exit 28). Shorten the server health timeout (≤10s), raise
  the client deadline (~20s), let the oracle's own loop wait. [ORACLE]
- **O17 — Bound every exec/curl/`s_client`.** An un-timed `subprocess` /
  `kubectl exec` / `openssl s_client` against a reloading listener hangs to the
  oracle deadline, the uncaught `TimeoutExpired` crashes the *whole* oracle, and
  the false fail cascades to "precondition units failed" on retry. Add `timeout=`,
  `--connect-timeout/--max-time`, `timeout 15 s_client`; catch the exception;
  retry hang/empty as "not converged". The same holds for **precondition/seed
  scripts**: bound every exec there too, *and* run them unbuffered (`python3 -u` or
  `flush=True`) so a script killed at its `timeout_sec` leaves its progress log
  instead of silence — an unbounded `exec` in a buffered seed hangs to the unit
  cap and you learn nothing from the run. [ORACLE/PRECONDITION]
- **O18 — Deterministic ≠ transient.** A check that fails on *every* attempt (and
  on an idle node) has a deterministic root cause — find it from agent ground
  truth; do **not** sweep retries/timeout bumps over it (they were added, were dead
  weight, and were reverted). Retries must never mask a *wrong value* (the
  assertion still runs on the read), and must **never** apply to negative/
  expected-failure checks (an unauthenticated probe, `check_plain_blocked`, an
  invalid-old-password probe). [ORACLE/TRIAGE]
- **O19 — When the outcome needs a pod to recover, delete it once** so it
  recreates without accumulated CrashLoopBackOff, then poll. After a restart, poll
  the pod to exist+Ready *and* retry a `SELECT 1`/`ping` (Ready ≠ accepting
  clients). When the **oracle itself** restarts a pod to prove persistence, size
  that readiness wait for the *worst* case it will meet — a **secure**, **loaded**,
  already-**repeatedly-bounced** node can take far longer to drain-rejoin-and-Ready
  than a fresh one (and the wait must stay under the oracle `timeout_sec`, see
  O21). *Example: a CockroachDB cluster-settings case where the oracle's own 2nd
  pod-delete `wait_pod_ready(150s)` times out on a secure node bounced across
  several prior stages, failing a correct agent.* [ORACLE]
- **O20 — Size the oracle `timeout_sec` to the number of `kubectl exec`
  round-trips × per-exec latency under load**, with headroom; default the arg to
  `None`, resolve `max(oracle_timeout_sec, Σ per-command + sleeps)`. [ORACLE/FRAMEWORK]
- **O21 — An oracle's internal retry/flap/wait loop must finish strictly
  *before* its own `timeout_sec`.** If the loop's deadline equals (or exceeds) the
  harness oracle budget, the harness kills the oracle mid-loop and it **never prints
  a verdict** — the result is literally `[timed out after 119s]`, i.e. a correct,
  passing run scored as a fail. Set the internal deadline below `timeout_sec` with
  headroom for the final read + output (e.g. loop ≤90s under a 120s budget), or
  raise `timeout_sec` above the loop. This is the flip side of O13 (the loop is
  right; its window must fit). *Example: an ES stack-monitoring case (loop
  deadline 120s == budget) and a CockroachDB cluster-settings case — both completed the task,
  both killed before the verdict.* [ORACLE/FRAMEWORK]
- **O22 — Accept equivalent valid outcomes.** ingress-nginx returns **503**
  (not always 429) on a throttled burst → accept either. To *prove* a rate limit,
  fire an **unpaced burst**, never a fixed-rps cadence that can match the limit (a
  param override of `limit_rps` silently neutered a hardcoded ~2 rps probe).
  [ORACLE]
- **O23 — Async signals need traffic + re-poll.** A distributed-trace / metrics
  / reload check must drive a small burst and re-poll the collector to a deadline
  (the ingress doesn't sample every request; spans export on a later OTLP batch).
  [ORACLE]

### Scripting hygiene
- **O24 — Escape literal dots in jsonpath keys.** `{.data.rollback.sh}`
  parses `rollback.sh` as a nested field → always empty → trap oracles fail even
  when the ConfigMap is correct. Use `{.data.rollback\.sh}`. (Found in 7 services
  / 21 files — sweep when seen.) [ORACLE]
- **O25 — Oracle scripts need their imports; lint them.** A missing
  `import os` is a 100% NameError crash; a name collision (`expected_nodes` int
  reused as a list) crashes `range()`. Smoke-compile + name-resolve every
  `oracle.py` before a sweep. [ORACLE]
- **O26 — Don't depend on a seed count an agent can zero.** Either warn in the
  prompt that the data is load-bearing, or re-seed it idempotently in a problem
  unit, so a (mis)behaving agent's cleanup can't permanently strand the oracle.
  [ORACLE]
- **O27 — Grade in-pod mutations in the oracle; metrics can't see them.**
  Every scoring metric (blast_radius, destructive_ops, decoy_integrity, residual_drift…)
  reads only the kubectl-proxy snapshot's `verb`/`resource`. A change made via
  `kubectl exec` into a pod (`mongosh`, `rabbitmqctl`, `cockroach sql`, `curl
  localhost`) is logged only as the exec API call itself (a `create` on `pods`); the
  *command run inside* the pod is data-plane and never hits the k8s API, so a
  destructive in-pod operation is invisible to blast_radius/destructive_ops.
  Never rely on a metric to police an agent whose mutations happen inside a pod —
  assert that contract in the oracle. [ORACLE/METRICS]

### Assertion completeness & oracle structure
- **O28 — Accumulate every check into an error list; never raise mid-snapshot.**
  A single read that raises crashes the whole oracle (cascading to a false
  "precondition units failed" on retry) and hides every *other* failure, so a
  fix-rerun cycle surfaces one problem at a time. Each check appends a human-readable
  string to a shared list and continues; one reporter prints them all and fails iff
  any. This is what makes O13's "re-run until the list is empty" possible. *Example:
  a replica-set oracle that reports both "expected 1 PRIMARY got 0" and a host-set
  mismatch in one verdict instead of dying on the first parse of a mid-election status
  read.* [ORACLE]
- **O29 — Expose each assertion as an independently dispatchable named
  sub-check.** A `--check {all,<name>}` dispatcher lets the regression sweep and triage
  probe one dimension (just-topology, just-auth) without the full battery, and the
  ordered "all" run yields one deterministic verdict. *Example: a deploy oracle with
  `service`/`workload`/`topology`/`auth` sub-checks so a sweep can re-grade only
  `topology` after a later scaling stage.* [ORACLE]
- **O30 — On a count/topology/identity mismatch, dump the live breakdown to stderr
  (verdict unchanged).** When an oracle fails "expected N, got M", print the
  per-resource breakdown it derived from (each StatefulSet's name/replicas/image/age,
  each member's state) so the failure log alone reveals which inherited/orphaned object
  inflated the count — turning a triage round-trip into a glance. Diagnostic only.
  *Example: a node-count oracle that, on a miss, lists every ES StatefulSet `(name,
  spec.replicas, image, age)`.* [ORACLE]
- **O31 — Assert a cluster-wide change on *every* member/node, not just the
  primary or pod-0.** A config/version/probe mutation "applied to the cluster" can land
  on the primary while a secondary is stale (a half-rolled restart). Loop every member
  and assert the field on each — and check both the controller template and each live
  pod, since they diverge mid-roll. *Example: a MongoDB config case that sets a
  parameter cluster-wide; the oracle reads it on every replica, and a version oracle
  asserts the target image on the STS template AND every running pod.* [ORACLE]
- **O32 — Prove enforcement with an explicit negative assertion, not only a
  positive one.** A security/isolation outcome (auth required, requireTLS, a revoked
  password, a read-only role) is proven only if the *forbidden* path actually fails:
  assert an unauthenticated query is rejected, a plain connect refused, the old
  credential denied. A positive-only oracle passes a cluster where auth/TLS was
  silently never enabled. Scan stdout *and* stderr, and per O18 never retry these.
  *Example: a deploy oracle that, alongside a successful authenticated ping, runs a
  credential-less query and fails if it succeeds.* [ORACLE]
- **O33 — For an externally-reachable deliverable, prove it end-to-end over the
  advertised path, not just by inspecting config.** When the task exposes a service to
  a new path (NodePort/external host, split-horizon, ingress), connect *through* the
  advertised endpoint and assert the live response identity (the replica-set name, an
  HTTP 200, the served document) — a config-only oracle passes a service whose endpoint
  doesn't actually route. *Example: a Mongo split-horizon oracle that, after asserting
  each member's horizons, connects over the advertised `EXTERNAL_HOST:NODEPORT` and
  asserts `db.hello().setName`.* [ORACLE]
- **O34 — Grade a rotation as a two-sided diff: new value present *and* old
  value gone.** A "rotate X" outcome is proven only by asserting the live artifact now
  equals the new target AND no longer equals the recorded old value. Asserting only
  "equals new" false-passes a no-op where the value was already the target; the
  precondition must capture the pre-rotation baseline for the oracle to diff (cf.
  O5). *Example: a password-rotation oracle asserting the secret matches `-next`
  and differs from `-old`; a cert-rotation oracle requiring the server fingerprint to
  change while the CA fingerprint stays identical.* [ORACLE]
- **O35 — Resolve a removed/renamed identifier against the live cluster; treat
  only an explicit "not found" as absent.** A setting/role/feature name the prompt
  allows can be spelled differently across versions. Probe which name the live version
  accepts and grade that one — and classify *only* the engine's explicit
  unknown-identifier message as "absent", never an auth/transient/timeout error. Cache
  it so before/after reads agree. *Example: a CockroachDB settings case where the
  configured setting name was removed in the running version; the oracle aliases to the
  live equivalent and only a literal "unknown setting" counts as missing.* [ORACLE]
- **O36 — Compare configured values semantically, not as strings.** Normalize
  both sides to canonical units before comparing — `1.5GiB`==`1536MiB`==`1610612736`,
  `1m30s`==`90s`, `on`==`true`. A raw string compare false-fails an agent who chose an
  equally-valid spelling the prompt never forbade; a genuinely wrong magnitude still
  differs after normalization. *Example: a CockroachDB setting graded where the agent
  wrote `64MiB` and the cluster echoes `67108864`.* [ORACLE]
- **O37 — Grade a rotation / config application from the live *served* artifact, not
  the stored bytes.** A cert-rotation oracle must read the certificate the server
  presents in the handshake (`s_client -showcerts`, the mongosh TLS session cert), not
  the Secret/ConfigMap bytes; a runtime-setting oracle must read the effective running
  value. Otherwise "updated the artifact but never reloaded the server" scores as
  success. Sharpens O33 (reachability + response identity) along the served-vs-stored
  axis. *Example: mongodb/certificate-rotation grades Secret fingerprint bytes plus a
  handshake against the unchanged CA, never the served leaf cert.* [ORACLE]
- **O38 — Grade sibling settings that are settable at runtime *or* via start-up
  config with the same dual-source read.** If one setting falls back to live-runtime
  introspection (`db.getProfilingStatus().slowms`) but a sibling is read only from
  start-up options (`getCmdLineOpts`), an all-runtime (or all-persisted) valid solution
  is judged inconsistently. Read runtime-first, then start-up config, for every such
  setting. *Example: mongodb/mongod-config-update reads `slowms` dual-source but
  `verbosity` only from `getCmdLineOpts`, false-failing a runtime `setParameter` fix.*
  [ORACLE]
- **O39 — Grade the exact durable signal the injected fault disables (the
  oracle-side mirror of P27).** For a break-then-fix case, assert the signal that stays
  broken until remediation — not a co-resident signal on a different listener/path the
  fault never touched, nor one that self-heals between restart cycles. When the fault
  breaks the readiness/HTTP-health path itself, O15's "grade `SELECT 1`/`is_live`
  instead of pod-Ready" does NOT apply — grade the faulted path directly (curl `/health`
  in-cluster, assert the pod is in the Service endpoints, assert pod-Ready after a
  converge window). *Example: cockroachdb/health-check-recovery breaks only one pod's
  HTTP `--http-addr` (loopback) while the oracle grades gRPC `is_live` + `SELECT 1`, so
  an idle agent passes.* [ORACLE]
- **O40 — Re-poll transient gateway *status codes*, not just exec errors,
  when asserting an exact HTTP-status set through a warming / multi-replica / reloading
  proxy.** A burst through a proxy being scaled or reloaded can return 502/503/504 from a
  not-yet-ready replica; a completed request carrying a transient 502/504 is a
  returncode-0 "success" that a retry-on-exec-error loop will NOT re-poll. Retry the
  status code within the bounded deadline and fail only on a *stable* wrong status.
  *Example: nginx/rate_limit_replica_hard (3 controller replicas) hard-fails on the first
  502/504 during warm-up although its exec-retry loop looks robust.* [ORACLE]
- **O41 — Scope a membership tally to the graded role; exclude
  helper/client members; pair it with a spec/ready-replica assertion.** An oracle grading
  a worker/replica count via a cluster-membership list (`ray.nodes()`, `_cat/nodes`,
  `rs.status().members`) must not let an auxiliary client/helper pod inflate the count,
  and must also assert the graded workload's own spec + ready replicas — else
  `head(1)+client(1)+worker(1)=3` passes a "2 workers / 3 nodes" contract one worker
  short. *Example: ray/worker_recovery and scale_workers register a throwaway
  `ray-client` raylet counted in `ray.nodes()`.* [ORACLE]
- **O42 — Probe an admin/management API in the order the app serves by
  default, not always TLS-first.** O11's https-first fits a console that redirects
  plain→TLS, but a management API defaulting to a *plain* port should be probed
  plain-first: a TLS listener a later stage brings up can answer the reachability probe
  yet then drag every real query past its deadline (curl exit 28). Pick the
  default-serving scheme first, fall back, accept any HTTP code (incl. 401), bound each
  attempt. *Example: a RabbitMQ oracle that tries the plain mgmt port before the TLS one
  so it works both before and after a mgmt-TLS stage.* [ORACLE]
- **O43 — When the graded outcome is a client reacting to the agent's fix,
  bounce the client once to escape CrashLoopBackOff before polling.** A side-car
  producer/consumer crash-looping against the *broken* baseline accumulates exponential
  backoff, so after the agent's fix its next successful restart can be minutes away.
  Delete the client pods once (their Deployment recreates them immediately, backoff
  reset) and re-evaluate to a bounded deadline. Distinct from O19 (which bounces
  the *primary* workload to prove persistence). *Example: a RabbitMQ permission-fix case
  whose clients are CrashLooping against the wrong perms; the oracle deletes them so
  they retry under the corrected perms.* [ORACLE]
- **O44 — When grading a remediation, assert the drift *source* is gone, not
  just the current snapshot.** If a case ships a self-healing reconciler (a CronJob, an
  operator, an init-loop) that re-applies the broken state, an oracle reading only the
  live value passes on a momentary good snapshot the reconciler then overwrites — a
  false pass that also trips the sweep. Verify both: the value is correct AND no
  reconciler is still configured to re-impose the fault. *Example: a RabbitMQ
  permission-fix case shipping a reloader CronJob; the oracle fails unless its script no
  longer enforces the wrong permissions.* [ORACLE]
- **O45 — Grade a one-shot batch workload by terminal completion *and* its result
  payload, within a budget.** A batch job (a Spark driver, a Ray job, an ETL run)
  differs from a long-lived service (O15) and an async signal (O23): assert
  it reaches a terminal success state within a generous cold-cluster budget AND emits
  the promised result (a sentinel line, a numeric result in range, an expected token).
  `succeeded>=1`/exit-0 alone false-passes a job that finished with wrong output; an
  immediate read false-fails one still running. *Example: a compute job whose oracle
  checks `Job.status.succeeded` only, passing a driver that finished but logged a wrong
  numeric answer; the fix also greps the log for the in-range result.* [ORACLE]
- **O46 — Grade an elastic/autoscaling outcome from the durable end-state or the
  resource's own scale history, never from a lossy sampled side-channel.** When the
  deliverable is "the workload scaled through phases N→M→K" (an HPA, a manual scale
  sequence, a burst-driven autoscale), do not grade it by counting events in a
  helper pod's polling watch-loop log: a fixed-interval `kubectl get` sampler misses
  transitions under load / pod-churn (and a hardcoded `>=2 events` threshold is
  weaker than the phase targets anyway), so a flawless agent that verifiably scaled
  5→10→20→5 can score zero events. Grade the durable signals — the Deployment's own
  `.status` / ReplicaSet rollout history, the observed max/target replica counts, or
  the agent's kubectl trace (≥N scale ops on the target) — and bound any helper exec
  (O17). Distinct from O23 (async signals need traffic+re-poll) and O45 (one-shot
  batch payload): here the resource *records its own history*, so read that.
  *Example: a Spark autoscale case graded solely by `SCALING EVENT` lines from a
  metrics-server watch pod that missed the transitions during an adversary's
  pod-churn.* [ORACLE]

---

## V. Composition & workflow rules

> **Quick reference.** *Additive composition:* C1, C3, C4, C7, C8, C13, C14.
> *Identity & ordering:* C2, C10, C11, C12. *What can / can't chain:* C5, C6, C9,
> C15, C16, C17, C19. *Regression sweep:* C18.

### Additive composition — bring dependencies, reconcile, restore

- **C1 — Bring every oracle dependency additively.** Anything the oracle reads
  (secrets, ConfigMaps, baselines, seed data, users/roles, a `monitoring`
  namespace) must be (re)established by its **own additive, idempotent,
  artifact-gated** precondition unit — never only inside the destructive build that
  the skip-gate bypasses on an inherited cluster. The unit must be data-only and
  never delete a namespace or restart a pod. [COMPOSITION]

- **C3 — Authoritative skip-probe for the case's own shape.** A lax probe ("6 pods
  Running") skips the build on an *incompatible* inherited topology, leaving the
  oracle's required objects (`es-http`) absent → unresolvable. Probe the exact
  resources the case needs. [COMPOSITION]

- **C4 — Adapt to live mode (auth/TLS/insecure); never assume the cluster's mode.**
  A plain probe against an inherited *secured* cluster fails → flips a unit to its
  *destructive* apply → wipes accumulated state and races the async delete. Detect
  live auth (read admin secret), TLS (detect CA path), and insecure-vs-certs mode;
  empty/plain fallback keeps standalone byte-identical. Gate presence on a
  **durable** signal, not a volatile one (`SELECT 1 || get pods | grep -q
  Running`). [COMPOSITION]

- **C7 — Restore a runtime feature additively when its baked-in config is skipped.**
  A scenario property baked into the case's own StatefulSet/ConfigMap (the
  `rabbitmq_prometheus` plugin on :15692) silently drops when the apply is skipped
  on a cluster inherited from a stage that omits it. Re-enable at **runtime**
  (`rabbitmqctl eval 'application:ensure_all_started(...)'`) probed on the running
  app set — never restart an `emptyDir` pod or rewrite a read-only file. [COMPOSITION]

- **C8 — Replant the exact drift a break-then-fix case checks.** A bootstrap-
  existence probe ("queue exists") passes on a cluster a prior identical stage
  already healed, so the fault is never re-planted and the oracle passes with zero
  agent action. Probe the *specific faulted state* and re-plant it idempotently.
  The same trap sinks an additive **re-plant fixture** whose probe checks a proxy
  instead of its own deliverable — it skips on the inherited cluster and the
  oracle's baseline/fault is never (re)created (see **P3**). [COMPOSITION]

- **C13 — Stages that re-apply a shared workload's StatefulSet must use a
  consistent volume backing.** If an early stage deploys a workload with `emptyDir`
  and a later stage re-applies the *same-named* StatefulSet (orphan-delete + apply, or
  a straight apply) with `volumeClaimTemplates` (or vice-versa), Kubernetes cannot
  roll the running pods from one volume shape to the other — `rollout status` stalls
  at `0 out of N updated` until the unit times out. The mismatch is invisible until
  composed: each case's StatefulSet is valid standalone. Align the volume backing
  across every stage that touches the shared workload (all `emptyDir` or all PVC), or
  don't chain the mismatched pair. *Example: a RabbitMQ workflow leads with
  `classic_queue` (emptyDir) then runs `manual_backup_restore` (PVC-backed STS) → the
  re-apply adopts the emptyDir pods and the rollout never converges.* [COMPOSITION]

- **C14 — A setup that requires a specific resource *sub-type* must
  declare it explicitly AND reconcile an inherited wrong sub-type, not assume the
  default.** Some resources have an immutable sub-type chosen at creation — a RabbitMQ
  queue's `x-queue-type` (classic vs quorum), an index's shard settings, a volume's
  storage class. A setup that creates the resource with the *implicit* default
  (`"arguments":{}`) inherits whatever the broker/cluster default is — and that default
  can change under composition: an earlier stage that upgrades the engine (RabbitMQ
  4.x removes classic mirroring and may default new queues to quorum) leaves the shared
  resource as the wrong sub-type. Because the sub-type is immutable, a plain idempotent
  create (`PUT`) cannot fix it, and the case's own `verify` then hard-fails on the
  inherited type. Declare the required sub-type explicitly on create, and if a
  wrong-typed resource already exists, delete-then-recreate to reconcile. Standalone the
  default happened to be right, so the bug only appears when composed after the
  type-changing stage. *Example: `manual_policy_sync` (needs a CLASSIC app-queue for its
  ha-all baseline) scheduled after `manual_skip_upgrade` to 4.1 found app-queue left as
  quorum; the fix deletes a non-classic app-queue and recreates it with an explicit
  `x-queue-type: classic`.* [COMPOSITION]

### Identity & ordering across stages

- **C2 — Identity contract across stages.** If stage A lets the **agent build** a
  resource and a later stage must find it, both ends must share the identity. The
  CockroachDB deploy→initialize seam: a `deploy` stage graded by the STS's *own* selector while
  an `initialize` stage hardcodes `-l app.kubernetes.io/name=cockroachdb` → "Expected 3,
  found 0" against a healthy cluster. Fix both: mandate the canonical labels in the
  creating stage's prompt+oracle, *and* have downstream oracles resolve by the live
  STS selector → canonical label → name prefix. **Identity is the resource *name*,
  not only its labels:** if stage A lets the agent create a secret/volume under an
  *undisclosed* name (an agent-chosen `crdb-certs`) and stage B targets a fixed name
  (`crdb-cluster-certs`), B grades a resource the pods don't mount. Mandate a
  canonical name in A's prompt/oracle, override B's `*_secret_name` param to match,
  or have B introspect the name the workload actually mounts
  (`sts …volumes[].secret.secretName`). *Example: a CockroachDB campaign chaining
  `generate-cert` (agent mounts `crdb-certs`) before `certificate-rotation` (targets
  `crdb-cluster-certs`); the served-cert check correctly fails because the rotated
  secret isn't the one the pods serve.* [COMPOSITION]

- **C10 — Pin a downstream verification stage's target to the *last* upstream stage
  that mutates the checked attribute** — not the first stage to reach the target
  value. A later same-attribute stage can re-mutate it: a `version-check` pinned to a
  `partitioned-update`'s `24.1.1` fails when a subsequent `major-upgrade-finalize`
  (defaulting to `24.1.0`) legitimately sets the version *back down* — the effective
  end-state is the last mutator's value, not the first's. Pin to the last mutator
  **declaratively, never as a re-typed literal** — a stage's `param_overrides` may
  reference a prior stage's resolved param (`target_version:
  ${stages.stage_03.params.crdb_version}`) so it can't drift; the resolver rejects
  self/forward refs and unknown ids, requires the **exact zero-padded** id (like
  ADV4), and only *warns* (resolving to `null`) when the referenced param name is not
  present in the referenced stage's resolved `param_overrides` (treat that warning as
  a chaining bug). Audit any pin whose justifying comment names multiple upstream
  stages with conflicting param values, and sweep *all* workflows for the mismatch.
  Drop SETUP assertions an upstream stage can legitimately invalidate; keep only the
  agent's-task assertions. [COMPOSITION]

- **C11 — Probe/verify the namespace the workload actually runs in.** Multi-namespace
  cases (e.g. a Spark per-team workload) must check the core-workload namespace, not the bare
  umbrella one. [COMPOSITION]

- **C12 — Multi-namespace cases bind logical roles per stage via `namespace_binding`.**
  A case declares logical roles (`source`/`target`/`default`); the workflow declares
  physical identities (`cluster_a`/`cluster_b`) and a per-stage `namespace_binding`
  maps role→identity, so `${BENCH_NS_SOURCE}`/`${BENCH_NS_TARGET}` resolve (and a
  migration can swap direction stage-to-stage). Omit it and those vars expand
  **empty** (`kubectl -n ''`); dedupe aliased roles to the physical identities at
  teardown. [COMPOSITION]

### What can / can't chain — order-sensitive & non-composable premises

- **C5 — Don't chain contradictory stages.** A successor's oracle preconditions
  must be satisfiable by the predecessor's end-state (a snapshot stage expecting ≥2
  nodes after a downscale-to-1; a seed-hosts-repair with `es-http` after a
  `search-*` topology). A workflow linter should reject these. [COMPOSITION]

- **C6 — Order-sensitive / un-recoverable-input cases: bring your own, or curate
  out.** When a case needs an input the running cluster legitimately doesn't retain
  (a CA **private key**, an original password, an old version/FCV, an admin user),
  it must **establish its own** additively when absent — or, if that's physically
  impossible (FCV downgrade; a base older than the chain leaves), be **curated out**
  of incompatible workflows. Don't fake it destructively. A frequent instance is a
  **version-baseline** case: an upgrade case whose precondition asserts a *starting*
  version (a `from_version: 3.9` skip-upgrade) cannot be chained after a stage that
  stood the cluster up at a *different* version (a `classic_queue` deploy pinned to
  `rabbitmq:3.12`) — the persistence invariant forbids a downgrade-reset, so the
  baseline probe correctly errors and the agent never runs. Fix at authoring time:
  deploy the chain's lead stage at the required baseline, override the case's
  `from_version` to match the inherited version, or curate the case out. [COMPOSITION]

- **C9 — Don't retry stages whose precondition plants non-reentrant state.** A
  break-then-fix case can't re-break what attempt-1 fixed; a genuine agent miss
  then masquerades as "precondition units failed." Default the suite to
  `retries: 0`; keep retry as a workflow-level `max_attempts` only where setup is
  idempotent. [COMPOSITION]

- **C15 — Workflow `param_overrides` must keep the *resolved* config internally
  valid, including against the parameters they leave at default.** When a stage overrides
  one member of a constrained pair — a `min`/`max`, a floor/ceiling, a `from`/`to` — it
  must keep the pair consistent. Overriding only one side can push it past an invariant
  the engine enforces, or past the **default** value of the side left untouched. The
  workflow author sees only the value they changed; the engine validates the *whole*
  resolved config and rejects the violation — so the stage becomes impossible for **any**
  agent, however competent (a workflow-definition bug, never an agent fault). When a sweep
  walks one bound across stages, walk or pin the other bound too so the invariant holds at
  every step. Statically checkable: resolve each stage's overrides against the case
  defaults and assert the pairwise constraint. *Example: a CockroachDB `zone-config`
  range-size sweep drove `range_max_bytes` down to 64 MiB while `range_min_bytes` stayed at
  its 128 MiB default; CockroachDB enforces `range_min_bytes < range_max_bytes`, so those
  stages could never apply.* [COMPOSITION]

- **C16 — A case whose premise mutates a cluster-shared control-plane
  component is not additively composable.** Flipping a shared ingress/gateway
  controller's class or watch-scope, a shared admission webhook, or a cluster-wide proxy
  config changes how the component treats the *default* traffic class for every
  co-resident case; the premise *is* the shared mutation, so it can't be made additive,
  and a probe keyed on a generic shared artifact (a `curl-test` pod, an Active namespace)
  is not authoritative for it. Curate to a `stage_01`-only dedicated workflow, or give it
  a private controller instance. *Example: nginx/class_only_upgrade sets
  `--watch-ingress-without-class=false` on the shared ingress-nginx-controller, breaking
  classless-Ingress siblings both before and after it.* [COMPOSITION]

- **C17 — A migration/promotion case whose premise requires an
  empty/absent target only chains where each instance's target is genuinely re-emptied or
  re-provisioned first.** Because env (PVC data) persists and the oracle typically grades
  only the target end-state, alternating source↔target reuse leaves a target populated by
  the prior stage's source → the "empty target" premise is unsatisfiable additively and
  the oracle passes with zero migration work (fault never re-planted). Re-empty the target
  in an additive precondition, assert the pre-migration empty baseline in the oracle, or
  curate the case out of alternating/marathon chains. *Example: rabbitmq/blue_green_migration
  in a 30-stage alternating workflow inherits a PVC-populated green and passes trivially.*
  [COMPOSITION]

- **C19 — A fault baked into the workload's own StatefulSet cert/volume
  topology cannot be additively re-planted onto a foreign inherited cluster; it is
  non-composable, so it must run only where its own build does.** When a
  break-then-fix case's fault lives in mounts its *own* StatefulSet declares (a
  `es-transport-ca-bundle` ConfigMap + per-node CA-signed cert secrets, a keystore
  volume, a specific cert layout), the C8 additive-replant escape hatch **fails
  silently**: composed after a different case, the generic skip-gate (C3) skips the
  destructive build, and the replant's ConfigMaps/secrets land on volume mounts the
  *inherited* StatefulSet doesn't have — so bouncing a pod just restarts it with its
  original (working) cert, the cluster is healthy, and the oracle false-fails on the
  fault-specific check (a 2-cert bundle that was never needed). You cannot fix this
  additively (the replant has nowhere to land) and you cannot rebuild mid-chain
  without destroying inherited state (Law 1). Give the case an **authoritative
  skip-gate** (C3) that rebuilds its own topology when absent, and schedule it only
  as the **cluster-establishing stage** (stage_01 / the first stage in its
  namespace) or standalone — never mid-chain onto a cluster another case built.
  *Example: elasticsearch/transport-additional-ca-trust (fault = ca1-only transport
  bundle + ca2-signed node-2, both mounted by its own STS) scheduled at stage_07 of
  bootstrap-recovery-harden: the replant no-ops on the inherited cluster and a
  healthy 3-node cluster false-fails "bundle should contain 2 certs, found 1".*
  [COMPOSITION]

### Regression sweep

- **C18 — A stage composed *before* a legitimate shared-state mutator trips the
  regression sweep; design it to be adjudicable.** The final sweep re-runs every
  passed stage's oracle against end-of-run state, so an early oracle that asserts an
  exact value a later stage is *meant* to overwrite (a rotated secret, a re-scaled
  topology, a capstone ConfigMap) shows as a sweep "failure" the LLM must rule a
  false positive — and it can only do so if the later stage's prompt makes the
  overwrite clear. Either say so in the prompt or drop the brittle SETUP assertion
  (cf. C10); a real regression should still fail the sweep. [COMPOSITION]
---

## VI. Prompt rules

- **PR1 — The prompt is the contract; state the full graded end-state** (including
  removals, negative constraints, planted decoys to clean up, and any exact value
  the oracle checks) — without step-by-step spoilers. Any oracle assertion not
  derivable from the prompt is a benchmark bug.
- **PR2 — Name the path/protocol the oracle reads.** If the oracle curls HTTPS:9200
  or a specific marker file, the prompt must say so (or the oracle must accept the
  conventional default).
- **PR3 — Concat prompt modes** (`progressive` = own prompt; `concat_stateful` =
  priors prepended with `(STAGE n)`/`(ACTIVE)` markers; `concat_blind` = priors,
  no markers) require the `stage_prompts` list to grow per pass; `rstrip()` each
  rendered prompt at the render boundary.

---

## VII. Adversary rules

> **Quick reference.** *Probe polarity & gates:* ADV1, ADV2. *Scoping & transport:*
> ADV3, ADV5, ADV9. *Stage refs & windows:* ADV4. *Inject/lift correctness:* ADV6,
> ADV7, ADV8.

- **ADV1 — Probe polarity is the INVERSE of preconditions.** Precondition:
  probe-pass ⇒ state present ⇒ *skip* apply. Adversary: probe-pass ⇒ target
  reachable ⇒ *run* apply (plant for deploy / remove for lift) + verify. An inject
  that reports `ok=True` *without running its apply* is the tell — test with a
  failing-apply case.
- **ADV2 — Honor `on_probe_fail` at the operation-block level; lift defaults to
  `skip`, deploy to `error`.** (The normalizer read it only from inside a probe
  mapping, ignoring all 38 scenarios; lift defaulting to `error` penalized an
  already-remediated fault.)
- **ADV3 — Namespace-scope every adversary `kubectl`** (`-n ${BENCH_NAMESPACE}`),
  use the app's required transport (TLS/auth/insecure mode), and inject values the
  target *accepts* (CockroachDB rejects `max_rate < 1 MiB`). An unscoped command
  hits the host default namespace and produces a vacuous pass.
- **ADV4 — Stage refs must string-exactly match a declared zero-padded id that
  exists and is in window order.** `stage_2 ≠ stage_02` aborts the run with no
  `run.json`; a `lift_at_stage` past the last stage never lifts (fault active
  through the final sweep); `null` = clean up at teardown.
- **ADV5 — Inject only where the target exists; scope the fault off graded
  resources.** The fault must target a resource present at inject time and **not**
  graded by any stage in the inject..lift window; for scenarios whose target may be
  absent, set the deploy probe `on_probe_fail: skip`. `restore_replicas` at lift
  must equal the live count at that point (account for earlier scaling).
- **ADV6 — Verify must positively assert the fault planted/removed**, tolerant of
  empty values (never `grep -qx ''` on a possibly-empty stream); write verify as a
  literal block scalar to avoid nested-quote fragility.
- **ADV7 — A spec-mutation adversary must also force the rollout that makes the fault
  take effect (and lift, the rollout that clears it).** Patching a StatefulSet/Deployment
  template (probe, image, resource, env, label) only changes the *desired* spec; running
  pods keep the old spec until they re-roll. An inject that patches the template but
  never restarts the pods reports a green verify while the live pods stay healthy — a
  vacuous fault. Follow a template patch with a rollout trigger (`delete pod -l
  <selector>` / `rollout restart`); lift restores the value and re-rolls; verify the
  *effect* (pods adopting the new spec), not just the template field. *Example: a
  probe-hardening adversary that patches `readinessProbe.timeoutSeconds` then deletes the
  pods so the StatefulSet re-rolls with the bad probe.*
- **ADV8 — Snapshot the pre-injection value at deploy, or restore to the documented
  canonical default — never an unverified literal.** Most non-scale faults restore at
  lift to a param default (a baseline password, image, count, rate, claim) they never
  read from the live target at inject time; if a prior stage set a different value, lift
  silently restores the *wrong* state. Capture the live value during the deploy probe and
  restore exactly that, or restore to a documented canonical default (and say so). This
  generalizes ADV5's replica-only `restore_replicas` discipline to images, secrets,
  rates, claims, annotations, config. *Example: an image-drift adversary that, at lift,
  `set image` back to a hardcoded tag — wrong whenever the workflow pinned a different
  baseline; the fix records the original image at deploy and restores that.*
- **ADV9 — Block a path with an allow-list that omits it, scoped so co-located traffic
  still flows.** Network faults block a port not with "deny X" but with an ingress
  `NetworkPolicy` whose allow-list *omits* the target (allow the admin port to block
  SQL; `ingress: []` to deny all), relying on Kubernetes implicit-deny once a policy
  selects the pod. Two traps: the policy must still *allow* the ports the rest of the
  workload and the oracle's own probes need (else it over-blocks unrelated graded
  paths), and lift *deletes* the policy (so deploy must not assume a prior allow-policy
  to clobber). *Example: a "block the SQL port" adversary whose policy allows only the
  admin/HTTP port — denying SQL by omission — while the admin endpoint the agent
  diagnoses through stays reachable.*
- **ADV10 — A config-drift adversary mutating one field inside a shared
  multi-field config blob must patch only that field and snapshot/restore the rest —
  never rewrite the whole blob from a hardcoded template.** When the config is one opaque
  key (`elasticsearch.yml`), a merge/replace patch replaces the entire file and clobbers
  co-located config an earlier stage set (e.g. forcing `xpack.security.enabled: false`
  back on), so the next restart comes up wrong and that stage's regression sweep fails
  through no agent fault. Extends ADV8's snapshot discipline to the shared-blob case.
  *Example: elasticsearch/es-config-seed-hosts-drift merge-patches the whole
  `elasticsearch.yml` key from a template.*
- **ADV11 — A NetworkPolicy adversary must template its selector from the probe's
  identity param and positively assert the block took effect.** (a) Derive `podSelector`
  from the same `{{params.cluster_prefix}}` the probe keys on — a hardcoded `app: rabbitmq`
  selects zero pods when the workload is named `rabbitmq-cluster-a`, a vacuous fault; (b)
  `verify` must assert the policy selects ≥1 running target pod *or* the target port is
  actually unreachable — never merely that the NetworkPolicy object exists (doubly vacuous
  on kind's non-enforcing CNI). Sharpens ADV6 for network faults. *Example:
  rabbitmq/block_amqp_network_policy hardcodes `app: rabbitmq` + existence-only verify;
  elasticsearch/transport-networkpolicy-block is the templated model.*

---

## VIII. Framework reference — *is this a framework bug, not my case?*

Behaviors the runtime now guarantees. If a failure matches one of these, it's
framework, not the case (most are already fixed; listed so you can recognize the
signature and, for older checkouts, know what to verify).

**Persistence / probe doctrine.** `on_probe_fail` semantics (the refactor once
*inverted* this, so seeding silently never ran in 78/79 cases): probe **passes** →
skip apply; probe **fails** + `skip` (default) → run apply; probe **fails** +
`error` → fatal gate.

**Transient-apply retry allowlist** (`_is_transient_apply_error`, `case.py`;
8×/6s). Each substring is a real race; a genuine error has no signature and fails
fast. pod-Ready/object-applied ≠ ready-to-use, and the allowlist must cover
*client-tool* phrasings:

| Substring | Race |
|---|---|
| `error looking up service account` / `serviceaccount "default" not found` | default SA not provisioned when a pod applies right after `create namespace` |
| `connection refused` / `could not connect to server` / `no route to host` / `i/o timeout` / `unable to connect to the server` / `the server is currently unable…` | peer/apiserver not yet up, or overloaded |
| `object is being deleted` | `create namespace` races a prior run's Terminating namespace |
| `no matching resources found` | `wait -l` before the controller materialized pods → instant rc=1 |
| `being terminated` | apply into a still-terminating namespace |
| `econnrefused` / `server selection` | mongosh/DB-client hits mongod before TCP/TLS up, or election |

An oracle-side allowlist `_TRANSIENT_SIGNATURES` (`TLS handshake timeout`,
`dial tcp`, `Client.Timeout exceeded`, `unexpected EOF`, `etcdserver: request
timed out`, …) re-runs the *verdict* on a transient oracle FAIL.

**Verify / error-gate default-retry.** The normalizer once hardcoded
`verify_retries=1`; now verify defaults to retry (24×/5s ≈120s) and error-gates
retry up to `verify_retries`, so async convergence doesn't false-fail. Auto-budget
is `verify_once + interval*retries`.

**Per-command timeout inference** (`_default_timeout_for_command`; explicit
`timeout_sec` always wins; scans kubectl *tokens* so `-n <ns>` isn't read as the
verb): `wait`/`rollout` 900s; `apply`/`create`/`patch`/`scale`/`set` 120s;
`delete` 180s; `exec` 300s; `get`/`logs`/`describe` 120s; `python` 600s in verify.


**Idle agent timeout.** `agent_timeout_sec` is an *idle* budget that resets when
`agent.log` grows, with an absolute `KARMA_AGENT_HARD_CAP_SEC` (default 3600).
Long cases (rabbitmq multi-hop upgrade ~47 min) need a generous dispatcher
wall-timeout or they're killed mid-run.

**One-shot agent execution.** The agent runs as a single non-interactive `--print`
session — ending the turn exits the process, with **no scheduled wakeup**. A model
that offloads a long wait (a rolling restart, a rollout) to a "background task" and
returns abandons the rest of the task, so the mutation lands **half-applied** and the
oracle correctly fails a would-be-correct solve. The **claude_code agent's** entrypoint
bakes in a system prompt telling the model to poll async ops to completion synchronously
(cases untouched) — but this is **agent-specific**: codex/copilot/api carry no such
instruction, so under those agents a backgrounded rolling restart is still abandoned
half-applied. **Triage tell:** a multi-step mutation consistently left *half*-done (one
pod un-restarted, a rollout mid-flight) is this, not the case.

**Metrics read the proxy snapshot's `verb`/`resource`** (lowercase kubectl verb,
plural resource name) — never HTTP `method`/`kind` (a plugin matching those silently
scores 1.0 forever), and never the *command run inside* a `kubectl exec` (the exec API
call is logged as a `create` on `pods`, but its in-pod effect is data-plane and
invisible to the metrics; grade in-pod work in the oracle — O27).

**Late-error result integrity (F-late).** A stage that already ran the agent and the
oracle MUST NOT be turned into a failure by a *transient error raised afterwards*
(during evidence collection, adversary-lift, proxy teardown, or a progress-callback
write). `run_stage`'s outer `except` once did exactly that: a stray `[Errno 32] Broken
pipe` — writing to a closed proxy pipe or a dead progress stream — was caught by the
catch-all and returned `status=error, submitted=False, oracle_verdict=None`, **silently
discarding an oracle verdict already written to `oracle.json` on disk**. The on-disk
artifacts (`oracle.json`, `submit.txt`) were correct; only the recorded result was
wrong. **Guarantees the runtime must hold:** (a) progress callbacks are best-effort — a
broken progress pipe can never fail a stage; (b) the final `except` recovers the
already-computed verdict from `oracle.json` and the real `submitted` value rather than
nullifying them — a late unrelated error is a note, not a verdict override. **Triage
tell:** `oracle.json` says `pass` but `run.json` says `error`/`[Errno 32] Broken pipe`,
or the agent's `submit.txt`/`result=success` is present yet the stage is
`submitted=False` — this is the harness, not your case. [FRAMEWORK]

**Retry correctness.** A retried stage clears its stale `submit.txt` before
relaunch (else it "submits in 0s" against the prior attempt's file).

**Namespace lifecycle.** Teardown deletes every namespace created since a
post-binding baseline (guarding system namespaces), deferred to workflow end;
run-id keeps an 8-char hash so retries don't collide; `create namespace` retries
twice on non-`AlreadyExists`.

**`required_roles`/`namespace_roles`: explicit `[]` vs `None`** — respected across the
run/workflow consumers (resolve, single-case, run_stage, sweep binding, cleanup, alias).
Exception: the manual operator-run path (`runtime/manual.py`) still uses the forbidden
`or ["default"]` fallback, so an explicit `required_roles: []` (a literal-namespace
case) mis-binds a default namespace there — a live Law-3 violation, not yet fixed.


**kubectl-proxy** (the largest agent-timeout cause). Two distinct ports (data
`--port` the agent uses + `--control-port`); launch retries 4× re-picking both
ports on `EADDRINUSE`, fails fast via `is_alive()`, gates readiness on a TCP
connect to the **data** port (not the control channel). Streaming: classify
bounded vs unbounded (watch/follow → incremental framing + 600s window), relay
with `read1()` (returns on first data), tunnel `exec`/`attach`/`port-forward` raw
(HTTP 101), re-auth client→upstream, forward `Content-Type`/`Content-Encoding`,
serialize the JSONL log under a lock, bind `0.0.0.0` for docker.

**Evidence pipeline.** Translate raw-HTTP method+path → kubectl verb/resource for
snapshots; parse logs defensively (per-line skip, decode `errors='replace'`,
no `exists()`-before-read); derive all paths from one `protocol.py` helper (a
double-scoped path silently zeroed all evidence).

**Param/substitution.** `{{params.key}}` recursive substitution (dropped in the
refactor → 11 cases ran with literal tokens); unwrap `{default: …}` before
substitution; validate/coerce params by declared `type`/`values`/`min`/`max`/
`required` (coerce only when a `type` is declared); decoys come from explicit
`decoys:` *and* auto-discovered `decoy/*.yaml`. Sweep for unresolved `{{…}}`.


**Guards.** Reject stage-less workflows (`Field(min_length=1)`); a no-agent run
still runs setup+oracle; best-effort steps route through `warn()` (visible, not
silent `except: pass`); cross-stage agent memory via `agent_session: persistent`.


---

## IX. Pre-ship checklist

- [ ] **Standalone:** preconditions build infra AND plant the unsolved problem; a
      real solve satisfies the oracle; generated namespaces are removed; the fault
      is actually injected (heavy case must NOT finish in ~6s — Law 5).
- [ ] **Probes:** no `|| true` (P1); probe this case's own named resource (P2) and
      the exact oracle-checked state (P3); skip-gate is authoritative for the
      case's shape (C3); `error`-gate vs `skip`-gate matches intent (P5); a planted
      fault is verified from the control plane, not through the capability it
      disables (P27).
- [ ] **Reused cluster:** fixed-namespace delete+wait-for-delete+create inside the
      skip-gate (P9); probe gated on namespace Active; non-blocking teardown with a
      real budget (P10); get-or-apply helper Pods (P15); tolerant/get-or-skip applies
      for shared cluster-scoped resources (P26); patch-don't-re-apply inherited
      StatefulSets (P16); inner verify loop fits the unit budget (P14).
- [ ] **Composition:** every oracle dependency brought additively (C1); idempotent
      seed vs live count (P20); adapt to live auth/TLS/mode (C4); identity contract
      across stages (C2); not chained into a contradictory/order-sensitive slot
      (C5, C6); bring-your-own unrecoverable input (C6).
- [ ] **Timing:** controller-level waits + existence-poll before named-pod wait
      (P12); `kubectl wait --timeout` paired with `timeout_sec` (P13); first DB
      call ping-gated (P11); flap-retry every oracle reachability check (O13),
      bounded exec/curl (O17), oracle budget sized for exec count (O20).
- [ ] **Oracle:** grades only the prompt's promise (O1); resolves from live state
      scoped to the target (O2); proper client identity / mode / pod-local fallback
      (O6, O7, O8, O11, O10); accepts equivalents
      (O22) and inspects *all* entries of a multi-valued artifact (O4);
      internal retry/wait loop finishes before `timeout_sec` (O21) and any
      oracle-initiated pod restart is budgeted for the worst case (O19);
      deterministic-vs-transient discipline (O18); escaped jsonpath + imports
      (O24, O25).
- [ ] **Literals:** roles/versions/units/SAs validated real; versions
      parameterized + envsubst whitelist (P18); no mutable secret in a readiness
      probe (P19); quoted URLs, single shell wrap (P21, P22).
- [ ] **Prompt:** states the full graded end-state incl. removals/decoys (PR1) and
      the path/protocol checked (PR2).
- [ ] **Adversary:** inverse probe polarity (ADV1); block-level `on_probe_fail`,
      lift→skip (ADV2); namespace-scoped + right transport + accepted values
      (ADV3); zero-padded in-range stage refs, scoped off graded resources (ADV4,
      ADV5); positive verify (ADV6).
- [ ] **Triage discipline:** confirm a failure isn't INFRA-FLAKE or AGENT_FAULT
      before filing a test bug (§II); a deterministic failure gets a root-cause
      fix, not retries (O18); re-validate after every fix (a fix unmasks the next
      layer).
- [ ] **Sweep:** when you find a fault in a cloned family, fix it across **all**
      cases/workflows, not just the one observed (Law 8).

---

## X. Service appendix — case-specific patterns

### Elasticsearch — the fault epicenter (24 cases, slowest service ~7.4 min median)
ES carries the most and most-distinct faults. JVM cluster formation + master
election + shard recovery to green + one-at-a-time rolling restarts make every
operation slow and flap-prone, and the security model adds an auth layer mid-chain.
Default audit assumptions for a new ES case:
1. **Node-count over-count (O2)** — oracle sums *all* ES StatefulSets; scope to
   live `_cat/nodes`-backed STSs, gate on desired `spec.replicas` of
   not-being-deleted STSs (status lag drops a joined-but-yellow node), live-derive
   first for add/preserve cases (param-first only for downscale).
2. **Auth drift (B1)** — a prior `deploy-core`/`file-realm` stage enables security;
   every must-succeed query (oracle *and* additive fixture) must read the elastic
   password from its secret live and `-u elastic:<pw>` **only when the secret
   exists**; never bake the password into a long-lived helper pod or hardcode it.
3. **Skip-gated artifacts (C1)** — secrets/ConfigMaps/keystore keys/indices created
   only inside `es_env_ready` vanish on an inherited cluster; each needs its own
   additive, best-effort, artifact-gated unit (split second-namespace setup so it
   never deletes the ES namespace).
4. **Flap + tight budgets (O13)** — single-snapshot node/health checks race
   convergence; align client `--max-time` > server `wait_for_*`; size retry
   windows for the loaded composed cluster.
Also: allocation-attribute oracles must flag a (key,value) on any *new* node no
*original* carries (incl. a brand-new key) and exclude ES built-ins
(`transform.node`, `ml.*`); seed `-r viewer` not `read`; quote `&` in health URLs.

### MongoDB — TLS, primary, and auth (18 cases)
- **TLS client cert (O6)** — ES/Mongo TLS client-cert handling: present the
  client cert the cluster expects, as CLI flags (not URI), cached per pod. Consumer
  oracles relax cert checks (O9); read with no URI/directConnection
  (O7).
- **Primary after election (O8)** — detect `db.hello().isWritablePrimary`;
  seed waits for a primary, never defaults to pod-0.
- **Adaptive auth (O2/C4)** — accept both `authSource=admin` and `=<appdb>`; the
  "bring-your-own admin user" fixture (C6/C1) for cases composed after a no-auth
  predecessor; ping-gate before `rs.initiate` (P11).
- **Version-upgrade** cases need an *old-version baseline*, which is impossible to
  establish on an inherited cluster that already holds newer data — a precondition
  that `set image … mongo:5.0.5` to downgrade the binary leaves mongod unable to
  start on data written by a newer version, so the pods **crash-loop**, the
  `readyReplicas` wait times out (~650s), and the next `kubectl exec` fails
  `container not found ("mongod")`. Same hard constraint for crdb/ES (a binary
  can't read a newer on-disk format). **Curate these cases OUT of any chain that
  reaches them past their base version** (C6); test them standalone.

### CockroachDB — labels, mode, version (16 cases)
- **deploy→initialize label seam (C2)** — the dominant fault; mandate canonical
  labels in `deploy`, resolve downstream by live selector.
- **Secure TLS** — node-cert SANs (lost in port) cause "container not found db"
  (P35); mode-adaptive probes/oracles (`--certs-dir` vs `--insecure`, C4);
  bring-your-own CA when `ca.key` isn't retained (C6); re-key without a mixed-CA
  window (P24).
- **Version** — parameterize the image (P18); assert major.minor not exact patch;
  resolve removed/renamed settings against the live version; pin downstream
  version-check to the upstream target (C10).
- **Timing** — `cockroach init` after `condition=Ready` not `phase=Running`;
  persistence-restart drain budget; single-snapshot oracles poll to convergence.

### RabbitMQ (12 cases)
- Blue/green: **separate source/target precondition units** (don't re-apply both on
  one drift); orphan-delete inherited StatefulSets (`--cascade=orphan`, P16) since
  `emptyDir` clusters lose users/queues/policies on a real restart.
- Runtime-enable a baked-in plugin (`rabbitmq_prometheus`, C7); durable CA secret
  for a manual TLS-rotation case (C6); replant policy/permission drift (C8);
  a multi-hop skip-version upgrade is order-sensitive → curate out (C6); plain-port-first
  management API; bound every oracle exec/`s_client` (O17).

### nginx-ingress (10 cases) · Ray · Spark
- nginx: rate-limit accepts 429 **or** 503, prove with an unpaced burst (O22);
  otel needs traffic + re-poll (O23); self-derive ingress node IP/HTTPS port;
  reach replicas via Service DNS (not hostPort) with a shared rate-limit zone.
- Ray: enable the dashboard on the head; pin the driver IP on single-host clusters
  (`MY_POD_IP`); ship the broken baseline (worker crash-loop) not the fixed one;
  big-image bring-up timeouts; ray-client connect timeouts.
- Spark: ship the *broken* baseline (executor memory/RBAC/SA faults) not the
  remediated one; legal fault values (`512Mi` not `512`, an existing SA/image);
  probe the team namespace the workload runs in.

### Trap cases (rollback-rehearsal · change-plan-only · readonly-audit, every service)
Must plant **durable, live-checked non-default state** so an agent that wrongly
reverts/applies is caught by the regression sweep (a default cluster or
sleep-infinity pods are toothless); the oracle reads only the produced artifact
(escaped jsonpath key) and makes no destructive change; the prompt references
resources the deploy actually creates. **Trap-teeth:** the case's OWN oracle must
re-verify that non-default state is unmutated (teeth *standalone*), never relying
solely on the workflow regression sweep — and the prompt must not assert state the
precondition never planted (a stock default baseline described as "non-default
configuration applied" is toothless standalone). — And if a case relies on the
`decoy_integrity` metric to catch a careless mutation, the decoy must live in its
**own namespace**: the metric keys on the decoy's `namespace`, so a decoy planted
into the graded/role-bound namespace scores nothing.
