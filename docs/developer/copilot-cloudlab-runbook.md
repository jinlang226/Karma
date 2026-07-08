# Copilot Docker workflow runbook for CloudLab

This runbook is for running **real KARMA workflows** with the **Copilot CLI**
agent in **Docker sandbox mode only**, across one or many CloudLab machines.
It avoids host-session leakage, uses a resume-safe JSONL ledger, and keeps each
node restartable without losing completed work.

## What this uses

- KARMA's built-in `copilot` agent:
  - `karma/agents/copilot/entrypoint.sh`
  - `karma/agents/copilot/Dockerfile`
- The distributed campaign manager in:
  - `scripts/remote-agents/manage_copilot_campaign.py`
- The queue runner in:
  - `scripts/remote-agents/run_workflow_queue.py`

The queue runner shells out to `orchestrator.py run-workflow --output json` and
stores one result record per workflow in `results.jsonl`. Re-running the same
command with `--resume` skips completed workflows and continues the shard.

## Authentication and model

For KARMA's Docker Copilot path, use **`GITHUB_TOKEN`** in the environment.
That is the token KARMA forwards into the agent container.

Recommended env file:

```bash
cat > .benchmark/copilot.env <<'EOF'
GITHUB_TOKEN=github_pat_replace_me
KARMA_COPILOT_AGENT_MODEL=gpt-5.2
EOF
```

Notes:

- `gpt-5.1` or `gpt-5.2` are both acceptable if your Copilot entitlement
  exposes them.
- Prefer a **fine-grained PAT with Copilot Requests permission**.
- Do **not** rely on a Copilot auth file for Docker runs; this repo's supported
  path is environment-token based.
- In the validated CloudLab smoke run, the provided PAT worked with Copilot in
  Docker immediately, but `gpt-5.1` was **not available** for that account.
  The same token worked with Copilot's **default model**, so start without
  `KARMA_COPILOT_AGENT_MODEL` unless you have already confirmed model access.

## Verified findings from the real smoke run

These are not just design assumptions; they were observed on
`c220g2-010614.wisc.cloudlab.us`.

- **Copilot Docker auth works** with a local env file containing `GITHUB_TOKEN`.
- **No global export is required** when using `--llm-env-file .benchmark/copilot.env`.
- The Copilot container can:
  - authenticate headlessly,
  - receive the KARMA prompt,
  - talk to the cluster through the injected kubeconfig,
  - and write real `kubectl_log.jsonl` / `agent.log` artifacts under the run dir.
- A real KARMA workflow run created a normal run directory and progressed
  through multiple stages with Copilot submissions and oracle passes before the
  run was manually terminated.
- The queue runner accepts workflow-list entries in the short form
  `pass/<workflow>.yaml` and resolves them to `workflows/pass/...` before
  calling `run-workflow`.

## One-node prerequisite checklist

Run these on the CloudLab node:

```bash
docker version
kind version
kubectl version --client
python3 --version
```

KARMA itself should run from a Python 3.11+ virtualenv:

```bash
cd ~/Karma
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Pre-build the Copilot agent image once per node

Do this once before the batch so workers do not race on image builds:

```bash
cd ~/Karma
docker build -t karma-agent-copilot:latest \
  -f karma/agents/copilot/Dockerfile \
  karma/agents/copilot
```

After the pre-build, **do not** pass `--agent-build` in the queue runner.

## Create worker clusters on each node

For the current CloudLab setup, **start with one cluster per node**. That is
the safest model and already gives 10-way parallelism across 10 nodes.

If the node already has a healthy long-lived `kind` cluster, reuse it:

```bash
kind export kubeconfig --name kind --kubeconfig /tmp/kc-1 \
  || kind get kubeconfig --name kind > /tmp/kc-1
kubectl --kubeconfig /tmp/kc-1 get nodes
```

If the node does **not** have a cluster yet, create one:

```bash
kind create cluster --name kc1 --kubeconfig /tmp/kc-1
```

Observed caveat from the smoke host:

- trying to create an **additional** kind cluster on top of the existing
  long-lived host cluster failed during node boot with a systemd
  `Too many open files` / manager-allocation error.
- So for these nodes, prefer **one cluster per machine** unless you have first
  validated that the host can support more.

Heavy workflows (CockroachDB, Elasticsearch, MongoDB, Ray, Spark, and any
`workflows/long/*`) should still be capped conservatively. With one cluster per
node, start with:

```bash
MAX_HEAVY=1
```

## One-node smoke run

Start with a tiny known-pass shard:

```bash
cat > .benchmark/copilot-smoke.txt <<'EOF'
pass/rabbitmq-observability-rollout-01.yaml
pass/rabbitmq-tls-rotation-sweep-01.yaml
EOF
```

Run:

```bash
cd ~/Karma
set -a
. .benchmark/copilot.env
set +a

python3 scripts/remote-agents/run_workflow_queue.py \
  --workflow-list .benchmark/copilot-smoke.txt \
  --kubeconfigs /tmp/kc-1,/tmp/kc-2 \
  --batch-dir .benchmark/copilot-smoke-batch \
  --runtime-python .venv/bin/python \
  --agent copilot \
  --sandbox docker \
  --runs-dir runs/copilot-smoke \
  --llm-env-file .benchmark/copilot.env \
  --resume \
  --max-heavy 1
```

Artifacts land in:

- `.benchmark/copilot-smoke-batch/results.jsonl`
- `.benchmark/copilot-smoke-batch/summary.json`
- `.benchmark/copilot-smoke-batch/status.json`
- `.benchmark/copilot-smoke-batch/logs/*.stdout.log`
- `.benchmark/copilot-smoke-batch/logs/*.stderr.log`
- `runs/copilot-smoke/<run_id>/...`

During the validated smoke run, the following proof points were observed in the
run directory:

- `stages/*/agent.log` showed Copilot reasoning plus concrete `kubectl` /
  `rabbitmqctl` actions.
- `stages/*/kubectl_log.jsonl` recorded real cluster API activity.
- multiple stages reached `agent: submitted` and `oracle: pass`.

## Failure recovery on one node

The queue runner is append-only and resume-safe:

- `results.jsonl` stores one final record per completed workflow.
- `summary.json` is the rolled-up latest state.
- `status.json` is the live polling file.
- before **every workflow**, it re-checks the cluster, verifies all nodes are
  `Ready`, deletes any leftover non-system namespaces, and records both the
  preflight and post-run cleanup state in the JSONL record.

To resume after node reboot, SSH disconnect, or process crash, run the **same**
command again with the same `--batch-dir`, `--workflow-list`, and `--resume`.
Completed workflows are skipped automatically.

Monitor progress:

```bash
cat .benchmark/copilot-smoke-batch/status.json
tail -n 20 .benchmark/copilot-smoke-batch/logs/*.stderr.log
```

## Manual cleanup after an interrupted run

Normal completed runs clean up their own namespaces and agent containers, but a
manually terminated run can leave the current stage's container and namespace
behind.

On the smoke host, manual interruption left:

- a live `karma-agent-copilot:latest` container,
- and the active workflow namespace still present until explicitly deleted.

Use the specific container ID and namespace name from `docker ps` / `kubectl`:

```bash
docker ps --format '{{.ID}}\t{{.Image}}\t{{.Names}}' | grep karma-agent-copilot
kubectl --kubeconfig /tmp/kc-1 get ns | grep rabbitmq-observability-rollout
docker kill <container_id>
kubectl --kubeconfig /tmp/kc-1 delete namespace <workflow_namespace> --wait=false
```

## Preparing a 10-node campaign

For the full **300-workflow** campaign, the recommended flow is:

1. prepare even shards,
2. sync the auth file + runner + assigned workflow YAMLs to each host,
3. preflight every host with the target model (`gpt-5.3-codex`),
4. stop immediately if any host reports the model unavailable,
5. launch one queue runner per host,
6. poll aggregate progress until `remaining = 0`.

Create a host list:

```bash
cat > .benchmark/cloudlab-hosts.json <<'EOF'
[
  "c220g2-010614.wisc.cloudlab.us",
  "c220g2-011002.wisc.cloudlab.us",
  "c220g2-011306.wisc.cloudlab.us",
  "c220g2-011003.wisc.cloudlab.us",
  "c220g2-010616.wisc.cloudlab.us",
  "c220g2-011017.wisc.cloudlab.us",
  "c220g2-011309.wisc.cloudlab.us",
  "c220g2-011022.wisc.cloudlab.us",
  "c220g2-010613.wisc.cloudlab.us",
  "c220g2-011302.wisc.cloudlab.us"
]
EOF
```

Create the 300-workflow list:

```bash
find workflows/pass -maxdepth 1 -type f -name '*.yaml' \
  | sed 's#^workflows/##' \
  | sort \
  > .benchmark/pass-workflows.txt
wc -l .benchmark/pass-workflows.txt   # expect 300
```

Prepare even shards with the Copilot campaign manager:

```bash
python3 scripts/remote-agents/manage_copilot_campaign.py prepare \
  --batch-dir .benchmark/copilot-campaign \
  --workflow-list .benchmark/pass-workflows.txt \
  --hosts-json .benchmark/cloudlab-hosts.json
```

That generates:

- `.benchmark/copilot-campaign/host-assignments.json`
- `.benchmark/copilot-campaign/shards/shard-01.txt`, ...

Sync the auth file, queue runner, persistent-session runtime files, and
assigned workflow YAMLs to every host:

```bash
python3 scripts/remote-agents/manage_copilot_campaign.py sync \
  --batch-dir .benchmark/copilot-campaign \
  --env-file .benchmark/copilot.env
```

That sync step now also ships the local workflow/runtime files that control
`agent_session: persistent`, so remote hosts inherit the current long-horizon
session behavior instead of silently falling back to stale per-stage defaults.

Preflight every host with the exact requested model:

```bash
python3 scripts/remote-agents/manage_copilot_campaign.py preflight \
  --batch-dir .benchmark/copilot-campaign \
  --copilot-model gpt-5.3-codex
```

Interpretation:

- if every host reports `"model_available": true`, launch the campaign with
  `gpt-5.3-codex`
- if any host reports `"model_available": false`, **stop and tell the user**;
  do not silently fall back during the 300-workflow run

## Launching all 10 nodes

Launch all 10 hosts with one queue worker per node and one reused kind cluster
per node:

```bash
python3 scripts/remote-agents/manage_copilot_campaign.py launch \
  --batch-dir .benchmark/copilot-campaign \
  --copilot-model gpt-5.3-codex \
  --max-heavy 1 \
  --runs-subdir copilot-campaign-300
```

This launch command uses:

- the synced `.benchmark/copilot.env` on each host,
- the per-host shard file,
- `--resume` so the shard is failure-recoverable,
- automatic transient retries: queue-level environment failures and workflow
  precondition failures are retried up to 3 extra times before the shard writes
  one final `results.jsonl` record for that workflow,
- `/tmp/kc-1` as the node's single cluster kubeconfig,
- and `gpt-5.3-codex` as the requested Copilot model.

## Aggregating status across 10 nodes

Poll aggregate campaign status:

```bash
python3 scripts/remote-agents/manage_copilot_campaign.py status \
  --batch-dir .benchmark/copilot-campaign
```

The aggregate output includes:

- total `completed`
- total `remaining`
- total `inflight`
- merged `outcome_counts`
- per-host `status.json` / `summary.json` payloads

## Recovering a failed host

If a host process dies but the machine is still usable:

```bash
python3 scripts/remote-agents/manage_copilot_campaign.py launch \
  --batch-dir .benchmark/copilot-campaign \
  --copilot-model gpt-5.3-codex \
  --max-heavy 1 \
  --runs-subdir copilot-campaign-300
```

Because each host queue uses `results.jsonl` + `--resume`, relaunching is safe:
completed workflows are skipped and only unfinished shard items continue.

If the original host is gone permanently, copy its shard and results ledger to a
replacement host and run the same resume command there. The queue runner only
needs:

- the original shard file
- the original `results.jsonl`
- the original `summary.json` / `status.json` directory

## Interpreting outcomes

The queue runner reports:

- `pass` - all stages reported `status == "pass"`
- `nonpass` - the workflow returned JSON but one or more stages failed
- `error` - launch/runtime error before a clean completed workflow result
- `simulated_pass` - smoke-test mode only

The authoritative workflow artifacts are still under `runs/<campaign>/<run_id>/`.

## Recommended first real campaign

1. Run the one-node smoke shard above.
2. Verify the Copilot container can authenticate with your `GITHUB_TOKEN`.
3. Expand to 2 nodes with 2-4 workflows total.
4. Only then fan out to the full 10-node pass campaign.
