# Repeat Matrix Temp Bundle

This folder is a tracked copy of the reusable repeat/self-loop helpers that normally live under `.benchmark/smoke/`, which is gitignored.

## What Is Included

- `run_repeat_matrix.py`
  - Bulk runner for the active repeat/self-loop matrix.
  - Writes generated workflow JSON files into `generated/`.
  - Writes the latest summary to `repeat_matrix_summary.json`.
- `run_param_sweep_examples.py`
  - Helper runner for the hand-written parameterized repeat workflows in
    `examples/`.
  - Uses the correct absolute stage-agent path automatically.
  - Writes the latest summary to `param_sweep_examples_summary.json`.
- `run_param_sweep_matrix.py`
  - Bulk runner for parameter-swept repeat workflows across the active testcase
    corpus.
  - Uses testcase-declared timeout budgets by default.
  - For the local workflow profile, auto-targets the current kube-apiserver
    directly instead of depending on local proxy autostart.
  - Still supports the older capped fast-fail mode with
    `--timeout-profile fixed_fast_fail`.
- `repeat_stage_agent.py`
  - Stage agent with the smoke solvers used by repeat workflows.
- `local_workflow_profile.yaml`
  - Local workflow-run profile used by the matrix runner.
- `examples/`
  - Hand-written example repeat workflows for a few representative cases.
- `generated/`
  - Output directory used by the copied matrix runner.

## What Is Intentionally Not Copied

- Historical run artifacts such as old `repeat_matrix_summary.json` contents.
- Dated reports such as `.benchmark/smoke/repeat_matrix_report_2026-03-20.md`.
- `__pycache__/`.
- The large set of previously generated `repeat_*.json` files, because this copied runner regenerates them locally in `generated/`.

## When To Use This Folder

Use this folder when you want a tracked, shareable copy of the self-loop tooling without relying on the gitignored `.benchmark/` tree.

Good situations:

- another human or AI needs to rerun the 80-case repeat matrix from a fresh clone
- you want to run a subset of repeat/self-loop cases locally
- you want example repeat workflows for manual `workflow-run` testing
- you want a starting point for future repeat-matrix improvements without digging through ignored files

## Prerequisites

- a working local cluster and kubeconfig
- repository dependencies installed, including `.venv`
- `python3` available
- `orchestrator.py workflow-run` working in this repo
- if you run from a restricted sandbox, you may still need an unsandboxed run
  path so bundled `kubectl` steps can reach the local Kind API

## Common Commands

Run all runnable repeat/self-loop cases:

```bash
.venv/bin/python temp_testing/repeat_matrix/run_repeat_matrix.py --runnable-only
```

Run one service:

```bash
.venv/bin/python temp_testing/repeat_matrix/run_repeat_matrix.py ray
```

Run one exact case:

```bash
.venv/bin/python temp_testing/repeat_matrix/run_repeat_matrix.py ray/cluster_ready
```

Run a manual example workflow:

```bash
.venv/bin/python orchestrator.py workflow-run \
  --profile temp_testing/repeat_matrix/local_workflow_profile.yaml \
  --workflow temp_testing/repeat_matrix/examples/repeat_ray_dashboard.yaml \
  --agent-cmd "python3 /absolute/path/to/temp_testing/repeat_matrix/repeat_stage_agent.py --solver ray_dashboard"
```

List the hand-written parameter sweep examples:

```bash
.venv/bin/python temp_testing/repeat_matrix/run_param_sweep_examples.py --list
```

Run one hand-written parameter sweep example:

```bash
.venv/bin/python temp_testing/repeat_matrix/run_param_sweep_examples.py repeat_ray_job_execution_param_sweep
```

Run one parameter-sweep family with the default declared timeout budget:

```bash
.venv/bin/python temp_testing/repeat_matrix/run_param_sweep_matrix.py rabbitmq-experiments
```

Run the older capped fast-fail sweep mode explicitly:

```bash
.venv/bin/python temp_testing/repeat_matrix/run_param_sweep_matrix.py \
  --timeout-profile fixed_fast_fail \
  rabbitmq-experiments
```

## Output Locations

- generated workflows: `temp_testing/repeat_matrix/generated/`
- latest matrix summary: `temp_testing/repeat_matrix/repeat_matrix_summary.json`
- latest parameter-sweep example summary: `temp_testing/repeat_matrix/param_sweep_examples_summary.json`
- latest parameter-sweep matrix summary: `temp_testing/repeat_matrix/param_sweep_matrix_summary.json`
- full workflow run logs: `runs/`

## Maintenance Notes

- The canonical working copy still lives under `.benchmark/smoke/`.
- This folder is a tracked temp/testing bundle for reuse and handoff.
- If the ignored source helpers change later, update this bundle deliberately rather than assuming it stays in sync automatically.
