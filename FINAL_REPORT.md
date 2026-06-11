# KARMA — Real‑Agent Validation & Hardening: Final Report

Agent model used throughout: **`claude --model sonnet`** (Claude Sonnet) via the
`claude_code` agent in local sandbox. Runs live in `runs/` (deduped to the
latest run per case). Every fix was referenced against the old monolith at
`../kubernetes-microservice-benchmark` and verified with a real agent.

## 1. Headline results

**Real‑agent pass rate: 58/79 (73%)** on the full case suite (latest verdict per
case, after fixes). The earlier raw figure (33/70) was depressed by an
intermittent monthly **spend‑limit** (18 spoiled runs) and by bugs since fixed.

| Service | Pass | Notes |
|---|---|---|
| demo | 2/2 | |
| spark | 5/6 | |
| ray | 6/7 | |
| cockroachdb | 11/13 | up from 4/13 |
| mongodb | 14/18 | up from 10/18 (3 case fixes + time) |
| elasticsearch | 11/16 | |
| rabbitmq | 4/9 | deep porting bugs remain (reported) |
| nginx‑ingress | 4/7 | replica_hard needs multi‑node (reported) |
| adversary‑capstone | 1/1 | 10 stages, 2 injections, sweep — all pass |

## 2. Framework regressions found + fixed (the core wins)

Both were **dropped features** from the `app/` → `karma/` refactor, surfaced only
by *real‑agent* runs (unit tests and no‑agent runs missed them).

1. **`required_roles: []` namespace bug** (68 literal‑namespace cases). `resolve`
   + `run_stage` + sweeps treated explicit `[]` as falsy and bound a `default`
   role, setting `BENCH_NAMESPACE` to a `karma-*` namespace — so oracles using
   `bench_namespace("spark-pi")` queried the wrong namespace and got NotFound on
   resources that were right there. Fixed to respect explicit `[]`.
2. **Per‑command timeout regression.** The old `default_timeout_sec_for_command`
   inferred each command's timeout from its verb (wait/rollout → 15 min, exec →
   5 min, /bin/sh → 5 min); the refactor flattened it to **120s**, killing slow
   cluster‑startup commands despite `kubectl --timeout=600s`. Restored
   (verb‑scanned so the `-n <ns>` flag value isn't mistaken for the subcommand).

## 3. Case bugs

**Fixed + real‑agent‑verified (3):**
- `mongodb/deploy` — precondition `verify` required Running pods the agent
  hasn't created yet (deploy task). Verify the namespace instead. → passes.
- `mongodb/statefulset-customization` — precondition deliberately breaks the STS
  for the agent to fix, but `verify` checked for Running pods. Verify the
  baseline STS exists. → **oracle passes**.
- `mongodb/external-access-horizons` — `node_ip` excluded control‑plane nodes,
  empty on single‑node kind → `exit 1`. Fall back to the control‑plane IP. →
  passes.

**Reported (deep porting bugs / environment — not framework):**
- `cockroachdb/certificate-rotation` — the cockroachdb pod with TLS never
  starts (cert pipeline collapsed in the port) → exec "container not found (db)".
- `rabbitmq/manual_tls_rotation`, `manual_backup_restore` — the resource‑creation
  steps were replaced by a broken `setup_precondition_check.py --apply` (the
  script is check‑only, no `--apply` in either repo), so the TLS secret / PVC is
  never created → rabbitmq pods stuck ContainerCreating.
- `rabbitmq/blue_green_migration` — the `rabbitmq-green` statefulset is never
  created (blue/green setup gap).
- `nginx/rate_limit_replica_hard` — needs 3 ingress‑controller replicas; only 1
  becomes Ready on a single‑node kind cluster (environment, not code).
- Oracle bugs: `nginx/renew_tls_secret` (oracle aborts on its own missing
  `INGRESS_NODE_IP`/`HTTPS_PORT` env); `nginx/otel_log_format` (needs traffic gen).

> Note: several of my *initial* triage diagnoses were wrong (e.g. external‑access
> `$node_ip`, rabbitmq "no matching resources" was normal probe behavior). The
> old‑repo diff + real‑agent re‑runs corrected them — which is exactly why
> empirical verification mattered.

## 4. Agent capability (the benchmark signal)

Of the genuine agent failures, **time was usually not the limiter**: 5 of 7
"time‑up" cases (cockroachdb/generate‑cert, mongodb/{password‑rotation,
version‑upgrade‑hard}, elasticsearch/full‑restart‑ha‑hard, ray/deploy_cluster)
**pass cleanly when given a fair attempt** — they weren't stuck, just needed the
run. Only `elasticsearch/safe-downscale-with-shard-migration` and
`rabbitmq/manual_skip_upgrade` genuinely time out (≥600–750s). The verbose
`agent.log` (now captured) shows the per‑case behavior.

## 5. Feature work delivered

- **Agent log** — `claude_code` entrypoint now streams the full turn‑by‑turn
  (reasoning + every tool_use/tool_result) to `agent.log` in real time via `tee`,
  so even killed/timed‑out runs keep their partial log.
- **Retry on oracle fail** — restored as a workflow‑level, stage‑agnostic
  `max_attempts` (default 1), surfaced in `run-workflow --max-attempts`, the HTTP
  API, and a Run Config UI control. Each attempt re‑runs the whole stage.
- **Judge** — 0–100 score (0.1 precision); input now includes the agent log and
  the regression‑sweep result; the Results‑page "target path not found" bug
  fixed; judges failed runs too.
- **Adversary capstone** — a 10‑stage workflow with 2 (overlapping) injections,
  validating the previously‑untested adversary inject/lift, regression sweep, and
  judge pipeline end‑to‑end with a real agent (all 10 stages pass).
- **UI** — run cancellation actually stops the run; orphaned‑run reconciliation
  on startup; agent recorded in config; parsed run/workflow names (app · name +
  greyer timestamp); breadcrumb clears on back; adversary injections shown on the
  stage list (tinted boxes, inject/lift marks, legend); clickable stage boxes →
  case detail; "Run" tab renamed "Cases"; flexible param grid; sticky workflow
  header; select + batch‑run saved workflows; run‑detail stage block; and more.

## 6. Operational notes

- Parallel agents burn the monthly **spend limit** ~2× and trip it; it auto‑stops
  the agent (returns a spend‑limit message). Spoiled runs are identifiable and
  re‑runnable once it resets.
- Multi‑node scenarios (nginx 3‑replica) and heavy TLS/PVC cases need either a
  multi‑node cluster or the dropped resource‑creation pipelines restored.

## 7. Follow‑ups (confirmed feasible, not yet done)

- Surface **adversary injection scenarios in the Cases tab** as browseable,
  parameterizable entries (the runtime already supports adversary `param_overrides`).
- Restore the dropped resource‑creation pipelines for the 4 deep rabbitmq/cockroachdb
  TLS/PVC/blue‑green cases.
