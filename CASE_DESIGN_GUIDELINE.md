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
FRAMEWORK · TRIAGE — with a stable id (e.g. `P3`, `O-flap`, `C2`) for
cross-reference, and each carries a short **Example** at the application level to
make the pattern recognizable.

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
| oracle fails but the prompt never asked for the missing thing | oracle-contract drift / non-additive dep | O-contract, C3 |
| run aborts with **no `run.json`** | adversary stage-ref mismatch (pre-run) | ADV4 |
| a heavy case "trivially passes" / finishes in ~6s | silent no-op precondition (Law 5) | P-noop |
| zero kubectl activity in evidence | proxy log double-nesting / empty KUBECONFIG / schema mismatch | F-E13, F-E15, F-B7 |
| a check fails on **every** attempt (and on an idle node) | **deterministic** root cause — find it from agent ground truth; do **not** paper over with retries/timeout bumps | O3 |

**Re-validate after every fix** — surface fixes routinely unmask the next layer
(http→https reaches ES → exposes 401; pinning a blocker surfaces a downstream
version mismatch).

---

## III. Precondition rules

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
- **P-noop — Verify the fault actually planted.** An atomic JSON-patch with one
  bad pointer (`/.../cases/requests/memory` where `cases` should be `resources`)
  silently no-ops the *whole* patch → the StatefulSet stays healthy → trivial
  pass. [PRECONDITION]

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
  load); with the default 120s command cap it dies mid-wait. Use `--wait=false` +
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
  budget. *Example: an ES master-downscale whose `seq 1 30`×`sleep 3`
  (~240s) verify in a 120s unit was retried ~13× → blew the 600s cap.* [PRECONDITION]
- **P14b — To set per-unit `retries`/`interval_sec`, author the structured block
  form** `{commands: […], retries: N, interval_sec: M}` for probe/apply/verify — a
  bare string or list carries no per-unit retry budget. A malformed wrapper or wrong
  key surfaces at load as the misleading *"verify command(s) are required"*, not a
  runtime error.

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
  customized the STS → immutable-field Forbidden.* [COMPOSITION]
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
- **P-decoy — Decoy planting aborts the stage on a missing/ill-scoped manifest;
  treat it like a precondition.** `plant_decoys` *raises* (killing the stage before
  the agent runs) when a `decoys:` path is absent or its apply fails, and a decoy
  with no explicit `namespace:` lands in the proxy default, not the role-bound one.
  Validate every decoy path exists, render-resolves, and carries the intended
  namespace — a broken decoy is a silent "stage error," not a graded fail. [PRECONDITION]
  (`viewer`, not the non-existent `read`), image tags, memory units (`512Mi`, not
  bare `512`), service-account names, settings names — a wrong value fails (often
  silently). [PRECONDITION]
- **P18 — Parameterize versions/images; don't hardcode.** A skip-gated destructive
  apply pinned to a fixed image clobbers a workflow's version baseline. Use a
  `*_version` param. **Sub-trap:** whitelist the variable *name* in single quotes
  (`envsubst '${BENCH_PARAM_CRDB_VERSION}'`) — an unquoted name is expanded by the
  inner `/bin/sh -c` to `envsubst "23.2.0"` *before* envsubst runs, leaving the
  literal `${…}`. Preserve downward-API refs (`$(POD_NAME)`). [PRECONDITION/COMPOSITION]
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
- **P23 — Size scheduling *requests* to fit the target node** (request governs
  scheduling; the DB scales cache to the *limit*, so a small request is safe). 5×2Gi
  requests don't fit a 7.6Gi node → pods Pending. [PRECONDITION]
- **P24 — TLS re-key without a mixed-CA window.** A CA swap done with rolling
  `kubectl delete pod` leaves old- and new-cert pods that can't handshake. Scale to
  0 first then up, or use `podManagementPolicy: Parallel` (PVCs untouched → data /
  node IDs persist); make `rollout status` best-effort and let a `SELECT 1`/health
  loop be the real gate. [PRECONDITION]
- **P25 — Use portable tool flags.** `openssl x509 -not_before/-not_after` needs
  3.2+; use `openssl ca -startdate/-enddate -days N`. Verify fixture-gen inside the
  *runner* image, not the dev host. [PRECONDITION]
- **P-secure — A secure-TLS cert must carry the exact SANs/identity the handshake
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
- **P-toolimage — Pin helper/tool images to a fixed tag that ships the binary;
  never `:latest`.** A helper pod (openssl-toolbox, curl-test, mongo-client) on a
  `:latest` tag **drifts** — a later image build can move or drop the very binary
  your precondition execs, so `kubectl exec toolbox -- sh -c 'openssl …'` dies
  `command not found` (exit 127) the next time the image is pulled. Pin to a
  specific version known to ship the binary on `PATH`, and prefer a purpose-built
  image (`alpine/openssl:3.1.4`) over a bare base that `apk add`s at runtime (that
  races the exec). This is the *helper-pod* counterpart of P18 (parameterize the
  **workload** image so workflows can override it, but **pin** the **tool** image)
  and the precondition-side of O-binary (exec a binary only into a pod that ships
  it). Especially insidious under composition: a get-or-apply toolbox + a skip-gated
  cert path means the drift only bites when the cert artifact *isn't* inherited and
  the exec actually runs. *Example: a CockroachDB cert-rotation case whose
  openssl-toolbox on `alpine/openssl:latest` → cert-gen script "openssl: command
  not found"; a whole family of cert cases shared the same `:latest` toolbox.* [PRECONDITION]
- **P-order — Order precondition units by dependency; make the dependency explicit.**
  Units run in declared order, so a fixture that authenticates as admin (seed data,
  create a downstream user, plant an auth-gated fault) must be declared *after* the
  unit that establishes that credential — otherwise it runs against a not-yet-existing
  principal and its `apply` silently no-ops on an inherited cluster. State the order
  in a comment so a later edit can't reorder it. *Example: a MongoDB precondition where
  the admin-user fixture is declared before the seed fixture so seeding can
  authenticate, and a get-or-apply openssl-toolbox precedes any unit that execs
  openssl.* [PRECONDITION]

---

## IV. Oracle rules

### Grade the contract, from live state, scoped to the target
- **O1 — Grade only what the prompt promised.** Any exact filename, count, label,
  version, role/object name, magic probe value, or `replSetName` the oracle checks
  must appear in the prompt or be planted by the precondition — never left for the
  agent to guess. Prefer grading the **effective outcome** (can read reports;
  denied writes) over an undisclosed identifier. [ORACLE/PROMPT]
- **O2 — Resolve expectations from live state, scoped to the target object.**
  Never sum a global topology a namespace legitimately accumulates; never hardcode
  a standalone count/name/scheme. Count only StatefulSets backing live
  `_cat/nodes` members (gate on **desired `spec.replicas` of not-being-deleted**
  STSs, *not* transient `status.readyReplicas`); resolve service/version/setting
  from the live cluster; read `BENCH_PARAM_*` with the old value as default.
  **Exception:** where the count/mode *is* the graded outcome (downscale,
  decommission, generate-cert), stay param-first — deriving from live would mask a
  failed operation. [ORACLE]
- **O-contract — Don't contradict the prompt or your own precondition.** Wrong
  scheme (`http://` vs required HTTPS), wrong hardcoded path, demanding both
  sidecars when the prompt named one, accepting only `--advertise-host` not
  `--advertise-addr=$(hostname -f)`, or asserting backend-TLS the precondition
  deliberately deployed as plain-HTTP — all fail an honest agent. [ORACLE]
- **O-multi — Inspect *every* entry of a multi-valued artifact; accept a valid
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
- **O-relative — Validate against an *absolute* target, not an inherited
  artifact.** A "rotate to ~1y" check that required the new cert to *outlive* the
  inherited old one breaks when chained after a multi-year cert. Derive the target
  from the prompt ("≥10 months from now"), or from the *recorded* baseline for
  relative asks ("+1", "2x") — never the raw inherited value. [ORACLE]

### Connection & client identity
- **O-tls — Connect exactly as the agent's proven command does** (ground truth
  from `agent.log`/`kubectl_log`). Under mutual `requireTLS` a certless or
  wrong-cert connection is dropped (`connection <monitor> … closed`). Pass
  `--tls/--tlsCAFile/--tlsCertificateKeyFile` as **CLI flags** (mongosh *ignores*
  file-path TLS options in a URI; a `mongodb://` URI defaults `tls=false` and
  overrides `--tls`); present the client cert the cluster expects; cache cert paths
  **per target pod** (different pods mount different certs); `test -f` each path so
  standalone stays plain. [ORACLE]
- **O-direct — Don't impose a connection mode the agent never uses.** Reading
  `rs.conf()`/`rs.status()` against default localhost starts replica-set SDAM
  monitoring, which drops under `requireTLS`; a short `serverSelectionTimeoutMS`
  then drops under load. Read with **no URI / no directConnection / default
  timeouts** (as the agent does), from the **first member that answers**.
  [ORACLE]
- **O-primary — Detect the live primary; don't assume pod-0.** After an election
  (arbiters/scaling stage) the primary moves; primary-only ops execed into a fixed
  `…-replica-0` fail `not primary and secondaryOk=false`. Detect via
  `db.hello().isWritablePrimary` across members (cached); standalone resolves to
  pod-0 unchanged. [ORACLE]
- **O-consumer — Consumer oracles that only need to *connect* should relax cert
  checks** (`--tlsAllowInvalidCertificates/Hostnames`, or `sslmode=require` +
  client cert through a proxy whose hostname a backend cert can't match). Only the
  TLS-*defining* cases keep strict validation. [ORACLE]
- **O-pod-local — Fall back to pod-local when a Service has no endpoints.** When a
  check goes through a Service that a prior stage may have drained, fall back to
  `kubectl exec <pod> -- curl localhost:<port>`. [ORACLE]
- **O-scheme — Fetch admin/console endpoints scheme-adaptively** (`https -k -L`
  then `http`) and SQL/HTTP mode-adaptively (`ls ca.crt` → `--certs-dir` vs
  `--insecure`). A secured endpoint 307-redirects plain HTTP to HTML. [ORACLE]
- **O-binary — Exec a binary only into a pod whose image ships it** (run
  `openssl s_client` from the broker pod, not a curl-only helper). [ORACLE]

### Robustness & timing
- **O-flap — Poll volatile state to convergence.** Multi-node clusters flap at the
  readiness edge (GC, shard recovery, master election, rolling restart) though
  stably green. Refactor volatile checks into `evaluate()` and re-run for a bounded
  deadline (~75–150s), passing on the first clean snapshot; keep config/cert/count
  checks single-pass. Not a loosening — a genuinely degraded cluster fails every
  attempt. [ORACLE]
- **O-flap-restart — A count/topology tally read *after* a solution that touches the
  pod template is volatile — flap-retry it.** The "keep count checks single-pass"
  carve-out in O-flap only holds when nothing restarts the pods. If the agent's task
  is a label/probe/resource/config edit to a StatefulSet, it forces a **rolling
  restart**, and the last-restarted member spends seconds in a rejoin window
  (mongod `STARTUP2`/`RECOVERING`, an ES node re-electing, a crdb node re-Ready)
  during which a member/replica/node tally reads short. So a PRIMARY/SECONDARY count,
  a "`N` nodes" check, or a "`N` ready" check that follows a template mutation MUST
  use the O-flap convergence wrapper (or wait on `rollout status` first), not a
  single snapshot. *Example: a MongoDB case whose solution sets a StatefulSet
  `monitoring=enabled` label → rolling restart → an oracle that reads `rs.status()` once with
  the last-restarted pod at age 7s → "expected 2 SECONDARY, got 1" on a stably-healthy set.* [ORACLE]
- **O-funcready — Grade *functional* readiness (the service serves), not just the
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
- **O-maxtime — Client `--max-time` must exceed any server-side `wait_for_*`** it
  triggers (else curl exit 28). Shorten the server health timeout (≤10s), raise
  the client deadline (~20s), let the oracle's own loop wait. [ORACLE]
- **O-bound — Bound every exec/curl/`s_client`.** An un-timed `subprocess` /
  `kubectl exec` / `openssl s_client` against a reloading listener hangs to the
  oracle deadline, the uncaught `TimeoutExpired` crashes the *whole* oracle, and
  the false fail cascades to "precondition units failed" on retry. Add `timeout=`,
  `--connect-timeout/--max-time`, `timeout 15 s_client`; catch the exception;
  retry hang/empty as "not converged". [ORACLE]
- **O3 — Deterministic ≠ transient.** A check that fails on *every* attempt (and
  on an idle node) has a deterministic root cause — find it from agent ground
  truth; do **not** sweep retries/timeout bumps over it (they were added, were dead
  weight, and were reverted). Retries must never mask a *wrong value* (the
  assertion still runs on the read), and must **never** apply to negative/
  expected-failure checks (an unauthenticated probe, `check_plain_blocked`, an
  invalid-old-password probe). [ORACLE/TRIAGE]
- **O-restart — When the outcome needs a pod to recover, delete it once** so it
  recreates without accumulated CrashLoopBackOff, then poll. After a restart, poll
  the pod to exist+Ready *and* retry a `SELECT 1`/`ping` (Ready ≠ accepting
  clients). When the **oracle itself** restarts a pod to prove persistence, size
  that readiness wait for the *worst* case it will meet — a **secure**, **loaded**,
  already-**repeatedly-bounced** node can take far longer to drain-rejoin-and-Ready
  than a fresh one (and the wait must stay under the oracle `timeout_sec`, see
  O-deadline). *Example: a CockroachDB cluster-settings case where the oracle's own 2nd
  pod-delete `wait_pod_ready(150s)` times out on a secure node bounced across
  several prior stages, failing a correct agent.* [ORACLE]
- **O-budget — Size the oracle `timeout_sec` to the number of `kubectl exec`
  round-trips × per-exec latency under load**, with headroom; default the arg to
  `None`, resolve `max(oracle_timeout_sec, Σ per-command + sleeps)`. [ORACLE/FRAMEWORK]
- **O-deadline — An oracle's internal retry/flap/wait loop must finish strictly
  *before* its own `timeout_sec`.** If the loop's deadline equals (or exceeds) the
  harness oracle budget, the harness kills the oracle mid-loop and it **never prints
  a verdict** — the result is literally `[timed out after 119s]`, i.e. a correct,
  passing run scored as a fail. Set the internal deadline below `timeout_sec` with
  headroom for the final read + output (e.g. loop ≤90s under a 120s budget), or
  raise `timeout_sec` above the loop. This is the flip side of O-flap (the loop is
  right; its window must fit). *Example: an ES stack-monitoring case (loop
  deadline 120s == budget) and a CockroachDB cluster-settings case — both completed the task,
  both killed before the verdict.* [ORACLE/FRAMEWORK]
- **O-equiv — Accept equivalent valid outcomes.** ingress-nginx returns **503**
  (not always 429) on a throttled burst → accept either. To *prove* a rate limit,
  fire an **unpaced burst**, never a fixed-rps cadence that can match the limit (a
  param override of `limit_rps` silently neutered a hardcoded ~2 rps probe).
  [ORACLE]
- **O-async — Async signals need traffic + re-poll.** A distributed-trace / metrics
  / reload check must drive a small burst and re-poll the collector to a deadline
  (the ingress doesn't sample every request; spans export on a later OTLP batch).
  [ORACLE]

### Scripting hygiene
- **O-jsonpath — Escape literal dots in jsonpath keys.** `{.data.rollback.sh}`
  parses `rollback.sh` as a nested field → always empty → trap oracles fail even
  when the ConfigMap is correct. Use `{.data.rollback\.sh}`. (Found in 7 services
  / 21 files — sweep when seen.) [ORACLE]
- **O-imports — Oracle scripts need their imports; lint them.** A missing
  `import os` is a 100% NameError crash; a name collision (`expected_nodes` int
  reused as a list) crashes `range()`. Smoke-compile + name-resolve every
  `oracle.py` before a sweep. [ORACLE]
- **O-seed — Don't depend on a seed count an agent can zero.** Either warn in the
  prompt that the data is load-bearing, or re-seed it idempotently in a problem
  unit, so a (mis)behaving agent's cleanup can't permanently strand the oracle.
  [ORACLE]
- **O-exec-metric — Grade in-pod mutations in the oracle; metrics can't see them.**
  Every scoring metric (blast_radius, destructive_ops, decoy_integrity, residual_drift…)
  reads only the kubectl-proxy snapshot's `verb`. A change made via `kubectl exec`
  into a pod (`mongosh`, `rabbitmqctl`, `cockroach sql`, `curl localhost`) records
  `verb=exec`, so a destructive in-pod operation scores a *perfect* blast_radius.
  Never rely on a metric to police an agent whose mutations happen inside a pod —
  assert that contract in the oracle. [ORACLE/METRICS]

### Assertion completeness & oracle structure
- **O-collect — Accumulate every check into an error list; never raise mid-snapshot.**
  A single read that raises crashes the whole oracle (cascading to a false
  "precondition units failed" on retry) and hides every *other* failure, so a
  fix-rerun cycle surfaces one problem at a time. Each check appends a human-readable
  string to a shared list and continues; one reporter prints them all and fails iff
  any. This is what makes O-flap's "re-run until the list is empty" possible. *Example:
  a replica-set oracle that reports both "expected 1 PRIMARY got 0" and a host-set
  mismatch in one verdict instead of dying on the first parse of a mid-election status
  read.* [ORACLE]
- **O-subcheck — Expose each assertion as an independently dispatchable named
  sub-check.** A `--check {all,<name>}` dispatcher lets the regression sweep and triage
  probe one dimension (just-topology, just-auth) without the full battery, and the
  ordered "all" run yields one deterministic verdict. *Example: a deploy oracle with
  `service`/`workload`/`topology`/`auth` sub-checks so a sweep can re-grade only
  `topology` after a later scaling stage.* [ORACLE]
- **O-diag — On a count/topology/identity mismatch, dump the live breakdown to stderr
  (verdict unchanged).** When an oracle fails "expected N, got M", print the
  per-resource breakdown it derived from (each StatefulSet's name/replicas/image/age,
  each member's state) so the failure log alone reveals which inherited/orphaned object
  inflated the count — turning a triage round-trip into a glance. Diagnostic only.
  *Example: a node-count oracle that, on a miss, lists every ES StatefulSet `(name,
  spec.replicas, image, age)`.* [ORACLE]
- **O-everymember — Assert a cluster-wide change on *every* member/node, not just the
  primary or pod-0.** A config/version/probe mutation "applied to the cluster" can land
  on the primary while a secondary is stale (a half-rolled restart). Loop every member
  and assert the field on each — and check both the controller template and each live
  pod, since they diverge mid-roll. *Example: a MongoDB config case that sets a
  parameter cluster-wide; the oracle reads it on every replica, and a version oracle
  asserts the target image on the STS template AND every running pod.* [ORACLE]
- **O-negative — Prove enforcement with an explicit negative assertion, not only a
  positive one.** A security/isolation outcome (auth required, requireTLS, a revoked
  password, a read-only role) is proven only if the *forbidden* path actually fails:
  assert an unauthenticated query is rejected, a plain connect refused, the old
  credential denied. A positive-only oracle passes a cluster where auth/TLS was
  silently never enabled. Scan stdout *and* stderr, and per O3 never retry these.
  *Example: a deploy oracle that, alongside a successful authenticated ping, runs a
  credential-less query and fails if it succeeds.* [ORACLE]
- **O-e2e — For an externally-reachable deliverable, prove it end-to-end over the
  advertised path, not just by inspecting config.** When the task exposes a service to
  a new path (NodePort/external host, split-horizon, ingress), connect *through* the
  advertised endpoint and assert the live response identity (the replica-set name, an
  HTTP 200, the served document) — a config-only oracle passes a service whose endpoint
  doesn't actually route. *Example: a Mongo split-horizon oracle that, after asserting
  each member's horizons, connects over the advertised `EXTERNAL_HOST:NODEPORT` and
  asserts `db.hello().setName`.* [ORACLE]
- **O-rotate-diff — Grade a rotation as a two-sided diff: new value present *and* old
  value gone.** A "rotate X" outcome is proven only by asserting the live artifact now
  equals the new target AND no longer equals the recorded old value. Asserting only
  "equals new" false-passes a no-op where the value was already the target; the
  precondition must capture the pre-rotation baseline for the oracle to diff (cf.
  O-relative). *Example: a password-rotation oracle asserting the secret matches `-next`
  and differs from `-old`; a cert-rotation oracle requiring the server fingerprint to
  change while the CA fingerprint stays identical.* [ORACLE]
- **O-resolve — Resolve a removed/renamed identifier against the live cluster; treat
  only an explicit "not found" as absent.** A setting/role/feature name the prompt
  allows can be spelled differently across versions. Probe which name the live version
  accepts and grade that one — and classify *only* the engine's explicit
  unknown-identifier message as "absent", never an auth/transient/timeout error. Cache
  it so before/after reads agree. *Example: a CockroachDB settings case where the
  configured setting name was removed in the running version; the oracle aliases to the
  live equivalent and only a literal "unknown setting" counts as missing.* [ORACLE]
- **O-equiv-value — Compare configured values semantically, not as strings.** Normalize
  both sides to canonical units before comparing — `1.5GiB`==`1536MiB`==`1610612736`,
  `1m30s`==`90s`, `on`==`true`. A raw string compare false-fails an agent who chose an
  equally-valid spelling the prompt never forbade; a genuinely wrong magnitude still
  differs after normalization. *Example: a CockroachDB setting graded where the agent
  wrote `64MiB` and the cluster echoes `67108864`.* [ORACLE]
- **O-mgmt-order — Probe an admin/management API in the order the app serves by
  default, not always TLS-first.** O-scheme's https-first fits a console that redirects
  plain→TLS, but a management API defaulting to a *plain* port should be probed
  plain-first: a TLS listener a later stage brings up can answer the reachability probe
  yet then drag every real query past its deadline (curl exit 28). Pick the
  default-serving scheme first, fall back, accept any HTTP code (incl. 401), bound each
  attempt. *Example: a RabbitMQ oracle that tries the plain mgmt port before the TLS one
  so it works both before and after a mgmt-TLS stage.* [ORACLE]
- **O-client-bounce — When the graded outcome is a client reacting to the agent's fix,
  bounce the client once to escape CrashLoopBackOff before polling.** A side-car
  producer/consumer crash-looping against the *broken* baseline accumulates exponential
  backoff, so after the agent's fix its next successful restart can be minutes away.
  Delete the client pods once (their Deployment recreates them immediately, backoff
  reset) and re-evaluate to a bounded deadline. Distinct from O-restart (which bounces
  the *primary* workload to prove persistence). *Example: a RabbitMQ permission-fix case
  whose clients are CrashLooping against the wrong perms; the oracle deletes them so
  they retry under the corrected perms.* [ORACLE]
- **O-drift-source — When grading a remediation, assert the drift *source* is gone, not
  just the current snapshot.** If a case ships a self-healing reconciler (a CronJob, an
  operator, an init-loop) that re-applies the broken state, an oracle reading only the
  live value passes on a momentary good snapshot the reconciler then overwrites — a
  false pass that also trips the sweep. Verify both: the value is correct AND no
  reconciler is still configured to re-impose the fault. *Example: a RabbitMQ
  permission-fix case shipping a reloader CronJob; the oracle fails unless its script no
  longer enforces the wrong permissions.* [ORACLE]
- **O-job — Grade a one-shot batch workload by terminal completion *and* its result
  payload, within a budget.** A batch job (a Spark driver, a Ray job, an ETL run)
  differs from a long-lived service (O-funcready) and an async signal (O-async): assert
  it reaches a terminal success state within a generous cold-cluster budget AND emits
  the promised result (a sentinel line, a numeric result in range, an expected token).
  `succeeded>=1`/exit-0 alone false-passes a job that finished with wrong output; an
  immediate read false-fails one still running. *Example: a compute job whose oracle
  checks `Job.status.succeeded` only, passing a driver that finished but logged a wrong
  numeric answer; the fix also greps the log for the in-range result.* [ORACLE]

---

## V. Composition & workflow rules

- **C1 — Bring every oracle dependency additively.** Anything the oracle reads
  (secrets, ConfigMaps, baselines, seed data, users/roles, a `monitoring`
  namespace) must be (re)established by its **own additive, idempotent,
  artifact-gated** precondition unit — never only inside the destructive build that
  the skip-gate bypasses on an inherited cluster. The unit must be data-only and
  never delete a namespace or restart a pod. [COMPOSITION]
- **C2 — Identity contract across stages.** If stage A lets the **agent build** a
  resource and a later stage must find it, both ends must share the identity. The
  CockroachDB deploy→initialize seam: a `deploy` stage graded by the STS's *own* selector while
  an `initialize` stage hardcodes `-l app.kubernetes.io/name=cockroachdb` → "Expected 3,
  found 0" against a healthy cluster. Fix both: mandate the canonical labels in the
  creating stage's prompt+oracle, *and* have downstream oracles resolve by the live
  STS selector → canonical label → name prefix. [COMPOSITION]
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
- **C5 — Don't chain contradictory stages.** A successor's oracle preconditions
  must be satisfiable by the predecessor's end-state (a snapshot stage expecting ≥2
  nodes after a downscale-to-1; a seed-hosts-repair with `es-http` after a
  `search-*` topology). A workflow linter should reject these. [COMPOSITION]
- **C6 — Order-sensitive / un-recoverable-input cases: bring your own, or curate
  out.** When a case needs an input the running cluster legitimately doesn't retain
  (a CA **private key**, an original password, an old version/FCV, an admin user),
  it must **establish its own** additively when absent — or, if that's physically
  impossible (FCV downgrade; a base older than the chain leaves), be **curated out**
  of incompatible workflows. Don't fake it destructively. [COMPOSITION]
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
- **C9 — Don't retry stages whose precondition plants non-reentrant state.** A
  break-then-fix case can't re-break what attempt-1 fixed; a genuine agent miss
  then masquerades as "precondition units failed." Default the suite to
  `retries: 0`; keep retry as a workflow-level `max_attempts` only where setup is
  idempotent. [COMPOSITION]
- **C10 — Pin a downstream verification stage's target to the *last* upstream stage
  that mutates the checked attribute** — not the first stage to reach the target
  value. A later same-attribute stage can re-mutate it: a `version-check` pinned to a
  `partitioned-update`'s `24.1.1` fails when a subsequent `major-upgrade-finalize`
  (defaulting to `24.1.0`) legitimately sets the version *back down* — the effective
  end-state is the last mutator's value, not the first's. Pin to the last mutator
  (declaratively via C10b's `${stages.<id>.params.<n>}` so it can't drift), audit any
  pin whose justifying comment names multiple upstream stages with conflicting param
  values, and sweep *all* workflows for the mismatch. Drop SETUP assertions an
  upstream stage can legitimately invalidate; keep only the agent's-task assertions.
  [COMPOSITION]
- **C10b — Pin a downstream target *declaratively* with `${stages.<id>.params.<n>}`,
  never a re-typed literal.** A stage's `param_overrides` may reference a prior
  stage's resolved param (`target_version: ${stages.stage_03.params.crdb_version}`);
  the resolver rejects self/forward refs and unknown ids, requires the **exact
  zero-padded** id (like ADV4), and only *warns* (resolving to `null`) when an
  intervening overlapping-namespace stage may have invalidated the source — treat
  that warning as a chaining bug. This is the wiring that makes C10 robust instead of
  a hand-copied default that silently drifts. [COMPOSITION]
- **C-sweep — A stage composed *before* a legitimate shared-state mutator trips the
  regression sweep; design it to be adjudicable.** The final sweep re-runs every
  passed stage's oracle against end-of-run state, so an early oracle that asserts an
  exact value a later stage is *meant* to overwrite (a rotated secret, a re-scaled
  topology, a capstone ConfigMap) shows as a sweep "failure" the LLM must rule a
  false positive — and it can only do so if the later stage's prompt makes the
  overwrite clear. Either say so in the prompt or drop the brittle SETUP assertion
  (cf. C10); a real regression should still fail the sweep. [COMPOSITION]
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
oracle correctly fails a would-be-correct solve. The runtime appends an agent-scoped
system prompt telling the model to poll async ops to completion synchronously (cases
untouched). **Triage tell:** a multi-step mutation consistently left *half*-done (one
pod un-restarted, a rollout mid-flight) is this, not the case.

**Metrics read the proxy snapshot's `verb`/`resource`** (lowercase kubectl verb,
plural resource name) — never HTTP `method`/`kind` (a plugin matching those silently
scores 1.0 forever), and never anything inside a `kubectl exec` (verb=`exec` → metric
sees zero mutations; grade in-pod work in the oracle — O-exec-metric).

**Retry correctness.** A retried stage clears its stale `submit.txt` before
relaunch (else it "submits in 0s" against the prior attempt's file).

**Namespace lifecycle.** Teardown deletes every namespace created since a
post-binding baseline (guarding system namespaces), deferred to workflow end;
run-id keeps an 8-char hash so retries don't collide; `create namespace` retries
twice on non-`AlreadyExists`.

**`required_roles`/`namespace_roles`: explicit `[]` vs `None`** — respected across
every consumer (resolve, single-case, run_stage, sweep binding, cleanup, alias).


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
      call ping-gated (P11); flap-retry every oracle reachability check (O-flap),
      bounded exec/curl (O-bound), oracle budget sized for exec count (O-budget).
- [ ] **Oracle:** grades only the prompt's promise (O1); resolves from live state
      scoped to the target (O2); proper client identity / mode / pod-local fallback
      (O-tls, O-direct, O-primary, O-scheme, O-pod-local); accepts equivalents
      (O-equiv) and inspects *all* entries of a multi-valued artifact (O-multi);
      internal retry/wait loop finishes before `timeout_sec` (O-deadline) and any
      oracle-initiated pod restart is budgeted for the worst case (O-restart);
      deterministic-vs-transient discipline (O3); escaped jsonpath + imports
      (O-jsonpath, O-imports).
- [ ] **Literals:** roles/versions/units/SAs validated real (P17); versions
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
      fix, not retries (O3); re-validate after every fix (a fix unmasks the next
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
4. **Flap + tight budgets (O-flap)** — single-snapshot node/health checks race
   convergence; align client `--max-time` > server `wait_for_*`; size retry
   windows for the loaded composed cluster.
Also: allocation-attribute oracles must flag a (key,value) on any *new* node no
*original* carries (incl. a brand-new key) and exclude ES built-ins
(`transform.node`, `ml.*`); seed `-r viewer` not `read`; quote `&` in health URLs.

### MongoDB — TLS, primary, and auth (18 cases)
- **TLS client cert (O-tls)** — ES/Mongo TLS client-cert handling: present the
  client cert the cluster expects, as CLI flags (not URI), cached per pod. Consumer
  oracles relax cert checks (O-consumer); read with no URI/directConnection
  (O-direct).
- **Primary after election (O-primary)** — detect `db.hello().isWritablePrimary`;
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
  (P-secure); mode-adaptive probes/oracles (`--certs-dir` vs `--insecure`, C4);
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
  management API; bound every oracle exec/`s_client` (O-bound).

### nginx-ingress (10 cases) · Ray · Spark
- nginx: rate-limit accepts 429 **or** 503, prove with an unpaced burst (O-equiv);
  otel needs traffic + re-poll (O-async); self-derive ingress node IP/HTTPS port;
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
resources the deploy actually creates. — And if a case relies on the
`decoy_integrity` metric to catch a careless mutation, the decoy must live in its
**own namespace**: the metric keys on the decoy's `namespace`, so a decoy planted
into the graded/role-bound namespace scores nothing.
