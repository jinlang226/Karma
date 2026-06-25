# KARMA Case Design Encyclopedia

A complete reference for what can go wrong when a KARMA test case is composed into
a multi-stage workflow — and the design rule that prevents each fault. Written for
**both a human author and an AI agent** editing cases, preconditions, oracles,
prompts, adversaries, and the runtime.

It is mined from the entire `refactor` commit history (594 commits): every
`fix(...)` that corrected a real precondition / oracle / composition / framework
fault is distilled here into a deduplicated, case-agnostic rule with its evidence
commit(s). Entries are tagged by **category** — PRECONDITION · ORACLE ·
COMPOSITION · PROMPT · ADVERSARY · FRAMEWORK · TRIAGE — and given a stable id
(e.g. `P3`, `O5`, `C2`) for cross-reference.

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
   when the state is genuinely absent. *(31c4fe8, 624bfbc, f4e5b41, 9796757)*

2. **Pod-Ready ≠ Ready-to-Use.** Applied / scheduled / `condition=ready` does not
   mean the service accepts connections. Every cross-component interaction (apply
   into a fresh namespace, first `mongosh`/`cockroach` call, `wait -l`, an oracle
   query) must tolerate the async-convergence window with **signature-scoped
   retries that never loosen a pass criterion**. *(§VIII transient allowlist;
   verify/error-gate default-retry; flap-retry oracles)*

3. **Explicit empty ≠ missing.** `required_roles: []` ("I manage my own literal
   namespaces") must be distinguished *everywhere* from `None`/absent ("give me a
   default"). Never `x or [default]`; always `if x is None`. *(0ecf92b, b5fea6e,
   7c8fa6c, c23ba96)*

4. **Producer and consumer must agree** — on artifact paths (one canonical
   `protocol.py` helper), on log schema (raw-HTTP vs kubectl-verb), on env-var
   names (`BENCH_*`), and on inline formats (adversary `scenario`/`inject_at_stage`).
   A mismatch fails **silently** (empty logs, zero metrics, dead features) — far
   more dangerous than a crash. *(0286bc0, 30f95e7, 526fda9)*

5. **A silent no-op is a false-pass trap.** An atomic JSON-patch with a bad
   pointer, an unscoped `kubectl` command, an unresolved `{{param}}` token, a
   force-skipped destructive apply on contaminated state, or an adversary that
   reports `ok=True` without running its apply — each lets a *broken* case pass.
   Always verify that setup/injection actually changed state. *(61386287,
   e48a426, 688ae39, eba2912)*

6. **The oracle is authoritative.** The LLM judge can never override an oracle
   verdict; the regression sweep re-runs the oracle against still-live state with
   each stage's real `BENCH_*` bindings; a crashed agent defers to the oracle, not
   a "timeout" label. Oracles must verify the *prompt's* contract, accept all
   valid solution forms, poll volatile state to convergence, and never contradict
   their own precondition. *(460b111b)*

7. **Compose additively or don't compose.** A case is chainable mid-workflow only
   if its precondition can be satisfied *additively* on the inherited cluster and
   every oracle dependency is brought by an additive, skip-gated fixture.
   Destructive / order-sensitive / recovery-from-degraded / version-downgrade
   premises belong at `stage_01` or in short dedicated workflows — **curate them
   out** of marathons. *(53ca124d, 25817195, 6d2e9bf0)*

8. **Fix the pattern, sweep the suite.** Every distinct bug below was fixed across
   *all* affected cases in one sweep, not just the one that failed. A fault in a
   cloned case family (trap cases, helper pods, node-count oracles) exists in all
   of them. *(f4e5b41 27 cases, 1c229739 912 workflows, 4fccd53 11 oracles)*

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
version mismatch). *(52b63cee, a0b7598)*

---

## III. Precondition rules

### Probe semantics & specificity
- **P1 — Skip-probe success is exit-code based; never `|| true`.** The port that
  replaced readiness probes with `kubectl get pods | grep -c Running || true`
  made every probe exit 0, so the harness treated the scenario as set up and
  **skipped the apply** — 57 heavy cases "passed" in ~6s against an empty
  namespace. A fresh (0-pod) namespace must yield non-zero (apply runs); pods
  present → zero (apply skipped). Guarded by `tests/unit/test_case_probes.py`.
  *(6c0ce29) [PRECONDITION]*
- **P2 — Probe for *this case's own named resource*, not "any pod."** `get pods |
  grep -c Running` matches a foreign/leftover cluster in a shared namespace → the
  case runs against the wrong cluster and its oracle can't find its pods. Probe
  `get pod {{params.cluster_prefix}}-0` / `get statefulset <name>`. *(e1291da,
  bbb61ec, 2c1018ea) [PRECONDITION]*
- **P3 — Probe by intent (your own deliverable), not a proxy marker.** A skip-probe
  must test the **exact artifact the oracle checks**, not a *nearby* marker a prior
  stage may have left present. An additive re-plant fixture especially must gate on
  the thing *it* produces (the baseline ConfigMap / secret / seed it writes), never
  on a sibling resource — else on an inherited cluster the proxy is present, the
  fixture skips, and the oracle's dependency is never created. *Evidence: es
  transform-job-recovery — the re-plant fixture skips on "transform `_stats`==200"
  while its real job is to write the `transform-checkpoint` ConfigMap; composed
  after another ES stage the transform is live but the ConfigMap is absent →
  oracle "Unable to read checkpoint_before". Fix: probe `get configmap
  transform-checkpoint`.* *(bb254c58, a36bfe64) [PRECONDITION]*
- **P4 — Force-fail the probe (`exit 1`) when the case must always re-provision**
  a clean namespace and has no idempotent target to detect. *(bbb61ec)
  [PRECONDITION]*
- **P5 — `error`-gate = assertion only; `skip`-gate = repair.** A unit that must
  *mutate* state to restore a baseline must be `on_probe_fail: skip` with a real
  `apply` (the runner only runs `apply` for a skip-gate; an error-gate's apply is
  dead code). Sweep by *behavior* (asserts-broken-baseline), not by grepping
  strings. *(9357407, fb0d798, 81d8747) [PRECONDITION]*
- **P-noop — Verify the fault actually planted.** An atomic JSON-patch with one
  bad pointer (`/.../cases/requests/memory` where `cases` should be `resources`)
  silently no-ops the *whole* patch → the StatefulSet stays healthy → trivial
  pass. *(61386287) [PRECONDITION]*

### Verify must assert the right post-state
- **P6 — Verify what is true *after setup, before the agent acts*.** A case that
  deliberately breaks a StatefulSet must not verify `grep -c Running` (the pods are
  *meant* broken); a case where the agent does the deploy must verify the
  namespace/baseline, not Running pods (else instant precondition error). *(10bbbd63,
  abf32a22, 61386287) [PRECONDITION]*
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
  leaves intact. *Evidence: es master-downscale-voting-exclusions — sets
  `auto_shrink_voting_configuration=false` while healthy, scales the masters 3→1
  (quorum lost → no elected master), then its `voting_drift_ready` verify does
  `GET /_cluster/settings` which returns `503 master_not_discovered` on a
  master-less cluster → the verify never matches → 600s setup timeout, agent never
  runs. (A shorter loop just fails faster — the check is unsatisfiable, not slow.)
  Fix: verify the StatefulSet is at 1 replica (control-plane) and trust the
  pre-scale-down `acknowledged:true`, instead of re-reading cluster settings on the
  broken cluster.* [PRECONDITION] (the deeper root cause behind some P14 "timeouts").
- **P7 — Tolerate (`|| true`) flaky steps the oracle doesn't depend on.** A
  race-prone, non-essential setup step (`ray start` GCS registration) must not
  abort the whole precondition. *(d486d91) [PRECONDITION]*
- **P8 — Additive composition fixtures are strictly best-effort.** A fixture that
  re-establishes an oracle artifact must verify a no-op `true`, `|| true` every
  apply, no `exit 1` — so a slow/missing artifact degrades to a clean oracle FAIL,
  never a framework precondition ERROR (which is worse and regresses a passing
  stage). *(bb06d25b, f176b778, 041974bc) [PRECONDITION]*

### Cluster reuse, namespaces, timing
- **P9 — Be robust to a reused/dirty cluster.** A fixed (case-owned) namespace
  left Active (orphaned by a crashed run) or Terminating makes a bare `create` —
  or an apply racing an async delete — fail. Inside the skip-gate:
  `kubectl delete namespace X --ignore-not-found --wait=false` →
  `kubectl wait --for=delete namespace/X --timeout=300s 2>/dev/null || true` →
  `create`. Also gate the env probe on the namespace being **Active**, so a
  leftover ready helper pod in a dying namespace can't false-skip the rebuild.
  *(624bfbc) [PRECONDITION]*
- **P10 — Namespace teardown must be non-blocking with a real budget.** A bare
  `kubectl delete namespace` blocks until PVC finalizers release (minutes under
  load); with the default 120s command cap it dies mid-wait. Use `--wait=false` +
  a tolerant `wait --for=delete … --timeout=400s || true` + `timeout_sec` ≥460s.
  *(0dea00c, 58c548c, 4618531) [PRECONDITION]*
- **P11 — Don't issue the first DB call the instant the pod is Ready.** Gate the
  first `mongosh`/`rs.initiate`/`cockroach` call with a `ping`-until-ok poll;
  mongosh emits `ECONNREFUSED` (not kubectl's "connection refused"). *(548e0001,
  5dfc4299) [PRECONDITION]* (Law 2.)
- **P12 — Wait at the controller level, not `wait -l` right after apply.** With
  zero pods matching yet, `kubectl wait --for=condition=ready pod -l <label>`
  returns instantly rc=1 ("no matching resources found"). Use
  `kubectl rollout status statefulset/<n>`; before waiting on a *named* pod,
  first poll for it to exist. *(8b4b9d4b, b86d0618, 440dad48) [PRECONDITION]*
- **P13 — Pair every `kubectl wait --timeout=N` with `timeout_sec ≥ N`** and a
  matching inner loop bound; a `--timeout` flag is **not honored** unless the
  unit's budget exceeds it. Size budgets for the *cold + loaded* worst case
  (rollout ≥600s, named-pod ready ≥300s), not the warm-local one — but with a
  ceiling: if it still times out at the final bump, the op is **failing, not
  slow** — read the logs, stop raising. *(8b4b9d4b, 2096ed93, 3515e028, 06be674)
  [PRECONDITION/FRAMEWORK]*
- **P14 — A seed/setup script must finish within its `timeout_sec`.** Keep the
  script's internal retry budget *under* the unit budget. A verify/health inner
  loop that runs *longer* than its unit's `timeout_sec` is killed mid-loop, and the
  harness then **re-runs the whole verify** — so an N-iteration loop that overruns
  multiplies into the precondition cap (`setup timeout: preconditions exceeded
  600s`) and the agent never launches. Size the inner loop strictly below one unit
  budget. *Evidence: es master-downscale-voting-exclusions — a `seq 1 30`×`sleep 3`
  (~240s) verify in a 120s unit was retried ~13× → blew the 600s cap.* *(2c1018ea,
  9c4d44a) [PRECONDITION]*
- **P14b — To set per-unit `retries`/`interval_sec`, author the structured block
  form** `{commands: […], retries: N, interval_sec: M}` for probe/apply/verify — a
  bare string or list carries no per-unit retry budget. A malformed wrapper or wrong
  key surfaces at load as the misleading *"verify command(s) are required"*, not a
  runtime error. *(84acec2 [PRECONDITION])*

### Manifests, literals, identity
- **P15 — Get-or-apply for helper Pods.** `kubectl apply` of a bare helper Pod
  (openssl-toolbox, curl-test, mongo-client, ray-client, file-realm-gen) is
  `Forbidden` when an inherited same-named Pod has a different (immutable) spec.
  Reuse if present, create only if absent. Only `kind: Pod` needs this
  (StatefulSet/Deployment/Service/Secret/ConfigMap are patchable). *(f4e5b41, 27
  cases / 36 sites) [PRECONDITION/COMPOSITION]*
- **P16 — Don't re-apply a whole manifest whose immutable fields a prior stage may
  have changed.** A `kubectl apply -f <full-statefulset>.yaml` onto an inherited
  StatefulSet whose immutable fields differ fails `updates to statefulset spec …
  are forbidden` and aborts the precondition (the agent never runs). Either
  **orphan-delete first** — `kubectl delete sts <x> --cascade=orphan
  --ignore-not-found` (preserves running pods + data PVCs, critical for `emptyDir`
  clusters) — **or patch only the field you need** (`kubectl patch` the readiness
  probe / image) instead of re-applying the whole manifest. *Evidence: mongo
  health-check-recovery stage_11 — re-applies the full manifest after stage_10
  customized the STS → immutable-field Forbidden.* *(7484f69) [COMPOSITION]*
- **P26 — Make shared cluster-scoped applies tolerant; never let them abort a
  precondition.** Cluster-scoped objects (`IngressClass`, `CRD`, `ClusterRole(Binding)`,
  `PersistentVolume`, `StorageClass`, `PriorityClass`) are **not namespaced**, so a
  prior or sibling case on a reused cluster already owns them — and many of their
  fields are immutable. A bare `kubectl apply` then fails (`IngressClass "nginx" …
  spec.controller: field is immutable`) and aborts the whole unit. Wrap such applies
  best-effort (`… || true`) or get-or-skip (`kubectl get ingressclass nginx ||
  apply`); the unit's `verify` (e.g. the controller Deployment is Ready) is the real
  gate. *Evidence: es secure-http-ingress (stage_03) and crdb expose-ingress
  (stage_11) both abort on a pre-existing `IngressClass nginx` left by an nginx
  case.* [PRECONDITION] (cluster-scoped sibling of P15.)
- **P-decoy — Decoy planting aborts the stage on a missing/ill-scoped manifest;
  treat it like a precondition.** `plant_decoys` *raises* (killing the stage before
  the agent runs) when a `decoys:` path is absent or its apply fails, and a decoy
  with no explicit `namespace:` lands in the proxy default, not the role-bound one.
  Validate every decoy path exists, render-resolves, and carries the intended
  namespace — a broken decoy is a silent "stage error," not a graded fail. *(3ca46b9)
  [PRECONDITION]*
  (`viewer`, not the non-existent `read`), image tags, memory units (`512Mi`, not
  bare `512`), service-account names, settings names — a wrong value fails (often
  silently). *(64c28dd6, 023073, 3e9d1d50) [PRECONDITION]*
- **P18 — Parameterize versions/images; don't hardcode.** A skip-gated destructive
  apply pinned to a fixed image clobbers a workflow's version baseline. Use a
  `*_version` param. **Sub-trap:** whitelist the variable *name* in single quotes
  (`envsubst '${BENCH_PARAM_CRDB_VERSION}'`) — an unquoted name is expanded by the
  inner `/bin/sh -c` to `envsubst "23.2.0"` *before* envsubst runs, leaving the
  literal `${…}`. Preserve downward-API refs (`$(POD_NAME)`). *(2c1018ea,
  59f05ac2) [PRECONDITION/COMPOSITION]*
- **P19 — Never bake a mutable secret into a readiness probe** (e.g.
  `${ELASTIC_PASSWORD}`); a later rotate stage breaks it, the pod goes NotReady,
  and the Service loses endpoints. Read the secret live from the mounted file.
  *(78c18c3b) [PRECONDITION]*
- **P20 — Seed idempotently against live state.** Seed against the *quantity the
  oracle checks* (deterministic `_id` + `refresh`; `countDocuments({}) >= N` and
  top-up only the difference) — never blind-POST onto an inherited index (doubles
  3→6) or drop+recreate a collection. *(64c28dd6, 23356a31) [PRECONDITION]*
- **P21 — Don't double-wrap shell commands.** The harness already runs commands in
  a shell; an extra `/bin/sh -c '… mongosh --eval '…''` closes the outer quote →
  `syntax error near unexpected token '('`. Guarded by
  `tests/unit/test_case_command_syntax.py`. *(3ad08b28) [PRECONDITION]*
- **P22 — Quote URLs with `&`/`?`.** `curl …/health?wait_for_status=yellow&timeout=5s`
  backgrounds curl (the `&`) → grep gets no input → burns the budget. *(83b6b77f)
  [PRECONDITION]*
- **P23 — Size scheduling *requests* to fit the target node** (request governs
  scheduling; the DB scales cache to the *limit*, so a small request is safe). 5×2Gi
  requests don't fit a 7.6Gi node → pods Pending. *(2b0045e8) [PRECONDITION]*
- **P24 — TLS re-key without a mixed-CA window.** A CA swap done with rolling
  `kubectl delete pod` leaves old- and new-cert pods that can't handshake. Scale to
  0 first then up, or use `podManagementPolicy: Parallel` (PVCs untouched → data /
  node IDs persist); make `rollout status` best-effort and let a `SELECT 1`/health
  loop be the real gate. *(7b5f47c6, 11ffb019) [PRECONDITION]*
- **P25 — Use portable tool flags.** `openssl x509 -not_before/-not_after` needs
  3.2+; use `openssl ca -startdate/-enddate -days N`. Verify fixture-gen inside the
  *runner* image, not the dev host. *(258ff61) [PRECONDITION]*
- **P-secure — A secure-TLS cert must carry the exact SANs/identity the handshake
  validates, or the node never comes up.** A ported/inlined cert-gen step that drops
  its `subjectAltName` (pod DNS, service name, `localhost`, advertised host) yields a
  cert that *exists* but fails mutual node-to-node TLS — pods stay NotReady and a
  downstream `exec` reports a phantom *"container not found"* (the container never
  started), masquerading as a timing bug. Generate certs from a static reviewed
  gen-script (heredoc SAN config) and verify a real handshake (`SELECT 1` /
  cluster-Ready), not just that the Secret exists. *Evidence: cockroachdb/
  certificate-rotation — inlined node-cert gen lost its SANs → `cockroach init` hit
  "container not found db".* *(9052eb3b) [PRECONDITION]*

---

## IV. Oracle rules

### Grade the contract, from live state, scoped to the target
- **O1 — Grade only what the prompt promised.** Any exact filename, count, label,
  version, role/object name, magic probe value, or `replSetName` the oracle checks
  must appear in the prompt or be planted by the precondition — never left for the
  agent to guess. Prefer grading the **effective outcome** (can read reports;
  denied writes) over an undisclosed identifier. *(e90fa97e, 34c86e3a, 06d3fadc)
  [ORACLE/PROMPT]*
- **O2 — Resolve expectations from live state, scoped to the target object.**
  Never sum a global topology a namespace legitimately accumulates; never hardcode
  a standalone count/name/scheme. Count only StatefulSets backing live
  `_cat/nodes` members (gate on **desired `spec.replicas` of not-being-deleted**
  STSs, *not* transient `status.readyReplicas`); resolve service/version/setting
  from the live cluster; read `BENCH_PARAM_*` with the old value as default.
  **Exception:** where the count/mode *is* the graded outcome (downscale,
  decommission, generate-cert), stay param-first — deriving from live would mask a
  failed operation. *(75fd064c, d219bc52, 77f2d17e, 984967bc, c9aa45fc) [ORACLE]*
- **O-contract — Don't contradict the prompt or your own precondition.** Wrong
  scheme (`http://` vs required HTTPS), wrong hardcoded path, demanding both
  sidecars when the prompt named one, accepting only `--advertise-host` not
  `--advertise-addr=$(hostname -f)`, or asserting backend-TLS the precondition
  deliberately deployed as plain-HTTP — all fail an honest agent. *(06d3fadc,
  52b63cee) [ORACLE]*
- **O-multi — Inspect *every* entry of a multi-valued artifact; accept a valid
  superset.** When the oracle reads something that can legitimately hold more than
  one value — a PEM file (CA **bundle**), a multi-doc YAML, a list, a label set —
  parse **all** entries, don't assume the first/only one. A tool that reads just
  the head (`openssl x509` on a bundle reads only the leading cert) silently grades
  the wrong element, and the agent's correct answer is often a *bundle/superset*
  (old+new for a zero-gap rollover). Assert "the required value is **present among**
  the entries," not "the single value equals X." *Evidence: es rotate-http-certs —
  agent set `ca.crt` to `old-ca + new-ca` (prompt-required trust bundle); oracle
  fingerprinted only the first cert → false "CA fingerprint did not change".*
  [ORACLE]
- **O-relative — Validate against an *absolute* target, not an inherited
  artifact.** A "rotate to ~1y" check that required the new cert to *outlive* the
  inherited old one breaks when chained after a multi-year cert. Derive the target
  from the prompt ("≥10 months from now"), or from the *recorded* baseline for
  relative asks ("+1", "2x") — never the raw inherited value. *(dd18c449,
  6e0734af, a36bfe64) [ORACLE]*

### Connection & client identity
- **O-tls — Connect exactly as the agent's proven command does** (ground truth
  from `agent.log`/`kubectl_log`). Under mutual `requireTLS` a certless or
  wrong-cert connection is dropped (`connection <monitor> … closed`). Pass
  `--tls/--tlsCAFile/--tlsCertificateKeyFile` as **CLI flags** (mongosh *ignores*
  file-path TLS options in a URI; a `mongodb://` URI defaults `tls=false` and
  overrides `--tls`); present the client cert the cluster expects; cache cert paths
  **per target pod** (different pods mount different certs); `test -f` each path so
  standalone stays plain. *(9f9abeb8, 6d59d772, cab1227f, 464f9659) [ORACLE]*
- **O-direct — Don't impose a connection mode the agent never uses.** Reading
  `rs.conf()`/`rs.status()` against default localhost starts replica-set SDAM
  monitoring, which drops under `requireTLS`; a short `serverSelectionTimeoutMS`
  then drops under load. Read with **no URI / no directConnection / default
  timeouts** (as the agent does), from the **first member that answers**.
  *(2c922919, f89c35e5, 9ae2e2d5) [ORACLE]*
- **O-primary — Detect the live primary; don't assume pod-0.** After an election
  (arbiters/scaling stage) the primary moves; primary-only ops execed into a fixed
  `…-replica-0` fail `not primary and secondaryOk=false`. Detect via
  `db.hello().isWritablePrimary` across members (cached); standalone resolves to
  pod-0 unchanged. *(2005e9f3, 0087336) [ORACLE]*
- **O-consumer — Consumer oracles that only need to *connect* should relax cert
  checks** (`--tlsAllowInvalidCertificates/Hostnames`, or `sslmode=require` +
  client cert through a proxy whose hostname a backend cert can't match). Only the
  TLS-*defining* cases keep strict validation. *(6f6eb75d, cfab204e, ca345f82)
  [ORACLE]*
- **O-pod-local — Fall back to pod-local when a Service has no endpoints.** When a
  check goes through a Service that a prior stage may have drained, fall back to
  `kubectl exec <pod> -- curl localhost:<port>`. *(64c28dd6) [ORACLE]*
- **O-scheme — Fetch admin/console endpoints scheme-adaptively** (`https -k -L`
  then `http`) and SQL/HTTP mode-adaptively (`ls ca.crt` → `--certs-dir` vs
  `--insecure`). A secured endpoint 307-redirects plain HTTP to HTML. *(ad1e4176,
  b6c7472f, 69d5c9ca) [ORACLE]*
- **O-binary — Exec a binary only into a pod whose image ships it** (run
  `openssl s_client` from the broker pod, not a curl-only helper). *(45c6683)
  [ORACLE]*

### Robustness & timing
- **O-flap — Poll volatile state to convergence.** Multi-node clusters flap at the
  readiness edge (GC, shard recovery, master election, rolling restart) though
  stably green. Refactor volatile checks into `evaluate()` and re-run for a bounded
  deadline (~75–150s), passing on the first clean snapshot; keep config/cert/count
  checks single-pass. Not a loosening — a genuinely degraded cluster fails every
  attempt. *(c298ae67, 4fccd53, 7c74c4f2) [ORACLE]*
- **O-maxtime — Client `--max-time` must exceed any server-side `wait_for_*`** it
  triggers (else curl exit 28). Shorten the server health timeout (≤10s), raise
  the client deadline (~20s), let the oracle's own loop wait. *(4fccd53) [ORACLE]*
- **O-bound — Bound every exec/curl/`s_client`.** An un-timed `subprocess` /
  `kubectl exec` / `openssl s_client` against a reloading listener hangs to the
  oracle deadline, the uncaught `TimeoutExpired` crashes the *whole* oracle, and
  the false fail cascades to "precondition units failed" on retry. Add `timeout=`,
  `--connect-timeout/--max-time`, `timeout 15 s_client`; catch the exception;
  retry hang/empty as "not converged". *(81d31a1, 23ed4b0) [ORACLE]*
- **O3 — Deterministic ≠ transient.** A check that fails on *every* attempt (and
  on an idle node) has a deterministic root cause — find it from agent ground
  truth; do **not** sweep retries/timeout bumps over it (they were added, were dead
  weight, and were reverted). Retries must never mask a *wrong value* (the
  assertion still runs on the read), and must **never** apply to negative/
  expected-failure checks (an unauthenticated probe, `check_plain_blocked`, an
  invalid-old-password probe). *(2ad0d02→fafc80ad, 2005e9f3) [ORACLE/TRIAGE]*
- **O-restart — When the outcome needs a pod to recover, delete it once** so it
  recreates without accumulated CrashLoopBackOff, then poll. After a restart, poll
  the pod to exist+Ready *and* retry a `SELECT 1`/`ping` (Ready ≠ accepting
  clients). When the **oracle itself** restarts a pod to prove persistence, size
  that readiness wait for the *worst* case it will meet — a **secure**, **loaded**,
  already-**repeatedly-bounced** node can take far longer to drain-rejoin-and-Ready
  than a fresh one (and the wait must stay under the oracle `timeout_sec`, see
  O-deadline). *Evidence: crdb cluster-settings stage_06 — the oracle's own 2nd
  pod-delete `wait_pod_ready(150s)` times out on a secure node bounced across
  stages 04/05/06, failing a correct agent.* *(d1d57ca, ff1033d3) [ORACLE]*
- **O-budget — Size the oracle `timeout_sec` to the number of `kubectl exec`
  round-trips × per-exec latency under load**, with headroom; default the arg to
  `None`, resolve `max(oracle_timeout_sec, Σ per-command + sleeps)`. *(68709b26,
  e985873) [ORACLE/FRAMEWORK]*
- **O-deadline — An oracle's internal retry/flap/wait loop must finish strictly
  *before* its own `timeout_sec`.** If the loop's deadline equals (or exceeds) the
  harness oracle budget, the harness kills the oracle mid-loop and it **never prints
  a verdict** — the result is literally `[timed out after 119s]`, i.e. a correct,
  passing run scored as a fail. Set the internal deadline below `timeout_sec` with
  headroom for the final read + output (e.g. loop ≤90s under a 120s budget), or
  raise `timeout_sec` above the loop. This is the flip side of O-flap (the loop is
  right; its window must fit). *Evidence: es stack-monitoring-sidecars (loop
  deadline 120s == budget) and crdb cluster-settings — both completed the task,
  both killed before the verdict.* [ORACLE/FRAMEWORK]
- **O-equiv — Accept equivalent valid outcomes.** ingress-nginx returns **503**
  (not always 429) on a throttled burst → accept either. To *prove* a rate limit,
  fire an **unpaced burst**, never a fixed-rps cadence that can match the limit (a
  param override of `limit_rps` silently neutered a hardcoded ~2 rps probe).
  *(487fa1f, e22e0a19) [ORACLE]*
- **O-async — Async signals need traffic + re-poll.** A distributed-trace / metrics
  / reload check must drive a small burst and re-poll the collector to a deadline
  (the ingress doesn't sample every request; spans export on a later OTLP batch).
  *(b104243) [ORACLE]*

### Scripting hygiene
- **O-jsonpath — Escape literal dots in jsonpath keys.** `{.data.rollback.sh}`
  parses `rollback.sh` as a nested field → always empty → trap oracles fail even
  when the ConfigMap is correct. Use `{.data.rollback\.sh}`. (Found in 7 services
  / 21 files — sweep when seen.) *(e49cbc8, a5f61a5, 6322351, 2b993dc, 5436107,
  71348049) [ORACLE]*
- **O-imports — Oracle scripts need their imports; lint them.** A missing
  `import os` is a 100% NameError crash; a name collision (`expected_nodes` int
  reused as a list) crashes `range()`. Smoke-compile + name-resolve every
  `oracle.py` before a sweep. *(4e76cbb9, 011c4f6) [ORACLE]*
- **O-seed — Don't depend on a seed count an agent can zero.** Either warn in the
  prompt that the data is load-bearing, or re-seed it idempotently in a problem
  unit, so a (mis)behaving agent's cleanup can't permanently strand the oracle.
  *(23356a31) [ORACLE]*
- **O-exec-metric — Grade in-pod mutations in the oracle; metrics can't see them.**
  Every scoring metric (blast_radius, destructive_ops, decoy_integrity, residual_drift…)
  reads only the kubectl-proxy snapshot's `verb`. A change made via `kubectl exec`
  into a pod (`mongosh`, `rabbitmqctl`, `cockroach sql`, `curl localhost`) records
  `verb=exec`, so a destructive in-pod operation scores a *perfect* blast_radius.
  Never rely on a metric to police an agent whose mutations happen inside a pod —
  assert that contract in the oracle. *(2c5d0ae, 18004bf) [ORACLE/METRICS]*

---

## V. Composition & workflow rules

- **C1 — Bring every oracle dependency additively.** Anything the oracle reads
  (secrets, ConfigMaps, baselines, seed data, users/roles, a `monitoring`
  namespace) must be (re)established by its **own additive, idempotent,
  artifact-gated** precondition unit — never only inside the destructive build that
  the skip-gate bypasses on an inherited cluster. The unit must be data-only and
  never delete a namespace or restart a pod. *(bb254c58, 102ddc2e, 9796757,
  a260ff9d) [COMPOSITION]*
- **C2 — Identity contract across stages.** If stage A lets the **agent build** a
  resource and a later stage must find it, both ends must share the identity. The
  crdb deploy→initialize seam: `deploy` graded by the STS's *own* selector while
  `initialize` hardcoded `-l app.kubernetes.io/name=cockroachdb` → "Expected 3,
  found 0" against a healthy cluster. Fix both: mandate the canonical labels in the
  creating stage's prompt+oracle, *and* have downstream oracles resolve by the live
  STS selector → canonical label → name prefix. *(9c4d44a, 06d3fadc) [COMPOSITION]*
- **C3 — Authoritative skip-probe for the case's own shape.** A lax probe ("6 pods
  Running") skips the build on an *incompatible* inherited topology, leaving the
  oracle's required objects (`es-http`) absent → unresolvable. Probe the exact
  resources the case needs. *(64c28dd6, ac0d4a24, 2c1018ea) [COMPOSITION]*
- **C4 — Adapt to live mode (auth/TLS/insecure); never assume the cluster's mode.**
  A plain probe against an inherited *secured* cluster fails → flips a unit to its
  *destructive* apply → wipes accumulated state and races the async delete. Detect
  live auth (read admin secret), TLS (detect CA path), and insecure-vs-certs mode;
  empty/plain fallback keeps standalone byte-identical. Gate presence on a
  **durable** signal, not a volatile one (`SELECT 1 || get pods | grep -q
  Running`). *(b874edf7, aefecc99, 1172fbfa) [COMPOSITION]*
- **C5 — Don't chain contradictory stages.** A successor's oracle preconditions
  must be satisfiable by the predecessor's end-state (a snapshot stage expecting ≥2
  nodes after a downscale-to-1; a seed-hosts-repair with `es-http` after a
  `search-*` topology). A workflow linter should reject these. *(53ca124d,
  25817195) [COMPOSITION]*
- **C6 — Order-sensitive / un-recoverable-input cases: bring your own, or curate
  out.** When a case needs an input the running cluster legitimately doesn't retain
  (a CA **private key**, an original password, an old version/FCV, an admin user),
  it must **establish its own** additively when absent — or, if that's physically
  impossible (FCV downgrade; a base older than the chain leaves), be **curated out**
  of incompatible workflows. Don't fake it destructively. *(11ffb019, 1363c0da,
  a36bfe64, 6d2e9bf0) [COMPOSITION]*
- **C7 — Restore a runtime feature additively when its baked-in config is skipped.**
  A scenario property baked into the case's own StatefulSet/ConfigMap (the
  `rabbitmq_prometheus` plugin on :15692) silently drops when the apply is skipped
  on a cluster inherited from a stage that omits it. Re-enable at **runtime**
  (`rabbitmqctl eval 'application:ensure_all_started(...)'`) probed on the running
  app set — never restart an `emptyDir` pod or rewrite a read-only file. *(ae4c99b)
  [COMPOSITION]*
- **C8 — Replant the exact drift a break-then-fix case checks.** A bootstrap-
  existence probe ("queue exists") passes on a cluster a prior identical stage
  already healed, so the fault is never re-planted and the oracle passes with zero
  agent action. Probe the *specific faulted state* and re-plant it idempotently.
  The same trap sinks an additive **re-plant fixture** whose probe checks a proxy
  instead of its own deliverable — it skips on the inherited cluster and the
  oracle's baseline/fault is never (re)created (see **P3**). *(88195c8, 9357407,
  3826aeb) [COMPOSITION]*
- **C9 — Don't retry stages whose precondition plants non-reentrant state.** A
  break-then-fix case can't re-break what attempt-1 fixed; a genuine agent miss
  then masquerades as "precondition units failed." Default the suite to
  `retries: 0`; keep retry as a workflow-level `max_attempts` only where setup is
  idempotent. *(9729cd95, abcb902b) [COMPOSITION]*
- **C10 — Pin a downstream verification stage's target to the effective upstream
  target.** A `version-check` stage defaulting to `24.1.0` rejects a cluster a
  prior `partitioned-update` legitimately took to `24.1.1`. Sweep *all* workflows
  for the param-default mismatch. Drop SETUP assertions an upstream stage can
  legitimately invalidate; keep only the agent's-task assertions. *(a0b75982,
  1c229739, 8a1bf243) [COMPOSITION]*
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
  (cf. C10); a real regression should still fail the sweep. *(f1d9e9e) [COMPOSITION]*
- **C11 — Probe/verify the namespace the workload actually runs in.** Multi-namespace
  cases (spark-team-a) must check the core-workload namespace, not the bare
  umbrella one. *(e88e994, 7dbe2a3) [COMPOSITION]*
- **C12 — Multi-namespace cases bind logical roles per stage via `namespace_binding`.**
  A case declares logical roles (`source`/`target`/`default`); the workflow declares
  physical identities (`cluster_a`/`cluster_b`) and a per-stage `namespace_binding`
  maps role→identity, so `${BENCH_NS_SOURCE}`/`${BENCH_NS_TARGET}` resolve (and a
  migration can swap direction stage-to-stage). Omit it and those vars expand
  **empty** (`kubectl -n ''`); dedupe aliased roles to the physical identities at
  teardown. *(4c3011a8) [COMPOSITION]*

---

## VI. Prompt rules

- **PR1 — The prompt is the contract; state the full graded end-state** (including
  removals, negative constraints, planted decoys to clean up, and any exact value
  the oracle checks) — without step-by-step spoilers. Any oracle assertion not
  derivable from the prompt is a benchmark bug. *(5f1578a, 83f3e04c, e90fa97e)*
- **PR2 — Name the path/protocol the oracle reads.** If the oracle curls HTTPS:9200
  or a specific marker file, the prompt must say so (or the oracle must accept the
  conventional default). *(06d3fadc)*
- **PR3 — Concat prompt modes** (`progressive` = own prompt; `concat_stateful` =
  priors prepended with `(STAGE n)`/`(ACTIVE)` markers; `concat_blind` = priors,
  no markers) require the `stage_prompts` list to grow per pass; `rstrip()` each
  rendered prompt at the render boundary. *(89d75ac, f491585)*

---

## VII. Adversary rules

- **ADV1 — Probe polarity is the INVERSE of preconditions.** Precondition:
  probe-pass ⇒ state present ⇒ *skip* apply. Adversary: probe-pass ⇒ target
  reachable ⇒ *run* apply (plant for deploy / remove for lift) + verify. An inject
  that reports `ok=True` *without running its apply* is the tell — test with a
  failing-apply case. *(eba2912)*
- **ADV2 — Honor `on_probe_fail` at the operation-block level; lift defaults to
  `skip`, deploy to `error`.** (The normalizer read it only from inside a probe
  mapping, ignoring all 38 scenarios; lift defaulting to `error` penalized an
  already-remediated fault.) *(bc875d0)*
- **ADV3 — Namespace-scope every adversary `kubectl`** (`-n ${BENCH_NAMESPACE}`),
  use the app's required transport (TLS/auth/insecure mode), and inject values the
  target *accepts* (CockroachDB rejects `max_rate < 1 MiB`). An unscoped command
  hits the host default namespace and produces a vacuous pass. *(e48a426, 2befaff,
  06c9ee9)*
- **ADV4 — Stage refs must string-exactly match a declared zero-padded id that
  exists and is in window order.** `stage_2 ≠ stage_02` aborts the run with no
  `run.json`; a `lift_at_stage` past the last stage never lifts (fault active
  through the final sweep); `null` = clean up at teardown. *(90d12fe, 8acccba)*
- **ADV5 — Inject only where the target exists; scope the fault off graded
  resources.** The fault must target a resource present at inject time and **not**
  graded by any stage in the inject..lift window; for scenarios whose target may be
  absent, set the deploy probe `on_probe_fail: skip`. `restore_replicas` at lift
  must equal the live count at that point (account for earlier scaling). *(e22e0a19,
  28d5458, 1eb377f)*
- **ADV6 — Verify must positively assert the fault planted/removed**, tolerant of
  empty values (never `grep -qx ''` on a possibly-empty stream); write verify as a
  literal block scalar to avoid nested-quote fragility. *(a974b32, bc875d0)*

---

## VIII. Framework reference — *is this a framework bug, not my case?*

Behaviors the runtime now guarantees. If a failure matches one of these, it's
framework, not the case (most are already fixed; listed so you can recognize the
signature and, for older checkouts, know what to verify).

**Persistence / probe doctrine.** `on_probe_fail` semantics (the refactor once
*inverted* this, so seeding silently never ran in 78/79 cases): probe **passes** →
skip apply; probe **fails** + `skip` (default) → run apply; probe **fails** +
`error` → fatal gate. *(31c4fe8)*

**Transient-apply retry allowlist** (`_is_transient_apply_error`, `case.py`;
8×/6s). Each substring is a real race; a genuine error has no signature and fails
fast. pod-Ready/object-applied ≠ ready-to-use, and the allowlist must cover
*client-tool* phrasings:

| Substring | Race | Commit |
|---|---|---|
| `error looking up service account` / `serviceaccount "default" not found` | default SA not provisioned when a pod applies right after `create namespace` | e667697 |
| `connection refused` / `could not connect to server` / `no route to host` / `i/o timeout` / `unable to connect to the server` / `the server is currently unable…` | peer/apiserver not yet up, or overloaded | d6a3dea |
| `object is being deleted` | `create namespace` races a prior run's Terminating namespace | bdb3656 |
| `no matching resources found` | `wait -l` before the controller materialized pods → instant rc=1 | 440dad4 |
| `being terminated` | apply into a still-terminating namespace | adfd2e0 |
| `econnrefused` / `server selection` | mongosh/DB-client hits mongod before TCP/TLS up, or election | 333953b |

An oracle-side allowlist `_TRANSIENT_SIGNATURES` (`TLS handshake timeout`,
`dial tcp`, `Client.Timeout exceeded`, `unexpected EOF`, `etcdserver: request
timed out`, …) re-runs the *verdict* on a transient oracle FAIL. *(48cec9d)*

**Verify / error-gate default-retry.** The normalizer once hardcoded
`verify_retries=1`; now verify defaults to retry (24×/5s ≈120s) and error-gates
retry up to `verify_retries`, so async convergence doesn't false-fail. Auto-budget
is `verify_once + interval*retries`. *(f917714, 81d8747)*

**Per-command timeout inference** (`_default_timeout_for_command`; explicit
`timeout_sec` always wins; scans kubectl *tokens* so `-n <ns>` isn't read as the
verb): `wait`/`rollout` 900s; `apply`/`create`/`patch`/`scale`/`set` 120s;
`delete` 180s; `exec` 300s; `get`/`logs`/`describe` 120s; `python` 600s in verify.
*(abdf96d, 9d66dd0)*

**Idle agent timeout.** `agent_timeout_sec` is an *idle* budget that resets when
`agent.log` grows, with an absolute `KARMA_AGENT_HARD_CAP_SEC` (default 3600).
Long cases (rabbitmq multi-hop upgrade ~47 min) need a generous dispatcher
wall-timeout or they're killed mid-run. *(c1662e0)*

**One-shot agent execution.** The agent runs as a single non-interactive `--print`
session — ending the turn exits the process, with **no scheduled wakeup**. A model
that offloads a long wait (a rolling restart, a rollout) to a "background task" and
returns abandons the rest of the task, so the mutation lands **half-applied** and the
oracle correctly fails a would-be-correct solve. The runtime appends an agent-scoped
system prompt telling the model to poll async ops to completion synchronously (cases
untouched). **Triage tell:** a multi-step mutation consistently left *half*-done (one
pod un-restarted, a rollout mid-flight) is this, not the case. *(bfd37f9)*

**Metrics read the proxy snapshot's `verb`/`resource`** (lowercase kubectl verb,
plural resource name) — never HTTP `method`/`kind` (a plugin matching those silently
scores 1.0 forever), and never anything inside a `kubectl exec` (verb=`exec` → metric
sees zero mutations; grade in-pod work in the oracle — O-exec-metric). *(f414be82,
2c5d0ae)*

**Retry correctness.** A retried stage clears its stale `submit.txt` before
relaunch (else it "submits in 0s" against the prior attempt's file). *(6e078a7)*

**Namespace lifecycle.** Teardown deletes every namespace created since a
post-binding baseline (guarding system namespaces), deferred to workflow end;
run-id keeps an 8-char hash so retries don't collide; `create namespace` retries
twice on non-`AlreadyExists`. *(32414ae, 9516dfe, 0891a0b)*

**`required_roles`/`namespace_roles`: explicit `[]` vs `None`** — respected across
every consumer (resolve, single-case, run_stage, sweep binding, cleanup, alias).
*(0ecf92b, b5fea6e, 7c8fa6c, c23ba96)*

**kubectl-proxy** (the largest agent-timeout cause). Two distinct ports (data
`--port` the agent uses + `--control-port`); launch retries 4× re-picking both
ports on `EADDRINUSE`, fails fast via `is_alive()`, gates readiness on a TCP
connect to the **data** port (not the control channel). Streaming: classify
bounded vs unbounded (watch/follow → incremental framing + 600s window), relay
with `read1()` (returns on first data), tunnel `exec`/`attach`/`port-forward` raw
(HTTP 101), re-auth client→upstream, forward `Content-Type`/`Content-Encoding`,
serialize the JSONL log under a lock, bind `0.0.0.0` for docker. *(c0bec94,
db46282, 807a733, 70c3187, 9d04c40, 23ac28c, 8027d1b)*

**Evidence pipeline.** Translate raw-HTTP method+path → kubectl verb/resource for
snapshots; parse logs defensively (per-line skip, decode `errors='replace'`,
no `exists()`-before-read); derive all paths from one `protocol.py` helper (a
double-scoped path silently zeroed all evidence). *(0286bc0, a5b01e2, 30f95e7)*

**Param/substitution.** `{{params.key}}` recursive substitution (dropped in the
refactor → 11 cases ran with literal tokens); unwrap `{default: …}` before
substitution; validate/coerce params by declared `type`/`values`/`min`/`max`/
`required` (coerce only when a `type` is declared); decoys come from explicit
`decoys:` *and* auto-discovered `decoy/*.yaml`. Sweep for unresolved `{{…}}`.
*(90e059a1, ffcedfd, 2dd6c750, 3ca46b9)*

**Guards.** Reject stage-less workflows (`Field(min_length=1)`); a no-agent run
still runs setup+oracle; best-effort steps route through `warn()` (visible, not
silent `except: pass`); cross-stage agent memory via `agent_session: persistent`.
*(faab02c, 8787dd0, 898a547, b4b3d00)*

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
- **TLS client cert (O-tls)** — the `external-access-horizons` saga: present the
  client cert the cluster expects, as CLI flags (not URI), cached per pod. Consumer
  oracles relax cert checks (O-consumer); read with no URI/directConnection
  (O-direct).
- **Primary after election (O-primary)** — detect `db.hello().isWritablePrimary`;
  seed waits for a primary, never defaults to pod-0.
- **Adaptive auth (O2/C4)** — accept both `authSource=admin` and `=<appdb>`; the
  "bring-your-own admin user" fixture (C6/C1) for cases composed after a no-auth
  predecessor; ping-gate before `rs.initiate` (P11).
- **Version-upgrade** FCV downgrade is impossible → curate out of chains past its
  base (C6).

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
  for `manual_tls_rotation` (C6); replant policy/permission drift (C8);
  multi-hop `skip_upgrade` is order-sensitive → curate out (C6); plain-port-first
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
resources the deploy actually creates. *(0a2b05ef)* — And if a case relies on the
`decoy_integrity` metric to catch a careless mutation, the decoy must live in its
**own namespace**: the metric keys on the decoy's `namespace`, so a decoy planted
into the graded/role-bound namespace scores nothing. *(2c5d0ae)*

### Third re-run (`-rerererun`) verified faults → rule (worklist)
All **case-definition** bugs (none were framework bugs); 8 of 9 failures, plus 1
genuine agent fault excluded. Each is the *universal* rule it instances — fix the
pattern across every case, not just the one observed (Law 8).

| Case / stage | Class | Rule | Fix |
| --- | --- | --- | --- |
| es/transform-job-recovery (long stage_11) | WF | P3 | re-plant fixture probes its own ConfigMap, not `transform/_stats` |
| es/secure-http-ingress (stage_03) | precond | P26 | tolerant apply of the shared `IngressClass nginx` |
| crdb/expose-ingress (long stage_11) | precond | P26 | `\|\| true` the kind ingress-deploy apply |
| mongo/health-check-recovery (long stage_11) | WF | P16 | patch the probe, don't re-apply the whole STS |
| es/master-downscale-voting-exclusions (stage_1) | precond | **P27** (+P14) | verify the fault from the control plane (STS at 1 replica) — `/_cluster/settings` is unreadable on the quorum-broken cluster; shorter loop alone just fails faster |
| es/stack-monitoring-sidecars (stage_09) | oracle | O-deadline | flap-loop deadline below `timeout_sec` |
| crdb/cluster-settings (stage_06) | oracle | O-restart, O-deadline | budget the oracle's own pod-restart for the secure/loaded worst case |
| es/rotate-http-certs (stage_03) | oracle | O-multi | fingerprint *all* certs in the `ca.crt` bundle |
| mongo/password-rotation (stage_05) | **AGENT_FAULT** | — | agent fabricated a literal password; leave as a valid measurement |

**Dominant universals this campaign:** P26 (cluster-scoped immutable applies on a
reused cluster — 2 cases), O-deadline (oracle's own loop killed before its verdict —
2 cases), and the P3/C8 "probe your own deliverable" composition trap. Several
others (P14, O-restart) were *incomplete* prior sweeps — the rule existed but hadn't
reached the case.
