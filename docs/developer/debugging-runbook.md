# Debugging Runbook

This guide is for developers debugging benchmark runs and workflow stages in this repo.

It covers:

- Debugger UI usage
- CLI workflow runs
- Manual Docker "act as agent" debugging
- Common stuck states and what they mean

## 1) Pick the Right Mode

Use this quick mapping:

- Fast single-case iteration: Debugger UI Manual Runner
- Workflow chain debugging with stage transitions: Debugger UI Workflow Runner
- Reproducible command-line investigation: `python3 orchestrator.py workflow-run ...`
- Full manual control inside agent container: Docker sandbox + hold command (`sleep`)

## 2) Debugger UI Tips

Start UI:

```bash
pip install -r requirements.txt
python3 main.py
```

Open [http://localhost:8080](http://localhost:8080).

Workflow Runner behavior:

- `Run (Debug)` uses a local debug profile with interactive controls on.
  If no custom `agent_cmd` is provided, it injects a hold command (`sleep 86400`), so no autonomous solver actions run.
  You manually perform cluster operations, then use UI/submit signal to advance.
- `Run (Docker)` uses normal docker workflow profile (interactive controls off by default).

If you need step-by-step manual stage driving, prefer:

- `Run (Debug)` in UI, or
- CLI + explicit docker hold command (section 4).

## 3) CLI Baseline (Workflow)

Standard workflow run:

```bash
python3 orchestrator.py workflow-run \
  --workflow workflows/workflow-demo.yaml
```

Common useful flags:

- `--sandbox local|docker`
- `--submit-timeout`, `--setup-timeout`, `--verify-timeout`, `--cleanup-timeout`
- `--setup-timeout-mode auto|fixed`
- `--agent`, `--agent-build`, `--agent-cmd`

## 4) Manual Docker Debug (Act as Agent)

Purpose: start workflow, keep agent container alive, then `docker exec` into it and manually run commands + submit.

### Step A: Launch with hold command

```bash
python3 orchestrator.py workflow-run \
  --workflow workflows/rabbitmq-upgrade-tls-migration-a-to-b.yaml \
  --sandbox docker \
  --agent cli-runner \
  --agent-build \
  --agent-cmd "sleep 86400" \
  --setup-timeout 180 \
  --setup-timeout-mode auto \
  --submit-timeout 1200 \
  --verify-timeout 180 \
  --cleanup-timeout 180
```

### Step B: Locate latest run bundle

```bash
RUN_DIR=$(ls -dt runs/*workflow_run_* | head -n1)
BUNDLE="$RUN_DIR/agent_bundle"
echo "$RUN_DIR"
ls -1 "$BUNDLE"
```

### Step C: Find running container and exec

```bash
CID=$(docker ps --format '{{.ID}}\t{{.Mounts}}' | grep "$BUNDLE:/workspace" | awk 'NR==1{print $1}')
docker exec -it "$CID" /bin/bash
```

Inside container:

```bash
cd /workspace
ls
```

### Step D: Drive the stage manually

Normal stage loop:

1. Read `PROMPT.md`
2. Run kubectl fixes
3. `touch submit.signal`
4. Wait until `submit.signal` is consumed and `submit.ack` appears
5. Wait for fresh `submit_result.json`
6. Read `submit_result.json` to decide retry/advance/final

If run is at `waiting_start`, you must first:

```bash
touch start.signal
```

This only happens when `--manual-start` is enabled. For normal flow, do not use `--manual-start`.

## 5) Common States and Meaning

- `waiting_start`: orchestrator is waiting for `start.signal`
- `waiting_submit`: stage is ready, waiting for `submit.signal`
- `next_stage_setup_failed`: current stage passed, but next stage setup/preconditions failed
- `workflow_complete`: final stage finished (pass or exhausted failure)

## 6) Files to Inspect First

In run dir (`runs/<...>`):

- `agent_bundle/PROMPT.md`
- `agent_bundle/submit.ack`
- `agent_bundle/submit_result.json`
- `workflow_state.json`
- `workflow_stage_results.jsonl`
- `workflow_transition.log`
- `agent.log`
- `stage_runs/<nn>_<stage_id>/preoperation.log`
- `stage_runs/<nn>_<stage_id>/verification_*.log`
- `stage_runs/<nn>_<stage_id>/cleanup.log`

## 7) Concat Blind Reminder

For `concat_blind` mode:

- Prompt intentionally hides active-stage marker.
- Do not infer progress from prompt text only.
- Use `submit_result.json` (`continue`, `final`, `can_retry`) and `workflow_state.json`.
- Do not rely on stage-identifying fields in agent-bundle `submit_result.json`; they are redacted in `concat_blind`.

## 8) Kubernetes Reality Check

Use host `kubectl` for actual cluster state:

```bash
kubectl get ns
kubectl -n <ns> get pods
kubectl -n <ns> get svc
kubectl -n <ns> logs <pod>
```

This avoids confusion between prompt state and real cluster status.
