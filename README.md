# KARMA

KARMA is a benchmark harness for Kubernetes agent tasks. It lets you run single cases or multi-stage workflows against a real cluster, capture artifacts and metrics, and optionally score the agent trajectory with an LLM judge.

## Repo map

- `main.py`: starts the local web UI.
- `orchestrator.py`: headless CLI entrypoint for `run`, `batch`, and `workflow-run`.
- `app/`: runtime implementation.
  - `app/orchestrator_core/`: CLI parsing, workflow engine, agent runtime, artifact wiring.
  - `app/runner_core/`: UI job model, preview generation, run/job helpers.
  - `app/judge/`: rubric loading, input building, scoring, and judge client.
- `resources/`: benchmark corpus. Each case lives at `resources/<service>/<case>/test.yaml` with supporting files under `resource/`, `oracle/`, `solver/`, and optional `judge.yaml`.
- `workflows/`: multi-stage workflow specs that chain cases together.
- `agent_tests/`: agent container images and wrappers such as `react` and `cli-runner`.
- `profiles/`: reusable CLI run profiles for `orchestrator.py --profile`.
- `docs/`: design docs, architecture notes, and developer runbooks.
- `tests/`: unit and integration coverage.

## Mental model

KARMA has two layers of workload definition:

1. A case in `resources/**/test.yaml` defines one benchmark task.
   It owns preconditions, oracle verification, cleanup, metrics, and prompt text.
2. A workflow in `workflows/*.yaml` chains cases into stages.
   The workflow engine prepares each stage, launches the agent, verifies results, and optionally performs a final sweep across completed stages.

At runtime the orchestrator creates `runs/<run_id>/` with:

- workflow state and transition logs
- per-stage run directories
- an `agent_bundle/` workspace containing `PROMPT.md`, kubeconfig, and submit files
- metrics, snapshots, and optional judge artifacts

## Quick start

Install dependencies and start the UI:

```bash
pip install -r requirements.txt
python3 main.py
```

Then open `http://localhost:8080`.

The UI is the easiest way to inspect services, cases, workflows, and generated CLI commands.

## Local cluster setup

For local benchmark development, create a Kind cluster with:

```bash
./scripts/setup-cluster.sh --provider kind
```

That bootstrap creates a 4-node Kind cluster, waits for core system pods, and runs a DNS smoke.

Setup details and options live in [docs/developer/kind-cluster-setup.md](/Users/junhan.ouyang/personal-code/Karma/docs/developer/kind-cluster-setup.md).

## Common entrypoints

Run a single case:

```bash
python3 orchestrator.py run \
  --service nginx-ingress \
  --case https_ingress_ready
```

Run a workflow:

```bash
python3 orchestrator.py workflow-run \
  --workflow workflows/workflow-demo.yaml
```

Run a workflow through a checked-in profile:

```bash
python3 orchestrator.py workflow-run \
  --profile profiles/debug.yaml \
  --workflow workflows/workflow-demo.yaml
```

Run tests:

```bash
python3 tests/run_unit.py
python3 tests/run_integration.py
```

## Run profiles

`orchestrator.py` supports reusable YAML or JSON profiles through `--profile`.

This is the profile system that matters for commands like:

```bash
python3 orchestrator.py workflow-run --profile profiles/codex.yaml --workflow ...
```

How it works:

- `run`, `batch`, and `workflow-run` all accept `--profile <path>`.
- Profile values are loaded first, then explicit CLI flags override them.
- The simplest profile is a flat mapping whose keys match CLI flag names in snake case, for example `agent_build`, `agent_cmd`, `final_sweep_mode`.
- `command:` is optional but recommended so the file only applies to the intended subcommand.
- Advanced layout is also supported with `common`, `args`, and `commands.workflow-run`, but flat files are easiest to read and ship.
- `workflow` can live in the profile or be passed on the CLI. The CLI also accepts `workflow_path` as an alias.

Minimal example:

```yaml
command: workflow-run
sandbox: docker
agent: cli-runner
agent_build: true
max_attempts: 1
```

Shipped profiles:

- `profiles/debug.yaml`: Docker workflow run with `cli-runner` held open via `sleep 86400` for manual debugging.
- `profiles/codex.yaml`: Docker workflow run with `cli-runner` invoking Codex headlessly inside the agent container.

The profile loader is implemented in `app/orchestrator_core/cli.py`.

## Run profiles vs judge profiles

This repo currently uses the word "profile" in two different places:

- Run profiles: `orchestrator.py --profile profiles/*.yaml`
- Judge rubric profiles: `resources/*/judge_base.yaml` plus per-case `judge.yaml`

Judge rubric profiles are not execution presets. They are rubric overlays used by the LLM judge, for example `rabbitmq_data_plane_migration_v1` or `mongodb_rbac_regression_awareness_v1`.

## Agents

Two important built-in agent paths are:

- `agent_tests/react`: simple LLM-driven ReAct wrapper.
- `agent_tests/cli-runner`: generic CLI container for Codex, Claude Code, and similar tools.

`cli-runner` does not run an agent by itself. You provide `--agent-cmd`, which is why it pairs naturally with run profiles.

## Optional proxy and kubectl tracing

If you want per-run kubectl tracing and proxy-managed kubeconfig setup, use:

```bash
source ./scripts/setup-proxy.sh
```

That prepares `.benchmark/kubeconfig-proxy`, exports the needed environment variables, and enables `action_trace.jsonl` capture for runs.

## Important artifacts

For workflow runs, the first files to inspect are usually:

- `runs/<run_id>/workflow_state.json`
- `runs/<run_id>/workflow_transition.log`
- `runs/<run_id>/submit_results.log`
- `runs/<run_id>/workflow_final_sweep.json`
- `runs/<run_id>/agent_bundle/PROMPT.md`
- `runs/<run_id>/agent_bundle/submit_result.json`
- `runs/<run_id>/stage_runs/<nn>_<stage_id>/preoperation.log`
- `runs/<run_id>/stage_runs/<nn>_<stage_id>/verification_*.log`

## Suggested starting points by task

If you want to understand:

- overall architecture: `docs/overview.md` and `docs/architecture.md`
- how workflows work: `docs/design/workflow-model.md`
- prompt/runtime expectations for agents: `docs/design/workflow-agent-progress-contract.md`
- debugger and manual workflow inspection: `docs/developer/debugging-runbook.md`
- maintainer code map: `docs/developer/internals.md`
- case authoring: `docs/developer/adding-a-test-case.md`

## Typical workflow for repo exploration

If you are new to the repo, this order usually works well:

1. Read `docs/overview.md`.
2. Open one workflow from `workflows/`.
3. Open one case from `resources/<service>/<case>/test.yaml`.
4. Trace `orchestrator.py -> app/orchestrator_core/cli.py -> app/orchestrator_core/workflow_engine.py`.
5. Use the UI or `workflow-run --profile profiles/debug.yaml` to inspect real run artifacts.
